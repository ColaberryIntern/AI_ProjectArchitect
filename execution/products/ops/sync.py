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
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import store, sync_coordinator, tokens

logger = logging.getLogger(__name__)

from collections import deque
from threading import Lock

BC_ACCOUNT_ID = os.environ.get("BC_ACCOUNT_ID", "3945211")
BC_API_BASE = f"https://3.basecampapi.com/{BC_ACCOUNT_ID}"
USER_AGENT = "Advisor Ops Command Center (ali@colaberry.com)"
# "Past month" semantics: only pull tasks with activity in the last N days.
FRESHNESS_DAYS = int(os.environ.get("OPS_FRESHNESS_DAYS", "30"))
# Skip projects whose own updated_at is older than this — saves a lot of
# API calls walking dead projects.
PROJECT_FRESHNESS_DAYS = int(os.environ.get("OPS_PROJECT_FRESHNESS_DAYS", "30"))
HTTP_TIMEOUT = int(os.environ.get("OPS_HTTP_TIMEOUT", "20"))
# Throttle: BC allows ~50 req/10s = ~5 req/s sustained. Sleep between
# every call to stay well under the limit (200ms = 5 req/s).
HTTP_THROTTLE_SECONDS = float(os.environ.get("OPS_HTTP_THROTTLE_SECONDS", "0.22"))
# Retry-after ceiling on 429 — never sleep more than this even if BC says so.
MAX_RETRY_AFTER = int(os.environ.get("OPS_MAX_RETRY_AFTER", "30"))
# L6 (2026-06-09 audit): hard wall-clock budget on a full sync. Without
# this, a BC outage could keep one sync thread alive for nearly an hour
# (HTTP_TIMEOUT=20s * ~150 calls). At budget, we stop walking, record
# 'partial', and release the coordinator slot gracefully. Tunable via
# env so we can lower it temporarily if BC starts misbehaving in prod.
SYNC_BUDGET_SECONDS = float(os.environ.get("OPS_SYNC_BUDGET_SECONDS", "120"))

# Ring buffer of recent errors caught silently by per-project resilience.
# Surfaces via /my-day/_health. Audit 2026-06-04 revealed errors were
# disappearing into try/except without trace; this gives them a home.
_RECENT_ERRORS_LOCK = Lock()
_RECENT_ERRORS: deque = deque(maxlen=50)


def _record_error(user_id: str, kind: str, detail: str) -> None:
    """Stash a silent failure so /my-day/_health can show it."""
    with _RECENT_ERRORS_LOCK:
        _RECENT_ERRORS.append({
            "ts": _now_iso(),
            "user_id": user_id,
            "kind": kind,
            "detail": detail[:300],
        })


def recent_errors() -> list[dict]:
    """Snapshot of the silent-error ring buffer."""
    with _RECENT_ERRORS_LOCK:
        return list(_RECENT_ERRORS)


def clear_recent_errors() -> None:
    with _RECENT_ERRORS_LOCK:
        _RECENT_ERRORS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bc_get(path: str, token: str, params: dict | None = None, _retry: int = 1):
    """GET a BC endpoint. Returns parsed JSON, or None on 400/404.

    Throttles by HTTP_THROTTLE_SECONDS before each call so we don't
    sprint through BC's 50-req-per-10-second budget. On 429 we honor
    Retry-After (capped at MAX_RETRY_AFTER) and retry once.
    """
    url = f"{BC_API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    if HTTP_THROTTLE_SECONDS > 0:
        time.sleep(HTTP_THROTTLE_SECONDS)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return None
        if e.code == 429 and _retry > 0:
            # Respect Retry-After header (BC sends a number of seconds);
            # fall back to a sensible default if missing.
            try:
                wait_for = int(e.headers.get("Retry-After", "5"))
            except (ValueError, TypeError):
                wait_for = 5
            wait_for = min(MAX_RETRY_AFTER, max(1, wait_for))
            logger.info("BC 429 on %s — sleeping %ds then retrying", path, wait_for)
            time.sleep(wait_for)
            return _bc_get(path, token, params, _retry=_retry - 1)
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


def _todo_is_relevant(todo: dict) -> bool:
    """Include a todo if it has recent activity OR a future due date."""
    if _is_fresh(todo.get("updated_at")):
        return True
    due_on = todo.get("due_on")
    if due_on:
        try:
            d = datetime.strptime(due_on, "%Y-%m-%d").date()
            return d >= datetime.now(timezone.utc).date()
        except ValueError:
            pass
    return False


