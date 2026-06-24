"""Reconcile a desired-state ``project-plan.json`` into Basecamp.

Deterministic, rerunnable execution layer (CLAUDE.md Layer 3). Maps
initiative→todolist, list→group, todo→todo, and uses ``bc_manifest`` as the
plan-id↔BC-id join table so steady-state runs are O(manifest) with zero writes
for unchanged nodes (hash gate). On a first/interrupted run with no manifest it
adopts existing BC items by breadcrumb (title-within-parent) so it never
double-creates.

All Basecamp HTTP goes through ``mcp_tools._bc_request`` (operator-scoped OAuth,
429/401 self-heal). Endpoint shapes follow
``docs/specs/myday-project-build-bc-api-sequence.md``; the action table follows
``docs/specs/myday-project-build-reconciliation-matrix.md``.
"""
from __future__ import annotations

import html
import logging
import time
from datetime import date, datetime, timedelta, timezone

from execution.advisory import bc_manifest, project_plan
from execution.products.library import mcp_tools

logger = logging.getLogger(__name__)

_THROTTLE_S = 0.22  # ~5 req/s, matches ops/sync.py to stay under BC's budget
AI_EMOJI = "🤖"
HUMAN_EMOJI = "🧑"


# ── small helpers ───────────────────────────────────────────────────

def _base(bucket: int) -> str:
    return f"https://3.basecampapi.com/{mcp_tools._bc_account()}/buckets/{bucket}"


def _due_on(start_date: str, offset_days) -> str | None:
    try:
        d = date.fromisoformat(start_date)
    except (TypeError, ValueError):
        return None
    try:
        off = int(offset_days)
    except (TypeError, ValueError):
        off = 1  # every todo must have a due date; default to +1 day
    return (d + timedelta(days=max(1, off))).isoformat()


def _norm_title(t: str) -> str:
    return " ".join((t or "").strip().casefold().split())


def _strip_render(title: str) -> str:
    """Plan titles are clean; strip any phase tag defensively for matching."""
    return project_plan.strip_phase_tag(title or "")


def _todo_content(node: dict) -> str:
    phase = (node.get("phase") or "BUILD").upper()
    emoji = HUMAN_EMOJI if node.get("kind") == "human" else AI_EMOJI
    return f"[{phase}] {emoji} {_strip_render(node.get('title', ''))}"


def _todo_description(node: dict) -> str:
    tag = "[Human]" if node.get("kind") == "human" else "[AI]"
    parts: list[str] = []
    acc = (node.get("acceptance") or "").strip()
    if acc:
        parts.append(f"<p><strong>Acceptance:</strong> {html.escape(acc)}</p>")
    parts.append(f"<p>Phase: {html.escape((node.get('phase') or 'BUILD').upper())} &middot; {tag}</p>")
    return "".join(parts)


def _resolve_assignee(plan: dict, node: dict, creator_id: int | None) -> int | None:
    """All todos land in the creator's queue; a peopleMap entry can override."""
    people = plan.get("peopleMap") or {}
    logical = node.get("assignee")
    if logical and people.get(logical):
        try:
            return int(people[logical])
        except (TypeError, ValueError):
            pass
    return creator_id


def _paginate(user, url: str):
    """Yield items across BC's page-appended pagination."""
    page = 1
    sep = "&" if "?" in url else "?"
    while True:
        pg = mcp_tools._bc_request("GET", f"{url}{sep}page={page}", user=user)
        if not isinstance(pg, list) or not pg:
            return
        for item in pg:
            yield item
        if len(pg) < 15:  # BC default page size; short page == last page
            return
        page += 1
        time.sleep(_THROTTLE_S)


# ── BC write primitives ─────────────────────────────────────────────

def _discover_todoset(user, bucket: int) -> int | None:
    proj = mcp_tools._bc_request("GET", f"https://3.basecampapi.com/{mcp_tools._bc_account()}/projects/{bucket}.json", user=user)
    for dock in (proj.get("dock") or []):
        if dock.get("name") == "todoset":
            url = dock.get("url") or ""
            # the dock url ends with /todosets/{id}.json
            try:
                return int(url.rstrip(".json").split("/")[-1])
            except (ValueError, IndexError):
                return dock.get("id")
    return None


def _create_todolist(user, bucket, todoset, name, description=""):
    r = mcp_tools._bc_request(
        "POST", f"{_base(bucket)}/todosets/{todoset}/todolists.json",
        payload={"name": name, "description": description}, user=user)
    return r.get("id")


