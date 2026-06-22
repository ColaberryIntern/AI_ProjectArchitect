"""Unit tests for execution/products/ops/sync.py — the Basecamp pull engine.

Coverage rationale (per 2026-06-09 audit, finding H5):
The classifier, freshness gate, 429 retry, paginator, and the
pull_todos_for_user/_project orchestrators had ZERO test coverage prior
to this file. A regression here silently breaks the My Day queue for
every user — the symptom the 2026-06-04 stale-task audit chased was a
member of this class. These tests lock in current behavior so the
upcoming SyncCoordinator refactor (Phase 2) has a safety net.

No network. urllib.request.urlopen is patched everywhere. No real BC
calls, no sleeping (HTTP_THROTTLE_SECONDS forced to 0).
"""
from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from execution.products.ops import store, sync, sync_coordinator


# ── Shared fixtures ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    """Disable the BC throttle for speed. Real value is 0.22s/call, which
    would add ~10s across this file."""
    monkeypatch.setattr(sync, "HTTP_THROTTLE_SECONDS", 0)


@pytest.fixture(autouse=True)
def _clear_ring_buffer():
    """Each test starts with an empty silent-error ring buffer."""
    sync.clear_recent_errors()
    yield
    sync.clear_recent_errors()


@pytest.fixture(autouse=True)
def _clear_walk_deadline():
    """Reset the thread-local per-walk deadline between tests so a test that
    sets it can't poison a sibling running on the same thread."""
    sync._walk_ctx.deadline = None
    yield
    sync._walk_ctx.deadline = None


@pytest.fixture(autouse=True)
def _fresh_coordinator():
    """Each test starts with a brand-new SyncCoordinator. Without this,
    a test that fails mid-sync leaves a slot locked, and subsequent
    tests see "already_running" instead of running the actual code path."""
    sync_coordinator.reset_coordinator_for_tests()
    yield
    sync_coordinator.reset_coordinator_for_tests()


@pytest.fixture
def isolated_ops_root(tmp_path, monkeypatch):
    """Redirect the file-backed store to tmp_path so tests don't pollute
    output/ops/. Patched in the store module that sync.py imports."""
    monkeypatch.setattr(store, "OPS_ROOT", tmp_path / "ops")
    return tmp_path / "ops"


def _iso(dt: datetime) -> str:
    """Z-suffixed UTC ISO timestamp (what BC returns)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago(days: int) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=days))


def _date_offset(days: int) -> str:
    """YYYY-MM-DD offset from today."""
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _first_page_only(items, params):
    """Mimic BC collection pagination: return ``items`` on page 1 and an empty
    list on every later page, so a `_paginate` loop terminates. The walk
    paginates the todolists AND groups fetches (2026-06-18 fix), so mocks for
    those endpoints must model the same page-exhaustion the todos.json mocks
    already do — otherwise the paginator sees the same non-empty page forever."""
    return items if (params or {}).get("page", 1) == 1 else []


# ════════════════════════════════════════════════════════════════════
# _is_fresh — freshness gate for "activity within OPS_FRESHNESS_DAYS"
# ════════════════════════════════════════════════════════════════════


class TestIsFresh:
    def test_none_returns_true_conservative(self):
        """No timestamp → keep the item. Better to over-include than to
        silently drop a row whose timestamp we can't parse."""
        assert sync._is_fresh(None) is True

    def test_empty_string_returns_true_conservative(self):
        assert sync._is_fresh("") is True

    def test_recent_z_suffix_returns_true(self):
        assert sync._is_fresh(_days_ago(1)) is True

    def test_recent_iso_with_offset_returns_true(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert sync._is_fresh(ts) is True

    def test_naive_timestamp_treated_as_utc(self):
        """BC occasionally returns naive timestamps; we must default them
        to UTC rather than crash or treat as local time."""
        naive = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat()
        assert sync._is_fresh(naive) is True

    def test_old_timestamp_returns_false(self):
        # Older than FRESHNESS_DAYS (default 30) → drop
        assert sync._is_fresh(_days_ago(60)) is False

    def test_malformed_returns_true_conservative(self):
        """Garbage timestamp → keep, don't crash. This is the
        conservative default; flipping it to False would silently drop
        every BC row when timestamp format drifts."""
        assert sync._is_fresh("not-a-timestamp") is True

    def test_boundary_at_freshness_window(self, monkeypatch):
        """A timestamp 1 second inside the window is fresh; 1 second
        outside is not. Documents the inclusive boundary."""
        monkeypatch.setattr(sync, "FRESHNESS_DAYS", 30)
        just_inside = datetime.now(timezone.utc) - timedelta(days=30) + timedelta(seconds=5)
        just_outside = datetime.now(timezone.utc) - timedelta(days=30) - timedelta(seconds=5)
        assert sync._is_fresh(_iso(just_inside)) is True
        assert sync._is_fresh(_iso(just_outside)) is False


# ════════════════════════════════════════════════════════════════════
# _has_future_due — "due date today or later" predicate
# ════════════════════════════════════════════════════════════════════


class TestHasFutureDue:
    def test_no_due_returns_false(self):
        assert sync._has_future_due({"due_on": None}) is False
        assert sync._has_future_due({}) is False

    def test_future_due_returns_true(self):
        assert sync._has_future_due({"due_on": _date_offset(7)}) is True

    def test_today_returns_true(self):
        """Boundary: due TODAY is still considered future. Documents
        that '== today' counts as actionable, not expired."""
        assert sync._has_future_due({"due_on": _date_offset(0)}) is True

    def test_past_returns_false(self):
        assert sync._has_future_due({"due_on": _date_offset(-1)}) is False

    def test_malformed_returns_false(self):
        assert sync._has_future_due({"due_on": "not-a-date"}) is False


# ════════════════════════════════════════════════════════════════════
# _todo_is_relevant — combined freshness gate
# ════════════════════════════════════════════════════════════════════


class TestTodoIsRelevant:
    def test_recent_activity_included(self):
        assert sync._todo_is_relevant({"updated_at": _days_ago(1)}) is True

    def test_old_activity_but_future_due_included(self):
        assert sync._todo_is_relevant({
            "updated_at": _days_ago(90),
            "due_on": _date_offset(5),
        }) is True

    def test_old_activity_past_due_excluded(self):
        assert sync._todo_is_relevant({
            "updated_at": _days_ago(90),
            "due_on": _date_offset(-1),
        }) is False

    def test_old_activity_no_due_excluded(self):
        assert sync._todo_is_relevant({
            "updated_at": _days_ago(90),
            "due_on": None,
        }) is False


# ════════════════════════════════════════════════════════════════════
# _classify_for_user — THE critical 4-tier classifier
# Any regression here silently drops tasks from a user's queue.
# ════════════════════════════════════════════════════════════════════


class TestClassifyForUser:
    BC_USER = 17454835  # arbitrary BC user id for the test "current user"

    def test_user_in_assignees_returns_assigned(self):
        todo = {
            "assignees": [{"id": self.BC_USER}, {"id": 999}],
            "updated_at": _days_ago(1),
        }
        assert sync._classify_for_user(todo, self.BC_USER) == "assigned"

    def test_cb_system_clone_returns_assigned(self):
        """The CB System AI clone (37708014) is the same operator as the
        human. If this literal changes or is removed, every CB-assigned
        task silently disappears from Ali's queue. Lock it in."""
        todo = {
            "assignees": [{"id": 37708014}],
            "updated_at": _days_ago(1),
        }
        assert sync._classify_for_user(todo, self.BC_USER) == "assigned"

    def test_other_user_with_future_due_returns_due(self):
        """Someone else assigned, but it has a future due date → user
        is 'watching for the due date' on a project they follow."""
        todo = {
            "assignees": [{"id": 999}],
            "updated_at": _days_ago(1),
            "due_on": _date_offset(5),
        }
        assert sync._classify_for_user(todo, self.BC_USER) == "due"

    def test_no_assignees_recent_activity_returns_unassigned(self):
        """No one assigned + recent activity → 'someone needs to claim'."""
        todo = {
            "assignees": [],
            "updated_at": _days_ago(1),
        }
        assert sync._classify_for_user(todo, self.BC_USER) == "unassigned"

    def test_other_user_recent_activity_no_due_returns_watching(self):
        """Other user assigned, no future due, but project is active.
        Per the docstring: 'everything CB has access to with activity in
        the past 30 days'. Without this tier, Ram/Luda/etc. tasks vanish
        from the all-projects view."""
        todo = {
            "assignees": [{"id": 999}],
            "updated_at": _days_ago(1),
        }
        assert sync._classify_for_user(todo, self.BC_USER) == "watching"

    def test_other_user_no_future_no_activity_returns_none(self):
        """No claim path on this user → excluded from queue."""
        todo = {
            "assignees": [{"id": 999}],
            "updated_at": _days_ago(90),
        }
        assert sync._classify_for_user(todo, self.BC_USER) is None

    def test_no_assignees_old_activity_returns_none(self):
        """Stale unassigned task → excluded (no one to claim it now)."""
        todo = {
            "assignees": [],
            "updated_at": _days_ago(90),
        }
        assert sync._classify_for_user(todo, self.BC_USER) is None

    def test_assigned_takes_precedence_over_due(self):
        """User both assigned AND there's a future due — must classify
        as 'assigned' so the queue counts the user's plate correctly."""
        todo = {
            "assignees": [{"id": self.BC_USER}],
            "updated_at": _days_ago(1),
            "due_on": _date_offset(5),
        }
        assert sync._classify_for_user(todo, self.BC_USER) == "assigned"

    def test_no_assignees_key_at_all(self):
        """BC sometimes omits the key entirely; treat as empty list."""
        todo = {"updated_at": _days_ago(1)}
        assert sync._classify_for_user(todo, self.BC_USER) == "unassigned"


# ════════════════════════════════════════════════════════════════════
# _bc_get — HTTP wrapper with 429 retry. Mock urllib.request.urlopen.
# ════════════════════════════════════════════════════════════════════


def _urlopen_cm(body: bytes, status: int = 200):
    """Build a context-manager mock that mimics urllib.request.urlopen."""
    resp = MagicMock()
    resp.read = MagicMock(return_value=body)
    resp.status = status
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=resp)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _http_error(code: int, retry_after: str | None = None, body: bytes = b""):
    """Build a urllib HTTPError ready to be raised by a urlopen mock."""
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(
        url="https://3.basecampapi.com/test",
        code=code,
        msg=str(code),
        hdrs=headers,
        fp=io.BytesIO(body),
    )