def _has_future_due(todo: dict) -> bool:
    due_on = todo.get("due_on")
    if not due_on:
        return False
    try:
        return datetime.strptime(due_on, "%Y-%m-%d").date() >= datetime.now(timezone.utc).date()
    except ValueError:
        return False


def _classify_for_user(todo: dict, bc_user_id: int) -> str | None:
    """Return inclusion_reason if this todo belongs in the user's queue, else None.

    Tiers (most-direct first):
      'assigned'   - assigned to the user's BC id (or their AI clone)
      'due'        - has a future due date in a project the token sees,
                     even if assigned to someone else (the user follows
                     because their AI clone is on the project)
      'unassigned' - no assignees + recent activity (someone needs to claim)
      'watching'   - assigned to someone else + recent activity in a
                     project the user's token sees. Per user spec:
                     'everything @CB has access to that has tasks
                     unopened with activity in the past 30 days'.
                     Without this, tasks owned by Ram/Luda/etc. in
                     active projects get silently dropped at sync time.
    """
    assignees = [a.get("id") for a in (todo.get("assignees") or [])]
    if bc_user_id in assignees:
        return "assigned"
    # CB System (37708014) clone is included as "assigned" too — same user
    if 37708014 in assignees:
        return "assigned"
    if _has_future_due(todo):
        return "due"
    if not assignees and _is_fresh(todo.get("updated_at")):
        return "unassigned"
    if assignees and _is_fresh(todo.get("updated_at")):
        return "watching"
    return None


def _paginate(path: str, token: str, max_pages: int = 50, params: dict | None = None):
    """Yield items from a paginated BC list endpoint until empty/400."""
    base = dict(params or {})
    for page in range(1, max_pages + 1):
        chunk = _bc_get(path, token, {**base, "page": page})
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


def _walk_project_todos(proj: dict, token: str, bc_user_id: int) -> list[store.OpsTodo]:
    """Pull every active + completed todo in one BC project that's relevant
    to the user. Returns the list of OpsTodo rows ready for upsert.

    Extracted so both pull_todos_for_user (loops all 50 projects) and
    pull_todos_for_project (targeted single-project sync on Mark Done) can
    share the same walking logic — keeps them from drifting out of sync.
    """
    bucket = proj.get("id")
    proj_name = proj.get("name") or f"Project {bucket}"
    out: list[store.OpsTodo] = []

    # BC API v3: the todoset id lives in the project's `dock` array,
    # not under a /buckets/{id}/todosets.json collection endpoint.
    proj_full = _bc_get(f"/projects/{bucket}.json", token) or {}
    todoset_dock = next(
        (d for d in proj_full.get("dock", []) if d.get("name") == "todoset"),
        None,
    )
    ts_id = todoset_dock.get("id") if todoset_dock else None
    if not ts_id:
        return out

    def _emit(t: dict, src_id: int, src_name: str) -> None:
        is_completed = bool(t.get("completed"))
        reason = _classify_for_user(t, bc_user_id)
        if not is_completed and reason is None:
            return
        if not _todo_is_relevant(t):
            return
        assignee_objs = t.get("assignees") or []
        completion = t.get("completion") or {}
        cby = completion.get("creator") or {}
        completed_at = completion.get("created_at") or ""
        created_at = t.get("created_at") or ""
        cycle_seconds = 0
        if completed_at and created_at:
            try:
                c_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                cr_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if c_dt.tzinfo is None: c_dt = c_dt.replace(tzinfo=timezone.utc)
                if cr_dt.tzinfo is None: cr_dt = cr_dt.replace(tzinfo=timezone.utc)
                cycle_seconds = max(0, int((c_dt - cr_dt).total_seconds()))
            except (ValueError, TypeError):
                pass
        out.append(store.OpsTodo(
            bc_id=t["id"],
            bc_project_id=bucket,
            bc_project_name=proj_name,
            bc_todolist_id=src_id,
            bc_todolist_name=src_name,
            title=t.get("title") or t.get("content") or "(untitled)",
            description=(t.get("description") or "")[:5000],
            status="completed" if is_completed else "active",
            due_on=t.get("due_on"),
            assignee_ids=[a.get("id") for a in assignee_objs],
            assignee_names=[a.get("name") for a in assignee_objs if a.get("name")],
            inclusion_reason=reason or "assigned",
            bc_app_url=t.get("app_url", ""),
            bc_created_at=created_at,
            bc_updated_at=t.get("updated_at") or "",
            completed_by_id=cby.get("id") if is_completed else None,
            completed_by_name=cby.get("name", "") if is_completed else "",
            completed_at=completed_at if is_completed else "",
            cycle_seconds=cycle_seconds if is_completed else 0,
            last_synced_at=_now_iso(),
        ))

    def _collect(src_id: int, src_name: str) -> None:
        """Pull active + completed todos from one todos source (a todolist
        OR a todo group — both share the /todolists/{id}/todos.json shape)."""
        active_todos = list(_paginate(
            f"/buckets/{bucket}/todolists/{src_id}/todos.json",
            token, max_pages=10,
        ))
        completed_todos = list(_paginate(
            f"/buckets/{bucket}/todolists/{src_id}/todos.json",
            token, max_pages=5, params={"completed": "true"},
        ))
        for t in active_todos + completed_todos:
            _emit(t, src_id, src_name)

    lists = _bc_get(f"/buckets/{bucket}/todosets/{ts_id}/todolists.json", token) or []
    for lst in lists:
        lst_id = lst.get("id")
        lst_name = lst.get("name") or "?"
        if not lst_id:
            continue
        _collect(lst_id, lst_name)
        # Descend into BC todo GROUPS (the "Week 01", "Sprint 2" sub-sections
        # a todolist can be split into). A grouped todo is NOT returned by the
        # parent list's /todos.json, so without this every task filed under a
        # group is invisible to My Day — the 2026-06-17 Swati incident: 48 of
        # her Curriculum tasks lived in 12 week-groups under a list whose own
        # top level was empty. Each group has its own id and behaves like a
        # sub-list for the todos endpoint; attribute its todos to a
        # "<list>: <group>" name so the My Day grouping mirrors Basecamp.
        groups = _bc_get(
            f"/buckets/{bucket}/todolists/{lst_id}/groups.json", token) or []
        for g in groups:
            gid = g.get("id")
            if not gid:
                continue
            gname = g.get("name") or g.get("title") or "?"
            _collect(gid, f"{lst_name}: {gname}")
    return out


