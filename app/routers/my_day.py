"""My Day — per-user task triage surface (the AI Ops Command Center).

Routes:
  GET  /my-day/                       — queue (active todos sorted by urgency)
  GET  /my-day/todo/{bc_id}           — workspace with Claude Code prompt
  POST /my-day/sync                   — manual sync trigger
  POST /my-day/todo/{bc_id}/dismiss   — local soft-dismiss (does NOT touch BC)
  POST /my-day/todo/{bc_id}/undismiss — reverse a dismissal

Authn: required. Uses the same session middleware as /library/.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from datetime import datetime, timezone

from execution.products.library import auth_google, tenancy
from execution.products.ops import rollup, scorer, store, suggestions, sync

router = APIRouter(prefix="/my-day", tags=["my-day"])

# Phase A: until full multi-tenant sync is wired, Ali defaults to the
# legacy bucket (Ali Personal, 7463955) so the queue has real data.
ALI_LEGACY_BUCKET = 7463955


def _session_user(request: Request):
    """Same pattern as library: cookie-based JWT or dev fallback to Ali."""
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    user = auth_google.current_user_from_cookie(cookie)
    if user:
        return user
    # Dev fallback so the page works pre-SSO locally
    if not auth_google.is_enabled():
        return tenancy.get_user("ali@colaberry.com")
    return None


def _require_user(request: Request):
    user = _session_user(request)
    if not user:
        raise HTTPException(303, headers={"Location": "/auth/login?next=/my-day/"})
    return user


def _ctx(request: Request, user, **extra) -> dict:
    base = {
        "request": request,
        "current_product": "ops",
        "current_session_user": user,
        "company_id": user.company_id,
        "company_display": (tenancy.get_company(user.company_id).display_name
                            if tenancy.get_company(user.company_id) else user.company_id),
    }
    base.update(extra)
    return base


def _greeting() -> str:
    h = datetime.now().hour
    if h < 12:
        return "Good morning"
    if h < 17:
        return "Good afternoon"
    return "Good evening"


def _sync_relative(last_sync_iso: str) -> str:
    if not last_sync_iso:
        return ""
    try:
        ts = last_sync_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = (datetime.now(timezone.utc) - dt).total_seconds()
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{int(secs // 60)} min ago"
        if secs < 86400:
            return f"{int(secs // 3600)} hr ago"
        return f"{int(secs // 86400)} days ago"
    except (ValueError, TypeError):
        return ""


@router.get("/")
async def ops_home(request: Request):
    user = _require_user(request)
    state = store.load_state(user.email)
    todos = store.load_todos(user.email)
    projects = store.load_projects(user.email)

    # Pre-compute aggregates: status, per-list rollups, focus task
    status = rollup.overall(todos)
    list_rollups = rollup.per_list(todos)
    active_todos = [t for t in todos if t.status == "active" and not t.is_dismissed]
    active_sorted = sorted(active_todos, key=lambda t: (-t.urgency_score, t.due_on or "9999-12-31"))
    human_todos = [t for t in active_sorted if rollup.tier(t) == "H"]
    ai_todos = [t for t in active_sorted if rollup.tier(t) == "AI"]

    # The single "YOUR TURN" focus
    focus = human_todos[0] if human_todos else (active_sorted[0] if active_sorted else None)
    focus_suggestion = suggestions.build_suggestion(focus) if focus else None
    focus_prompt = suggestions.generate_prompt(focus, focus_suggestion) if focus else ""

    # Single-project context (used for big-picture banner)
    project_one = projects[0] if len(projects) == 1 else None

    # Heat-of-day greeting + relative sync
    first_name = (user.display_name or user.email).split()[0]
    greeting = _greeting()
    sync_rel = _sync_relative(state.last_sync_at) if state.last_sync_at else ""

    return request.app.state.templates.TemplateResponse(
        request, "my_day/home.html",
        _ctx(request, user,
             # Top-of-page status
             status=status,
             greeting=greeting,
             first_name=first_name,
             # Sync context
             state=state,
             sync_relative=sync_rel,
             # Project context
             project_one=project_one,
             project_count=len(projects),
             # Focus card
             focus=focus,
             focus_suggestion=focus_suggestion,
             focus_prompt=focus_prompt,
             # Lists + tasks
             list_rollups=list_rollups,
             human_todos=human_todos,
             ai_todos=ai_todos,
             total_open=status.open_count),
    )


@router.get("/todo/{bc_id}")
async def ops_todo(bc_id: int, request: Request):
    user = _require_user(request)
    todo = store.get_todo(user.email, bc_id)
    if not todo:
        raise HTTPException(404, f"Todo {bc_id} not in your queue (run /my-day/sync to refresh)")
    suggestion = suggestions.build_suggestion(todo)
    prompt = suggestions.generate_prompt(todo, suggestion)
    return request.app.state.templates.TemplateResponse(
        request, "my_day/workspace.html",
        _ctx(request, user, todo=todo, suggestion=suggestion, prompt=prompt),
    )


@router.post("/sync")
async def ops_sync(request: Request):
    user = _require_user(request)
    # For Ali: bypass projects.json (CB System sees 0) and walk bucket 7463955.
    # Other users: rely on projects.json discovery (their AI clone token).
    legacy = ALI_LEGACY_BUCKET if user.email == "ali@colaberry.com" else None
    result = sync.pull_todos_for_user(user.email, ali_legacy_bucket=legacy)
    if result.get("status") not in ("token_missing", "bc_user_id_missing"):
        # Re-score everything immediately after sync
        scorer.score_all_todos(user.email)
    return RedirectResponse("/my-day/?synced=1", status_code=303)


@router.post("/todo/{bc_id}/dismiss")
async def ops_dismiss(bc_id: int, request: Request):
    user = _require_user(request)
    from datetime import datetime, timezone
    store.update_todo(
        user.email, bc_id,
        is_dismissed=True,
        dismissed_at=datetime.now(timezone.utc).isoformat(),
        dismissed_by=user.email,
        dismissed_reason="manual",
    )
    return RedirectResponse(request.headers.get("Referer", "/my-day/"), status_code=303)


@router.post("/todo/{bc_id}/undismiss")
async def ops_undismiss(bc_id: int, request: Request):
    user = _require_user(request)
    store.update_todo(user.email, bc_id, is_dismissed=False)
    return RedirectResponse(request.headers.get("Referer", "/my-day/?show_dismissed=1"), status_code=303)


@router.post("/todo/{bc_id}/complete")
async def ops_complete(bc_id: int, request: Request):
    """Mark a todo complete BOTH in BC (write-back via AI clone token)
    AND locally. If BC write-back fails, we still mark local — operator
    won't get stuck looking at a stale queue, and the audit row preserves
    intent. Next sync will reconcile.
    """
    user = _require_user(request)
    todo = store.get_todo(user.email, bc_id)
    if not todo:
        raise HTTPException(404, f"Todo {bc_id} not in your queue")
    # Local first
    store.update_todo(user.email, bc_id, status="completed")
    # Then BC
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur
    from execution.products.ops import tokens
    token, _src = tokens.get_user_token(user.email)
    bc_status = "skipped (no token)"
    if token:
        try:
            req = _ur.Request(
                f"https://3.basecampapi.com/3945211/buckets/{todo.bc_project_id}/todos/{bc_id}/completion.json",
                data=b"", method="POST",
                headers={"Authorization": f"Bearer {token}",
                         "User-Agent": "Advisor My Day (ali@colaberry.com)"},
            )
            with _ur.urlopen(req, timeout=10) as r:
                bc_status = f"ok ({r.status})"
        except _ue.HTTPError as e:
            bc_status = f"http_{e.code}"
        except Exception as e:  # noqa: BLE001
            bc_status = f"err: {type(e).__name__}"
    # Audit (printable in logs for now)
    import logging as _log
    _log.getLogger(__name__).info(
        "my-day complete: user=%s bc_id=%s bc_status=%s",
        user.email, bc_id, bc_status,
    )
    return RedirectResponse(request.headers.get("Referer", "/my-day/"), status_code=303)
