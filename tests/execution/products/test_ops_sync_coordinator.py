"""Unit tests for execution/products/ops/sync_coordinator.py.

This is the structural fix for audit findings H1 (lock TTL), H2
(check-then-set race), and M1 (scheduler bypasses lock). The tests
must lock in the atomicity guarantee — a refactor that breaks it
silently re-introduces the prior failure modes.
"""
from __future__ import annotations

import threading
import time

import pytest

from execution.products.ops import sync_coordinator


@pytest.fixture
def coord():
    """Fresh coordinator per test with a tight TTL so expiry tests
    don't have to wait 2 minutes."""
    return sync_coordinator.SyncCoordinator(ttl_seconds=0.5)


# ════════════════════════════════════════════════════════════════════
# try_start_sync — atomic slot acquisition (H2 fix)
# ════════════════════════════════════════════════════════════════════


class TestTryStartSync:
    def test_first_caller_acquires_slot(self, coord):
        assert coord.try_start_sync("a@x.com") is True

    def test_second_caller_denied_while_first_holds(self, coord):
        assert coord.try_start_sync("a@x.com") is True
        assert coord.try_start_sync("a@x.com") is False

    def test_different_users_dont_collide(self, coord):
        """Per-user slots — Ali's sync mustn't block Kes's."""
        assert coord.try_start_sync("a@x.com") is True
        assert coord.try_start_sync("b@x.com") is True

    def test_finish_releases_slot(self, coord):
        coord.try_start_sync("a@x.com")
        coord.finish_sync("a@x.com")
        assert coord.try_start_sync("a@x.com") is True

    def test_finish_is_idempotent(self, coord):
        """Safe to call finish even when not holding — supports the
        `finally:` idiom without bookkeeping the True/False return."""
        coord.finish_sync("never-held@x.com")  # no exception
        coord.try_start_sync("a@x.com")
        coord.finish_sync("a@x.com")
        coord.finish_sync("a@x.com")  # double-finish: also fine

    def test_atomic_under_concurrent_callers(self, coord):
        """Spawn 50 threads that all try to claim the same user slot
        simultaneously. EXACTLY ONE must win. If try_start_sync's
        check-then-set is not atomic, multiple winners → audit H2."""
        winners: list[bool] = []
        barrier = threading.Barrier(50)

        def _race():
            barrier.wait()  # release all threads at the same moment
            winners.append(coord.try_start_sync("a@x.com"))

        threads = [threading.Thread(target=_race) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(winners) == 1, f"expected 1 winner, got {sum(winners)}"


# ════════════════════════════════════════════════════════════════════
# TTL — stale-lock auto-clear (H1 fix)
# ════════════════════════════════════════════════════════════════════


class TestStaleSync:
    def test_expired_lock_is_taken_by_next_caller(self, coord):
        """The whole point of H1. Caller A acquires, then crashes
        without releasing. After TTL, caller B should succeed."""
        assert coord.try_start_sync("a@x.com") is True
        # Simulate a crashed worker by waiting past the TTL (0.5s)
        time.sleep(0.6)
        assert coord.try_start_sync("a@x.com") is True

    def test_unexpired_lock_blocks(self, coord):
        """Sanity check that TTL doesn't fire prematurely."""
        coord.try_start_sync("a@x.com")
        time.sleep(0.1)  # well under 0.5s TTL
        assert coord.try_start_sync("a@x.com") is False

    def test_is_sync_in_flight_respects_ttl(self, coord):
        coord.try_start_sync("a@x.com")
        assert coord.is_sync_in_flight("a@x.com") is True
        time.sleep(0.6)
        assert coord.is_sync_in_flight("a@x.com") is False


# ════════════════════════════════════════════════════════════════════
# wait_for_sync — used by Mark Done's _sync_with_budget
# ════════════════════════════════════════════════════════════════════


class TestWaitForSync:
    def test_returns_immediately_when_no_sync(self, coord):
        t0 = time.time()
        assert coord.wait_for_sync("idle@x.com", timeout=5.0) is True
        assert (time.time() - t0) < 0.1

    def test_returns_when_slot_clears(self, coord):
        """A background thread holds the slot for 0.3s; wait_for_sync
        must return True somewhere between then and the 2s timeout."""
        coord.try_start_sync("a@x.com")

        def _release_after_delay():
            time.sleep(0.3)
            coord.finish_sync("a@x.com")

        threading.Thread(target=_release_after_delay, daemon=True).start()
        t0 = time.time()
        result = coord.wait_for_sync("a@x.com", timeout=2.0, poll_interval=0.05)
        elapsed = time.time() - t0
        assert result is True
        assert 0.2 < elapsed < 1.0  # roughly when the helper released

    def test_returns_false_on_timeout(self, coord):
        coord.try_start_sync("a@x.com")
        t0 = time.time()
        result = coord.wait_for_sync("a@x.com", timeout=0.2, poll_interval=0.05)
        elapsed = time.time() - t0
        assert result is False
        assert 0.15 < elapsed < 0.35  # honored timeout, didn't return early

    def test_returns_true_when_ttl_expires_during_wait(self, coord):
        """If the in-flight sync's TTL fires during the wait, we should
        observe the slot as free — H1's recovery surface."""
        coord.try_start_sync("a@x.com")
        # TTL is 0.5s; wait long enough for it to expire
        result = coord.wait_for_sync("a@x.com", timeout=2.0, poll_interval=0.05)
        assert result is True


# ════════════════════════════════════════════════════════════════════
# in_flight_age_seconds — diagnostic for /my-day/_health
# ════════════════════════════════════════════════════════════════════


class TestInFlightAgeSeconds:
    def test_none_when_no_sync(self, coord):
        assert coord.in_flight_age_seconds("idle@x.com") is None

    def test_grows_over_time(self, coord):
        coord.try_start_sync("a@x.com")
        a0 = coord.in_flight_age_seconds("a@x.com")
        assert a0 is not None and a0 < 0.1
        time.sleep(0.2)
        a1 = coord.in_flight_age_seconds("a@x.com")
        assert a1 > a0

    def test_none_after_expiry(self, coord):
        """Diagnostic mirrors is_sync_in_flight's TTL semantics: an
        expired lock reads as 'no sync in flight', not 'sync is 999s old'."""
        coord.try_start_sync("a@x.com")
        time.sleep(0.6)
        assert coord.in_flight_age_seconds("a@x.com") is None


# ════════════════════════════════════════════════════════════════════
# Module-level singleton
# ════════════════════════════════════════════════════════════════════


class TestSingleton:
    def test_get_coordinator_returns_same_instance(self):
        sync_coordinator.reset_coordinator_for_tests()
        c1 = sync_coordinator.get_coordinator()
        c2 = sync_coordinator.get_coordinator()
        assert c1 is c2

    def test_reset_creates_new_instance(self):
        sync_coordinator.reset_coordinator_for_tests()
        c1 = sync_coordinator.get_coordinator()
        sync_coordinator.reset_coordinator_for_tests()
        c2 = sync_coordinator.get_coordinator()
        assert c1 is not c2

    def test_default_ttl_is_safe(self):
        """The module-level default TTL must be >> observed worst-case
        sync wall time (~60s) so a healthy-but-slow sync isn't pre-empted."""
        assert sync_coordinator.LOCK_TTL_SECONDS_DEFAULT >= 90.0
