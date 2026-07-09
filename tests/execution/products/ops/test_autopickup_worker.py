"""Tests for execution/products/ops/autopickup_worker.py.

Covers the pure-Python orchestration (allowlist loading, top-N selection,
seen-set idempotency, comment rendering, refusal-of-already-drafted) with
no real LLM and no real BC calls. The BC and LLM layers are monkeypatched
to deterministic stubs.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from execution.products.ops import autopickup_worker as wi


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def tmp_paths(tmp_path, monkeypatch):
    """Redirect every disk path (seen, heartbeat, audit, allowlist) to tmp."""
    monkeypatch.setattr(wi, "SEEN_PATH", tmp_path / "_autopickup" / "seen.json")
    monkeypatch.setattr(wi, "HEARTBEAT_PATH", tmp_path / "_autopickup" / "heartbeat.json")
    monkeypatch.setattr(wi, "AUDIT_DIR", tmp_path / "_autopickup")
    monkeypatch.setattr(wi, "ALLOWLIST_FILE", tmp_path / "allowlist.json")
    yield tmp_path


def _todo(bc_id, *, project=7463955, status="active",
                  category="", urgency=80, title="Test ticket",
                  due="2026-06-30", dismissed=False):
    return SimpleNamespace(
        bc_id=bc_id, bc_project_id=project, status=status,
        category=category, urgency_score=urgency, title=title,
        due_on=due, is_dismissed=dismissed,
        description="", bc_project_name="Ali Personal",
        bc_todolist_name="AI Products", bc_app_url=f"https://bc/{bc_id}",
        # project_url/list_url are real OpsTodo properties; the prompt renderer
        # reads them, so the stub must supply them too.
        project_url=f"https://bc/{bc_id}/proj",
        list_url=f"https://bc/{bc_id}/list",
        bc_updated_at="2026-06-09T10:00:00Z",
    )


# ── Allowlist loading ─────────────────────────────────────────────


class TestLoadAllowlist:

    def test_falls_back_to_env_when_file_missing(self, monkeypatch):
        monkeypatch.setenv("OPS_AUTOPICKUP_BUCKETS", "111,222")
        out = wi._load_allowlist()
        ids = [e["bucket_id"] for e in out]
        assert ids == [111, 222]
        for e in out:
            assert e["mode"] == "draft-only"
            assert e["rate_limit_per_15m"] == 10

    def test_reads_typed_entries_from_file(self, tmp_paths):
        (tmp_paths / "allowlist.json").write_text(json.dumps({
            "buckets": [
                {"bucket_id": 7463955, "name": "Ali Personal",
                  "approved_by": "ali@colaberry.com",
                  "approved_at": "2026-06-09",
                  "mode": "draft-only", "rate_limit_per_15m": 15},
                {"bucket_id": 1234, "name": "Other",
                  "approved_by": "other@x.com",
                  "approved_at": "2026-06-10",
                  "mode": "local-execute", "rate_limit_per_15m": 5},
            ],
        }), encoding="utf-8")
        out = wi._load_allowlist()
        assert len(out) == 2
        assert out[0]["bucket_id"] == 7463955
        assert out[0]["rate_limit_per_15m"] == 15
        assert out[1]["mode"] == "local-execute"

    def test_drops_malformed_entries(self, tmp_paths):
        (tmp_paths / "allowlist.json").write_text(json.dumps({
            "buckets": [
                "not-a-dict",
                {"bucket_id": "not-an-int"},
                {"name": "missing-bucket-id"},
                {"bucket_id": 999, "name": "ok"},
            ],
        }), encoding="utf-8")
        out = wi._load_allowlist()
        assert len(out) == 1
        assert out[0]["bucket_id"] == 999

    def test_malformed_file_falls_back_to_env(self, tmp_paths, monkeypatch):
        (tmp_paths / "allowlist.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("OPS_AUTOPICKUP_BUCKETS", "777")
        out = wi._load_allowlist()
        assert [e["bucket_id"] for e in out] == [777]


# ── Top-N selection ───────────────────────────────────────────────


class TestUserTopAITodos:

    def test_filters_to_bucket_and_excludes_dismissed(self, monkeypatch):
        todos = [
            _todo(1, project=7463955, urgency=90),
            _todo(2, project=7463955, urgency=80, dismissed=True),  # dropped
            _todo(3, project=9999999, urgency=95),                   # wrong bucket
            _todo(4, project=7463955, status="completed", urgency=99),  # not active
            _todo(5, project=7463955, urgency=70),
        ]
        monkeypatch.setattr(wi.store, "load_todos", lambda e: todos)
        out = wi._user_top_ai_todos("x@y.com", 7463955)
        assert [t.bc_id for t in out] == [1, 5]  # urgency desc

    def test_excludes_human_required_category(self, monkeypatch):
        todos = [
            _todo(1, category="human_required", urgency=99),  # excluded
            _todo(2, category="", urgency=80),
            _todo(3, category="default", urgency=70),
        ]
        monkeypatch.setattr(wi.store, "load_todos", lambda e: todos)
        out = wi._user_top_ai_todos("x@y.com", 7463955)
        assert [t.bc_id for t in out] == [2, 3]

    def test_returns_top_n_only(self, monkeypatch):
        todos = [_todo(i, urgency=100 - i) for i in range(1, 11)]
        monkeypatch.setattr(wi.store, "load_todos", lambda e: todos)
        monkeypatch.setattr(wi, "TOP_N", 4)
        out = wi._user_top_ai_todos("x@y.com", 7463955)
        assert len(out) == 4
        assert [t.bc_id for t in out] == [1, 2, 3, 4]


# ── Seen-set ──────────────────────────────────────────────────────


class TestSeenSet:

    def test_roundtrip(self):
        assert wi._seen() == set()
        s = {wi._seen_key(123, "2026-06-09T10:00:00Z")}
        wi._save_seen(s)
        assert wi._seen() == s

    def test_key_changes_with_updated_at(self):
        k1 = wi._seen_key(123, "2026-06-09T10:00:00Z")
        k2 = wi._seen_key(123, "2026-06-09T11:00:00Z")
        assert k1 != k2

    def test_caps_at_5000_entries(self):
        s = {f"todo:{i}:t" for i in range(6000)}
        wi._save_seen(s)
        reread = wi._seen()
        assert len(reread) <= 5000


# ── Comment rendering ─────────────────────────────────────────────


class TestRenderComment:

    def _plan(self, **overrides):
        base = {
            "action": "Open backend/foo.py and add bar()",
            "why": "Ticket states bar is missing.",
            "side_effects": ["Adds a new exported function bar() to foo.py"],
            "confidence_pct": 90,
            "needs_input": [],
        }
        base.update(overrides)
        return base

    def test_basic_shape_without_prompt(self):
        html = wi._render_comment(self._plan(), "ap-001")
        assert "Auto-pickup: proposed next step" in html
        assert "confidence: 90%" in html
        assert "Open backend/foo.py" in html
        assert "ap-001" in html
        assert "<details>" not in html  # no prompt -> no details block

    def test_embeds_claude_code_prompt_in_details(self):
        prompt = "Implement bar() in foo.py.\n\nCONTEXT:\n..."
        html = wi._render_comment(self._plan(), "ap-002",
                                                claude_code_prompt=prompt)
        assert "<details>" in html
        assert "<pre" in html
        assert "Implement bar() in foo.py" in html

    def test_html_escapes_prompt_so_markup_renders_as_text(self):
        prompt = "<script>alert(1)</script>"
        html = wi._render_comment(self._plan(), "ap-003",
                                                claude_code_prompt=prompt)
        assert "&lt;script&gt;" in html
        assert "<script>alert(1)</script>" not in html

    def test_renders_needs_input_block(self):
        html = wi._render_comment(
            self._plan(needs_input=["Which database backend?",
                                                  "What's the auth model?"]),
            "ap-004",
        )
        assert "Needs your input" in html
        assert "Which database backend?" in html

    def test_zero_side_effects_says_none_specified(self):
        html = wi._render_comment(self._plan(side_effects=[]), "ap-005")
        assert "none specified" in html


# ── Disabled by default ───────────────────────────────────────────


class TestEnabledFlag:

    def test_scan_all_users_is_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(wi, "ENABLED", False)
        r = wi.scan_all_users()
        assert r == {"status": "disabled",
                            "hint": "set OPS_AUTOPICKUP_ENABLED=true to enable"}

    def test_scan_all_users_walks_phase1_users_when_enabled(self, monkeypatch):
        monkeypatch.setattr(wi, "ENABLED", True)
        monkeypatch.setattr(wi, "PHASE1_USERS", ["a@x.com", "b@x.com"])
        # Stub scan_for_user so we don't hit BC
        called = []

        def fake_scan(email):
            called.append(email)
            return {"user_email": email, "drafted": 0}
        monkeypatch.setattr(wi, "scan_for_user", fake_scan)
        r = wi.scan_all_users()
        assert called == ["a@x.com", "b@x.com"]
        assert "users" in r
        assert r["users"]["a@x.com"]["drafted"] == 0


# ── scan_for_user end-to-end (BC + LLM stubbed) ──────────────────


class TestScanForUser:

    def _stub_bc(self, monkeypatch, *, updated_at="2026-06-09T10:00:00Z",
                          existing_comments=None):
        monkeypatch.setattr(wi, "_fetch_bc_todo",
                                       lambda b, t, tok: {"updated_at": updated_at,
                                                              "description": "<p>desc</p>"})
        monkeypatch.setattr(wi, "_fetch_recent_comments",
                                       lambda b, t, tok: existing_comments or [])

    def _stub_llm_yes(self, monkeypatch):
        monkeypatch.setattr(wi, "_llm_propose", lambda **kwargs: {
            "action": "Do the thing", "why": "It is in the ticket.",
            "side_effects": ["Side"], "confidence_pct": 90, "needs_input": [],
        })

    def _stub_post_ok(self, monkeypatch):
        monkeypatch.setattr(wi, "_bc_post_comment",
                                       lambda b, t, h, tok: (True, "ok",
                                                                  {"id": 999, "app_url": "u"}))

    def test_no_token_returns_no_token_status(self, monkeypatch):
        monkeypatch.setattr(wi.tokens, "get_user_token",
                                       lambda email: (None, "vault"))
        r = wi.scan_for_user("x@y.com")
        assert r["error"] == "no_token"

    def test_drafts_a_comment_when_path_is_clear(self, monkeypatch):
        monkeypatch.setattr(wi.tokens, "get_user_token",
                                       lambda email: ("FAKE_TOKEN", "vault"))
        monkeypatch.setattr(wi.store, "load_todos",
                                       lambda e: [_todo(123, urgency=99)])
        self._stub_bc(monkeypatch)
        self._stub_llm_yes(monkeypatch)
        # Capture the posted comment so we can assert the embedded prompt.
        posted = {}
        monkeypatch.setattr(wi, "_bc_post_comment",
                            lambda b, t, h, tok: (posted.update(html=h)
                                                  or (True, "ok",
                                                      {"id": 999, "app_url": "u"})))
        # Stub the enhance pairing (FIELDS now, not a hand-written prompt).
        # Patch the REAL module attributes, not sys.modules: the worker does
        # `from . import llm_suggest`, and once any other module (e.g. my_day)
        # has imported the real submodule, that bound attribute wins over a
        # sys.modules swap — so a setitem stub would be silently bypassed.
        from execution.products.ops import llm_suggest as _ls
        monkeypatch.setattr(_ls, "enhance", lambda uid, t, c: {
            "action_kind": "decision",
            "goal_line": "A vendor decision posted to BC.",
            "specific_steps": ["Choose ONE: (a) Acme (b) Globex"],
            "stop_conditions": [],
        })
        from execution.products.library import tenancy as _ten
        monkeypatch.setattr(_ten, "get_user",
                            lambda email: SimpleNamespace(user_id="u-1"))

        # File-based allowlist with one bucket
        (wi.ALLOWLIST_FILE.parent).mkdir(parents=True, exist_ok=True)
        wi.ALLOWLIST_FILE.write_text(json.dumps({
            "buckets": [{"bucket_id": 7463955, "name": "Ali"}],
        }), encoding="utf-8")

        r = wi.scan_for_user("ali@colaberry.com")
        assert r["drafted"] == 1
        assert r["buckets_checked"] == 1
        # The embedded prompt is rendered through the shared Summary/Downloads/
        # Details template from the LLM fields (not a hand-written prompt).
        assert "## Summary" in posted["html"]
        assert "A vendor decision posted to BC." in posted["html"]

    def test_skips_when_already_drafted_in_recent_comments(self, monkeypatch):
        monkeypatch.setattr(wi.tokens, "get_user_token",
                                       lambda email: ("FAKE_TOKEN", "vault"))
        monkeypatch.setattr(wi.store, "load_todos",
                                       lambda e: [_todo(123, urgency=99)])
        self._stub_bc(monkeypatch, existing_comments=[
            {"content": "<p>autopickup_id: ap-prev</p>",
              "creator": {"name": "CB"},
              "created_at": "2026-06-09T09:00:00Z"},
        ])
        self._stub_llm_yes(monkeypatch)
        self._stub_post_ok(monkeypatch)
        (wi.ALLOWLIST_FILE.parent).mkdir(parents=True, exist_ok=True)
        wi.ALLOWLIST_FILE.write_text(json.dumps({
            "buckets": [{"bucket_id": 7463955, "name": "Ali"}],
        }), encoding="utf-8")

        r = wi.scan_for_user("ali@colaberry.com")
        assert r["drafted"] == 0
        assert r["skipped_seen"] == 1

    def test_respects_per_bucket_rate_limit(self, monkeypatch):
        monkeypatch.setattr(wi.tokens, "get_user_token",
                                       lambda email: ("FAKE", "vault"))
        # 3 todos but rate limit is 1
        monkeypatch.setattr(wi.store, "load_todos",
                                       lambda e: [_todo(i, urgency=100 - i)
                                                          for i in range(1, 4)])
        monkeypatch.setattr(wi, "TOP_N", 5)
        self._stub_bc(monkeypatch)
        self._stub_llm_yes(monkeypatch)
        self._stub_post_ok(monkeypatch)
        # Stub enhance + tenancy
        import sys
        sys.modules["execution.products.ops.llm_suggest"] = type(sys)("x")
        sys.modules["execution.products.ops.llm_suggest"].enhance = \
            lambda u, t, c: None
        sys.modules["execution.products.library.tenancy"] = type(sys)("x")
        sys.modules["execution.products.library.tenancy"].get_user = \
            lambda e: None

        (wi.ALLOWLIST_FILE.parent).mkdir(parents=True, exist_ok=True)
        wi.ALLOWLIST_FILE.write_text(json.dumps({
            "buckets": [{"bucket_id": 7463955, "name": "Ali",
                                "rate_limit_per_15m": 1}],
        }), encoding="utf-8")

        r = wi.scan_for_user("ali@colaberry.com")
        assert r["drafted"] == 1
        assert "rate_limited_buckets" in r