def _create_group(user, bucket, list_bc_id, name):
    r = mcp_tools._bc_request(
        "POST", f"{_base(bucket)}/todolists/{list_bc_id}/groups.json",
        payload={"name": name}, user=user)
    return r.get("id")


def _create_todo(user, bucket, parent_bc_id, content, description, assignee_ids, due_on):
    payload = {"content": content, "description": description}
    if assignee_ids:
        payload["assignee_ids"] = assignee_ids
    if due_on:
        payload["due_on"] = due_on
    r = mcp_tools._bc_request(
        "POST", f"{_base(bucket)}/todolists/{parent_bc_id}/todos.json",
        payload=payload, user=user)
    return r.get("id")


def _update_todo(user, bucket, todo_bc_id, content, description, assignee_ids, due_on):
    # PUT replaces passed fields — always send the full desired set.
    payload = {"content": content, "description": description, "assignee_ids": assignee_ids or []}
    if due_on:
        payload["due_on"] = due_on
    mcp_tools._bc_request("PUT", f"{_base(bucket)}/todos/{todo_bc_id}.json", payload=payload, user=user)


def _archive(user, bucket, bc_id):
    mcp_tools._bc_request("PUT", f"{_base(bucket)}/recordings/{bc_id}/status/archived.json", user=user)


# ── breadcrumb adoption (first / interrupted run) ───────────────────

def _adopt_by_breadcrumb(user, bucket, todoset, plan, manifest):
    """Populate the manifest from existing BC items by matching title within
    parent, so an interrupted first run never double-creates."""
    existing_lists = {_norm_title(l.get("name") or l.get("title")): l.get("id")
                      for l in _paginate(user, f"{_base(bucket)}/todosets/{todoset}/todolists.json")}
    for init in plan.get("initiatives") or []:
        lid = existing_lists.get(_norm_title(init.get("title")))
        if not lid:
            continue
        bc_manifest.upsert_entry(manifest, init["id"], bc_type="todolist", bc_id=lid,
                                 content_hash="", status=init.get("status", "active"))
        existing_groups = {_norm_title(g.get("name") or g.get("title")): g.get("id")
                           for g in _paginate(user, f"{_base(bucket)}/todolists/{lid}/groups.json")}
        for feat in init.get("lists") or []:
            gid = existing_groups.get(_norm_title(feat.get("title")))
            if not gid:
                continue
            bc_manifest.upsert_entry(manifest, feat["id"], bc_type="group", bc_id=gid,
                                     content_hash="", parent_bc_id=lid, status=feat.get("status", "active"))
            existing_todos = {_norm_title(t.get("title") or t.get("content")): t.get("id")
                              for t in _paginate(user, f"{_base(bucket)}/todolists/{gid}/todos.json")}
            for todo in feat.get("todos") or []:
                tid = existing_todos.get(_norm_title(_todo_content(todo))) or \
                    existing_todos.get(_norm_title(todo.get("title")))
                if tid:
                    bc_manifest.upsert_entry(manifest, todo["id"], bc_type="todo", bc_id=tid,
                                             content_hash="", parent_bc_id=gid, status=todo.get("status", "active"))


# ── the reconcile ───────────────────────────────────────────────────

