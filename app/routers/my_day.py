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

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from datetime import datetime, timezone

from fastapi import Form

from execution.products.library import auth_google, mcp_token, tenancy
from execution.products.ops import (
    bc_comments, context_collector, llm_suggest, personas, plan_inference,
    rollup, scorer, standing_orders, store, suggestions, sync,
    sync_coordinator, tokens,
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

    mcp_status_value = "red"
    try:
        mcp_status_value = mcp_token.status_for_user(user) if user else "red"
    except Exception:
        pass
    base = {
        "request": request,
        "current_product": "library",
        "library_nav_active": "my_day",
        "mcp_status": mcp_status_value,
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


_STOPWORDS = {"a", "an", "and", "the", "of", "for", "to", "in", "on", "with",
                          "from", "by", "or", "is", "are", "was", "were", "be", "been",
                          "this", "that", "these", "those", "as", "at", "it", "its",
                          "if", "then", "so", "but", "we", "i", "me", "my", "your",
                          "their", "them", "they", "do", "does", "did", "will", "can",
                          "could", "should", "would", "have", "has", "had", "than"}


def _tokenize(text: str) -> set:
    if not text:
        return set()
    out = set()
    cur = []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                w = "".join(cur)
                if len(w) >= 3 and w not in _STOPWORDS:
                    out.add(w)
                cur = []
    if cur:
        w = "".join(cur)
        if len(w) >= 3 and w not in _STOPWORDS:
            out.add(w)
    return out


def _suggest_library_assets_for_focus(user, focus) -> list:
    """Return up to 3 library assets whose name/description/tags overlap
    the focus task. Same-company hits rank above community.

    Output items are template-friendly dicts: category, asset_id, name,
    description, why_useful, claude_prompt, owning_company_id, vetted.

    Best-effort: if anything fails, returns []. Never raises.
    """
    if not focus:
        return []
    try:
        from execution.products.library import inventory
        from app.routers import library as _library_routes

        focus_keywords = (
            _tokenize(getattr(focus, "title", ""))
            | _tokenize(getattr(focus, "description", "") or "")
            | set(getattr(focus, "tags", []) or [])
            | _tokenize(getattr(focus, "bc_project_name", "") or "")
        )
        if len(focus_keywords) < 2:
            return []
    except Exception:
        return []

    viewer_co = getattr(user, "company_id", None) or "community"
    scored = []
    try:
        # Search across the categories most likely to be actionable in My
        # Day. Skipping policy / governance / chaos etc. -- those don't
        # typically apply to an in-flight task.
        for category in ("skills", "agents", "prompts", "workflows",
                                  "capabilities", "templates", "mcp"):
            try:
                rows = inventory.load_category(category)
            except Exception:
                continue
            # Try same-company first (those rank higher); fall back to all
            # visible to the operator (which includes community via the
            # filter -- see inventory.filter_for_company).
            visible = inventory.filter_for_company(rows, category, viewer_co)
            for r in visible:
                name = r.get("name") or r.get("id") or ""
                desc = r.get("description") or ""
                tags = r.get("tags") or []
                asset_tokens = (
                    _tokenize(name) | _tokenize(desc) | set(t.lower() for t in tags)
                )
                overlap = len(focus_keywords & asset_tokens)
                if overlap == 0:
                    continue
                owning = (r.get("owning_company_id") or "community").strip() or "community"
                # Same-company gets a +5 boost so a slightly-fuzzier
                # match from your own company beats a strong community match.
                score = overlap + (5 if owning == viewer_co else 0)
                scored.append((score, owning != viewer_co, category, r))
    except Exception:
        return []

    scored.sort(key=lambda x: (-x[0], x[1]))
    top = scored[:3]
    out = []
    for _score, _is_community, category, row in top:
        asset_id = row.get("id") or row.get("name") or ""
        try:
            claude_prompt = _library_routes.build_claude_prompt(
                category, asset_id, row.get("name") or asset_id,
            )
        except Exception:
            claude_prompt = ""
        # Rule: every Workspace action must ship with a Prompt. If the library
        # builder couldn't produce one, fall back to a minimal copyable prompt
        # so the suggested-asset card never renders a lone Workspace button.
        if not claude_prompt:
            _asset_name = row.get("name") or asset_id
            claude_prompt = (
                f"Help me use the library asset '{_asset_name}' ({category}) for "
                f"my current task. Open /library/{category}/{asset_id}, summarize "
                f"what it provides, and walk me through applying it step by step."
            )
        out.append({
            "category": category,
            "asset_id": asset_id,
            "name": row.get("name") or asset_id,
            "description": (row.get("description") or "")[:200],
            "owning_company_id": (row.get("owning_company_id") or "community"),
            "vetted": bool(row.get("vetted")),
            "claude_prompt": claude_prompt,
        })
    return out


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


def _log_bg_exception(user_email: str, kind: str, exc: BaseException) -> None:
    """Record a background-thread exception in the shared silent-error
    ring buffer so /my-day/_health surfaces it. Replaces the prior
    'except Exception: pass' pattern in every BG sync site, which the
    2026-06-09 audit (M5) flagged as a swallowed-failure class:
    top-level exceptions in router-spawned threads vanished without
    trace, so the only way to diagnose a stuck sync was to add print
    statements and redeploy."""
    detail = f"{type(exc).__name__}: {exc}"
    try:
        sync._record_error(user_email, kind, detail)
    except Exception:
        # Defensive: even the recorder shouldn't crash a daemon thread.
        # Module-level logger gets it as a last resort.
        import logging
        logging.getLogger(__name__).warning(
            "bg-sync exception (recorder also failed): user=%s kind=%s detail=%s",
            user_email, kind, detail,
        )


def _kick_bg_full_sync(user_email: str, focus_project_id: int | None = None) -> None:
    """Spawn a background sync. Mutual exclusion is enforced INSIDE
    pull_todos_for_user / pull_todos_for_project via SyncCoordinator
    (2026-06-09 audit H1/H2 fix) — if a sync is already in flight, the
    thread no-ops via the inner `already_running` short-circuit, so no
    router-level lock dict needed.

    `focus_project_id` — when the operator is viewing ONE project, walk
    that project first via the targeted, budget-exempt
    pull_todos_for_project, THEN run the full sweep for everything else.
    This is the 2026-06-18 'sync still doesn't match BC' follow-up to the
    #53 pagination fix: the full sweep walks projects under a round-robin
    cursor capped at SYNC_BUDGET_SECONDS (120s). An operator with more
    projects than fit in one budget window — CB System sees 50+ — can
    trigger sync after sync and never have the project they are STARING AT
    refreshed, because the cursor keeps deferring it past the budget. The
    targeted walk is a single project (~2-3s, no budget cap), so the viewed
    project ALWAYS matches BC after a sync. Both calls go through the
    coordinator sequentially in this one thread, so they never race each
    other; the targeted walk's data lands even if the full sweep then
    no-ops via `already_running`.

    History: this used to manage a `_maybe_async_sync._locks` function-
    attribute dict. That dict was a hidden global mutated from four
    call sites with no TTL, racy check-then-set, and bypassed by the
    cron scheduler. All four findings (H1, H2, M1, and the lock-clever-
    ness L1) are retired by moving the gate into SyncCoordinator."""
    import threading

    def _run():
        try:
            produced = False
            if focus_project_id is not None:
                # Targeted walk of the viewed project first — budget-exempt,
                # so the round-robin cursor can't starve it.
                pr = sync.pull_todos_for_project(user_email, focus_project_id)
                produced = pr.get("status") == "ok"
            r = sync.pull_todos_for_user(user_email)
            produced = r.get("status") in ("ok", "partial") or produced
            if produced:
                scorer.score_all_todos(user_email)
        except Exception as e:
            _log_bg_exception(user_email, "bg_full_sync", e)

    threading.Thread(target=_run, daemon=True, name=f"bg-sync-{user_email}").start()


def _maybe_async_sync(user_email: str, state) -> None:
    """Legacy background-only nudge. Kept so cb_mention_worker + other
    callers don't break. New code should use _natural_flow_sync."""
    if _store_age_seconds(state) < 180:
        return
    _kick_bg_full_sync(user_email)


def _natural_flow_sync(user_email: str, state, project_filter_id: int | None) -> bool:
    """Page-load 'natural flow' sync: ALWAYS non-blocking. Kicks a
    background sync when the store is stale OR when the last sync
    didn't complete cleanly, returns immediately.

    H4 fix (2026-06-09 audit): the gate now requires BOTH (age < 90s)
    AND (last_sync_status == "ok"). The prior version only checked
    age, so a partial sync that errored on the user's most-active
    project still bumped last_sync_at, and the gate would say 'fresh
    enough' even though the data the user actually cares about
    hadn't refreshed. We now treat 'partial' and 'failed' as reasons
    to immediately retry on the next page load, regardless of age.

    History: this used to do an inline pull_todos_for_project (~2-3s
    typically, but observed up to 30s when BC was slow or the project
    had many lists). That froze the page for the user. Now we always
    just kick the background sync; the user sees current store data
    (which is at worst a few minutes stale), and the next page load /
    Sync-button click picks up the freshly-synced data.

    The background scheduler still runs every 5 min as a backstop.
    Mark Done's targeted sync (ops_complete) covers the write path's
    freshness need without blocking.
    """
    age = _store_age_seconds(state)
    last_status = state.last_sync_status or ""
    # Fresh AND clean → no work. Stale OR last-was-bad → retry.
    if age < 90 and last_status == "ok":
        return False
    # Kick a bg sync. When the operator is viewing one project, walk THAT
    # project first (targeted, budget-exempt) so the view they're looking
    # at always reflects BC — the full sweep's round-robin SYNC_BUDGET_SECONDS
    # cap can otherwise defer it indefinitely (CB System sees 50+ projects).
    # Still non-blocking: returns immediately either way.
    _kick_bg_full_sync(user_email, focus_project_id=project_filter_id)
    return False  # never reload state inline -- avoid the 30s freeze


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
    if view not in ("briefing", "kanban", "heatmap", "extract"):
        view = "briefing"
    # Tier filter: assignment-based (assigned|due|unassigned|all) OR
    # kind-based (human|ai). The 6 values are mutually exclusive in the UI.
    # Default is 'assigned' so each operator's first view of My Day is
    # their own plate, not the org-wide queue. Earlier we defaulted to
    # 'all' because BC-title-tagged tasks (e.g. '[Ali] foo') without a
    # formal assignee get hidden under 'assigned' and that surprised
    # the operator — that's still true, but most users want to see
    # their assigned work first and can toggle to All for the org view.
    tier = request.query_params.get("tier", "assigned")
    if tier not in ("assigned", "due", "unassigned", "watching", "all", "human", "ai"):
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

    # Apply project filter (parsed earlier as _early_proj for the
    # natural-flow sync; re-bind to the name the downstream code uses).
    project_filter_id = _early_proj

    # List filter (drill-in from Feasibility table)
    list_filter_raw = request.query_params.get("list", "")
    try:
        list_filter_id = int(list_filter_raw) if list_filter_raw else None
    except ValueError:
        list_filter_id = None

    # Person filter — clickable assignee pill on the YOUR TURN card sets this,
    # or the "Filter by person" collapsed chip row does. Substring match on
    # assignee_names so 'Christian' matches 'Christian Outlaw'.
    person_filter = (request.query_params.get("person", "") or "").strip() or None

    # People chip data — scoped to the active project + list filters (but
    # NOT the person filter, since selecting a person would otherwise
    # collapse the chip row to just that one name). This mirrors how the
    # list chips are already scoped to the active project: pick a project,
    # see only people who appear in that project; drill into a list, see
    # only people on that list. Top 50 keeps the chip row finite.
    people_scope_todos = active_unfiltered
    if project_filter_id is not None:
        people_scope_todos = [t for t in people_scope_todos if t.bc_project_id == project_filter_id]
    if list_filter_id is not None:
        people_scope_todos = [t for t in people_scope_todos if t.bc_todolist_id == list_filter_id]
    people_counts: Counter = Counter()
    for t in people_scope_todos:
        for a in (t.assignee_names or []):
            if a:
                people_counts[a] += 1
    people_for_chips = people_counts.most_common(50)

    if project_filter_id is not None:
        scoped_todos = [t for t in todos if t.bc_project_id == project_filter_id]
    else:
        scoped_todos = todos

    if list_filter_id is not None:
        scoped_todos = [t for t in scoped_todos if t.bc_todolist_id == list_filter_id]

    if person_filter is not None:
        p_lower = person_filter.lower()
        scoped_todos = [
            t for t in scoped_todos
            if any(p_lower in (a or "").lower() for a in (t.assignee_names or []))
        ]

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

    # Pre-compute deterministic Claude Code prompts for the kanban view
    # (the 30 visible cards per column × 4 columns = up to 120 prompts).
    # Deterministic builder is fast (no LLM round trip); user can click
    # 📋 on any kanban card to copy without leaving the board.
    kanban_prompts: dict[int, str] = {}
    if view == "kanban":
        seen_ids: set[int] = set()
        for col_todos in kanban_cols.values():
            for t in col_todos[:30]:
                if t.bc_id in seen_ids:
                    continue
                seen_ids.add(t.bc_id)
                try:
                    s = suggestions.build_suggestion(t)
                    kanban_prompts[t.bc_id] = suggestions.generate_prompt(t, s, persona=getattr(user, "prompt_persona", None))
                except Exception:
                    kanban_prompts[t.bc_id] = ""
    # ── Heat map extras: People group, scatter-plot points, next-task prompts ──
    # People is the third Heat map group (alongside Projects + Lists). The
    # scatter plot plots all three groups in one view; each point carries the
    # KPIs the user asked for (At-Risk score, Ticket-Late %, AI/Human %).
    person_rollups: list = []
    scatter_points: list[dict] = []
    heat_prompts: dict[int, str] = {}

    def _scatter_point(cat: str, name: str, r) -> dict:
        open_c = getattr(r, "open_count", 0) or 0
        late_pct = round(100 * r.overdue_count / open_c) if open_c else 0
        ai_pct = round(100 * r.ai_count / open_c) if open_c else 0
        band = rollup.score_band(r.score)
        return {
            "cat": cat, "name": name, "score": r.score,
            "late_pct": late_pct, "ai_pct": ai_pct,
            "open": open_c, "overdue": r.overdue_count,
            "human": r.human_count, "ai": r.ai_count,
            "color": band["color"], "band": band["label"],
        }

    def _prompt_for(t) -> str:
        try:
            s = suggestions.build_suggestion(t)
            return suggestions.generate_prompt(t, s, persona=getattr(user, "prompt_persona", None))
        except Exception:
            return ""

    if view == "heatmap":
        person_rollups = rollup.per_person(scoped_todos)
        for p in project_rollups:
            scatter_points.append(_scatter_point("Project", p.project_name, p))
        for r in list_rollups:
            scatter_points.append(_scatter_point("List", r.list_name, r))
        for pr in person_rollups:
            scatter_points.append(_scatter_point("Person", pr.name, pr))
        # Deterministic prompts for the "next task" on each card (Prompt button).
        for grp in (project_rollups, list_rollups, person_rollups):
            for r in grp:
                nb = getattr(r, "next_blocking", None)
                if nb and nb.bc_id not in heat_prompts:
                    heat_prompts[nb.bc_id] = _prompt_for(nb)

    # Briefing feasibility table: precompute prompts for each list's next
    # blocking task so the black 📋 Prompt button can copy without a round trip.
    #
    # seq_prompts covers EVERY task rendered in the PROJECT TIMELINE rows, not
    # just the next-blocking step. Rule: every Workspace action must ship with a
    # matching Prompt button, so each timeline row needs its own ready-to-paste
    # prompt. The deterministic builder is cheap (no LLM round trip), same as
    # the kanban precompute above. Keyed by bc_id and deduped across lists.
    row_prompts: dict[int, str] = {}
    seq_prompts: dict[int, str] = {}
    if view == "briefing":
        for r in list_rollups:
            nb = r.next_blocking
            if nb and nb.bc_id not in row_prompts:
                row_prompts[nb.bc_id] = _prompt_for(nb)
            for t in r.open_todos:
                if t.bc_id not in seq_prompts:
                    seq_prompts[t.bc_id] = _prompt_for(t)

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
        # Try LLM enhancement first: pull recent comments, send to GPT, get
        # ticket-specific FIELDS back (not a prompt). Cached per ticket.
        token, _src = tokens.get_user_token(user.email)
        enhanced = None
        comments_text = ""
        if not is_post_action:
            comments_text = bc_comments.fetch_recent_comments(focus, token) if token else ""
            enhanced = llm_suggest.enhance(user.user_id, focus, comments_text)
        if enhanced:
            # Fold the LLM fields into the deterministic suggestion so the focus
            # card renders through the SAME BLUF generate_prompt as everything else.
            focus_suggestion = suggestions.merge_llm_suggestion(focus, enhanced)
            focus_llm_source = "llm"
        else:
            # Deterministic fallback when OpenAI is unavailable, errors, or
            # we deliberately skipped it for the post-action snappy render.
            focus_suggestion = suggestions.build_suggestion(focus)
        # One BLUF renderer for both paths; standing orders + inlined comments
        # on the focus card regardless of whether the LLM was available.
        focus_prompt = standing_orders.append_orders(
            suggestions.generate_prompt(focus, focus_suggestion, comments=comments_text,
                                        persona=getattr(user, "prompt_persona", None))
        )

    # Single-project context (used for big-picture banner)
    project_one = projects[0] if len(projects) == 1 else None

    # Heat-of-day greeting + relative sync
    first_name = (user.display_name or user.email).split()[0]
    greeting = _greeting()
    sync_rel = _sync_relative(state.last_sync_at) if state.last_sync_at else ""
    # L5 (2026-06-09 audit): when no full sync has run but Mark Done has,
    # surface the targeted-sync timestamp instead of "Not synced yet".
    targeted_sync_rel = (
        _sync_relative(state.last_targeted_sync_at)
        if state.last_targeted_sync_at and not state.last_sync_at
        else ""
    )

    # Project chip data: tier-filtered (so counts match what user sees
    # after clicking) but project/list-unfiltered (so all projects can
    # appear as chips). Bug it fixes: clicking a chip that says "17 open"
    # showing an empty queue because the 17 are all in other tiers.
    chip_source = _filter_by_tier(todos)
    all_project_rollups = rollup.per_project(chip_source)

    # List chip data — similar logic: tier-filtered + project-filtered
    # (so the chip set scopes to "lists in the currently-filtered project"
    # when a project is selected; otherwise all lists). Cap at 50 to keep
    # the chip row finite. Sort by overdue desc then open count desc so
    # the user's eye lands on the most-at-risk lists first.
    chip_list_source = chip_source
    if project_filter_id is not None:
        chip_list_source = [t for t in chip_list_source if t.bc_project_id == project_filter_id]
    all_list_rollups_full = rollup.per_list(chip_list_source)
    all_list_rollups_full.sort(key=lambda r: (-r.overdue_count, -r.open_count))
    all_list_rollups_for_chips = all_list_rollups_full[:50]

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
        "extract": "my_day/extract_tab.html",
    }.get(view, "my_day/home.html")

    # Suggested library assets for the focus task: query inventory for
    # assets whose name / description / tags overlap the focus task's
    # title + tags. Same-company hits rank above community. Top 3 only.
    library_suggestions = _suggest_library_assets_for_focus(user, focus) if focus else []

    # Extract tab needs per-list completed-todo rollup + classifier tags.
    # Cheap to compute (filters the in-memory todos list once) so we do it
    # inline rather than gating behind view==extract; downstream templates
    # ignore the extras.
    extract_lists = []
    if view == "extract":
        from collections import defaultdict
        from execution.products.library import extract_classifier
        completed = store.list_completed_for_user(user.email, days=90)
        by_list: dict[tuple, dict] = defaultdict(lambda: {
            "list_id": None, "list_name": "", "project_id": None,
            "project_name": "", "completed_count": 0,
            "task_titles": [], "task_descriptions": [], "most_recent": "",
            "task_samples": [],
        })
        for t in completed:
            key = (t.bc_project_id, t.bc_todolist_id)
            row = by_list[key]
            row["list_id"] = t.bc_todolist_id
            row["list_name"] = t.bc_todolist_name or "(unnamed list)"
            row["project_id"] = t.bc_project_id
            row["project_name"] = t.bc_project_name or "(unnamed project)"
            row["completed_count"] += 1
            row["task_titles"].append(t.title)
            if t.description:
                row["task_descriptions"].append(t.description[:500])
            if t.completed_at > row["most_recent"]:
                row["most_recent"] = t.completed_at
            if len(row["task_samples"]) < 3:
                row["task_samples"].append({
                    "bc_id": t.bc_id, "title": t.title,
                    "completed_at": t.completed_at,
                })
        # Compute tags + relative-time + sort. Most-recently-active first.
        extract_lists = []
        for row in by_list.values():
            row["suggested_tags"] = extract_classifier.suggest_for_list(
                row["list_name"], row["task_titles"], row["task_descriptions"],
            )
            row["time_ago"] = _sync_relative(row["most_recent"])
            extract_lists.append(row)
        extract_lists.sort(key=lambda r: r["most_recent"], reverse=True)

    response = request.app.state.templates.TemplateResponse(
        request, template_name,
        _ctx(request, user,
             # View routing
             view=view,
             project_filter=project_filter_id,
             list_filter=list_filter_id,
             person_filter=person_filter,
             people_for_chips=people_for_chips,
             all_list_rollups_for_chips=all_list_rollups_for_chips,
             all_list_rollups_total=len(all_list_rollups_full),
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
             targeted_sync_relative=targeted_sync_rel,
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
             # Heat map: third (people) group + scatter + per-card prompts
             person_rollups=person_rollups,
             scatter_points=scatter_points,
             heat_prompts=heat_prompts,
             row_prompts=row_prompts,
             seq_prompts=seq_prompts,
             # Extract tab (view=extract)
             extract_lists=extract_lists,
             output_type_meta=(
                 __import__("execution.products.library.extract_classifier",
                                    fromlist=["OUTPUT_TYPES"]).OUTPUT_TYPES
                 if view == "extract" else {}
             ),
             kanban_cols=kanban_cols,
             kanban_prompts=kanban_prompts,
             completer_stats=completer_stats,
             recent_completed_all=recent_completed_all,
             human_todos=human_todos,
             ai_todos=ai_todos,
             total_open=status.open_count,
             my_day_total_open=status.open_count,
             library_suggestions=library_suggestions,
             show_welcome_banner=(
                 request.query_params.get("welcome") == "1"
             )),
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
    prompt = suggestions.generate_prompt(todo, suggestion, persona=getattr(user, "prompt_persona", None))
    return request.app.state.templates.TemplateResponse(
        request, "my_day/workspace.html",
        _ctx(request, user, todo=todo, suggestion=suggestion, prompt=prompt,
             personas=personas.PERSONAS,
             active_persona=personas.get(getattr(user, "prompt_persona", None))["id"]),
    )


@router.post("/persona")
async def ops_set_persona(request: Request, persona: str = Form(""),
                          next: str = Form("/my-day/")):
    """Save the operator's prompt-delivery persona (server-side, per operator).
    It then applies to every prompt they copy, on every surface and device,
    until they change it. Redirects back so the page re-renders with the new
    persona embedded in the prompt."""
    user = _require_user(request)
    if personas.is_valid(persona):
        user.prompt_persona = persona
        tenancy.upsert_user(user)
    dest = next if next.startswith("/my-day") else "/my-day/"
    return RedirectResponse(url=dest, status_code=303)


@router.post("/sync")
async def ops_sync(
    request: Request,
    view: str = Form(""),
    tier: str = Form(""),
    project: str = Form(""),
    list_id: str = Form("", alias="list"),
    person: str = Form(""),
):
    """Kick a background sync and redirect immediately.

    User feedback: the previous blocking sync left the dim 'Syncing…'
    overlay up for 30+ seconds while we walked all 50 projects with
    220ms throttle per HTTP call. Now we redirect right away with
    ?sync_started=1; the home page shows an in-progress banner and
    auto-refreshes 25s later so fresh data is visible without the
    user having to click refresh.

    Filter preservation: the Sync forms in home/kanban/heatmap emit the
    active view/tier/project/list/person as hidden inputs so the redirect
    lands the user back on the same filtered view. Previously we hard-
    coded the destination to bare /my-day/, which silently dropped every
    URL-driven filter and felt like a state reset.

    Mutual exclusion is now enforced inside pull_todos_for_user via
    SyncCoordinator. If a sync is already in flight (scheduler cron,
    Mark Done's targeted refresh, a prior unfinished click), the thread
    no-ops via the inner `already_running` short-circuit. We still
    redirect with ?sync_started=1 so the user gets feedback; the page
    will reload against whichever sync finishes first.
    """
    import threading as _th
    user = _require_user(request)
    legacy = ALI_LEGACY_BUCKET if user.email == "ali@colaberry.com" else None

    # Defensive fallback: if the form lacked any filter fields (a stale
    # template version, a proxy stripping the POST body, or a direct
    # curl), parse the Referer query string so we still preserve filters.
    # Resolved BEFORE the sync kicks so the focused-project walk below
    # AND the redirect both see the recovered project filter.
    if not (view or tier or project or list_id or person):
        from urllib.parse import urlparse, parse_qs
        ref_qs = parse_qs(urlparse(request.headers.get("Referer", "")).query)
        view = ref_qs.get("view", [""])[0]
        tier = ref_qs.get("tier", [""])[0]
        project = ref_qs.get("project", [""])[0]
        list_id = ref_qs.get("list", [""])[0]
        person = ref_qs.get("person", [""])[0]

    # When Sync is triggered while viewing one project, walk THAT project
    # first (targeted, budget-exempt) so it always reflects BC. The full
    # sweep alone can defer it indefinitely behind the round-robin
    # SYNC_BUDGET_SECONDS cursor (CB System sees 50+ projects) — the user's
    # "MyDay should match BC after a sync, no matter what". See
    # _kick_bg_full_sync's focus_project_id contract.
    try:
        focus_project = int(project) if project else None
    except ValueError:
        focus_project = None

    def _bg():
        try:
            produced = False
            if focus_project is not None:
                pr = sync.pull_todos_for_project(user.email, focus_project)
                produced = pr.get("status") == "ok"
            result = sync.pull_todos_for_user(user.email, ali_legacy_bucket=legacy)
            produced = result.get("status") in ("ok", "partial") or produced
            if produced:
                scorer.score_all_todos(user.email)
        except Exception as e:
            _log_bg_exception(user.email, "manual_sync", e)

    _th.Thread(target=_bg, daemon=True, name=f"manual-sync-{user.email}").start()

    from urllib.parse import urlencode
    params: list[tuple[str, str]] = []
    if view: params.append(("view", view))
    if tier: params.append(("tier", tier))
    if project: params.append(("project", project))
    if list_id: params.append(("list", list_id))
    if person: params.append(("person", person))
    params.append(("sync_started", "1"))
    return RedirectResponse(f"/my-day/?{urlencode(params)}", status_code=303)


@router.get("/sync-status.json")
async def ops_sync_status(request: Request):
    """Polling endpoint for the in-progress sync banner.

    Returns SyncCoordinator's view of in-flight syncs for the
    requesting user plus the most recent state.json snapshot. The
    /my-day/ home page polls this every ~2.5s when ?sync_started=1
    is in the URL, replacing the prior fixed 25s countdown that
    could fire long before (or long after) the real sync completed.

    Audit M3 fix (2026-06-09). Per-user, lightweight, no admin gate
    (every user can poll their own sync status).
    """
    user = _require_user(request)
    state = store.load_state(user.email)
    coord = sync_coordinator.get_coordinator()
    from fastapi.responses import JSONResponse
    return JSONResponse(
        {
            "in_flight": coord.is_sync_in_flight(user.email),
            "in_flight_age_seconds": coord.in_flight_age_seconds(user.email),
            "last_sync_at": state.last_sync_at or "",
            "last_sync_status": state.last_sync_status or "",
        },
        headers={"Cache-Control": "no-store"},
    )


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
    """Kick a fresh BC sync + scorer pass, wait up to `budget_seconds` for
    it to finish. Returns True if it completed in time. Used by Mark
    Done so the next focus task is computed from fresh data — previously
    the local store could lag BC by up to 5 min (auto-sync interval)
    causing already-completed tasks to surface as the next 'YOUR TURN'.

    Coordinator integration: if a sync is already in flight, wait for IT
    rather than spawning a competing one (which would just no-op via
    `already_running` anyway). If no sync is in flight, spawn one and
    block on its `done` Event up to the budget. Returns True iff the
    spawned-or-awaited sync completed in time.
    """
    import threading

    coord = sync_coordinator.get_coordinator()
    if coord.is_sync_in_flight(user_email):
        return coord.wait_for_sync(user_email, budget_seconds)

    done = threading.Event()

    def _run():
        try:
            r = sync.pull_todos_for_user(user_email)
            if r.get("status") in ("ok", "partial"):
                scorer.score_all_todos(user_email)
        except Exception as e:
            _log_bg_exception(user_email, "mark_done_sync", e)
        finally:
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
        except Exception as e:
            _log_bg_exception(user.email, "mark_done_bg_full", e)
    _th.Thread(target=_bg_full, daemon=True, name=f"bg-full-sync-{user.email}").start()
    # Build redirect URL: preserve filter state via Referer + append
    # ?done=<title> so the next render shows a green flash confirming
    # the action actually fired.
    import urllib.parse as _up
    referer = request.headers.get("Referer", "/my-day/")
    sep = "&" if "?" in referer else "?"
    flash = f"{sep}done={_up.quote(title_snippet)}"
    return RedirectResponse(referer + flash, status_code=303)


# ── /my-day/reports/{filename} — serve generated HTML reports ────


@router.get("/reports/{filename}")
async def ops_report_file(filename: str, request: Request):
    """Serve a rendered HTML report from docs/reports/. Admin-only.

    Lets Ali view the workflow-generated status reports as rendered HTML
    instead of GitHub's text/plain raw view. Filename is sanitized
    against path traversal.
    """
    user = _require_user(request)
    if "admin" not in (user.roles or []):
        raise HTTPException(403, "admin role required")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    if not filename.endswith((".html", ".json")):
        raise HTTPException(400, "only .html / .json")
    from config.settings import PROJECT_ROOT
    from fastapi.responses import Response
    p = PROJECT_ROOT / "docs" / "reports" / filename
    if not p.exists():
        raise HTTPException(404, f"report not found: {filename}")
    media = "text/html; charset=utf-8" if filename.endswith(".html") else "application/json"
    return Response(content=p.read_text(encoding="utf-8"), media_type=media)


# ── /my-day/decisions-report — printable / shareable rich-action report ──


@router.get("/decisions-report")
async def ops_decisions_report(request: Request):
    """Email-style decisions report with full rich actions.

    Mirrors the layout of external "Decisions Report" digests (subject
    line + BIG PICTURE narrative + KPI tiles + YOUR TURN + List Scorecard)
    but adds the action surface from the briefing view: Mark Done button,
    Copy Claude Code prompt button, WHAT TO DO / Goal / Steps / Stop
    conditions.

    Use case: a single shareable URL that operates like the print/email
    digest but lets the recipient actually act on the focus task without
    bouncing back to /my-day/. Print-friendly CSS so it renders cleanly
    in a printed PDF or pasted into an email.
    """
    user = _require_user(request)
    state = store.load_state(user.email)

    project_filter_raw = request.query_params.get("project", "")
    try:
        project_filter_id = int(project_filter_raw) if project_filter_raw else None
    except ValueError:
        project_filter_id = None

    # Same natural-flow sync as ops_home so the report is fresh
    if _natural_flow_sync(user.email, state, project_filter_id):
        state = store.load_state(user.email)

    todos = store.load_todos(user.email)
    projects = store.load_projects(user.email)

    # Decisions report defaults to 'all' tier scope (not narrowed by tier)
    if project_filter_id is not None:
        scoped = [t for t in todos if t.bc_project_id == project_filter_id]
    else:
        scoped = todos

    status = rollup.overall(scoped)
    list_rollups = rollup.per_list(scoped)
    active = [t for t in scoped if t.status == "active" and not t.is_dismissed]
    active_sorted = sorted(active, key=lambda t: (-t.urgency_score, t.due_on or "9999-12-31"))
    human_todos = [t for t in active_sorted if rollup.tier(t) == "H"]
    focus = human_todos[0] if human_todos else (active_sorted[0] if active_sorted else None)

    focus_suggestion = None
    focus_prompt = ""
    focus_llm_source = "deterministic"
    is_post_action = bool(request.query_params.get("done") or request.query_params.get("skip"))
    if focus:
        token, _ = tokens.get_user_token(user.email)
        enhanced = None
        comments_text = ""
        if not is_post_action:
            comments_text = bc_comments.fetch_recent_comments(focus, token) if token else ""
            enhanced = llm_suggest.enhance(user.user_id, focus, comments_text)
        if enhanced:
            focus_suggestion = suggestions.merge_llm_suggestion(focus, enhanced)
            focus_llm_source = "llm"
        else:
            focus_suggestion = suggestions.build_suggestion(focus)
        focus_prompt = standing_orders.append_orders(
            suggestions.generate_prompt(focus, focus_suggestion, comments=comments_text,
                                        persona=getattr(user, "prompt_persona", None))
        )

    project_one = next((p for p in projects if p.bc_id == project_filter_id), None) if project_filter_id else None

    # "EITHER" tile: tasks neither categorically human-required nor an AI
    # action (waiting_dependency / unscored — could go either way).
    either_count = sum(
        1 for t in active
        if t.category not in ("human_required",) and rollup.tier(t) == "AI"
        and t.category in ("waiting_dependency", "unscored", "")
    )

    first_name = (user.display_name or user.email).split()[0]
    response = request.app.state.templates.TemplateResponse(
        request, "my_day/decisions_report.html",
        _ctx(request, user,
             status=status,
             list_rollups=list_rollups,
             focus=focus,
             focus_suggestion=focus_suggestion,
             focus_prompt=focus_prompt,
             focus_llm_source=focus_llm_source,
             first_name=first_name,
             project_one=project_one,
             project_filter=project_filter_id,
             either_count=either_count,
             total_open=status.open_count,
             my_day_total_open=status.open_count,
             active_list_count=len({t.bc_todolist_id for t in active}),
             generated_at=datetime.now(timezone.utc),
        ),
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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


# ── Phase 6: Extract surface ─────────────────────────────────
#
# Three routes that put the Phase 4 skill_extractor.extract() engine behind a
# manual UI in My Day. Per docs/specs/my-day-06-extract-surface.md.
#
# GET  /my-day/extract/         -> page (list of completed work + preview pane)
# GET  /my-day/extract/preview  -> render preview (no commit) for the modal
# POST /my-day/extract/commit   -> render + commit to skill-extracted/<slug>
#                                  + post BC echo on the source ticket
#
# All three use _require_user. Phase 6 anti-scope: never modify
# skill_extractor.py or extracted_writer.py from here -- those are the engine
# and onboarding also calls them.


@router.get("/extract/")
async def extract_index(request: Request, days: int = 90,
                                 project: str = "", list: str = "",
                                 suggest: str = ""):
    """List recently-completed BC todos the user can extract from.

    Drill-down from the My Day Extract tab:
      ?list=<todolist_id>  -> show only tasks from that BC list
      ?suggest=<output>    -> pre-select this output_type in the panel
      ?project=<name>      -> filter by project name (legacy)
    """
    user = _require_user(request)
    # Ops store is keyed by email (existing My Day convention; pull_todos_for_user
    # is called with user_email everywhere in this router).
    completed = store.list_completed_for_user(user.email, days=days)
    if project:
        completed = [t for t in completed if t.bc_project_name == project]
    selected_list_name = ""
    selected_project_name = ""
    if list:
        try:
            list_id = int(list)
        except ValueError:
            list_id = None
        if list_id is not None:
            scoped = [t for t in completed if t.bc_todolist_id == list_id]
            if scoped:
                selected_list_name = scoped[0].bc_todolist_name
                selected_project_name = scoped[0].bc_project_name
                completed = scoped

    # Per-task suggestion tags (Phase 7) so the drill-down view shows tags too.
    from execution.products.library import extract_classifier
    completed_with_tags = []
    for t in completed:
        text = f"{t.title or ''} {t.description or ''}"
        completed_with_tags.append({
            "todo": t,
            "tags": extract_classifier.suggest_output_types(text),
            "time_ago": _sync_relative(t.completed_at),
        })

    # List-level extraction: aggregate the whole list into one signal so the
    # user can extract the entire list as a single asset (different mode from
    # the per-task tag clicks below). Only computed when scoped to a list.
    list_level_tags: list[str] = []
    list_level_meta: dict = {}
    if selected_list_name and completed:
        titles = [t.title for t in completed]
        descs = [t.description for t in completed if t.description]
        list_level_tags = extract_classifier.suggest_for_list(
            selected_list_name, titles, descs,
        )
        # Use the most-recent completed task's BC bucket as the project/list
        # source for the list-level extract (every task in the scope shares
        # the same bucket_id + todolist_id by construction).
        first = completed[0]
        list_level_meta = {
            "list_id": first.bc_todolist_id,
            "list_name": selected_list_name,
            "project_id": first.bc_project_id,
            "project_name": selected_project_name,
            "task_count": len(completed),
            "time_ago": _sync_relative(first.completed_at),
        }

    # Project filter chips: distinct project names from the completed set
    project_counts: dict[str, int] = {}
    for t in completed:
        project_counts[t.bc_project_name] = project_counts.get(t.bc_project_name, 0) + 1
    project_list = sorted(project_counts.items(), key=lambda kv: -kv[1])

    # Available output_types (driven by templates on disk)
    from execution.products.library import extracted_writer
    output_types = extracted_writer.available_output_types()

    # Existing extracted artifacts (for the "previous extracts" panel).
    # Records are written keyed by user.email (set in extract_commit), so
    # query by email to match.
    prior = extracted_writer.list_records(created_by=user.email)

    return request.app.state.templates.TemplateResponse(
        request, "my_day/extract.html",
        _ctx(request, user,
                completed=completed,
                completed_with_tags=completed_with_tags,
                project_counts=project_list,
                active_project_filter=project,
                selected_list_id=list,
                selected_list_name=selected_list_name,
                selected_project_name=selected_project_name,
                preselect_output_type=suggest,
                list_level_tags=list_level_tags,
                list_level_meta=list_level_meta,
                days=days,
                output_types=output_types,
                output_type_meta=extract_classifier.OUTPUT_TYPES,
                prior_extracts=prior,
                library_nav_active="my_day"),
    )


@router.get("/extract/preview")
async def extract_preview(request: Request,
                                        source_kind: str = "bc_ticket",
                                        bc_id: str = "",
                                        bucket_id: str = "",
                                        output_type: str = "skill",
                                        slug: str = ""):
    """Render the artifact without committing. Returns JSON for the modal.

    For source_kind=bc_ticket: bc_id is a todo id; bucket_id is auto-derived
    from the user's local store.
    For source_kind=bc_list: bc_id is a todolist id; bucket_id (project id)
    MUST be supplied by the caller.
    """
    from fastapi.responses import JSONResponse
    user = _require_user(request)
    if not bc_id:
        return JSONResponse({"ok": False, "error": "bc_id required"}, status_code=400)
    try:
        bc_id_int = int(bc_id)
    except ValueError:
        return JSONResponse({"ok": False, "error": f"bc_id must be numeric: {bc_id!r}"}, status_code=400)

    resolved_bucket = bucket_id
    if source_kind == "bc_ticket" and not resolved_bucket:
        todo = store.get_todo(user.email, bc_id_int)
        if not todo:
            return JSONResponse(
                {"ok": False,
                 "error": f"BC todo {bc_id} not found in your local store; sync first"},
                status_code=404,
            )
        resolved_bucket = str(todo.bc_project_id)
    if not resolved_bucket:
        return JSONResponse({"ok": False, "error": "bucket_id required for source_kind=bc_list"}, status_code=400)

    # Token resolution: prefer per-user OAuth token, fall back to the shared
    # CB System token from env (current architecture: one principal reads BC
    # for everyone, per Ali's clarification). Without the fallback this route
    # 400s for any user who hasn't set up a personal BC OAuth token.
    token, _ = tokens.get_user_token(user.email)
    if not token:
        token = os.environ.get("BASECAMP_ACCESS_TOKEN", "")
    if not token:
        return JSONResponse({"ok": False, "error": "BC token missing (no user token and no CB System fallback)"}, status_code=500)

    from execution.products.library import skill_extractor
    try:
        result = skill_extractor.extract(
            source_kind=source_kind,
            bc_id=bc_id,
            output_type=output_type,
            slug=slug or None,
            commit=False,
            bc_token=token,
            bucket_id=resolved_bucket,
            created_by=user.email,
        )
    except Exception as e:
        # Without this catch, an exception inside extract() (template
        # render error, BC API hiccup, GitHub auth issue) escapes to
        # FastAPI's default handler which returns an HTML 500 page --
        # the client-side fetch chokes parsing "Internal Server Error"
        # as JSON and shows a misleading "Unexpected token I" error.
        return JSONResponse(
            {"ok": False, "error": str(e), "error_type": type(e).__name__},
            status_code=500,
        )
    return JSONResponse(result)


@router.post("/extract/commit")
async def extract_commit(request: Request,
                                          source_kind: str = Form("bc_ticket"),
                                          bc_id: str = Form(...),
                                          bucket_id: str = Form(""),
                                          output_type: str = Form("skill"),
                                          slug: str = Form(""),
                                          skip_bc_echo: bool = Form(False)):
    """Render + commit to skill-extracted/<slug> branch, then echo on source ticket.

    BC echo only fires for source_kind=bc_ticket (we know exactly which todo
    to comment on). For source_kind=bc_list there's no canonical single
    "source" todo, so we skip the echo.
    """
    from fastapi.responses import JSONResponse
    user = _require_user(request)

    try:
        bc_id_int = int(bc_id)
    except ValueError:
        return JSONResponse({"ok": False, "error": f"bc_id must be numeric: {bc_id!r}"}, status_code=400)

    resolved_bucket = bucket_id
    todo = None
    if source_kind == "bc_ticket":
        todo = store.get_todo(user.email, bc_id_int)
        if not todo:
            return JSONResponse(
                {"ok": False,
                 "error": f"BC todo {bc_id} not found in your local store"},
                status_code=404,
            )
        if not resolved_bucket:
            resolved_bucket = str(todo.bc_project_id)
    if not resolved_bucket:
        return JSONResponse({"ok": False, "error": "bucket_id required for source_kind=bc_list"}, status_code=400)

    # Token resolution: prefer per-user OAuth token, fall back to the shared
    # CB System token from env (current architecture: one principal reads BC
    # for everyone, per Ali's clarification). Without the fallback this route
    # 400s for any user who hasn't set up a personal BC OAuth token.
    token, _ = tokens.get_user_token(user.email)
    if not token:
        token = os.environ.get("BASECAMP_ACCESS_TOKEN", "")
    if not token:
        return JSONResponse({"ok": False, "error": "BC token missing (no user token and no CB System fallback)"}, status_code=500)

    # Pass the user's personal workspace repo so the extracted artifact ALSO
    # lands at .claude/extracted/<type>/<slug>.md in their workspace. Best-
    # effort -- the library push is the source of truth; workspace push is
    # a convenience so their local claude session sees the artifact on next pull.
    workspace_repo = ""
    if getattr(user, "workspace_repo", ""):
        workspace_repo = user.workspace_repo.replace("https://github.com/", "").strip("/")

    from execution.products.library import skill_extractor
    try:
        result = skill_extractor.extract(
            source_kind=source_kind,
            bc_id=bc_id,
            output_type=output_type,
            slug=slug or None,
            commit=True,
            bc_token=token,
            bucket_id=resolved_bucket,
            workspace_repo=workspace_repo,
            created_by=user.email,
            owning_company_id=getattr(user, "company_id", "") or "community",
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": str(e), "error_type": type(e).__name__},
            status_code=500,
        )

    # BC echo: per-task only (we have an exact recording to comment on).
    # List-level extracts skip the echo since there's no canonical single source.
    bc_echo_status = "skipped"
    bc_echo_error = ""
    if result.get("ok") and not skip_bc_echo and source_kind == "bc_ticket" and todo:
        try:
            html = _render_extract_echo_comment(todo, result, user)
            bc_comments.post(
                bucket_id=todo.bc_project_id,
                recording_id=todo.bc_id,
                html_body=html,
                token=token,
            )
            bc_echo_status = "sent"
        except Exception as e:
            bc_echo_status = "failed"
            bc_echo_error = f"{type(e).__name__}: {e}"

    result["bc_echo_status"] = bc_echo_status
    if bc_echo_error:
        result["bc_echo_error"] = bc_echo_error
    return JSONResponse(result)


def _render_extract_echo_comment(todo, extract_result: dict, user) -> str:
    """Render the BC echo comment posted on the source ticket after a successful
    extract. Op 3-style idempotent marker so re-extracting the same slug
    twice doesn't create a duplicate comment thread (BC users see one card).
    """
    output_type = extract_result.get("output_type", "?")
    slug = extract_result.get("slug", "?")
    branch = extract_result.get("branch", "?")
    file_path = extract_result.get("file_path", "?")
    raw_url = extract_result.get("raw_url", "")
    marker = f"step:extracted:{output_type}:{slug}"
    mentions = ""
    # Tag the original assignees so they get an email.
    for aid, aname in zip(todo.assignee_ids, todo.assignee_names):
        if aid:
            mentions += bc_comments.render_assignee_mention(aid, aname) + " "
    actor = user.display_name or user.email
    return (
        f"<!-- {marker} -->\n"
        f'<div style="border-left: 3px solid #2b6cb0; padding: 10px 14px; '
        f'background: #ebf8ff; border-radius: 4px;">'
        f'<div style="font-weight: 700; color: #2c5282;">'
        f"&#128279; Extracted as <code>{output_type}</code> by {actor}"
        f"</div>"
        f'<div style="margin-top: 6px; font-size: 13px;">'
        f"This ticket was converted to a reusable <strong>{output_type}</strong>."
        f"</div>"
        f'<ul style="margin: 6px 0 0 22px; padding: 0; font-size: 13px;">'
        f"<li>Slug: <code>{slug}</code></li>"
        f"<li>Branch: <code>{branch}</code></li>"
        f"<li>File: <code>{file_path}</code></li>"
        f'<li>Raw: <a href="{raw_url}">{raw_url}</a></li>'
        f"</ul>"
        f'<div style="margin-top: 8px; font-size: 12px;">{mentions}</div>'
        f"</div>"
    )