class TestBcGet:
    def test_200_returns_parsed_json(self, monkeypatch):
        monkeypatch.setattr(
            sync.urllib.request, "urlopen",
            MagicMock(return_value=_urlopen_cm(b'{"k": "v"}')),
        )
        assert sync._bc_get("/projects.json", "tok") == {"k": "v"}

    def test_400_returns_none(self, monkeypatch):
        """BC returns 400 for some 'no items' cases — treated as empty."""
        monkeypatch.setattr(
            sync.urllib.request, "urlopen",
            MagicMock(side_effect=_http_error(400)),
        )
        assert sync._bc_get("/projects.json", "tok") is None

    def test_404_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            sync.urllib.request, "urlopen",
            MagicMock(side_effect=_http_error(404)),
        )
        assert sync._bc_get("/buckets/1/todos/9.json", "tok") is None

    def test_500_transient_retried_then_raises(self, monkeypatch):
        """500 is transient: retried TRANSIENT_RETRIES times with backoff,
        and only if EVERY attempt fails does it propagate so per-project
        resilience records it. Before the 2026-06-18 522 fix the first 5xx
        raised immediately and aborted the whole project walk."""
        calls = {"n": 0}
        def _se(*a, **kw):
            calls["n"] += 1
            raise _http_error(500)
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_se))
        sleeps: list[float] = []
        monkeypatch.setattr(sync.time, "sleep", lambda s: sleeps.append(s))
        with pytest.raises(urllib.error.HTTPError):
            sync._bc_get("/projects.json", "tok")
        assert calls["n"] == sync.TRANSIENT_RETRIES + 1  # initial try + retries
        assert len(sleeps) == sync.TRANSIENT_RETRIES     # one backoff per retry

    def test_522_retried_then_succeeds(self, monkeypatch):
        """Cloudflare 522 (BC origin timeout) is transient: retry, then take
        the success. THE 2026-06-18 stale-list root cause — a 522 mid-walk
        used to abort the entire project walk, freezing My Day on the
        pre-restructure todolists. With retry, the walk pushes through."""
        responses = [_http_error(522), _http_error(522), _urlopen_cm(b'{"ok": 1}')]
        def _se(*a, **kw):
            r = responses.pop(0)
            if isinstance(r, urllib.error.HTTPError):
                raise r
            return r
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_se))
        sleeps: list[float] = []
        monkeypatch.setattr(sync.time, "sleep", lambda s: sleeps.append(s))
        assert sync._bc_get("/x", "tok") == {"ok": 1}
        assert len(sleeps) == 2  # two 522s → two backoff sleeps, then success

    def test_every_transient_code_is_retried(self, monkeypatch):
        """All of TRANSIENT_HTTP_CODES (not just 522) recover on retry."""
        monkeypatch.setattr(sync.time, "sleep", lambda s: None)
        for code in sorted(sync.TRANSIENT_HTTP_CODES):
            responses = [_http_error(code), _urlopen_cm(b'{"ok": 1}')]
            def _se(*a, _r=responses, **kw):
                r = _r.pop(0)
                if isinstance(r, urllib.error.HTTPError):
                    raise r
                return r
            monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_se))
            assert sync._bc_get("/x", "tok") == {"ok": 1}, f"code {code} not retried"

    def test_connection_error_retried_then_succeeds(self, monkeypatch):
        """A bare transport failure (no HTTP status) is transient too — a
        client-side timeout/reset must not kill the walk on the first blip."""
        responses = [urllib.error.URLError("connection reset"), _urlopen_cm(b'{"ok": 1}')]
        def _se(*a, **kw):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_se))
        monkeypatch.setattr(sync.time, "sleep", lambda s: None)
        assert sync._bc_get("/x", "tok") == {"ok": 1}

    def test_403_is_not_retried(self, monkeypatch):
        """403 is NOT transient — it's a membership gap with an actionable
        Basecamp-side fix. It must propagate on the FIRST call, no backoff,
        so forbidden_buckets / the identity guard fire correctly."""
        calls = {"n": 0}
        def _se(*a, **kw):
            calls["n"] += 1
            raise _http_error(403)
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_se))
        sleeps: list[float] = []
        monkeypatch.setattr(sync.time, "sleep", lambda s: sleeps.append(s))
        with pytest.raises(urllib.error.HTTPError):
            sync._bc_get("/x", "tok")
        assert calls["n"] == 1     # no retry
        assert sleeps == []        # no backoff

    def test_transient_not_retried_past_walk_deadline(self, monkeypatch):
        """Inside a _walk_deadline that has already elapsed, a transient 522 is
        NOT retried — it fails fast so a project walk can't run unbounded during
        a SUSTAINED BC outage (2026-06-18 hardening). Without the deadline the
        same 522 WOULD retry (see test_522_retried_then_succeeds)."""
        calls = {"n": 0}
        def _se(*a, **kw):
            calls["n"] += 1
            raise _http_error(522)
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_se))
        sleeps: list[float] = []
        monkeypatch.setattr(sync.time, "sleep", lambda s: sleeps.append(s))
        sync._walk_ctx.deadline = sync.time.time() - 1  # already passed
        with pytest.raises(urllib.error.HTTPError):
            sync._bc_get("/x", "tok")
        assert calls["n"] == 1     # failed fast, no retry
        assert sleeps == []        # no backoff sleep past the deadline


class TestWalkDeadline:
    """The thread-local per-walk deadline that bounds how long transient
    retries can stretch one project's walk (2026-06-18 hardening)."""

    def test_default_no_deadline(self):
        sync._walk_ctx.deadline = None
        assert sync._walk_deadline_passed() is False

    def test_future_deadline_not_passed_and_restores(self):
        sync._walk_ctx.deadline = None
        with sync._walk_deadline(1000):
            assert sync._walk_deadline_passed() is False  # 1000s in the future
        # restored to the prior (None) value on exit
        assert getattr(sync._walk_ctx, "deadline", None) is None

    def test_falsy_seconds_means_unbounded(self):
        with sync._walk_deadline(0):
            assert sync._walk_deadline_passed() is False

    def test_nested_deadlines_restore_outer(self):
        with sync._walk_deadline(1000):
            outer = sync._walk_ctx.deadline
            with sync._walk_deadline(2000):
                assert sync._walk_ctx.deadline != outer
            assert sync._walk_ctx.deadline == outer  # inner restored the outer