def reconcile(plan: dict, slug: str, user, bucket: int, *, creator_id: int | None = None,
              start_date: str | None = None) -> dict:
    """Create/update/retire Basecamp objects to match the plan. Idempotent.

    Returns a summary {created, updated, skipped, retired, errors}.
    """
    account = mcp_tools._bc_account()
    manifest = bc_manifest.ensure_manifest(slug, bucket, account, start_date=start_date)
    todoset = manifest.get("todoset")
    if not todoset:
        todoset = _discover_todoset(user, bucket)
        manifest["todoset"] = todoset
        bc_manifest.save_manifest(slug, manifest)
    start = manifest.get("startDate")

    if not manifest.get("entries"):
        try:
            _adopt_by_breadcrumb(user, bucket, todoset, plan, manifest)
            bc_manifest.save_manifest(slug, manifest)
        except Exception:
            logger.warning("breadcrumb adoption failed (continuing to create)", exc_info=True)

    summary = {"created": 0, "updated": 0, "skipped": 0, "retired": 0, "errors": []}
    plan_ids: set[str] = set()

    for init in plan.get("initiatives") or []:
        if init.get("status") == "proposed":
            continue
        plan_ids.add(init["id"])
        init_hash = project_plan.content_hash(init)
        m = bc_manifest.get_entry(manifest, init["id"])
        try:
            if not m or not m.get("bcId"):
                lid = _create_todolist(user, bucket, todoset, init["title"], init.get("charter", ""))
                bc_manifest.upsert_entry(manifest, init["id"], bc_type="todolist", bc_id=lid,
                                         content_hash=init_hash, status=init.get("status", "active"))
                summary["created"] += 1
            else:
                lid = m["bcId"]
                if m.get("contentHash") != init_hash:
                    mcp_tools._bc_request("PUT", f"{_base(bucket)}/todolists/{lid}.json",
                                          payload={"name": init["title"], "description": init.get("charter", "")}, user=user)
                    m["contentHash"] = init_hash
                    summary["updated"] += 1
                else:
                    summary["skipped"] += 1
            bc_manifest.save_manifest(slug, manifest)
        except Exception as e:
            summary["errors"].append(f"initiative {init['id']}: {e}")
            continue

        for feat in init.get("lists") or []:
            if feat.get("status") == "proposed":
                continue
            plan_ids.add(feat["id"])
            feat_hash = project_plan.content_hash(feat)
            fm = bc_manifest.get_entry(manifest, feat["id"])
            try:
                if not fm or not fm.get("bcId"):
                    gid = _create_group(user, bucket, lid, feat["title"])
                    bc_manifest.upsert_entry(manifest, feat["id"], bc_type="group", bc_id=gid,
                                             content_hash=feat_hash, parent_bc_id=lid, status=feat.get("status", "active"))
                    summary["created"] += 1
                else:
                    gid = fm["bcId"]
                    if fm.get("contentHash") != feat_hash:
                        mcp_tools._bc_request("PUT", f"{_base(bucket)}/todolists/groups/{gid}.json",
                                              payload={"name": feat["title"]}, user=user)
                        fm["contentHash"] = feat_hash
                        summary["updated"] += 1
                    else:
                        summary["skipped"] += 1
                bc_manifest.save_manifest(slug, manifest)
            except Exception as e:
                summary["errors"].append(f"feature {feat['id']}: {e}")
                continue

            for todo in feat.get("todos") or []:
                if todo.get("status") == "proposed":
                    continue
                plan_ids.add(todo["id"])
                t_hash = project_plan.content_hash(todo)
                tm = bc_manifest.get_entry(manifest, todo["id"])
                content = _todo_content(todo)
                desc = _todo_description(todo)
                assignee = _resolve_assignee(plan, todo, creator_id)
                due_on = _due_on(start, todo.get("dueOffsetDays"))
                try:
                    if not tm or not tm.get("bcId"):
                        tid = _create_todo(user, bucket, gid, content, desc,
                                           [assignee] if assignee else [], due_on)
                        bc_manifest.upsert_entry(manifest, todo["id"], bc_type="todo", bc_id=tid,
                                                 content_hash=t_hash, parent_bc_id=gid,
                                                 status=todo.get("status", "active"), due_on=due_on)
                        summary["created"] += 1
                    elif tm.get("contentHash") != t_hash:
                        _update_todo(user, bucket, tm["bcId"], content, desc,
                                     [assignee] if assignee else [], due_on)
                        tm["contentHash"] = t_hash
                        tm["due_on"] = due_on
                        summary["updated"] += 1
                    else:
                        summary["skipped"] += 1
                    bc_manifest.save_manifest(slug, manifest)
                except Exception as e:
                    summary["errors"].append(f"todo {todo['id']}: {e}")

    # Retire manifest entries no longer in the plan (or now retired).
    retired_in_plan = {n.get("id") for _, n, _ in project_plan.iter_nodes(plan)
                       if n.get("status") == "retired"}
    for node_id, entry in list((manifest.get("entries") or {}).items()):
        if entry.get("status") == "retired":
            continue
        if node_id not in plan_ids or node_id in retired_in_plan:
            try:
                _archive(user, bucket, entry["bcId"])
                entry["status"] = "retired"
                summary["retired"] += 1
                bc_manifest.save_manifest(slug, manifest)
            except Exception as e:
                summary["errors"].append(f"retire {node_id}: {e}")

    manifest["last_reconciled_at"] = bc_manifest.datetime.now(bc_manifest.timezone.utc).isoformat()
    bc_manifest.save_manifest(slug, manifest)
    return summary