def pull_todos_for_project(user_id: str, project_id: int) -> dict:
    """Targeted sync of ONE project. Much faster than pull_todos_for_user
    (~2-3s vs ~30s) because it skips the 49 other projects.

    Used by Mark Done so the just-touched project is refreshed before the
    user sees the next focus task — no more 'next' task surfacing while
    actually completed in BC.

    Coordinator-gated: if a full sync is already in flight for this user,
    return "already_running" rather than racing it. The in-flight full
    sync will walk this same project as part of its sweep, so the data
    will land naturally — no need to double-up BC API calls.
    """
    coord = sync_coordinator.get_coordinator()
    if not coord.try_start_sync(user_id):
        return {"status": "already_running", "project_id": project_id}
    try:
        token, _src = tokens.get_user_token(user_id)
        if not token:
            return {"status": "token_missing", "project_id": project_id}
        bc_user_id = tokens.get_user_bc_id(user_id)
        if not bc_user_id:
            return {"status": "bc_user_id_missing", "project_id": project_id}
        proj = _bc_get(f"/projects/{project_id}.json", token)
        if not proj:
            return {"status": "project_not_found", "project_id": project_id}
        try:
            fresh_todos = _walk_project_todos(proj, token, bc_user_id)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            _record_error(user_id, f"project_walk:{project_id}", err)
            return {"status": "error", "project_id": project_id, "error": err}
        proj_name = proj.get("name") or f"Project {project_id}"
        store.upsert_projects(user_id, [store.OpsProject(
            bc_id=project_id, name=proj_name,
            description=(proj.get("description") or "")[:500],
            last_synced_at=_now_iso(),
        )])
        store.upsert_todos(user_id, fresh_todos)
        # L5 (2026-06-09 audit): record the targeted touch so the UI can
        # show "Targeted sync 30s ago" instead of "Not synced yet" for
        # operators whose only sync activity has been Mark Done. Doesn't
        # touch last_sync_at — a targeted sync is NOT a full sync, and
        # the natural-flow gate must keep treating it that way.
        state = store.load_state(user_id)
        state.last_targeted_sync_at = _now_iso()
        store.save_state(state)
        return {
            "status": "ok",
            "project_id": project_id,
            "project_name": proj_name,
            "todos": len(fresh_todos),
        }
    finally:
        coord.finish_sync(user_id)


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

    Coordinator-gated: if another sync is already in flight for this
    user (manual click during cron, double-click on Sync, Mark Done's
    targeted sync racing the full one), return "already_running"
    without firing any BC HTTP calls. Caller (router / scheduler)
    treats this as "skipped" — not a failure. The stale-lock TTL in
    SyncCoordinator ensures a crashed sync's slot eventually frees.
    """
    coord = sync_coordinator.get_coordinator()
    if not coord.try_start_sync(user_id):
        return {"status": "already_running", "todos": 0, "projects": 0}
    try:
        return _pull_todos_for_user_inner(user_id, ali_legacy_bucket)
    finally:
        coord.finish_sync(user_id)


def _pull_todos_for_user_inner(user_id: str, ali_legacy_bucket: int | None) -> dict:
    """The actual walk, extracted so the coordinator wrapping stays a
    cheap pre-check. Exists because the prior body had early returns
    on token/bc_user_id missing — keeping them in a separate function
    means the wrapper's try/finally is shallow and obviously correct."""
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
    #    b. NO project-level freshness skip. A "quiet" project at the project
    #       level can still have a todo with a future due date Ali cares about.
    #       Filtering happens at the todo level (_todo_is_relevant) so we keep
    #       any task with recent activity OR an upcoming due date.
    #    c. Always include the user's explicitly-configured extra buckets even
    #       if they aren't in /projects.json (legacy private buckets).
    #    d. Include ali_legacy_bucket if supplied (Phase A demo).
    projects_raw: list[dict] = []
    seen_buckets: set[int] = set()
    skipped_quiet = 0  # kept in result for backward compat; always 0 now

    discovered = discover_projects(token)
    for p in discovered:
        bid = p.get("id")
        if not bid or bid in seen_buckets:
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

    # Round-robin resume cursor (budget_exceeded fix): rotate projects_raw so
    # this run continues right after the project the previous run last reached.
    # Without it, every run re-walks from the head and budgets out at the same
    # spot, so a user with more projects than fit in SYNC_BUDGET_SECONDS never
    # syncs the tail. With it, successive runs cover the whole list. Keyed by
    # bc_id (robust to projects being added/removed between runs); if the
    # cursor project is gone we just start from the head.
    if state.last_walked_bc_id and len(projects_raw) > 1:
        _ids = [p.get("id") for p in projects_raw]
        if state.last_walked_bc_id in _ids:
            _start = _ids.index(state.last_walked_bc_id) + 1
            projects_raw = projects_raw[_start:] + projects_raw[:_start]

    fresh_projects: list[store.OpsProject] = []
    fresh_todos: list[store.OpsTodo] = []
    partial = False
    project_errors: list[str] = []
    # Buckets that returned 403 Forbidden during the walk. A 403 is NOT a
    # transient failure like a 5xx or a timeout — it means the Basecamp
    # identity this token authenticates as (the operator's AI clone) is
    # not a member of that project, so it can list the project in
    # /projects.json but cannot read its todos. Tracked separately from
    # generic walk errors so the message can name the fix (grant the
    # clone access) instead of the useless raw "HTTP Error 403: Forbidden".
    # We deliberately still mark the sync `partial` — a membership gap is
    # a real, visible problem the operator must fix in Basecamp, not
    # something to silently swallow into stale data.
    forbidden_buckets: list[int] = []

    # Resolve which BC identity this token authenticates as, for the 403
    # messages and the wrong-account guard below. Two ids matter and they
    # are NOT interchangeable:
    #   - the LAUNCHPAD identity id in the OAuth grant metadata, and
    #   - the per-ACCOUNT person id returned by /my/profile.json.
    # A single human has BOTH (e.g. Launchpad 16988292 == account-person
    # 17454835), and project memberships + the classifier (get_user_bc_id)
    # use the ACCOUNT person id. The 2026-06-10 incident burned us here:
    # the guard compared the Launchpad id to the account id, found them
    # unequal (they never match for the same person), and falsely cried
    # "wrong account". So we resolve the account-scoped person id via
    # /my/profile.json and compare THAT. Best-effort: on failure we skip
    # the guard rather than false-positive.
    connected_identity = ""
    try:
        from execution.products.library import (
            basecamp_oauth_token as _bc_oauth,
            tenancy as _tncy,
        )
        _u = _tncy.get_user(user_id)
        if _u:
            _meta = _bc_oauth.get_grant_metadata(_u)
            if _meta:
                connected_identity = (_meta.get("bc_user_email") or "").strip()
    except Exception:
        pass

    token_person_id = 0
    try:
        _me = _bc_get("/my/profile.json", token)
        if _me:
            token_person_id = int(_me.get("id") or 0)
    except Exception:
        pass

    # ── Classify against the LIVE account-person id; self-heal the cache ──
    # token_person_id (from /my/profile.json) IS the id that todos assigned
    # to this operator carry — it's ground truth. The stored bc_user_id is
    # only a cache of it, and the self-serve connect flow historically cached
    # the WRONG value: the Launchpad identity id from /authorization.json,
    # which lives in a different namespace and never matches the account-person
    # id (the 2026-06-16 Swati incident — every one of her todos fell through
    # to the 'due'/'watching' noise tiers because her cached id matched zero
    # assignees). So prefer the live id for classification and refresh the
    # cache when it has drifted. This makes the whole bug class structurally
    # impossible: we classify against the id the token actually authenticates
    # as, not a cache that can rot.
    #
    # EXCEPTION — AI-clone connections (Ali's CB System, anyone's +ai persona):
    # the clone-by-design model deliberately authenticates as the clone
    # (token_person_id = clone id) while classifying against the HUMAN's
    # bc_user_id. Never overwrite the human id with the clone's id; a +ai
    # mis-connect is surfaced by the suspect/forbidden flagging below (which
    # tells the operator to reconnect as their human account), not silently
    # cached.
    classify_id = bc_user_id
    if token_person_id:
        try:
            from execution.products.library import (
                basecamp_provisioning as _prov,
                tenancy as _heal_tncy,
            )
            _heal_user = _heal_tncy.get_user(user_id)
            _is_clone_conn = _prov.is_ai_account_for_user(
                connected_identity, _heal_user)
        except Exception:
            _heal_user, _is_clone_conn = None, False
        if not _is_clone_conn:
            classify_id = token_person_id
            if token_person_id != bc_user_id and _heal_user is not None:
                logger.warning(
                    "ops sync: healing bc_user_id for %s: %s -> %s "
                    "(cached id disagreed with /my/profile.json account id)",
                    user_id, bc_user_id, token_person_id,
                )
                try:
                    _heal_user.bc_user_id = token_person_id
                    _heal_tncy.upsert_user(_heal_user)
                except Exception as e:  # noqa: BLE001 — heal is best-effort
                    logger.warning(
                        "ops sync: bc_user_id heal write failed for %s: %s",
                        user_id, type(e).__name__)

    # L6 (2026-06-09 audit): track wall clock so we can bail out
    # gracefully when SYNC_BUDGET_SECONDS is exceeded rather than
    # letting one runaway sync hold the coordinator slot for hours.
    sync_start = time.time()
    projects_walked = 0
    budget_exceeded = False

    for proj in projects_raw:
        # Budget check happens BEFORE each project walk so a slow first
        # project doesn't get cut off mid-walk (which would leave a
        # partial project state with no error trail).
        if time.time() - sync_start > SYNC_BUDGET_SECONDS:
            budget_exceeded = True
            partial = True
            skipped = len(projects_raw) - projects_walked
            err = (
                f"budget_exceeded after {int(time.time() - sync_start)}s; "
                f"{skipped}/{len(projects_raw)} projects deferred to the next "
                f"run (round-robin cursor advances each run)"
            )
            project_errors.append(err)
            logger.warning("ops sync: %s", err)
            _record_error(user_id, "budget_exceeded", err)
            break

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
            fresh_todos.extend(_walk_project_todos(proj, token, classify_id))
            projects_walked += 1
        except urllib.error.HTTPError as e:  # per-project resilience
            partial = True
            if e.code == 403:
                # Membership gap, not a transient failure. Name the
                # identity + the remediation so the partial-sync banner
                # and /my-day/_health tell the operator exactly what to
                # do instead of showing a bare "HTTP Error 403".
                who = connected_identity or "your Basecamp connection"
                err = (
                    f"bucket={bucket} ({proj_name[:40]}) 403 Forbidden — "
                    f"{who} is not a member of this project. Add that "
                    f"Basecamp identity to the project (People → Add people) "
                    f"and it will sync on the next walk."
                )
                forbidden_buckets.append(bucket)
                logger.warning("ops sync: %s", err)
                _record_error(user_id, "project_forbidden", err)
            else:
                err = f"bucket={bucket} HTTPError: HTTP Error {e.code}: {e.reason}"
                logger.warning("ops sync: %s", err)
                _record_error(user_id, "project_walk", err)
            project_errors.append(err)
        except Exception as e:  # noqa: BLE001 — per-project resilience
            partial = True
            err = f"bucket={bucket} {type(e).__name__}: {str(e)[:120]}"
            project_errors.append(err)
            logger.warning("ops sync: %s", err)
            # Capture for /my-day/_health visibility — these silent
            # partial-success errors caused the 2026-06-04 audit issue
            # where Press Mike completion didn't propagate.
            _record_error(user_id, "project_walk", err)

    # 2. Upsert into store
    p_created, p_updated = store.upsert_projects(user_id, fresh_projects)
    t_created, t_updated = store.upsert_todos(user_id, fresh_todos)

    # Self-annealing guard (2026-06-10): collapse a pile of per-bucket
    # 403s into ONE root-cause line, and — when the identity the token
    # authenticates as also differs from the id the classifier expects —
    # name the precise fix (reconnect as the right account). This is the
    # 'fail loud instead of a wall of 403s' improvement from the
    # identity-mismatch incident: Ali's OAuth was bound to a dead
    # duplicate BC person record (16988292) while his tasks live under
    # 17454835, so every walk 403'd with no hint of why.
    #
    # The id-mismatch branch is gated on `forbidden_buckets` being
    # non-empty ON PURPOSE so it can NEVER false-positive on the
    # legitimate AI-clone model, where the token (clone id) differs from
    # the classifier id (human) BY DESIGN but reads everything fine — a
    # correctly-granted clone produces zero forbidden buckets, so this
    # branch stays silent for it.
    identity_alert = ""
    # Compare the token's ACCOUNT-scoped person id (not the Launchpad id)
    # against the id the classifier expects. Equal → same person; the
    # forbidden buckets are genuine non-memberships, NOT a wrong-account
    # binding (this is the false-positive the 2026-06-10 fix retired).
    connection_identity_suspect = bool(
        forbidden_buckets and token_person_id and bc_user_id
        and token_person_id != bc_user_id
    )
    if forbidden_buckets:
        who = connected_identity or "your Basecamp connection"
        who_id = f" (BC person id {token_person_id})" if token_person_id else ""
        if connection_identity_suspect:
            identity_alert = (
                f"Basecamp connection bound to the wrong account: you are "
                f"connected as {who}{who_id}, which was forbidden on "
                f"{len(forbidden_buckets)} project(s), but your tasks are "
                f"tracked under BC person id {bc_user_id}. Reconnect at "
                f"/profile/connect-basecamp as that account."
            )
            _record_error(user_id, "connection_identity_suspect", identity_alert)
        else:
            identity_alert = (
                f"Basecamp connection ({who}{who_id}) was forbidden on "
                f"{len(forbidden_buckets)} project(s) it is not a member of. "
                f"If any should be visible, add that identity to them "
                f"(People → Add people)."
            )
            _record_error(user_id, "connection_forbidden_summary", identity_alert)

    # 3. State
    state.last_sync_at = _now_iso()
    state.last_sync_status = "partial" if partial else "ok"
    # Lead the banner with the single root-cause identity line when
    # present, then the actionable per-bucket 403 messages (a membership
    # gap is fixable by the operator, whereas a transient 5xx just retries
    # on its own), then generic errors fill any remaining slots.
    forbidden_errs = [e for e in project_errors if "403 Forbidden" in e]
    other_errs = [e for e in project_errors if "403 Forbidden" not in e]
    ordered = ([identity_alert] if identity_alert else []) + forbidden_errs + other_errs
    state.last_sync_error = "; ".join(ordered[:3]) if ordered else ""
    state.todos_synced = len(fresh_todos)
    state.projects_synced = len(fresh_projects)
    # Advance the round-robin cursor to the last project this run reached so
    # the next run continues past it. On a full-coverage run (no budget break)
    # this is the tail project, and the next run wraps harmlessly to the head.
    if fresh_projects:
        state.last_walked_bc_id = fresh_projects[-1].bc_id
    store.save_state(state)

    return {
        "status": state.last_sync_status,
        "token_source": source,
        "projects_discovered": len(discovered) if 'discovered' in locals() else 0,
        "projects_quiet_skipped": skipped_quiet,
        "projects_walked": projects_walked,
        "projects_created": p_created,
        "projects_updated": p_updated,
        "todos_assigned_to_user": len(fresh_todos),
        "todos_created": t_created,
        "todos_updated": t_updated,
        "errors": project_errors[:3],
        "forbidden_buckets": forbidden_buckets,
        "connection_identity_suspect": connection_identity_suspect,
        "budget_exceeded": budget_exceeded,
        "wall_time_seconds": round(time.time() - sync_start, 1),
    }
