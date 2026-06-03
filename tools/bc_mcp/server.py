"""Basecamp MCP server (stdio).

Exposes read + write tools for the AI_ProjectArchitect Basecamp project.
Auth is delegated to auth.py (CCPP token fetch + cache).

Register in .mcp.json:
    {
      "mcpServers": {
        "basecamp": {
          "command": "python",
          "args": ["tools/bc_mcp/server.py"]
        }
      }
    }
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import api, auth

mcp = FastMCP("basecamp")

DEFAULT_BUCKET = api.DEFAULT_BUCKET


def _slim_todo(t: dict) -> dict:
    """Trim a BC todo dict to the fields callers actually use."""
    return {
        "id": t.get("id"),
        "title": t.get("title") or t.get("content"),
        "completed": t.get("completed"),
        "due_on": t.get("due_on"),
        "url": t.get("app_url"),
        "assignees": [a.get("name") for a in t.get("assignees", [])],
        "description": (t.get("description") or "")[:500],
    }


@mcp.tool()
def health() -> dict:
    """Diagnostic: token cache state + a one-call ping to BC."""
    info = auth.token_info()
    try:
        me = api.get("/my/profile.json")
        info["bc_ping"] = "ok"
        info["bc_identity"] = me.get("name") if isinstance(me, dict) else str(me)[:60]
    except Exception as e:  # noqa: BLE001
        info["bc_ping"] = f"FAIL: {e}"
    return info


@mcp.tool()
def list_todolists(bucket_id: str = DEFAULT_BUCKET, status: str = "active") -> list[dict]:
    """List todolists in a Basecamp project (bucket). status: active|archived|trashed."""
    todoset = api.get(f"/buckets/{bucket_id}/todosets.json")
    # In BC3 a bucket has one Todoset; its `todolists_url` lists the lists.
    if isinstance(todoset, list):
        # `todosets` may return a list when scoped this way; fall through.
        out = []
        for ts in todoset:
            lid = ts.get("id")
            if lid:
                lists = api.paginated_get(
                    f"/buckets/{bucket_id}/todosets/{lid}/todolists.json",
                    {"status": status},
                )
                out.extend(lists)
        return [{"id": x.get("id"), "name": x.get("name"), "todos_url": x.get("todos_url")} for x in out]
    ts_id = todoset.get("id")
    lists = api.paginated_get(
        f"/buckets/{bucket_id}/todosets/{ts_id}/todolists.json", {"status": status}
    )
    return [{"id": x.get("id"), "name": x.get("name"), "completed_ratio": x.get("completed_ratio")} for x in lists]


@mcp.tool()
def list_todos(
    todolist_id: str,
    bucket_id: str = DEFAULT_BUCKET,
    include_completed: bool = False,
    only_completed: bool = False,
    include_archived: bool = False,
) -> list[dict]:
    """List todos in a todolist.

    By default returns only active (non-completed, non-archived) todos —
    Basecamp's `/todos.json` endpoint excludes completed items unless you
    explicitly ask for them via `?completed=true` (a separate query).

      include_completed=True  → also fetch + append completed todos
      only_completed=True     → fetch ONLY completed (overrides include_completed)
      include_archived=True   → also fetch archived todos
    """
    base = f"/buckets/{bucket_id}/todolists/{todolist_id}/todos.json"
    todos: list[dict] = []
    if not only_completed:
        todos += api.paginated_get(base)
    if include_completed or only_completed:
        todos += api.paginated_get(base, {"completed": "true"})
    if include_archived:
        todos += api.paginated_get(base, {"status": "archived"})
    return [_slim_todo(t) for t in todos]


@mcp.tool()
def get_todo(todo_id: str, bucket_id: str = DEFAULT_BUCKET) -> dict:
    """Fetch a single todo with full description + assignees + url."""
    t = api.get(f"/buckets/{bucket_id}/todos/{todo_id}.json")
    return _slim_todo(t) | {"description_full": t.get("description") or ""}


@mcp.tool()
def complete_todo(todo_id: str, bucket_id: str = DEFAULT_BUCKET) -> dict:
    """Mark a todo complete. Returns {ok: true} on 204."""
    api.post(f"/buckets/{bucket_id}/todos/{todo_id}/completion.json")
    return {"ok": True, "todo_id": todo_id, "action": "completed"}


@mcp.tool()
def uncomplete_todo(todo_id: str, bucket_id: str = DEFAULT_BUCKET) -> dict:
    """Reopen a previously-completed todo."""
    api.delete(f"/buckets/{bucket_id}/todos/{todo_id}/completion.json")
    return {"ok": True, "todo_id": todo_id, "action": "reopened"}


@mcp.tool()
def comment_todo(todo_id: str, content_html: str, bucket_id: str = DEFAULT_BUCKET) -> dict:
    """Post a comment on a todo. content_html accepts BC's rich-text subset
    (<div>, <p>, <strong>, <em>, <a>, <code>, <ul>, <li>, etc).
    """
    r = api.post(
        f"/buckets/{bucket_id}/recordings/{todo_id}/comments.json",
        {"content": content_html},
    )
    return {"ok": True, "comment_id": r.get("id") if isinstance(r, dict) else None}


@mcp.tool()
def create_todo(
    todolist_id: str,
    content: str,
    bucket_id: str = DEFAULT_BUCKET,
    description_html: str = "",
    assignee_ids: list[str] | None = None,
    due_on: str = "",
) -> dict:
    """Create a new todo. content = the visible title. due_on = YYYY-MM-DD or ''."""
    body: dict = {"content": content}
    if description_html:
        body["description"] = description_html
    if assignee_ids:
        body["assignee_ids"] = assignee_ids
    if due_on:
        body["due_on"] = due_on
    r = api.post(f"/buckets/{bucket_id}/todolists/{todolist_id}/todos.json", body)
    return _slim_todo(r) if isinstance(r, dict) else {"raw": str(r)[:300]}


@mcp.tool()
def search(query: str) -> list[dict]:
    """Account-wide keyword search. Returns the recordings BC matched."""
    r = api.get("/search.json", {"q": query})
    items = r.get("results", []) if isinstance(r, dict) else (r or [])
    return [
        {
            "id": x.get("id"),
            "type": x.get("type"),
            "title": x.get("title"),
            "url": x.get("app_url"),
            "bucket": (x.get("bucket") or {}).get("name"),
        }
        for x in items
    ][:50]


if __name__ == "__main__":
    mcp.run()
