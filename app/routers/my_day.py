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

from fastapi import Form

from execution.products.library import auth_google, tenancy
from execution.products.ops import (
    bc_comments, context_collector, llm_suggest, plan_inference,
    rollup, scorer, store, suggestions, sync, tokens,
)

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
    # Pull just enough from the library product to render the shared shell:
    # category counts, use-case count, workspace, pending_count. None of
    # these are critical to my-day rendering but the library base reads them.
    try:
        from execution.products.library import inventory
        from execution.products.library import store as lib_store
        from execution.products.library import use_cases
        counts = inventory.inventory_counts(viewer_company_id=user.company_id)
        use_case_count = use_cases.count("global")
        pending_count = len(lib_store.list_submissions(status="pending"))
    except Exception:
        counts = {}
        use_case_count = 0
        pending_count = 0

    # Library base also reads these — give safe defaults so it doesn't crash
    bell_count = 0
    queue_count = 0
    is_reviewer = False
    try:
        from execution.products.library import notifications as _notif
        bell_count = _notif.unread_count_for_user(user.user_id, user.company_id)
    except Exception:
        pass
    try:
        if tenancy.can_review(user):
            is_reviewer = True
            q = tenancy.queue_counts(user.company_id)
            queue_count = q.get("submitted", 0) + q.get("under_review", 0)
    except Exception:
        pass

    base = {
        "request": request,
        "current_product": "library",
        "library_nav_active": "my_day",
        "workspace": "global",
        "workspaces": [],   # avoid library workspace-switcher AttributeError
        "current_session_user": user,
        "actor": (user.display_name or user.email) if user else None,
        "counts": counts,
        "use_case_count": use_case_count,
        "pending_count": pending_count,
        "scope": "my-company",
        "viewer_company_id": user.company_id if user else None,
        "bell_count": bell_count,
        "queue_count": queue_count,
        "is_reviewer": is_reviewer,
        "company_id": user.company_id,
        "company_display": (tenancy.get_company(user.company_id).display_name
                            if tenancy.get_company(user.company_id) else user.company_id),
        "my_day_total_open": None,
    }
    # workspaces (list of strings) is read by the library workspace switcher
    try:
        from execution.products.library import store as _lib_store
        base["workspaces"] = _lib_store.list_workspaces()
    except Exception:
        pass
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


def _maybe_async_sync(user_email: str, state) -> None:
    """If the last sync is more than 3 min old, kick a background thread
    to refresh. Non-blocking — the page renders with current data and the
    next refresh shows the fresh state. Cheap idempotency via a module-
    level lock dict.
    """
    import threading
    last = state.last_sync_at or ""
    if last:
        try:
            ts = last.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            if age < 180:  # < 3 min — recent enough
                return
        except (ValueError, TypeError):
            pass
    # One in-flight per user
    if getattr(_maybe_async_sync, "_locks", None) is None:
        _maybe_async_sync._locks = {}
    locks = _maybe_async_sync._locks
    if locks.get(user_email):
        return
    locks[user_email] = True

    def _run():
        try:
            r = sync.pull_todos_for_user(user_email)
            if r.get("status") in ("ok", "partial"):
                scorer.score_all_todos(user_email)
        except Exception:
            pass
        finally:
            locks[user_email] = False

    threading.Thread(target=_run, daemon=True, name=f"sync-{user_email}").start()


