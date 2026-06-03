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

from execution.products.library import auth_google, tenancy
from execution.products.ops import scorer, store, suggestions, sync

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


@router.get("/")
async def ops_home(request: Request):
    user = _require_user(request)
    state = store.load_state(user.email)
    todos = store.load_todos(user.email)

    show_dismissed = request.query_params.get("show_dismissed") == "1"
    show_completed = request.query_params.get("show_completed") == "1"
    project_filter = request.query_params.get("project")

    visible = []
    for t in todos:
        if not show_dismissed and t.is_dismissed:
            continue
        if not show_completed and t.status == "completed":
            continue
        if project_filter and str(t.bc_project_id) != project_filter:
            continue
        visible.append(t)
    visible.sort(key=lambda x: (-x.urgency_score, x.due_on or "9999-12-31", x.title))

    # Stats
    overdue = sum(1 for t in visible if t.score_breakdown.get("breakdown", {}).get("due_days", 999) is not None
                  and (t.score_breakdown.get("breakdown", {}).get("due_days") or 999) < 0)
    red = sum(1 for t in visible if t.urgency_score >= 70)
    amber = sum(1 for t in visible if 40 <= t.urgency_score < 70)

    projects = store.load_projects(user.email)
    project_counts: dict[int, int] = {}
    for t in visible:
        project_counts[t.bc_project_id] = project_counts.get(t.bc_project_id, 0) + 1

    return request.app.state.templates.TemplateResponse(
        request, "my_day/home.html",
        _ctx(request, user,
             todos=visible,
             total=len(visible),
             overdue=overdue,
             red=red,
             amber=amber,
             state=state,
             projects=projects,
             project_counts=project_counts,
             project_filter=int(project_filter) if project_filter and project_filter.isdigit() else None,
             show_dismissed=show_dismissed,
             show_completed=show_completed),
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
