"""My Day — per-user task triage surface (the AI Ops Command Center).

Routes:
  GET  /my-day/                       — queue (active todos sorted by urgency)
  GET  /my-day/todo/{bc_id}           — workspace with Claude Code prompt
  POST /my-day/sync                   — manual sync trigger
  POST /my-day/todo/{bc_id}/dismiss   — local soft-dismiss (does NOT touch BC)
  POST /my-day/todo/{bc_id}/undismiss — reverse a dismissal
  GET  /my-day/_health                — operational health snapshot (admin only)

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
        # Preserve the original URL (including query string) so the user
        # round-trips back here after login. Previously hard-coded to
        # /my-day/ which broke /my-day/_health and /my-day/todo/{id}.
        from urllib.parse import quote
        qs = request.url.query
        full = request.url.path + ("?" + qs if qs else "")
        raise HTTPException(303, headers={"Location": f"/auth/login?next={quote(full, safe='')}"})
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
    is_admin = "admin" in (user.roles or [])
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
        "is_my_day_admin": is_admin,
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


def _store_age_seconds(state) -> float:
    """Seconds since last successful sync, or a large number if never."""
    last = state.last_sync_at or ""
    if not last:
        return 99999.0
    try:
        ts = last.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError):
        return 99999.0


def _kick_bg_full_sync(user_email: str) -> None:
    """Kick a background full sync if one isn't already in flight. Uses the
    per-user lock dict so a slower auto-scheduler and a page-load nudge
    can't double up."""
    import threading
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

    threading.Thread(target=_run, daemon=True, name=f"bg-sync-{user_email}").start()


def _maybe_async_sync(user_email: str, state) -> None:
    """Legacy background-only nudge. Kept so cb_mention_worker + other
    callers don't break. New code should use _natural_flow_sync."""
    if _store_age_seconds(state) < 180:
        return
    _kick_bg_full_sync(user_email)


def _natural_flow_sync(user_email: str, state, project_filter_id: int | None) -> bool:
    """Page-load 'natural flow' sync: when the store is stale (>90s), sync
    the project the user is about to view INLINE (~2-3s) so the focus task
    they see is computed against fresh data. Always kicks a background full
    sync so projects outside the current filter also catch up. Returns True
    if any inline work was done.

    This is what keeps the system synced through the natural flow of using
    the app — independent of whether the background APScheduler cron is
    firing. Mark Done's targeted sync (ops_complete) covers the same gap
    on the write path; this covers the read path.
    """
    age = _store_age_seconds(state)
    if age < 90:
        return False

    inline_did_work = False
    if project_filter_id is not None:
        try:
            r = sync.pull_todos_for_project(user_email, project_filter_id)
            inline_did_work = (r.get("status") == "ok")
        except Exception:
            pass

    _kick_bg_full_sync(user_email)
    return inline_did_work


