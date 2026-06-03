"""Basecamp sync — pull a user's assigned todos into the local mirror.

One sync run per user:
  1. Resolve their BC token + BC user id (see tokens.py)
  2. Discover projects the token can see (projects.json)
  3. For each project: walk todoset → todolists → todos
  4. Keep only items assigned to the user
  5. Freshness filter: drop anything not touched in OPS_FRESHNESS_DAYS (default 90)
  6. Upsert into store (idempotent — same row arriving twice converges)
  7. Persist state (last_sync_at, status, counts)

Failures are non-fatal per project — the sync continues and the failed
project gets logged. State reports partial.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import store, tokens

logger = logging.getLogger(__name__)

BC_ACCOUNT_ID = os.environ.get("BC_ACCOUNT_ID", "3945211")
BC_API_BASE = f"https://3.basecampapi.com/{BC_ACCOUNT_ID}"
USER_AGENT = "Advisor Ops Command Center (ali@colaberry.com)"
# "Past month" semantics: only pull tasks with activity in the last N days.
FRESHNESS_DAYS = int(os.environ.get("OPS_FRESHNESS_DAYS", "30"))
# Skip projects whose own updated_at is older than this — saves a lot of
# API calls walking dead projects.
PROJECT_FRESHNESS_DAYS = int(os.environ.get("OPS_PROJECT_FRESHNESS_DAYS", "30"))
HTTP_TIMEOUT = int(os.environ.get("OPS_HTTP_TIMEOUT", "20"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bc_get(path: str, token: str, params: dict | None = None):
    url = f"{BC_API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return None
        raise


def _is_fresh(updated_at: str | None) -> bool:
    if not updated_at:
        return True
    try:
        # Handle both 'Z' suffix and explicit offset
        ts = updated_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def _paginate(path: str, token: str, max_pages: int = 50):
    """Yield items from a paginated BC list endpoint until empty/400."""
    for page in range(1, max_pages + 1):
        chunk = _bc_get(path, token, {"page": page})
        if not chunk:
            return
        yield from chunk


def discover_projects(token: str) -> list[dict]:
    """All projects the token has access to.

    Note: BC's `/projects.json` does NOT accept `?status=active` — that
    returns HTTP 400. The bare endpoint returns all active projects with
    natural pagination. To get archived projects use `?status=archived`
    explicitly.
    """
    out: list[dict] = []
    for proj in _paginate("/projects.json", token):
        out.append(proj)
    return out


def _project_is_recently_active(proj: dict, cutoff: datetime) -> bool:
    """True iff the project's own updated_at is at-or-after the cutoff.
    Used to skip walking projects that have been quiet for the freshness
    window. Conservative: returns True if we can't parse the timestamp
    so we don't accidentally drop projects."""
    raw = proj.get("updated_at") or ""
    if not raw:
        return True
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def pull_todos_for_user(user_id: str, *, ali_legacy_bucket: int | None = None) -> dict:
    """Run a full sync for one user.

    `ali_legacy_bucket` — Phase A escape hatch. When supplied, skips
    `projects.json` (which returns 0 for CB System) and walks a single
    known bucket. Pass None in normal multi-user operation.
    """
    state = store.load_state(user_id)
    token, source = tokens.get_user_token(user_id)
    if not token:
        state.last_sync_status = "failed"
        state.last_sync_error = "token_missing"
        state.last_sync_at = _now_iso()
        store.save_state(state)
        return {"status": "token_missing", "todos": 0, "projects": 0}

    bc_user_id = tokens.get_user_bc_id(user_id)
    if not bc_user_id:
        state.last_sync_status = "failed"
        state.last_sync_error = "bc_user_id_missing"
        state.last_sync_at = _now_iso()
        store.save_state(state)
        return {"status": "bc_user_id_missing", "todos": 0, "projects": 0}

    # 1. Project discovery
    #    a. /projects.json returns everything the token can see (50+ for CB System).
    #    b. Skip projects whose own updated_at is older than PROJECT_FRESHNESS_DAYS
    #       — they're quiet, no point walking their todolists.
    #    c. Always include the user's explicitly-configured extra buckets even
    #       if they aren't in /projects.json (legacy private buckets).
    #    d. Include ali_legacy_bucket if supplied (Phase A demo).
    project_cutoff = datetime.now(timezone.utc) - timedelta(days=PROJECT_FRESHNESS_DAYS)
    projects_raw: list[dict] = []
    seen_buckets: set[int] = set()
    skipped_quiet = 0

    discovered = discover_projects(token)
    for p in discovered:
        bid = p.get("id")
        if not bid or bid in seen_buckets:
            continue
        if not _project_is_recently_active(p, project_cutoff):
            skipped_quiet += 1
            continue
        seen_buckets.add(bid)
        projects_raw.append(p)

    extra_buckets: list[int] = []
    try:
        from execution.products.library import tenancy as _tenancy
        u = _tenancy.get_user(user_id)
        if u and getattr(u, "bc_extra_buckets", None):
            extra_buckets = list(u.bc_extra_buckets)
    except Exception:
        pass
    if ali_legacy_bucket and ali_legacy_bucket not in extra_buckets:
        extra_buckets.append(ali_legacy_bucket)

    for bid in extra_buckets:
        if bid in seen_buckets:
            continue
        single = _bc_get(f"/projects/{bid}.json", token)
        if single:
            seen_buckets.add(bid)
            projects_raw.append(single)

    fresh_projects: list[store.OpsProject] = []
    fresh_todos: list[store.OpsTodo] = []
    partial = False
    project_errors: list[str] = []

    for proj in projects_raw:
        bucket = proj.get("id")
        if not bucket:
            continue
        proj_name = proj.get("name") or f"Project {bucket}"
        fresh_projects.append(store.OpsProject(
            bc_id=bucket,
            name=proj_name,
            description=(proj.get("description") or "")[:500],
            last_synced_at=_now_iso(),
        ))
        try:
            # BC API v3: the todoset id lives in the project's `dock` array,
            # not under a /buckets/{id}/todosets.json collection endpoint.
            # We need the full project payload to extract it.
            proj_full = _bc_get(f"/projects/{bucket}.json", token) or {}
            todoset_dock = next(
                (d for d in proj_full.get("dock", []) if d.get("name") == "todoset"),
                None,
            )
            ts_id = todoset_dock.get("id") if todoset_dock else None
            if not ts_id:
                continue
            lists = _bc_get(f"/buckets/{bucket}/todosets/{ts_id}/todolists.json", token) or []
            for lst in lists:
                lst_id = lst.get("id")
                lst_name = lst.get("name") or "?"
                if not lst_id:
                    continue
                todos = list(_paginate(
                    f"/buckets/{bucket}/todolists/{lst_id}/todos.json",
                    token, max_pages=10,
                ))
                for t in todos:
                    assignees = [a.get("id") for a in (t.get("assignees") or [])]
                    if bc_user_id not in assignees:
                        continue
                    if not _is_fresh(t.get("updated_at")):
                        continue
                    fresh_todos.append(store.OpsTodo(
                        bc_id=t["id"],
                        bc_project_id=bucket,
                        bc_project_name=proj_name,
                        bc_todolist_id=lst_id,
                        bc_todolist_name=lst_name,
                        title=t.get("title") or t.get("content") or "(untitled)",
                        description=(t.get("description") or "")[:5000],
                        status="completed" if t.get("completed") else "active",
                        due_on=t.get("due_on"),
                        assignee_ids=assignees,
                        bc_app_url=t.get("app_url", ""),
                        bc_created_at=t.get("created_at") or "",
                        bc_updated_at=t.get("updated_at") or "",
                        last_synced_at=_now_iso(),
                    ))
        except Exception as e:  # noqa: BLE001 — per-project resilience
            partial = True
            err = f"bucket={bucket} {type(e).__name__}: {str(e)[:120]}"
            project_errors.append(err)
            logger.warning("ops sync: %s", err)

    # 2. Upsert into store
    p_created, p_updated = store.upsert_projects(user_id, fresh_projects)
    t_created, t_updated = store.upsert_todos(user_id, fresh_todos)

    # 3. State
    state.last_sync_at = _now_iso()
    state.last_sync_status = "partial" if partial else "ok"
    state.last_sync_error = "; ".join(project_errors[:3]) if project_errors else ""
    state.todos_synced = len(fresh_todos)
    state.projects_synced = len(fresh_projects)
    store.save_state(state)

    return {
        "status": state.last_sync_status,
        "token_source": source,
        "projects_discovered": len(discovered) if 'discovered' in locals() else 0,
        "projects_quiet_skipped": skipped_quiet,
        "projects_walked": len(projects_raw),
        "projects_created": p_created,
        "projects_updated": p_updated,
        "todos_assigned_to_user": len(fresh_todos),
        "todos_created": t_created,
        "todos_updated": t_updated,
        "errors": project_errors[:3],
    }