@router.get("/")
async def ops_home(request: Request):
    user = _require_user(request)
    state = store.load_state(user.email)
    todos = store.load_todos(user.email)
    projects = store.load_projects(user.email)
    # Nudge: if data is stale, kick a background sync.
    _maybe_async_sync(user.email, state)

    view = request.query_params.get("view", "briefing")
    if view not in ("briefing", "kanban", "heatmap"):
        view = "briefing"
    project_filter_raw = request.query_params.get("project", "")
    # Tier filter: assignment-based (assigned|due|unassigned|all) OR
    # kind-based (human|ai). The 6 values are mutually exclusive in the UI.
    tier = request.query_params.get("tier", "assigned")
    if tier not in ("assigned", "due", "unassigned", "all", "human", "ai"):
        tier = "assigned"

    # Pre-compute counts per tier for the chip row (BEFORE filtering)
    from collections import Counter
    active_unfiltered = [t for t in todos if t.status == "active" and not t.is_dismissed]
    tier_counts_total = Counter(
        getattr(t, "inclusion_reason", "assigned") for t in active_unfiltered
    )
    tier_counts_total["all"] = sum(tier_counts_total.values())
    # Kind-based counts (Human vs AI — orthogonal to assignment)
    tier_counts_total["human"] = sum(1 for t in active_unfiltered if t.category == "human_required")
    tier_counts_total["ai"] = sum(1 for t in active_unfiltered if t.category != "human_required")

    # Apply project filter
    if project_filter_raw:
        try:
            project_filter_id = int(project_filter_raw)
        except ValueError:
            project_filter_id = None
    else:
        project_filter_id = None

    # List filter (drill-in from Feasibility table)
    list_filter_raw = request.query_params.get("list", "")
    try:
        list_filter_id = int(list_filter_raw) if list_filter_raw else None
    except ValueError:
        list_filter_id = None

    if project_filter_id is not None:
        scoped_todos = [t for t in todos if t.bc_project_id == project_filter_id]
    else:
        scoped_todos = todos

    if list_filter_id is not None:
        scoped_todos = [t for t in scoped_todos if t.bc_todolist_id == list_filter_id]

    # Apply tier filter
    if tier == "assigned":
        scoped_todos = [t for t in scoped_todos if getattr(t, "inclusion_reason", "assigned") == "assigned"]
    elif tier == "due":
        scoped_todos = [t for t in scoped_todos if getattr(t, "inclusion_reason", "assigned") == "due"]
    elif tier == "unassigned":
        scoped_todos = [t for t in scoped_todos if getattr(t, "inclusion_reason", "assigned") == "unassigned"]
    elif tier == "human":
        scoped_todos = [t for t in scoped_todos if t.category == "human_required"]
    elif tier == "ai":
        scoped_todos = [t for t in scoped_todos if t.category != "human_required"]
    # tier == "all" → no filter

    # Pre-compute aggregates against the scoped working set
    status = rollup.overall(scoped_todos)
    list_rollups = rollup.per_list(scoped_todos)
    project_rollups = rollup.per_project(scoped_todos)
    overall_health_data = rollup.overall_health(list_rollups)
    kanban_cols = rollup.kanban_columns(scoped_todos)
    # Pull ALL completed for the new past-tasks section (full list; UI caps)
    completer_stats, recent_completed_all = rollup.completions_summary(scoped_todos, limit=1000)
    active_todos = [t for t in scoped_todos if t.status == "active" and not t.is_dismissed]

    # Past/Future view counts (read query params)
    past_n_raw = request.query_params.get("past_n", "10")
    if past_n_raw == "all":
        past_n = len(recent_completed_all)
    else:
        try:
            past_n = max(0, int(past_n_raw))
        except ValueError:
            past_n = 10
    recent_completed = recent_completed_all[:past_n]

    future_n_raw = request.query_params.get("future_n", "10")
    if future_n_raw == "all":
        future_n = 9999
    else:
        try:
            future_n = max(0, int(future_n_raw))
        except ValueError:
            future_n = 10
    # Future tasks: active todos with a due date, sorted by due asc
    future_todos_all = [t for t in scoped_todos
                        if t.status == "active" and not t.is_dismissed and t.due_on]
    future_todos_all.sort(key=lambda t: t.due_on)
    future_todos = future_todos_all[:future_n]
    active_sorted = sorted(active_todos, key=lambda t: (-t.urgency_score, t.due_on or "9999-12-31"))
    human_todos = [t for t in active_sorted if rollup.tier(t) == "H"]
    ai_todos = [t for t in active_sorted if rollup.tier(t) == "AI"]

    # The single "YOUR TURN" focus
    focus = human_todos[0] if human_todos else (active_sorted[0] if active_sorted else None)
    focus_suggestion = None
    focus_prompt = ""
    focus_llm_source = "deterministic"
    if focus:
        # Try LLM enhancement first: pull recent comments, send to GPT,
        # get specific steps + Claude Code prompt back. Cached per ticket.
        token, _src = tokens.get_user_token(user.email)
        comments_text = bc_comments.fetch_recent_comments(focus, token) if token else ""
        enhanced = llm_suggest.enhance(user.user_id, focus, comments_text)
        if enhanced:
            focus_suggestion = {
                "action_kind": enhanced.get("action_kind", "default"),
                "one_line": enhanced.get("goal_line", ""),
                "steps": enhanced.get("specific_steps", []),
                "stop_conditions": enhanced.get("stop_conditions", []),
                "resources": [],
                "urgency_summary": f"score {focus.urgency_score}" + (f" · due {focus.due_on}" if focus.due_on else ""),
            }
            focus_prompt = enhanced.get("claude_code_prompt", "")
            focus_llm_source = "llm"
        else:
            # Deterministic fallback when OpenAI is unavailable or errors
            focus_suggestion = suggestions.build_suggestion(focus)
            focus_prompt = suggestions.generate_prompt(focus, focus_suggestion)

    # Single-project context (used for big-picture banner)
    project_one = projects[0] if len(projects) == 1 else None

    # Heat-of-day greeting + relative sync
    first_name = (user.display_name or user.email).split()[0]
    greeting = _greeting()
    sync_rel = _sync_relative(state.last_sync_at) if state.last_sync_at else ""

    # Aggregate the unfiltered projects list for the project-filter chip row
    # (so user can switch projects even when currently scoped to one)
    all_project_rollups = rollup.per_project(todos)

    template_name = {
        "kanban": "my_day/kanban.html",
        "heatmap": "my_day/heatmap.html",
    }.get(view, "my_day/home.html")

    return request.app.state.templates.TemplateResponse(
        request, template_name,
        _ctx(request, user,
             # View routing
             view=view,
             project_filter=project_filter_id,
             list_filter=list_filter_id,
             tier=tier,
             tier_counts=tier_counts_total,
             overall_health=overall_health_data,
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
             all_project_rollups=all_project_rollups,
             # Focus card (briefing only)
             focus=focus,
             focus_suggestion=focus_suggestion,
             focus_prompt=focus_prompt,
             focus_llm_source=focus_llm_source,
             # Lists + tasks
             list_rollups=list_rollups,
             project_rollups=project_rollups,
             kanban_cols=kanban_cols,
             completer_stats=completer_stats,
             recent_completed=recent_completed,
             recent_completed_total=len(recent_completed_all),
             past_n=past_n_raw,
             future_todos=future_todos,
             future_todos_total=len(future_todos_all),
             future_n=future_n_raw,
             human_todos=human_todos,
             ai_todos=ai_todos,
             total_open=status.open_count,
             my_day_total_open=status.open_count),
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
    todo = store.get_todo(user.email, bc_id)
    title_snippet = (todo.title[:100] if todo else "")
    store.update_todo(
        user.email, bc_id,
        is_dismissed=True,
        dismissed_at=datetime.now(timezone.utc).isoformat(),
        dismissed_by=user.email,
        dismissed_reason="manual",
    )
    import urllib.parse as _up
    referer = request.headers.get("Referer", "/my-day/")
    sep = "&" if "?" in referer else "?"
    flash = f"{sep}skip={_up.quote(title_snippet)}"
    return RedirectResponse(referer + flash, status_code=303)


@router.post("/todo/{bc_id}/undismiss")
async def ops_undismiss(bc_id: int, request: Request):
    user = _require_user(request)
    store.update_todo(user.email, bc_id, is_dismissed=False)
    return RedirectResponse(request.headers.get("Referer", "/my-day/?show_dismissed=1"), status_code=303)


@router.get("/submit")
async def submit_form(request: Request):
    """Magic Input form: paste a BC URL + what you need, get back a plan."""
    user = _require_user(request)
    return request.app.state.templates.TemplateResponse(
        request, "my_day/submit.html",
        _ctx(request, user),
    )


@router.post("/submit")
async def submit_handle(request: Request,
                        basecamp_url: str = Form(...),
                        user_feedback: str = Form(...),
                        output_type: str = Form(""),
                        success_criteria: str = Form("")):
    """Crawl the BC URL, infer goal + plan via LLM, render preview."""
    user = _require_user(request)
    token, _src = tokens.get_user_token(user.email)
    if not token:
        raise HTTPException(400, "No BC token configured for this user")

    # Step 1: crawl
    bundle = context_collector.collect(basecamp_url.strip(), token)

    # Step 2: infer plan
    plan = plan_inference.infer(
        user_feedback=user_feedback.strip(),
        basecamp_url=basecamp_url.strip(),
        output_type=output_type.strip(),
        success_criteria=success_criteria.strip(),
        context_bundle=bundle,
    )

    return request.app.state.templates.TemplateResponse(
        request, "my_day/plan_preview.html",
        _ctx(request, user,
             basecamp_url=basecamp_url.strip(),
             user_feedback=user_feedback.strip(),
             output_type_input=output_type.strip(),
             success_criteria_input=success_criteria.strip(),
             bundle=bundle,
             plan=plan),
    )


@router.post("/todo/{bc_id}/complete")
async def ops_complete(bc_id: int, request: Request):
    """Mark a todo complete BOTH in BC (write-back via AI clone token)
    AND locally. If BC write-back fails, we still mark local — operator
    won't get stuck looking at a stale queue, and the audit row preserves
    intent. Next sync will reconcile.
    """
    import logging as _log
    user = _require_user(request)
    todo = store.get_todo(user.email, bc_id)
    if not todo:
        _log.getLogger(__name__).warning(
            "my-day complete: user=%s bc_id=%s NOT IN QUEUE (404)",
            user.email, bc_id,
        )
        raise HTTPException(404, f"Todo {bc_id} not in your queue")
    title_snippet = todo.title[:100]
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
    _log.getLogger(__name__).info(
        "my-day complete: user=%s bc_id=%s title=%r bc_status=%s",
        user.email, bc_id, title_snippet, bc_status,
    )
    # Build redirect URL: preserve filter state via Referer + append
    # ?done=<title> so the next render shows a green flash confirming
    # the action actually fired.
    import urllib.parse as _up
    referer = request.headers.get("Referer", "/my-day/")
    sep = "&" if "?" in referer else "?"
    flash = f"{sep}done={_up.quote(title_snippet)}"
    return RedirectResponse(referer + flash, status_code=303)
