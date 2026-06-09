"""Single-flight coordinator for per-user Basecamp syncs.

Every sync trigger (manual ↻ Sync button, page-load natural-flow, Mark
Done targeted refresh, 5-min APScheduler cron) funnels through one
SyncCoordinator instance. This retires four audit findings in one
structural change:

  H1 — Lock has no TTL. Hung sync could strand a user permanently.
        Fix: _SyncLock.is_expired() after LOCK_TTL_SECONDS.

  H2 — `if not lock: lock = True` was a check-then-set race; two
        concurrent requests could both kick BC walks.
        Fix: try_start_sync atomic under a single threading.Lock.

  M1 — 5-min scheduler bypassed the per-user lock entirely, so cron
        + user-triggered sync could collide.
        Fix: scheduler also calls into pull_todos_for_user, which
        consults this coordinator. Cron returns "already_running" if
        the user just kicked a manual sync (intended behavior).

  (H3 — store-write race — addressed in execution/products/ops/store.py
        via a separate per-user write lock. The store layer owns its
        own concurrency invariants; this coordinator orchestrates the
        "is a full BC walk in progress" question.)

API surface kept minimal so the four call sites (sync.pull_todos_for_user,
sync.pull_todos_for_project, router._sync_with_budget, scheduler
cron) all use the same primitive.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


# Stale-lock backstop. Real syncs observed at ~30-60s; 120s leaves comfortable
# margin so a healthy-but-slow sync isn't pre-empted, but a SIGKILL'd or
# crashed worker doesn't strand the user for more than 2 minutes.
# Tunable via constant on the class so tests can shorten it.
LOCK_TTL_SECONDS_DEFAULT = 120.0


@dataclass
class _SyncLock:
    """One in-flight sync. `started_at` is wall-clock seconds (time.time())."""
    started_at: float

    def is_expired(self, ttl: float) -> bool:
        return (time.time() - self.started_at) > ttl


class SyncCoordinator:
    """Per-user single-flight coordinator. Thread-safe under concurrent
    callers from the FastAPI thread pool, the scheduler thread, and any
    background sync workers the router spawns."""

    def __init__(self, ttl_seconds: float = LOCK_TTL_SECONDS_DEFAULT):
        self._locks: dict[str, _SyncLock] = {}
        self._guard = threading.Lock()
        self._ttl = ttl_seconds

    # ── slot acquisition ────────────────────────────────────────────

    def try_start_sync(self, user_email: str) -> bool:
        """Atomic check-then-set. Returns True iff THIS caller now owns the
        sync slot for `user_email`. Returns False if another sync is in
        flight and within the TTL.

        Caller contract: on True, you MUST call finish_sync(user_email)
        in a finally block. On False, do not call finish_sync.

        TTL behavior: a lock older than ttl_seconds is treated as
        crashed/abandoned and silently replaced by this caller. This is
        the H1 fix — without it, an OOM-killed sync thread left the lock
        True forever and every subsequent natural-flow sync returned early.
        """
        with self._guard:
            existing = self._locks.get(user_email)
            if existing and not existing.is_expired(self._ttl):
                return False
            self._locks[user_email] = _SyncLock(started_at=time.time())
            return True

    def finish_sync(self, user_email: str) -> None:
        """Release the slot. Idempotent — safe to call from a finally
        block even if try_start_sync returned False (no-op in that case)."""
        with self._guard:
            self._locks.pop(user_email, None)

    # ── status query ────────────────────────────────────────────────

    def is_sync_in_flight(self, user_email: str) -> bool:
        """Non-blocking check. Honors TTL — an expired lock reads as
        not-in-flight. Used by the router's natural-flow sync to decide
        whether to kick a background pull."""
        with self._guard:
            existing = self._locks.get(user_email)
            return bool(existing and not existing.is_expired(self._ttl))

    def in_flight_age_seconds(self, user_email: str) -> float | None:
        """Diagnostic: how long has the in-flight sync been running?
        Returns None when no sync is in flight. Surfaced via
        /my-day/_health for operator visibility."""
        with self._guard:
            existing = self._locks.get(user_email)
            if not existing or existing.is_expired(self._ttl):
                return None
            return time.time() - existing.started_at

    # ── blocking wait ──────────────────────────────────────────────

    def wait_for_sync(self, user_email: str, timeout: float,
                                          poll_interval: float = 0.2) -> bool:
        """Block up to `timeout` seconds for any in-flight sync to clear.
        Returns True if the slot freed, False if timeout hit.

        Used by Mark Done's `_sync_with_budget` so the post-action
        focus task is computed against fresh data — if a background
        sync is already in flight, we wait for it instead of racing it.

        `poll_interval` is exposed for tests; production callers should
        accept the default (0.2s = 5 Hz polling)."""
        deadline = time.time() + timeout
        while self.is_sync_in_flight(user_email):
            if time.time() >= deadline:
                return False
            time.sleep(poll_interval)
        return True


# ── module-level singleton ──────────────────────────────────────────

_coordinator: SyncCoordinator | None = None
_coord_guard = threading.Lock()


def get_coordinator() -> SyncCoordinator:
    """Get the process-wide SyncCoordinator. Lazy-init so importing this
    module doesn't allocate threading primitives at module load time.

    Multi-worker note: each uvicorn worker has its own SyncCoordinator.
    This is acceptable for the current single-worker prod deploy; if we
    scale to multi-worker, the lock state moves to Redis or similar.
    Document the boundary, don't pretend it's solved."""
    global _coordinator
    with _coord_guard:
        if _coordinator is None:
            _coordinator = SyncCoordinator()
        return _coordinator


def reset_coordinator_for_tests() -> None:
    """Tests only: clear the singleton so each test starts with a fresh
    coordinator. Public for explicit test-fixture use; do NOT call from
    production code."""
    global _coordinator
    with _coord_guard:
        _coordinator = None
