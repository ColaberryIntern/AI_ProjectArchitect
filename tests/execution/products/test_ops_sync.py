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

    def test_500_raises(self, monkeypatch):
        """Server errors must propagate so per-project resilience can
        record them in the silent-error ring buffer."""
        monkeypatch.setattr(
            sync.urllib.request, "urlopen",
            MagicMock(side_effect=_http_error(500)),
        )
        with pytest.raises(urllib.error.HTTPError):
            sync._bc_get("/projects.json", "tok")

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
                return [{"id": 7001, "name": "Inbox"}]
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
                return [{"id": 7001, "name": "Inbox"}]
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
                return [{"id": 7001, "name": "Inbox"}]
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
        assert "2/3 projects unwalked" in any_match["detail"]

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
                return [{"id": 7001, "name": "Inbox"}]
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
                return [{"id": 7001, "name": "Inbox"}]
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