@router.get("/")
async def ops_home(request: Request):
    user = _require_user(request)
    state = store.load_state(user.email)

    # Parse project filter early so the natural-flow sync can target it.
    project_filter_raw = request.query_params.get("project", "")
    try:
        _early_proj = int(project_filter_raw) if project_filter_raw else None
    except ValueError:
        _early_proj = None

    # Natural-flow sync: if the store is stale, sync the focused project
    # inline (~2-3s) so the user always sees fresh state for what they're
    # looking at. Background-kicks a full sync for everything else.
    inline_synced = _natural_flow_sync(user.email, state, _early_proj)
    if inline_synced:
        # Reload state + todos so we see the freshly-upserted rows
        state = store.load_state(user.email)
    todos = store.load_todos(user.email)
    projects = store.load_projects(user.email)

    view = request.query_params.get("view", "briefing")
    if view not in ("briefing", "kanban", "heatmap"):
        view = "briefing"
    # Tier filter: assignment-based (assigned|due|unassigned|all) OR
    # kind-based (human|ai). The 6 values are mutually exclusive in the UI.
    # Default is 'all' so projects show every task they actually have —
    # otherwise tasks tagged with [Ali] in BC title (but not formally
    # assigned to Ali's BC user id) get hidden under the 'assigned' tier,
    # which surprised the user when AI Pathway projects appeared empty.
    tier = request.query_params.get("tier", "all")
    if tier not in ("assigned", "due", "unassigned", "watching", "all", "human", "ai"):
        tier = "all"

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

    # Apply project filter (parsed earlier as _early_proj for the
    # natural-flow sync; re-bind to the name the downstream code uses).
    project_filter_id = _early_proj

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

    # Tier filter as a reusable function so the project-chip counts can
    # apply the SAME filter without re-implementing it. Bug it fixes:
    # chip showing "17 open · 5 late" but click → 0 todos, because chip
    # count came from unfiltered todos while the click applies tier filter.
    def _filter_by_tier(t_list):
        if tier == "assigned":
            return [t for t in t_list if getattr(t, "inclusion_reason", "assigned") == "assigned"]
        if tier == "due":
            return [t for t in t_list if getattr(t, "inclusion_reason", "assigned") == "due"]
        if tier == "unassigned":
            return [t for t in t_list if getattr(t, "inclusion_reason", "assigned") == "unassigned"]
        if tier == "watching":
            return [t for t in t_list if getattr(t, "inclusion_reason", "assigned") == "watching"]
        if tier == "human":
            return [t for t in t_list if t.category == "human_required"]
        if tier == "ai":
            return [t for t in t_list if t.category != "human_required"]
        return t_list  # tier == "all"

    scoped_todos = _filter_by_tier(scoped_todos)

    # Pre-compute aggregates against the scoped working set
    status = rollup.overall(scoped_todos)
    list_rollups = rollup.per_list(scoped_todos)
    project_rollups = rollup.per_project(scoped_todos)
    overall_health_data = rollup.overall_health(list_rollups)
    kanban_cols = rollup.kanban_columns(scoped_todos)
    # Pull ALL completed — drill-down filters per-list for the unified PROJECT TIMELINE
    completer_stats, recent_completed_all = rollup.completions_summary(scoped_todos, limit=1000)
    active_todos = [t for t in scoped_todos if t.status == "active" and not t.is_dismissed]

    active_sorted = sorted(active_todos, key=lambda t: (-t.urgency_score, t.due_on or "9999-12-31"))
    human_todos = [t for t in active_sorted if rollup.tier(t) == "H"]
    ai_todos = [t for t in active_sorted if rollup.tier(t) == "AI"]

    # The single "YOUR TURN" focus
    focus = human_todos[0] if human_todos else (active_sorted[0] if active_sorted else None)
    focus_suggestion = None
    focus_prompt = ""
    focus_llm_source = "deterministic"
    # Post-action redirects (?done= / ?skip=) skip the LLM enhancement so the
    # post-Mark-done page renders instantly. Without this, after marking a
    # human task done the user waited 10-15s for a fresh LLM call against the
    # newly-promoted focus task, which made the dim overlay feel stuck.
    is_post_action = bool(request.query_params.get("done") or request.query_params.get("skip"))
    if focus:
        # Try LLM enhancement first: pull recent comments, send to GPT,
        # get specific steps + Claude Code prompt back. Cached per ticket.
        token, _src = tokens.get_user_token(user.email)
        enhanced = None
        if not is_post_action:
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
            # Deterministic fallback when OpenAI is unavailable, errors, or
            # we deliberately skipped it for the post-action snappy render.
            focus_suggestion = suggestions.build_suggestion(focus)
            focus_prompt = suggestions.generate_prompt(focus, focus_suggestion)

    # Single-project context (used for big-picture banner)
    project_one = projects[0] if len(projects) == 1 else None

    # Heat-of-day greeting + relative sync
    first_name = (user.display_name or user.email).split()[0]
    greeting = _greeting()
    sync_rel = _sync_relative(state.last_sync_at) if state.last_sync_at else ""

    # Project chip data: tier-filtered (so counts match what user sees
    # after clicking) but project/list-unfiltered (so all projects can
    # appear as chips). Bug it fixes: clicking a chip that says "17 open"
    # showing an empty queue because the 17 are all in other tiers.
    chip_source = _filter_by_tier(todos)
    all_project_rollups = rollup.per_project(chip_source)

    # If a project filter is active but the project has 0 in current tier,
    # inject a synthetic rollup so the chip still shows (otherwise the
    # active chip just vanishes and the user can't see what they filtered).
    if project_filter_id is not None and not any(
        p.project_id == project_filter_id for p in all_project_rollups
    ):
        proj_name = next(
            (t.bc_project_name for t in todos if t.bc_project_id == project_filter_id),
            f"Project {project_filter_id}",
        )
        all_project_rollups.insert(
            0,
            rollup.ProjectRollup(
                project_id=project_filter_id,
                project_name=proj_name,
                open_count=0,
            ),
        )

    template_name = {
        "kanban": "my_day/kanban.html",
        "heatmap": "my_day/heatmap.html",
    }.get(view, "my_day/home.html")

    response = request.app.state.templates.TemplateResponse(
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
             recent_completed_all=recent_completed_all,
             human_todos=human_todos,
             ai_todos=ai_todos,
             total_open=status.open_count,
             my_day_total_open=status.open_count),
    )
    # Disable browser caching so post-action redirects (?done=, ?skip=) and
    # background-sync refreshes always see fresh state. Without this, hitting
    # the back button or refresh could replay a cached page where the
    # just-completed task is still shown as next focus.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


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
    """Kick a background sync and redirect immediately.

    User feedback: the previous blocking sync left the dim 'Syncing…'
    overlay up for 30+ seconds while we walked all 50 projects with
    220ms throttle per HTTP call. Now we redirect right away with
    ?sync_started=1; the home page shows an in-progress banner and
    auto-refreshes 25s later so fresh data is visible without the
    user having to click refresh.

    Reuses the per-user lock dict from _maybe_async_sync so we don't
    race the background scheduler — if a sync is already in flight,
    the request just acknowledges and lets the existing one finish.
    """
    import threading as _th
    user = _require_user(request)
    legacy = ALI_LEGACY_BUCKET if user.email == "ali@colaberry.com" else None

    if getattr(_maybe_async_sync, "_locks", None) is None:
        _maybe_async_sync._locks = {}
    locks = _maybe_async_sync._locks

    if not locks.get(user.email):
        locks[user.email] = True

        def _bg():
            try:
                result = sync.pull_todos_for_user(user.email, ali_legacy_bucket=legacy)
                if result.get("status") not in ("token_missing", "bc_user_id_missing"):
                    scorer.score_all_todos(user.email)
            except Exception:
                pass
            finally:
                locks[user.email] = False

        _th.Thread(target=_bg, daemon=True, name=f"manual-sync-{user.email}").start()

    return RedirectResponse("/my-day/?sync_started=1", status_code=303)


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