class TestSyncBudgetTtlInvariant:
    def test_lock_ttl_exceeds_project_walk_ceiling(self):
        """The coordinator lock TTL MUST exceed a single project's bounded
        walk ceiling, or a long-but-healthy retry-extended walk gets
        pre-empted at the TTL and a parallel walk starts. Ties the two
        constants together so a future tweak to one can't silently break it."""
        assert (sync_coordinator.LOCK_TTL_SECONDS_DEFAULT
                > sync.PROJECT_SYNC_BUDGET_SECONDS)

    def test_429_honors_retry_after_and_retries_once(self, monkeypatch):
        """First call: 429 with Retry-After: 1. Second call: 200. The
        function must sleep then retry, returning the second response."""
        responses = [_http_error(429, retry_after="1"), _urlopen_cm(b'{"k": "v"}')]
        def _side_effect(*a, **kw):
            r = responses.pop(0)
            if isinstance(r, urllib.error.HTTPError):
                raise r
            return r
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_side_effect))
        sleeps: list[float] = []
        monkeypatch.setattr(sync.time, "sleep", lambda s: sleeps.append(s))
        result = sync._bc_get("/projects.json", "tok")
        assert result == {"k": "v"}
        assert 1 in sleeps  # honored Retry-After=1

    def test_429_caps_retry_after_at_max(self, monkeypatch):
        """If BC sends Retry-After: 9999, we must clamp to MAX_RETRY_AFTER
        so a hostile or buggy header can't strand the sync for hours."""
        monkeypatch.setattr(sync, "MAX_RETRY_AFTER", 30)
        responses = [_http_error(429, retry_after="9999"), _urlopen_cm(b'{"k": "v"}')]
        def _side_effect(*a, **kw):
            r = responses.pop(0)
            if isinstance(r, urllib.error.HTTPError):
                raise r
            return r
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_side_effect))
        sleeps: list[float] = []
        monkeypatch.setattr(sync.time, "sleep", lambda s: sleeps.append(s))
        sync._bc_get("/projects.json", "tok")
        assert max(sleeps) == 30  # clamped

    def test_429_default_when_retry_after_missing(self, monkeypatch):
        """No Retry-After header → fall back to 5s (not infinite, not 0)."""
        responses = [_http_error(429), _urlopen_cm(b'{"k": "v"}')]
        def _side_effect(*a, **kw):
            r = responses.pop(0)
            if isinstance(r, urllib.error.HTTPError):
                raise r
            return r
        monkeypatch.setattr(sync.urllib.request, "urlopen", MagicMock(side_effect=_side_effect))
        sleeps: list[float] = []
        monkeypatch.setattr(sync.time, "sleep", lambda s: sleeps.append(s))
        sync._bc_get("/projects.json", "tok")
        assert 5 in sleeps

    def test_429_retry_exhausted_raises(self, monkeypatch):
        """Two consecutive 429s → second one bubbles up (only 1 retry).
        Prevents infinite retry loops eating the sync thread."""
        monkeypatch.setattr(
            sync.urllib.request, "urlopen",
            MagicMock(side_effect=_http_error(429, retry_after="1")),
        )
        monkeypatch.setattr(sync.time, "sleep", lambda s: None)
        with pytest.raises(urllib.error.HTTPError):
            sync._bc_get("/projects.json", "tok")

    def test_query_params_encoded_into_url(self, monkeypatch):
        captured: dict = {}
        def _capture(req, timeout=None):
            captured["url"] = req.full_url
            return _urlopen_cm(b"[]")
        monkeypatch.setattr(sync.urllib.request, "urlopen", _capture)
        sync._bc_get("/projects.json", "tok", params={"page": 2, "status": "active"})
        assert "page=2" in captured["url"]
        assert "status=active" in captured["url"]


class TestWalkHeartbeat:
    """_walk_project_todos pulses the heartbeat once per todolist so a long
    walk keeps its coordinator slot alive (2026-06-22 mega-project hardening)."""

    def test_heartbeat_called_once_per_todolist(self, monkeypatch):
        BC_USER = 17454835
        def _bc(path, token, params=None, _retry=1, _transient=None):
            page = (params or {}).get("page", 1)
            if path == "/projects/101.json":
                return {"id": 101, "name": "P",
                        "dock": [{"name": "todoset", "id": 555}]}
            if path == "/buckets/101/todosets/555/todolists.json":
                return ([{"id": 7001, "name": "A"}, {"id": 7002, "name": "B"}]
                        if page == 1 else [])
            if path in ("/buckets/101/todolists/7001/todos.json",
                        "/buckets/101/todolists/7002/todos.json",
                        "/buckets/101/todolists/7001/groups.json",
                        "/buckets/101/todolists/7002/groups.json"):
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)
        beats: list[int] = []
        sync._walk_project_todos({"id": 101, "name": "P"}, "tok", BC_USER,
                                 heartbeat=lambda: beats.append(1))
        assert len(beats) == 2  # one per todolist

    def test_heartbeat_optional(self, monkeypatch):
        """heartbeat=None (the default / legacy call shape) must still work."""
        def _bc(path, token, params=None, _retry=1, _transient=None):
            if path == "/projects/101.json":
                return {"id": 101, "dock": [{"name": "todoset", "id": 555}]}
            if path == "/buckets/101/todosets/555/todolists.json":
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)
        out, seen = sync._walk_project_todos({"id": 101}, "tok", 1)
        assert out == [] and seen == set()


# ════════════════════════════════════════════════════════════════════
# _paginate — yield pages until empty or max_pages
# ════════════════════════════════════════════════════════════════════


class TestPaginate:
    def test_stops_on_empty_response(self, monkeypatch):
        pages = [[{"id": 1}, {"id": 2}], []]
        monkeypatch.setattr(sync, "_bc_get", MagicMock(side_effect=pages))
        items = list(sync._paginate("/x", "tok"))
        assert [i["id"] for i in items] == [1, 2]

    def test_stops_on_none(self, monkeypatch):
        """_bc_get returns None on 400/404 — paginator must stop, not
        crash trying to `yield from None`."""
        pages = [[{"id": 1}], None]
        monkeypatch.setattr(sync, "_bc_get", MagicMock(side_effect=pages))
        items = list(sync._paginate("/x", "tok"))
        assert [i["id"] for i in items] == [1]

    def test_respects_max_pages(self, monkeypatch):
        """Even with always-full pages, stop at max_pages to prevent
        runaway pulls if BC's pagination header lies."""
        infinite = MagicMock(return_value=[{"id": 99}])
        monkeypatch.setattr(sync, "_bc_get", infinite)
        items = list(sync._paginate("/x", "tok", max_pages=3))
        assert len(items) == 3
        assert infinite.call_count == 3

    def test_merges_base_params_with_page(self, monkeypatch):
        """Base params (e.g. {'completed': 'true'}) must be sent on
        every page request, not just the first."""
        calls: list[dict] = []
        def _capture(path, token, params):
            calls.append(dict(params))
            return []
        monkeypatch.setattr(sync, "_bc_get", _capture)
        list(sync._paginate("/x", "tok", max_pages=2, params={"completed": "true"}))
        assert calls[0] == {"completed": "true", "page": 1}


# ════════════════════════════════════════════════════════════════════
# pull_todos_for_user — full sync orchestration
# ════════════════════════════════════════════════════════════════════


def _project_dict(bid: int, name: str = "Test Project", ts_id: int = 555):
    """Build the /projects/{id}.json shape, with the todoset in the dock."""
    return {
        "id": bid,
        "name": name,
        "description": "",
        "updated_at": _days_ago(1),
        "dock": [{"name": "todoset", "id": ts_id}],
    }


def _todo_dict(bc_id: int, *, assignees=(17454835,), title="Task",
                completed=False, due_on=None, updated_at=None):
    return {
        "id": bc_id,
        "title": title,
        "description": "",
        "completed": completed,
        "due_on": due_on,
        "assignees": [{"id": a, "name": f"User {a}"} for a in assignees],
        "updated_at": updated_at or _days_ago(1),
        "created_at": _days_ago(5),
        "app_url": f"https://3.basecamp.com/3945211/todos/{bc_id}",
    }


