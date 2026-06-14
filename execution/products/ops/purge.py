"""Periodic active-row reconciliation for /my-day/ local mirror.

Closes two gaps, both rooted in the same fact: the sync walker is
upsert-only. It walks Basecamp top-down and writes whatever it finds;
it never removes a local row just because BC stopped returning it.
`store.upsert_todos` is explicit: rows present locally but absent in a
fresh walk are kept (no auto-purge). So two classes of row go stale:

  (a) Audit M6 (2026-06-09) — the walker's freshness gate
      (`_todo_is_relevant`) drops todos whose `updated_at` is older
      than `OPS_FRESHNESS_DAYS` with no future due date. A task active
      at last sync, then completed in BC AFTER it aged out, never
      re-enters the walker's scope — its row stays `active` forever.

  (b) Deleted-while-fresh lists — when an operator deletes a todolist
      in BC (e.g. the old "approval queue" list), the walker simply
      stops returning those todos. Nothing reconciles their absence,
      so the rows keep rendering as `active` in the report. The
      original M6 sweep could NOT catch these: it only looked at rows
      already past the freshness cutoff, and a just-deleted list's rows
      are typically still fresh. They sat in a blind spot until they
      eventually aged past 30 days.

The fix (Option 2, 2026-06-10): this sweep now re-checks EVERY active,
non-dismissed local row — not just the stale tail — and re-fetches each
one-at-a-time from BC. If BC says completed, we mirror that. If BC says
still-active, we leave it (next sweep retries). If BC returns 400/404
(task or its list was deleted), we mark the row `archived` so it stops
cluttering the queue without losing the audit row.

Bounded by `OPS_PURGE_CAP_PER_USER` (default 50) per run so a user with
many active rows can't dominate the per-cron budget. Rows are checked
oldest-`bc_updated_at`-first (preserving M6's zombie-clearing priority);
the unswept tail rolls over to the next sweep and converges over
successive runs.

Scheduler integration: invoked once per `OPS_PURGE_INTERVAL_HOURS`
(default 24) per user with a vault token. State.last_purge_at gates
the next run.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from . import store, sync, tokens

logger = logging.getLogger(__name__)

CAP_PER_USER = int(os.environ.get("OPS_PURGE_CAP_PER_USER", "50"))
PURGE_INTERVAL_HOURS = int(os.environ.get("OPS_PURGE_INTERVAL_HOURS", "24"))


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def is_purge_due(user_id: str) -> bool:
    """True if the user's last purge ran >= PURGE_INTERVAL_HOURS ago
    (or has never run). Used by the scheduler to skip users whose
    purge is still fresh."""
    state = store.load_state(user_id)
    last = _parse_iso(state.last_purge_at)
    if last is None:
        return True
    age = datetime.now(timezone.utc) - last
    return age >= timedelta(hours=PURGE_INTERVAL_HOURS)


def purge_stale_active_rows(user_id: str) -> dict:
    """Reconcile every local active row against BC.

    Re-fetches each active, non-dismissed row one-at-a-time and mirrors
    BC's truth: completed -> completed, deleted (400/404) -> archived,
    still-active -> left alone. Catches both M6 zombies (completed after
    aging out) and orphans from a deleted list (kept name for the
    scheduler/job-id contract; scope is broader than "stale" now — see
    module docstring).

    Returns a result dict the scheduler logs and `/my-day/_health`
    surfaces. `state.last_purge_at` is updated regardless of whether
    any rows changed, so `is_purge_due` won't fire again until the
    interval elapses.
    """
    token, _src = tokens.get_user_token(user_id)
    if not token:
        # Mirror sync.py: token-missing is a state-mutating event so
        # the operator can see "purge couldn't run because no token".
        state = store.load_state(user_id)
        state.last_purge_at = sync._now_iso()
        state.last_purge_status = "failed"
        state.last_purge_archived = 0
        store.save_state(state)
        return {"status": "token_missing"}

    state = store.load_state(user_id)
    todos = store.load_todos(user_id)

    # Every active, non-dismissed local row is a reconciliation
    # candidate. We no longer gate on a freshness cutoff: a list deleted
    # in BC while its rows are still "fresh" would otherwise sit in a
    # blind spot (walker stops returning it, old cutoff-gated purge never
    # looked at it) until it aged past 30 days. Dismissed rows are a
    # deliberate operator signal and stay untouched.
    active_rows: list[store.OpsTodo] = [
        t for t in todos if t.status == "active" and not t.is_dismissed
    ]

    # Oldest bc_updated_at first: preserves M6's priority of clearing the
    # most-stale zombies first when we hit the per-user cap. Fresher
    # orphans roll over to the next sweep and converge.
    active_rows.sort(key=lambda t: t.bc_updated_at)

    checked = 0
    updated_completed = 0
    archived_missing = 0
    archived_trashed = 0
    errors = 0

    for t in active_rows[:CAP_PER_USER]:
        try:
            bc_todo = sync._bc_get(
                f"/buckets/{t.bc_project_id}/todos/{t.bc_id}.json",
                token,
            )
            checked += 1
        except Exception as e:  # noqa: BLE001 — per-row resilience
            errors += 1
            sync._record_error(
                user_id,
                f"purge_fetch:{t.bc_id}",
                f"{type(e).__name__}: {str(e)[:200]}",
            )
            continue

        if bc_todo is None:
            # BC returned 400/404 — the task (or the whole todolist it
            # lived in, e.g. a deleted "approval queue") is gone. Archive
            # locally so it stops cluttering the queue without losing the
            # row. The user could still re-open via dismiss/undismiss
            # flows if they want, but the queue stops surfacing it.
            store.update_todo(user_id, t.bc_id, status="archived")
            archived_missing += 1
            continue

        # Basecamp soft-deletes ("trash") and archives DON'T 404 — the todo
        # endpoint still returns a JSON object, just with status != "active"
        # (e.g. "trashed", "archived"). The None check above only catches
        # hard 404s, so without this branch a trashed todo (the operator
        # "deleted" it in BC) survives the sweep and keeps ranking on the
        # human queue. Mirror BC: any non-active status means gone-from-queue
        # -> archive the local row.
        bc_status = bc_todo.get("status")
        if bc_status and bc_status != "active":
            store.update_todo(user_id, t.bc_id, status="archived")
            archived_trashed += 1
            continue

        if bc_todo.get("completed"):
            completion = bc_todo.get("completion") or {}
            creator = completion.get("creator") or {}
            store.update_todo(
                user_id, t.bc_id,
                status="completed",
                completed_at=completion.get("created_at") or "",
                completed_by_id=creator.get("id"),
                completed_by_name=creator.get("name") or "",
            )
            updated_completed += 1
        # else: BC still says active. Leave alone; next sweep retries.

    overall_status = "ok" if errors == 0 else "partial"
    state.last_purge_at = sync._now_iso()
    state.last_purge_status = overall_status
    state.last_purge_archived = updated_completed + archived_missing + archived_trashed
    store.save_state(state)

    result = {
        "status": overall_status,
        "active_found": len(active_rows),
        "checked": checked,
        "updated_completed": updated_completed,
        "archived_missing": archived_missing,
        "archived_trashed": archived_trashed,
        "errors": errors,
        "capped": len(active_rows) > CAP_PER_USER,
    }
    logger.info("ops purge: user=%s %s", user_id, result)
    return result