def _sync_with_budget(user_email: str, budget_seconds: float = 6.0) -> bool:
    """Kick a fresh BC sync + scorer pass, wait up to `budget_seconds` for it
    to finish. Returns True if it completed in time. Used by Mark Done so
    the next focus task is computed from fresh data — previously the local
    store could lag BC by up to 5 min (auto-sync interval) causing already-
    completed tasks to surface as the next 'YOUR TURN'.

    Reuses the per-user lock dict from _maybe_async_sync so we don't race
    the background scheduler — if a sync is already in flight, we poll the
    lock until it clears or the budget runs out.
    """
    import threading
    import time as _time

    if getattr(_maybe_async_sync, "_locks", None) is None:
        _maybe_async_sync._locks = {}
    locks = _maybe_async_sync._locks

    # If a background sync is already running, just wait for it.
    if locks.get(user_email):
        deadline = _time.time() + budget_seconds
        while locks.get(user_email) and _time.time() < deadline:
            _time.sleep(0.2)
        return not locks.get(user_email)

    locks[user_email] = True
    done = threading.Event()

    def _run():
        try:
            from execution.products.ops import scorer as _scorer
            r = sync.pull_todos_for_user(user_email)
            if r.get("status") in ("ok", "partial"):
                _scorer.score_all_todos(user_email)
        except Exception:
            pass
        finally:
            locks[user_email] = False
            done.set()

    threading.Thread(target=_run, daemon=True, name=f"force-sync-{user_email}").start()
    done.wait(timeout=budget_seconds)
    return done.is_set()