class TestPullTodosForUser:
    def test_missing_token_returns_error_writes_state(self, monkeypatch, isolated_ops_root):
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=(None, "missing")))
        result = sync.pull_todos_for_user("ali@colaberry.com")
        assert result["status"] == "token_missing"
        # State persisted so /my-day/_health can show the failure
        state = store.load_state("ali@colaberry.com")
        assert state.last_sync_status == "failed"
        assert state.last_sync_error == "token_missing"
        assert state.last_sync_at  # timestamp written

    def test_missing_bc_user_id_returns_error_writes_state(self, monkeypatch, isolated_ops_root):
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=None))
        result = sync.pull_todos_for_user("ali@colaberry.com")
        assert result["status"] == "bc_user_id_missing"
        state = store.load_state("ali@colaberry.com")
        assert state.last_sync_status == "failed"

    def test_happy_path_pulls_assigned_todo_and_upserts(self, monkeypatch, isolated_ops_root):
        """One project, one assigned todo → upserted, state=ok, counts right."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        # No extra buckets on the tenancy record.
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101)]),
        )
        # Dispatch table for the per-project walker.
        def _bc(path, token, params=None, _retry=1):
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Inbox"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                # First page returns one assigned todo; subsequent pages empty
                if params and params.get("page", 1) > 1:
                    return []
                if params and params.get("completed") == "true":
                    return []
                return [_todo_dict(9001, assignees=(BC_USER,), title="Press Mike")]
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "ok"
        assert result["todos_assigned_to_user"] == 1
        assert result["todos_created"] == 1
        # Store has the row
        todos = store.load_todos("ali@colaberry.com")
        assert len(todos) == 1
        assert todos[0].title == "Press Mike"
        assert todos[0].inclusion_reason == "assigned"
        # State updated
        state = store.load_state("ali@colaberry.com")
        assert state.last_sync_status == "ok"
        assert state.todos_synced == 1
        assert state.projects_synced == 1

    def test_walks_todos_inside_todo_groups(self, monkeypatch, isolated_ops_root):
        """Todos filed under a BC todo GROUP (a "Week 01" style sub-section
        of a list) must be pulled, not just the list's top-level todos.

        Regression guard for the 2026-06-17 Swati incident: her Curriculum
        list had ZERO top-level todos and 12 week-groups holding 48 assigned
        tasks; the old walk fetched only the empty list and dropped all 48.
        The grouped todos must land, attributed to a "<list>: <group>" name."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101)]),
        )

        def _bc(path, token, params=None, _retry=1):
            completed = bool(params and params.get("completed") == "true")
            page = (params or {}).get("page", 1)
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Curriculum"}], params)
            # The list's own top level is empty — every todo lives in a group.
            if path == "/buckets/101/todolists/7001/todos.json":
                return []
            if path == "/buckets/101/todolists/7001/groups.json":
                return _first_page_only(
                    [{"id": 8001, "name": "Week 01"},
                     {"id": 8002, "name": "Week 02"}], params)
            if path == "/buckets/101/todolists/8001/todos.json":
                if completed or page > 1:
                    return []
                return [_todo_dict(9101, assignees=(BC_USER,), title="Lab spec — W1")]
            if path == "/buckets/101/todolists/8002/todos.json":
                if completed or page > 1:
                    return []
                return [_todo_dict(9201, assignees=(BC_USER,), title="Lab spec — W2")]
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "ok"
        assert result["todos_assigned_to_user"] == 2
        todos = {t.title: t for t in store.load_todos("ali@colaberry.com")}
        assert set(todos) == {"Lab spec — W1", "Lab spec — W2"}
        # Grouped todos are attributed to "<list>: <group>" and the group id.
        assert todos["Lab spec — W1"].bc_todolist_name == "Curriculum: Week 01"
        assert todos["Lab spec — W1"].bc_todolist_id == 8001
        assert todos["Lab spec — W2"].bc_todolist_name == "Curriculum: Week 02"

    def test_list_with_no_groups_endpoint_still_works(self, monkeypatch, isolated_ops_root):
        """groups.json returning None/[] (lists without groups, or older BC
        accounts) must not break the walk — top-level todos still land."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101)]),
        )

        def _bc(path, token, params=None, _retry=1):
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Inbox"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                if params and (params.get("completed") == "true" or params.get("page", 1) > 1):
                    return []
                return [_todo_dict(9001, assignees=(BC_USER,), title="Top task")]
            if path == "/buckets/101/todolists/7001/groups.json":
                return None  # no groups
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")
        assert result["status"] == "ok"
        assert result["todos_assigned_to_user"] == 1
        assert store.load_todos("ali@colaberry.com")[0].title == "Top task"

    def test_paginates_todolists_so_new_lists_on_page_two_are_walked(
        self, monkeypatch, isolated_ops_root,
    ):
        """A project's todolists.json is paginated; new lists land on later
        pages (BC appends them in position order). The walk MUST fetch every
        page, or a freshly-created list's tasks are invisible while the old
        lists keep rendering.

        Regression guard for the 2026-06-18 incident: Ali created a batch of
        new lists + tasks in project 47126345 and My Day showed only the old
        tasks. Root cause: the todoset → todolists fetch was a single-page
        `_bc_get`, so any list past page 1 was never walked. The todos and
        projects fetches paginate; the todolists fetch did not."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101)]),
        )

        def _bc(path, token, params=None, _retry=1):
            page = (params or {}).get("page", 1)
            completed = bool(params and params.get("completed") == "true")
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                # Page 1 = the old lists; page 2 = the just-created list.
                if page == 1:
                    return [{"id": 7001, "name": "Phase 1 — old"}]
                if page == 2:
                    return [{"id": 7002, "name": "Phase 9 — NEW"}]
                return []
            if path == "/buckets/101/todolists/7001/todos.json":
                if completed or page > 1:
                    return []
                return [_todo_dict(9001, assignees=(BC_USER,), title="Old task")]
            if path == "/buckets/101/todolists/7002/todos.json":
                if completed or page > 1:
                    return []
                return [_todo_dict(9002, assignees=(BC_USER,), title="New task")]
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "ok"
        titles = {t.title for t in store.load_todos("ali@colaberry.com")}
        # The new list's task must be present — this is the whole bug.
        assert titles == {"Old task", "New task"}

    def test_paginates_groups_within_a_list(self, monkeypatch, isolated_ops_root):
        """A list's groups.json is paginated too. A group past page 1 (a
        newly-added "Week 13" under a list already holding 12 week-groups)
        must still be walked, or its tasks are invisible — the grouped-todo
        analogue of the todolists-pagination bug."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101)]),
        )

        def _bc(path, token, params=None, _retry=1):
            page = (params or {}).get("page", 1)
            completed = bool(params and params.get("completed") == "true")
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return [{"id": 7001, "name": "Curriculum"}] if page == 1 else []
            if path == "/buckets/101/todolists/7001/todos.json":
                return []  # empty top level; all todos live in groups
            if path == "/buckets/101/todolists/7001/groups.json":
                if page == 1:
                    return [{"id": 8001, "name": "Week 01"}]
                if page == 2:
                    return [{"id": 8013, "name": "Week 13 — NEW"}]
                return []
            if path == "/buckets/101/todolists/8001/todos.json":
                if completed or page > 1:
                    return []
                return [_todo_dict(9101, assignees=(BC_USER,), title="W1 task")]
            if path == "/buckets/101/todolists/8013/todos.json":
                if completed or page > 1:
                    return []
                return [_todo_dict(9113, assignees=(BC_USER,), title="W13 task")]
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "ok"
        titles = {t.title for t in store.load_todos("ali@colaberry.com")}
        assert titles == {"W1 task", "W13 task"}

    def test_per_project_exception_yields_partial_and_records_error(
        self, monkeypatch, isolated_ops_root,
    ):
        """A BC failure in one project must NOT abort the whole sync.
        The other projects still complete; state goes 'partial'; the
        silent-error ring buffer captures the bad bucket. This is the
        exact resilience pattern the 2026-06-04 audit codified."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101, "Good"), _project_dict(202, "Bad")]),
        )
        def _bc(path, token, params=None, _retry=1):
            if path == "/projects/101.json":
                return _project_dict(101, "Good")
            if path == "/projects/202.json":
                # Simulate BC blowing up only on the bad project
                raise urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b""))
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Inbox"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "partial"
        assert result["errors"]  # error captured in result
        # Silent-error ring buffer has the bad bucket entry
        errs = sync.recent_errors()
        assert any("202" in e["detail"] for e in errs)
        # State reflects partial
        state = store.load_state("ali@colaberry.com")
        assert state.last_sync_status == "partial"
        assert state.last_sync_error  # non-empty

    def test_403_forbidden_bucket_is_actionable_not_generic(
        self, monkeypatch, isolated_ops_root,
    ):
        """A 403 on a project walk is a MEMBERSHIP gap (the AI-clone
        identity the token authenticates as isn't a member of that
        project), not a transient failure. It must:
          - still mark the sync 'partial' (a real, visible problem — we
            do NOT silently swallow it into stale data),
          - be tracked in result['forbidden_buckets'] distinctly,
          - record under the 'project_forbidden' ring-buffer kind, and
          - surface an ACTIONABLE message (names the fix: add the BC
            identity to the project) instead of the useless bare
            'HTTP Error 403: Forbidden' the old generic branch produced.
        Regression guard for the 2026-06-10 'fix my health status' triage:
        Ali's main bucket 47502609 was 403ing and the banner gave no
        hint that the remedy was a Basecamp People→Add grant."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[
                _project_dict(101, "Good"),
                _project_dict(47502609, "AI Systems Architect Acc"),
            ]),
        )
        def _bc(path, token, params=None, _retry=1):
            if path == "/projects/101.json":
                return _project_dict(101, "Good")
            if path == "/projects/47502609.json":
                # The clone can list this project but not read it.
                raise urllib.error.HTTPError(
                    "u", 403, "Forbidden", {}, io.BytesIO(b""),
                )
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Inbox"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        # Membership gap stays visible — partial, not masked.
        assert result["status"] == "partial"
        # Tracked distinctly from generic walk errors.
        assert result["forbidden_buckets"] == [47502609]
        # Recorded under the dedicated kind, with actionable copy.
        errs = sync.recent_errors()
        forbidden = [e for e in errs if e["kind"] == "project_forbidden"]
        assert len(forbidden) == 1
        detail = forbidden[0]["detail"]
        assert "403 Forbidden" in detail
        assert "not a member" in detail
        assert "People" in detail  # names the BC remediation
        assert "47502609" in detail
        # The good project still synced (per-project resilience intact).
        assert result["projects_walked"] == 1
        # The banner string leads with the actionable 403 message.
        state = store.load_state("ali@colaberry.com")
        assert "not a member" in state.last_sync_error

    def test_widespread_403_with_wrong_account_flags_connection_suspect(
        self, monkeypatch, isolated_ops_root,
    ):
        """Self-annealing guard: when the token's ACCOUNT-scoped person id
        (/my/profile.json) differs from the id the classifier expects AND
        projects are 403ing, the connection is bound to the wrong Basecamp
        account. The sync collapses the pile of per-bucket 403s into one
        root-cause line naming both ids + the reconnect, and flags
        result['connection_identity_suspect']."""
        CLASSIFIER_ID = 17454835       # the account person id tasks live under
        TOKEN_PERSON_ID = 99999        # a genuinely different account person
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id",
                            MagicMock(return_value=CLASSIFIER_ID))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(i, f"P{i}") for i in (101, 202, 303)]),
        )
        def _bc(path, token, params=None, _retry=1):
            if path == "/my/profile.json":
                return {"id": TOKEN_PERSON_ID, "email_address": "ali@colaberry.com"}
            for pid in (101, 202, 303):
                if path == f"/projects/{pid}.json":
                    raise urllib.error.HTTPError(
                        "u", 403, "Forbidden", {}, io.BytesIO(b""))
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "partial"
        assert sorted(result["forbidden_buckets"]) == [101, 202, 303]
        assert result["connection_identity_suspect"] is True
        # The banner leads with the root-cause line naming both ids + fix.
        state = store.load_state("ali@colaberry.com")
        assert str(TOKEN_PERSON_ID) in state.last_sync_error
        assert str(CLASSIFIER_ID) in state.last_sync_error
        assert "/profile/connect-basecamp" in state.last_sync_error
        errs = sync.recent_errors()
        assert any(e["kind"] == "connection_identity_suspect" for e in errs)

    def test_same_person_two_id_namespaces_not_flagged(
        self, monkeypatch, isolated_ops_root,
    ):
        """Regression guard for the 2026-06-10 false positive. A single
        human has a Launchpad identity id AND a per-account person id that
        differ (e.g. Launchpad 16988292 == account-person 17454835). The
        guard must compare the ACCOUNT person id (/my/profile.json) to the
        classifier — when they match it is the SAME person, so forbidden
        buckets are genuine non-memberships, NOT a wrong-account binding.
        suspect must stay False even with 403s present."""
        SAME_ID = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id",
                            MagicMock(return_value=SAME_ID))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101, "P101"), _project_dict(202, "P202")]),
        )
        def _bc(path, token, params=None, _retry=1):
            if path == "/my/profile.json":
                # Account-scoped id matches the classifier — same human,
                # even though the Launchpad id (not consulted here) differs.
                return {"id": SAME_ID, "email_address": "ali@colaberry.com"}
            for pid in (101, 202):
                if path == f"/projects/{pid}.json":
                    raise urllib.error.HTTPError(
                        "u", 403, "Forbidden", {}, io.BytesIO(b""))
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")
        # 403s still mark partial (genuine non-memberships), but NOT suspect.
        assert result["status"] == "partial"
        assert len(result["forbidden_buckets"]) == 2
        assert result["connection_identity_suspect"] is False
        errs = sync.recent_errors()
        assert not any(e["kind"] == "connection_identity_suspect" for e in errs)

    def test_budget_exceeded_stops_walk_and_records_partial(
        self, monkeypatch, isolated_ops_root,
    ):
        """L6 (2026-06-09 audit): when total wall time exceeds the budget,
        the walker stops gracefully, records 'budget_exceeded' in the
        ring buffer, returns partial status with the unwalked count.
        Without this, a BC outage could keep the sync thread alive for
        nearly an hour (HTTP_TIMEOUT × pages × projects)."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        # Force a tiny budget so we exceed it inside the test.
        monkeypatch.setattr(sync, "SYNC_BUDGET_SECONDS", 0.05)
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(i) for i in (101, 202, 303)]),
        )
        # Make the first project's walk take >0.05s so the second
        # project's pre-walk check trips the budget.
        import time as _time
        def _bc(path, *a, **kw):
            if path == "/projects/101.json":
                _time.sleep(0.1)
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "partial"
        assert result["budget_exceeded"] is True
        assert result["projects_walked"] == 1  # only project 101 got in
        # The ring buffer captured it
        errs = sync.recent_errors()
        assert any(e["kind"] == "budget_exceeded" for e in errs)
        any_match = next(e for e in errs if e["kind"] == "budget_exceeded")
        assert "2/3 projects deferred" in any_match["detail"]
        # Cursor advanced to the one project we did walk, so the next run
        # resumes past it instead of re-walking the head.
        assert store.load_state("ali@colaberry.com").last_walked_bc_id == 101

    def test_budget_under_limit_is_ok(self, monkeypatch, isolated_ops_root):
        """Inverse of the budget test: when the sync finishes within
        budget, status is 'ok' and budget_exceeded is False. Verifies the
        budget check doesn't fire spuriously on fast syncs."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "SYNC_BUDGET_SECONDS", 60.0)
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(101)]),
        )
        def _bc(path, *a, **kw):
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user("ali@colaberry.com")
        assert result["status"] == "ok"
        assert result["budget_exceeded"] is False
        assert result["projects_walked"] == 1

    def test_resume_cursor_advances_to_tail_on_full_walk(self, monkeypatch, isolated_ops_root):
        """Round-robin cursor records the last project walked. On a full
        walk (no budget break) that's the tail project."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "SYNC_BUDGET_SECONDS", 60.0)
        monkeypatch.setattr(sync, "discover_projects", MagicMock(
            return_value=[_project_dict(101), _project_dict(202), _project_dict(303)]))

        def _bc(path, token=None, params=None, _retry=1):
            for pid in (101, 202, 303):
                if path == f"/projects/{pid}.json":
                    return _project_dict(pid)
                if path == f"/buckets/{pid}/todosets/555/todolists.json":
                    return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        sync.pull_todos_for_user("ali@colaberry.com")
        assert store.load_state("ali@colaberry.com").last_walked_bc_id == 303

    def test_resume_cursor_rotates_walk_order(self, monkeypatch, isolated_ops_root):
        """With a saved cursor, the next run walks starting right AFTER it
        (round-robin) rather than from the head."""
        BC_USER = 17454835
        st = store.load_state("ali@colaberry.com")
        st.last_walked_bc_id = 101
        store.save_state(st)
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "SYNC_BUDGET_SECONDS", 60.0)
        monkeypatch.setattr(sync, "discover_projects", MagicMock(
            return_value=[_project_dict(101), _project_dict(202), _project_dict(303)]))

        walked: list[int] = []
        def _bc(path, token=None, params=None, _retry=1):
            for pid in (101, 202, 303):
                if path == f"/projects/{pid}.json":
                    walked.append(pid)
                    return _project_dict(pid)
                if path == f"/buckets/{pid}/todosets/555/todolists.json":
                    return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        sync.pull_todos_for_user("ali@colaberry.com")
        assert walked == [202, 303, 101]
        assert store.load_state("ali@colaberry.com").last_walked_bc_id == 101

    def test_round_robin_covers_all_projects_across_budget_limited_runs(
        self, monkeypatch, isolated_ops_root,
    ):
        """Headline budget_exceeded fix: when each run only has budget for
        one project, three runs cover ALL three projects (instead of forever
        re-walking the head and starving the tail)."""
        import time as _time
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "SYNC_BUDGET_SECONDS", 0.05)
        monkeypatch.setattr(sync, "discover_projects", MagicMock(
            return_value=[_project_dict(101), _project_dict(202), _project_dict(303)]))

        def _bc(path, token=None, params=None, _retry=1):
            for pid in (101, 202, 303):
                if path == f"/projects/{pid}.json":
                    _time.sleep(0.1)  # burn the per-project budget so 1/run walks
                    return _project_dict(pid)
                if path == f"/buckets/{pid}/todosets/555/todolists.json":
                    return _first_page_only([{"id": pid * 10, "name": "L"}], params)
                if path == f"/buckets/{pid}/todolists/{pid * 10}/todos.json":
                    if params and (params.get("page", 1) > 1
                                   or params.get("completed") == "true"):
                        return []
                    return [_todo_dict(pid * 100, assignees=(BC_USER,), title=f"T{pid}")]
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        for _ in range(3):
            sync.pull_todos_for_user("ali@colaberry.com")

        todo_ids = {t.bc_id for t in store.load_todos("ali@colaberry.com")}
        assert todo_ids == {10100, 20200, 30300}

    def test_partial_still_updates_last_sync_at(self, monkeypatch, isolated_ops_root):
        """Locks in a behavior the 2026-06-09 audit flagged as H4 — a
        partial sync still bumps last_sync_at. If/when Phase 2 changes
        this, that test must be updated explicitly; the change is
        load-bearing for the _natural_flow_sync freshness gate."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(
            sync, "discover_projects",
            MagicMock(return_value=[_project_dict(202, "Bad")]),
        )
        def _bc(path, *a, **kw):
            if path == "/projects/202.json":
                raise RuntimeError("BC angry")
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        sync.pull_todos_for_user("ali@colaberry.com")
        state = store.load_state("ali@colaberry.com")
        # Documents H4: last_sync_at is set even when the walk failed.
        assert state.last_sync_at
        assert state.last_sync_status == "partial"

    def test_ali_legacy_bucket_appended_when_supplied(
        self, monkeypatch, isolated_ops_root,
    ):
        """Phase A escape hatch: a bucket id supplied via ali_legacy_bucket
        must be walked even if discover_projects() doesn't return it."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "discover_projects", MagicMock(return_value=[]))
        seen_paths: list[str] = []
        def _bc(path, *a, **kw):
            seen_paths.append(path)
            if path == "/projects/7463955.json":
                return _project_dict(7463955, "Ali Personal")
            if path == "/buckets/7463955/todosets/555/todolists.json":
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        sync.pull_todos_for_user("ali@colaberry.com", ali_legacy_bucket=7463955)
        assert "/projects/7463955.json" in seen_paths

    def test_already_running_returns_early_no_bc_calls(self, monkeypatch, isolated_ops_root):
        """Coordinator integration: when a sync is already in flight for
        this user, a second concurrent call must return 'already_running'
        without firing ANY BC HTTP calls. Verified by counting _bc_get
        invocations — must be exactly zero on the gated path."""
        # Manually grab the slot so the next call is gated.
        coord = sync_coordinator.get_coordinator()
        assert coord.try_start_sync("ali@colaberry.com") is True

        bc_calls: list[str] = []
        def _bc(path, *a, **kw):
            bc_calls.append(path)
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)
        # Token mocks set up in case the gate fails — make a regression
        # easy to debug rather than crashing with AttributeError.
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=999))

        result = sync.pull_todos_for_user("ali@colaberry.com")

        assert result["status"] == "already_running"
        assert result["todos"] == 0
        assert bc_calls == [], (
            f"gated sync still fired BC calls: {bc_calls} — coordinator broken"
        )

    def test_slot_released_after_success(self, monkeypatch, isolated_ops_root):
        """After pull_todos_for_user returns normally, the next call from
        the same user must succeed — the slot must be released via the
        finally block, not stuck True like the prior _maybe_async_sync
        lock could be."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "discover_projects", MagicMock(return_value=[]))
        monkeypatch.setattr(sync, "_bc_get", MagicMock(return_value=None))

        r1 = sync.pull_todos_for_user("ali@colaberry.com")
        r2 = sync.pull_todos_for_user("ali@colaberry.com")
        assert r1["status"] == "ok"
        assert r2["status"] == "ok"  # NOT "already_running"

    def test_slot_released_when_token_missing(self, monkeypatch, isolated_ops_root):
        """Early-return paths (token_missing, bc_user_id_missing) must
        still release the slot. Otherwise a single misconfigured user
        could permanently strand themselves."""
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=(None, "missing")))
        r1 = sync.pull_todos_for_user("ali@colaberry.com")
        assert r1["status"] == "token_missing"
        # Slot must be free — second call gets the same error, not "already_running"
        r2 = sync.pull_todos_for_user("ali@colaberry.com")
        assert r2["status"] == "token_missing"

    def test_slot_released_after_exception(self, monkeypatch, isolated_ops_root):
        """If something unexpected raises during the walk, the slot must
        still be released. discover_projects throws → coordinator's
        finally still fires → next call can proceed."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "discover_projects",
                            MagicMock(side_effect=RuntimeError("BC down")))

        with pytest.raises(RuntimeError):
            sync.pull_todos_for_user("ali@colaberry.com")

        coord = sync_coordinator.get_coordinator()
        assert coord.is_sync_in_flight("ali@colaberry.com") is False


