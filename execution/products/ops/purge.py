"""Periodic stale-row purge for /my-day/ local mirror.

Retires audit M6 (2026-06-09): the walker's freshness gate
(`_todo_is_relevant`) drops todos whose `updated_at` is older than
`OPS_FRESHNESS_DAYS` AND that have no future due date. A task that was
active at last sync, then completed in Basecamp AFTER it aged out of
the freshness window, never re-enters the walker's scope — its local
row stays `status='active'` forever.

This sweep finds those rows directly (status='active' AND
bc_updated_at past the cutoff) and re-fetches them one at a time from
BC. If BC says they're completed, we mirror that locally. If BC says
they're still active, we leave them alone (and they'll be picked up
again on the next sweep). If BC returns 404 (task deleted), we mark
the row archived so it stops cluttering the queue without losing the
audit row.

Bounded by `OPS_PURGE_CAP_PER_USER` (default 50) per run so a user
with a long-tail of stale-active rows can't dominate the per-cron
budget. The unswept tail rolls over to the next sweep.

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

FRESHNESS_DAYS = int(os.environ.get("OPS_FRESHNESS_DAYS", "30"))
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
    """Re-check local active rows that aged out of the sync window.

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
    cutoff = datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)
    todos = store.load_todos(user_id)

    # Find all rows that the sync walker would no longer touch but
    # that the local store still believes are open work for the user.
    stale_active: list[store.OpsTodo] = []
    for t in todos:
        if t.status != "active" or t.is_dismissed:
            continue
        dt = _parse_iso(t.bc_updated_at)
        if dt is None or dt >= cutoff:
            continue
        stale_active.append(t)

    # Oldest first so the most-stale rows clear first if we hit the cap.
    stale_active.sort(key=lambda t: t.bc_updated_at)

    checked = 0
    updated_completed = 0
    archived_missing = 0
    errors = 0

    for t in stale_active[:CAP_PER_USER]:
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
            # BC returned 400/404 — task was deleted. Archive locally
            # so it stops cluttering the queue without losing the row.
            # The user could still re-open via dismiss/undismiss flows
            # if they want, but the queue stops surfacing it.
            store.update_todo(user_id, t.bc_id, status="archived")
            archived_missing += 1
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
    state.last_purge_archived = updated_completed + archived_missing
    store.save_state(state)

    result = {
        "status": overall_status,
        "stale_active_found": len(stale_active),
        "checked": checked,
        "updated_completed": updated_completed,
        "archived_missing": archived_missing,
        "errors": errors,
        "capped": len(stale_active) > CAP_PER_USER,
    }
    logger.info("ops purge: user=%s %s", user_id, result)
    return result