@router.post("/todo/{bc_id}/complete")
async def ops_complete(bc_id: int, request: Request):
    """Mark a todo complete BOTH in BC (write-back via AI clone token)
    AND locally. If BC write-back fails, we still mark local — operator
    won't get stuck looking at a stale queue, and the audit row preserves
    intent. Then kick a fresh sync so the next focus task is computed
    against current BC state (not a 5-min-stale cache).
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
            with _ur.urlopen(req, timeout=6) as r:
                bc_status = f"ok ({r.status})"
        except _ue.HTTPError as e:
            bc_status = f"http_{e.code}"
        except Exception as e:  # noqa: BLE001
            bc_status = f"err: {type(e).__name__}"
    # Targeted sync of THE PROJECT we just touched — much faster than a
    # full 50-project sync (~2-3s vs ~30s) and catches the common case
    # where other people on the same project have closed tasks since the
    # last full sync. The full background sync still runs every 5 min for
    # cross-project state.
    proj_sync_result = {}
    try:
        proj_sync_result = sync.pull_todos_for_project(user.email, todo.bc_project_id)
    except Exception as e:
        proj_sync_result = {"status": "error", "error": str(e)}
    # Also re-score so the next focus selection sees current urgency.
    try:
        scorer.score_all_todos(user.email)
    except Exception:
        pass
    _log.getLogger(__name__).info(
        "my-day complete: user=%s bc_id=%s title=%r bc_status=%s proj_sync=%s",
        user.email, bc_id, title_snippet, bc_status, proj_sync_result.get("status", "?"),
    )
    # Kick a full background sync so OTHER projects are eventually fresh
    # too, without making the user wait for it.
    import threading as _th
    def _bg_full():
        try:
            sync.pull_todos_for_user(user.email)
            scorer.score_all_todos(user.email)
        except Exception:
            pass
    _th.Thread(target=_bg_full, daemon=True, name=f"bg-full-sync-{user.email}").start()
    # Build redirect URL: preserve filter state via Referer + append
    # ?done=<title> so the next render shows a green flash confirming
    # the action actually fired.
    import urllib.parse as _up
    referer = request.headers.get("Referer", "/my-day/")
    sep = "&" if "?" in referer else "?"
    flash = f"{sep}done={_up.quote(title_snippet)}"
    return RedirectResponse(referer + flash, status_code=303)


# ── /my-day/_health — operational visibility ──────────────────────


def _health_snapshot() -> dict:
    """Snapshot of /my-day/ system health. No PII; safe to log.

    Used by the /my-day/_health endpoint. Pulls from:
      - execution.products.ops.scheduler._scheduler  (cron state)
      - execution.products.pilot.scheduler._scheduler (pilot dash cron)
      - per-user state.json (last_sync_at, last_sync_status, error)
      - sync.recent_errors() (silent-failure ring buffer)
    """
    from datetime import datetime as _dt
    now = _dt.now(timezone.utc).isoformat()

    # Ops sync scheduler
    ops_sched_state = {"running": False, "jobs": []}
    try:
        from execution.products.ops import scheduler as _ops_sched
        s = _ops_sched._scheduler
        if s is not None and s.running:
            ops_sched_state["running"] = True
            for job in s.get_jobs():
                ops_sched_state["jobs"].append({
                    "id": job.id,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "trigger": str(job.trigger),
                })
    except Exception as e:
        ops_sched_state["error"] = f"{type(e).__name__}: {e}"

    # Pilot dash scheduler
    pilot_sched_state = {"running": False, "jobs": []}
    try:
        from execution.products.pilot import scheduler as _pilot_sched
        s = _pilot_sched._scheduler
        if s is not None and s.running:
            pilot_sched_state["running"] = True
            for job in s.get_jobs():
                pilot_sched_state["jobs"].append({
                    "id": job.id,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "trigger": str(job.trigger),
                })
    except Exception as e:
        pilot_sched_state["error"] = f"{type(e).__name__}: {e}"

    # Per-user sync state (one row per user with a state file)
    per_user = []
    try:
        users = tenancy.list_users(active_only=True)
        for u in users:
            st = store.load_state(u.email)
            age_sec = _store_age_seconds(st)
            per_user.append({
                "email": u.email,
                "last_sync_at": st.last_sync_at,
                "last_sync_status": st.last_sync_status,
                "last_sync_error": (st.last_sync_error or "")[:200],
                "age_seconds": int(age_sec) if age_sec < 99000 else None,
                "todo_count": len(store.load_todos(u.email)),
            })
    except Exception as e:
        per_user.append({"error": f"{type(e).__name__}: {e}"})

    # Pilot dash delivery config
    import os as _os
    pilot_delivery = {
        "delivery_enabled": _os.environ.get("PILOT_DASH_DELIVERY", "0") == "1",
        "test_mode": _os.environ.get("PILOT_DASH_TEST_MODE", "1") == "1",
        "smtp_creds_present": bool(
            _os.environ.get("GMAIL_SMTP_USERNAME", "").strip()
            and _os.environ.get("GMAIL_SMTP_APP_PASSWORD", "").strip()
        ),
    }

    return {
        "now": now,
        "ops_sync_scheduler": ops_sched_state,
        "pilot_dash_scheduler": pilot_sched_state,
        "per_user_sync": per_user,
        "recent_errors": sync.recent_errors(),
        "pilot_dash_delivery": pilot_delivery,
    }


@router.get("/_health")
async def ops_health(request: Request):
    """Operational health snapshot. Admin-only.

    Default JSON. Pass ?format=html for a renderable view (also served
    when the Accept header prefers HTML). Surfaces:
      - Both schedulers' state + next run time
      - Per-user last_sync_at + status + error
      - Recent silent-failure ring buffer (last 50)
      - Pilot dash delivery config

    Built post-audit 2026-06-04 so the next 'why isn't sync working'
    question can be answered without SSH-ing into the box.
    """
    user = _require_user(request)
    if "admin" not in (user.roles or []):
        raise HTTPException(403, "admin role required")

    snap = _health_snapshot()
    accept = (request.headers.get("accept") or "").lower()
    want_html = (
        request.query_params.get("format", "").lower() == "html"
        or ("text/html" in accept and "application/json" not in accept)
    )
    if not want_html:
        from fastapi.responses import JSONResponse
        return JSONResponse(content=snap)

    return request.app.state.templates.TemplateResponse(
        request, "my_day/_health.html",
        _ctx(request, user, snap=snap),
    )