# ════════════════════════════════════════════════════════════════════
# Disappeared-row reconciliation — the 2026-06-17 incident
#
# A todo completed directly in Basecamp lingered as the #1 "active" human
# task after a sync, because the walk's best-effort completed-fetch missed
# the completion (BC's completed-list ordering / page cap / group nesting)
# and the authoritative per-row reconcile only ran in the 24h purge. The
# walk now reconciles any row BC dropped from a FULLY-walked bucket's active
# set, confirmed by a direct per-row GET — promptly, every sync, with no
# false positives. Also closes the still-open question from the 2026-06-04
# Press-Mike audit (why a completion didn't propagate despite a sync).
# ════════════════════════════════════════════════════════════════════


class TestWalkDisappearedReconciliation:
    BC_USER = 17454835

    def _seed_active_row(self, user_id, bc_id, *, bucket=101, list_id=7001):
        """Plant a locally-active row as if a prior sync had pulled it."""
        store.upsert_todos(user_id, [store.OpsTodo(
            bc_id=bc_id, bc_project_id=bucket, bc_project_name="Test Project",
            bc_todolist_id=list_id, bc_todolist_name="Launch Readiness",
            title="Integrate website (training) % tracking", status="active",
            assignee_ids=[self.BC_USER], assignee_names=["Ali M."],
            inclusion_reason="assigned",
            bc_app_url=f"https://3.basecamp.com/3945211/todos/{bc_id}",
            bc_updated_at=_days_ago(2),
        )])

    def _wire_walk(self, monkeypatch, todo_endpoint):
        """One project (101), one list (7001) whose active AND completed
        fetches are BOTH empty — i.e. the completion slipped past the walk's
        completed-fetch. The direct per-row confirm GET
        (/buckets/101/todos/{id}.json) is served by
        `todo_endpoint(bc_id) -> dict | None`. Returns the captured path list."""
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id",
                            MagicMock(return_value=self.BC_USER))
        monkeypatch.setattr(sync, "discover_projects",
                            MagicMock(return_value=[_project_dict(101)]))
        seen_paths: list[str] = []

        def _bc(path, token, params=None, _retry=1):
            seen_paths.append(path)
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Launch Readiness"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                return []  # nothing active, nothing completed surfaced by walk
            if path == "/buckets/101/todolists/7001/groups.json":
                return None
            if path.startswith("/buckets/101/todos/"):
                bc_id = int(path.rsplit("/", 1)[1].split(".")[0])
                return todo_endpoint(bc_id)
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)
        return seen_paths

    def test_completed_in_bc_missed_by_walk_is_reconciled(
        self, monkeypatch, isolated_ops_root,
    ):
        """Headline regression guard: a row BC dropped from the active set,
        completed in BC but missed by the walk's completed-fetch, is confirmed
        via a direct GET and flipped to 'completed' on the SAME sync."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 9946498016)

        def endpoint(bc_id):
            # BC: a completed todo keeps status 'active' (status = trash/archive
            # state); completion is the `completed` flag.
            return {
                "id": bc_id, "completed": True, "status": "active",
                "completion": {"created_at": _days_ago(0),
                               "creator": {"id": 999, "name": "Ali M."}},
            }
        self._wire_walk(monkeypatch, endpoint)

        result = sync.pull_todos_for_user(u)

        assert result["status"] == "ok"
        assert result["rows_reconciled"] == 1
        row = store.get_todo(u, 9946498016)
        assert row.status == "completed"
        assert row.completed_by_name == "Ali M."

    def test_still_active_in_bc_is_left_alone_no_false_positive(
        self, monkeypatch, isolated_ops_root,
    ):
        """A row absent from the active fetch but STILL active in BC (e.g. it
        sat beyond the active-fetch page cap) must NOT be retired — the confirm
        GET is the safety net against false positives."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 5001)
        self._wire_walk(monkeypatch,
                        lambda bc_id: {"id": bc_id, "completed": False,
                                       "status": "active"})

        result = sync.pull_todos_for_user(u)

        assert result["rows_reconciled"] == 0
        assert store.get_todo(u, 5001).status == "active"

    def test_deleted_in_bc_404_is_archived(self, monkeypatch, isolated_ops_root):
        """BC returns 400/404 (task or its whole list deleted) → row archived."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 6001)
        self._wire_walk(monkeypatch, lambda bc_id: None)

        result = sync.pull_todos_for_user(u)

        assert result["rows_reconciled"] == 1
        assert store.get_todo(u, 6001).status == "archived"

    def test_trashed_in_bc_is_archived(self, monkeypatch, isolated_ops_root):
        """BC returns a non-active status (operator 'deleted'/trashed it in BC,
        which doesn't 404) → row archived."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 6002)
        self._wire_walk(monkeypatch,
                        lambda bc_id: {"id": bc_id, "completed": False,
                                       "status": "trashed"})

        result = sync.pull_todos_for_user(u)

        assert result["rows_reconciled"] == 1
        assert store.get_todo(u, 6002).status == "archived"

    def test_row_still_in_active_set_is_not_confirmed(
        self, monkeypatch, isolated_ops_root,
    ):
        """When BC still returns the todo in the active fetch, it is kept and
        NOT confirmed via a direct GET — no wasted call, no risk."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 7777)
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id",
                            MagicMock(return_value=self.BC_USER))
        monkeypatch.setattr(sync, "discover_projects",
                            MagicMock(return_value=[_project_dict(101)]))
        seen_paths: list[str] = []

        def _bc(path, token, params=None, _retry=1):
            seen_paths.append(path)
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Launch Readiness"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                if params and (params.get("completed") == "true"
                               or params.get("page", 1) > 1):
                    return []
                return [_todo_dict(7777, assignees=(self.BC_USER,),
                                   title="Integrate website (training) % tracking")]
            if path == "/buckets/101/todolists/7001/groups.json":
                return None
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user(u)

        assert result["rows_reconciled"] == 0
        assert store.get_todo(u, 7777).status == "active"
        assert "/buckets/101/todos/7777.json" not in seen_paths

    def test_errored_bucket_rows_are_not_reconciled(
        self, monkeypatch, isolated_ops_root,
    ):
        """A bucket whose walk RAISES must never have its rows reconciled — we
        do not confirm-and-archive on incomplete data. Guards against archiving
        an operator's live tasks during a BC outage."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 8001, bucket=202)
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id",
                            MagicMock(return_value=self.BC_USER))
        monkeypatch.setattr(sync, "discover_projects",
                            MagicMock(return_value=[_project_dict(202, "Bad")]))
        seen_paths: list[str] = []

        def _bc(path, token, params=None, _retry=1):
            seen_paths.append(path)
            if path == "/projects/202.json":
                raise urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b""))
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_user(u)

        assert result["status"] == "partial"
        assert result["rows_reconciled"] == 0
        assert store.get_todo(u, 8001).status == "active"  # untouched
        assert "/buckets/202/todos/8001.json" not in seen_paths

    def test_dismissed_row_is_never_reconciled(self, monkeypatch, isolated_ops_root):
        """A dismissed row is a deliberate operator signal — the reconciliation
        skips it exactly like the purge sweep does."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 5555)
        store.update_todo(u, 5555, is_dismissed=True)
        seen_paths = self._wire_walk(monkeypatch, lambda bc_id: None)

        result = sync.pull_todos_for_user(u)

        assert result["rows_reconciled"] == 0
        assert "/buckets/101/todos/5555.json" not in seen_paths

    def test_kill_switch_disables_reconciliation(self, monkeypatch, isolated_ops_root):
        """OPS_WALK_RECONCILE=0 turns the whole pass off (rollback lever)."""
        monkeypatch.setattr(sync, "WALK_RECONCILE", False)
        u = "ali@colaberry.com"
        self._seed_active_row(u, 4001)
        seen_paths = self._wire_walk(
            monkeypatch,
            lambda bc_id: {"id": bc_id, "completed": True, "status": "active",
                           "completion": {"created_at": _days_ago(0),
                                          "creator": {"id": 999, "name": "Ali M."}}},
        )

        result = sync.pull_todos_for_user(u)

        assert result["rows_reconciled"] == 0
        assert store.get_todo(u, 4001).status == "active"  # left as-is
        assert "/buckets/101/todos/4001.json" not in seen_paths

    def test_targeted_sync_also_reconciles_disappeared_rows(
        self, monkeypatch, isolated_ops_root,
    ):
        """pull_todos_for_project (Mark Done path) reconciles sibling
        completions the operator made directly in BC, not just the touched row."""
        u = "ali@colaberry.com"
        self._seed_active_row(u, 9001)
        self._wire_walk(
            monkeypatch,
            lambda bc_id: {"id": bc_id, "completed": True, "status": "active",
                           "completion": {"created_at": _days_ago(0),
                                          "creator": {"id": 999, "name": "Ali M."}}},
        )

        result = sync.pull_todos_for_project(u, 101)

        assert result["status"] == "ok"
        assert result["rows_reconciled"] == 1
        assert store.get_todo(u, 9001).status == "completed"


# ════════════════════════════════════════════════════════════════════
# pull_todos_for_project — targeted single-project sync (Mark Done path)
# ════════════════════════════════════════════════════════════════════


class TestPullTodosForProject:
    def test_missing_token(self, monkeypatch, isolated_ops_root):
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=(None, "missing")))
        result = sync.pull_todos_for_project("ali@colaberry.com", 101)
        assert result["status"] == "token_missing"

    def test_missing_bc_user_id(self, monkeypatch, isolated_ops_root):
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=None))
        result = sync.pull_todos_for_project("ali@colaberry.com", 101)
        assert result["status"] == "bc_user_id_missing"

    def test_project_not_found(self, monkeypatch, isolated_ops_root):
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=17454835))
        monkeypatch.setattr(sync, "_bc_get", MagicMock(return_value=None))
        result = sync.pull_todos_for_project("ali@colaberry.com", 101)
        assert result["status"] == "project_not_found"

    def test_happy_path_upserts_and_returns_count(self, monkeypatch, isolated_ops_root):
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        def _bc(path, token, params=None, _retry=1):
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Inbox"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                if params and params.get("page", 1) > 1:
                    return []
                if params and params.get("completed") == "true":
                    return []
                return [_todo_dict(9001, assignees=(BC_USER,), title="X")]
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)
        result = sync.pull_todos_for_project("ali@colaberry.com", 101)
        assert result["status"] == "ok"
        assert result["todos"] == 1
        # NB: pull_todos_for_project intentionally does NOT update
        # state.last_sync_at (this is the audit's L5 — documented here
        # so future changes are deliberate).
        state = store.load_state("ali@colaberry.com")
        assert state.last_sync_at == ""

    def test_walker_exception_records_error_and_returns_error(
        self, monkeypatch, isolated_ops_root,
    ):
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        def _bc(path, *a, **kw):
            if path == "/projects/101.json":
                return _project_dict(101)
            raise RuntimeError("boom")
        monkeypatch.setattr(sync, "_bc_get", _bc)
        result = sync.pull_todos_for_project("ali@colaberry.com", 101)
        assert result["status"] == "error"
        assert "boom" in result["error"]
        # Captured in the ring buffer too
        errs = sync.recent_errors()
        assert any("project_walk:101" in e["kind"] for e in errs)

    def test_already_running_when_full_sync_in_flight(
        self, monkeypatch, isolated_ops_root,
    ):
        """The Mark Done targeted sync must defer to an in-flight full
        sync — racing it would just double the BC API load AND risk
        the same H3 lost-write the audit flagged."""
        coord = sync_coordinator.get_coordinator()
        coord.try_start_sync("ali@colaberry.com")
        bc_calls: list[str] = []
        monkeypatch.setattr(sync, "_bc_get",
                            lambda p, *a, **kw: bc_calls.append(p))

        result = sync.pull_todos_for_project("ali@colaberry.com", 101)
        assert result["status"] == "already_running"
        assert bc_calls == []

    def test_sets_last_targeted_sync_at(self, monkeypatch, isolated_ops_root):
        """L5 (2026-06-09 audit): targeted sync writes a distinct
        timestamp so UI can distinguish 'Mark Done has run' from
        'no full sync ever ran'. Locks in the contract that
        last_sync_at stays empty but last_targeted_sync_at advances."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        def _bc(path, token, params=None, _retry=1):
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Inbox"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                return []
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)

        result = sync.pull_todos_for_project("ali@colaberry.com", 101)
        assert result["status"] == "ok"
        state = store.load_state("ali@colaberry.com")
        # last_sync_at intentionally still empty -- targeted sync is NOT
        # a full sync, and the natural-flow gate must keep treating it
        # that way.
        assert state.last_sync_at == ""
        # But last_targeted_sync_at is set so the UI can show feedback.
        assert state.last_targeted_sync_at != ""
        assert "T" in state.last_targeted_sync_at  # ISO-shaped

    def test_pull_todos_for_user_does_not_set_last_targeted_sync_at(
        self, monkeypatch, isolated_ops_root,
    ):
        """Symmetry test for L5: pull_todos_for_user updates last_sync_at
        but NOT last_targeted_sync_at. The two fields are independent
        signals -- full sync is the canonical 'data is fresh' marker."""
        BC_USER = 17454835
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id", MagicMock(return_value=BC_USER))
        monkeypatch.setattr(sync, "discover_projects", MagicMock(return_value=[]))
        monkeypatch.setattr(sync, "_bc_get", MagicMock(return_value=None))

        sync.pull_todos_for_user("ali@colaberry.com")
        state = store.load_state("ali@colaberry.com")
        assert state.last_sync_at != ""             # full sync did happen
        assert state.last_targeted_sync_at == ""    # but no targeted touch


# ════════════════════════════════════════════════════════════════════
# Silent-error ring buffer — used by /my-day/_health
# ════════════════════════════════════════════════════════════════════


class TestRecentErrors:
    def test_record_then_read(self):
        sync._record_error("u@x.com", "kind", "detail")
        errs = sync.recent_errors()
        assert errs[-1]["user_id"] == "u@x.com"
        assert errs[-1]["kind"] == "kind"
        assert errs[-1]["detail"] == "detail"
        assert errs[-1]["ts"]  # timestamped

    def test_detail_truncated_at_300_chars(self):
        sync._record_error("u@x.com", "kind", "x" * 500)
        errs = sync.recent_errors()
        assert len(errs[-1]["detail"]) == 300

    def test_clear_empties_buffer(self):
        sync._record_error("u@x.com", "kind", "detail")
        sync.clear_recent_errors()
        assert sync.recent_errors() == []


class TestBcUserIdSelfHeal:
    """The cache (User.bc_user_id) can rot. The self-serve connect flow used to
    store the *Launchpad identity id* instead of the *account-person id*, so a
    correctly-connected human matched ZERO todo assignees and saw none of their
    own tasks — the 2026-06-16 Swati incident (466 todos in queue, all noise,
    0 'assigned'). Sync resolves the live account-person id from
    /my/profile.json, classifies against THAT, and heals the stored cache —
    EXCEPT for AI-clone connections, where the human id must never be
    overwritten with the clone's id (the clone-by-design model)."""

    def _wire(self, monkeypatch, *, stale_id, profile_id, assignee_id, is_clone):
        from execution.products.library import (
            basecamp_oauth_token, basecamp_provisioning, tenancy,
        )
        monkeypatch.setattr(sync.tokens, "get_user_token",
                            MagicMock(return_value=("tok", "vault-oauth")))
        monkeypatch.setattr(sync.tokens, "get_user_bc_id",
                            MagicMock(return_value=stale_id))
        monkeypatch.setattr(sync, "discover_projects",
                            MagicMock(return_value=[_project_dict(101)]))
        user_obj = SimpleNamespace(email="swati@colaberry.com",
                                   user_id="swati@colaberry.com",
                                   bc_user_id=stale_id)
        captured: dict = {}
        monkeypatch.setattr(tenancy, "get_user",
                            MagicMock(return_value=user_obj))
        monkeypatch.setattr(tenancy, "upsert_user",
                            lambda u: captured.__setitem__("bc_user_id", u.bc_user_id) or u)
        monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                            MagicMock(return_value={"bc_user_email": "swati@colaberry.com"}))
        monkeypatch.setattr(basecamp_provisioning, "is_ai_account_for_user",
                            MagicMock(return_value=is_clone))

        def _bc(path, token, params=None, _retry=1):
            if path == "/my/profile.json":
                return {"id": profile_id, "email_address": "swati@colaberry.com"}
            if path == "/projects/101.json":
                return _project_dict(101)
            if path == "/buckets/101/todosets/555/todolists.json":
                return _first_page_only([{"id": 7001, "name": "Inbox"}], params)
            if path == "/buckets/101/todolists/7001/todos.json":
                if params and params.get("page", 1) > 1:
                    return []
                if params and params.get("completed") == "true":
                    return []
                return [_todo_dict(9001, assignees=(assignee_id,),
                                   title="Submit TWC registration")]
            return None
        monkeypatch.setattr(sync, "_bc_get", _bc)
        return captured

    def test_human_stale_cache_heals_and_classifies_against_account_id(
        self, monkeypatch, isolated_ops_root,
    ):
        STALE_LAUNCHPAD = 27309320   # what the broken connect flow cached
        ACCOUNT_PERSON = 48041031    # ground truth from /my/profile.json
        captured = self._wire(
            monkeypatch, stale_id=STALE_LAUNCHPAD, profile_id=ACCOUNT_PERSON,
            assignee_id=ACCOUNT_PERSON, is_clone=False,
        )

        result = sync.pull_todos_for_user("swati@colaberry.com")

        # The task assigned to the ACCOUNT id is now recognized as the user's,
        # even though the cached id (Launchpad) matched nothing.
        assert result["todos_assigned_to_user"] == 1
        todos = store.load_todos("swati@colaberry.com")
        assert len(todos) == 1
        assert todos[0].inclusion_reason == "assigned"
        # Cache healed to the account-person id so next run is correct offline.
        assert captured.get("bc_user_id") == ACCOUNT_PERSON

    def test_clone_connection_does_not_clobber_human_id(
        self, monkeypatch, isolated_ops_root,
    ):
        HUMAN = 17454835        # classifier id (human)
        CLONE_ACCOUNT = 37708014  # what the clone token authenticates as
        captured = self._wire(
            monkeypatch, stale_id=HUMAN, profile_id=CLONE_ACCOUNT,
            assignee_id=HUMAN, is_clone=True,
        )

        result = sync.pull_todos_for_user("swati@colaberry.com")

        # Classified against the HUMAN id (the clone reads the human's tasks
        # by design); the clone's account id was NOT written back.
        assert result["todos_assigned_to_user"] == 1
        assert "bc_user_id" not in captured  # no heal → upsert never called

    def test_human_matching_cache_is_not_rewritten(
        self, monkeypatch, isolated_ops_root,
    ):
        """When the cache already equals the account-person id, no needless
        tenancy write happens (heal only fires on drift)."""
        SAME = 48041031
        captured = self._wire(
            monkeypatch, stale_id=SAME, profile_id=SAME,
            assignee_id=SAME, is_clone=False,
        )

        result = sync.pull_todos_for_user("swati@colaberry.com")

        assert result["todos_assigned_to_user"] == 1
        assert "bc_user_id" not in captured
