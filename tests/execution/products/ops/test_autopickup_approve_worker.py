"""Tests for autopickup_approve_worker. Covers classifier, find-approval
walk, and scan_for_user orchestration. No network, no real BC, no real
LLM (the worker does not call any LLM)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from execution.products.ops import autopickup_approve_worker as aw


@pytest.fixture(autouse=True)
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(aw, "AUTOPICKUP_AUDIT_DIR", tmp_path / "_autopickup")
    monkeypatch.setattr(aw, "APPROVAL_DIR", tmp_path / "_autopickup_approvals")
    monkeypatch.setattr(aw, "PROCESSED_PATH",
                                  tmp_path / "_autopickup_approvals" / "processed.json")
    monkeypatch.setattr(aw, "HEARTBEAT_PATH",
                                  tmp_path / "_autopickup_approvals" / "heartbeat.json")
    yield tmp_path


# ── Classifier ────────────────────────────────────────────────────


class TestClassifyReply:

    @pytest.mark.parametrize("text", [
        "approve", "Approved", "APPROVE", "yes do it", "go for it",
        "ship it", "LGTM", "👍 nice work", "approved! thanks",
    ])
    def test_recognizes_approval_signals(self, text):
        assert aw._classify_reply(text) == "approved"

    @pytest.mark.parametrize("text", [
        "reject this", "rejected -- not now", "do not proceed",
        "don't", "no thanks", "stop", "hold off",
        "not yet, please clarify", "👎", "❌ wrong file",
    ])
    def test_recognizes_rejection_signals(self, text):
        assert aw._classify_reply(text) == "rejected"

    def test_both_signals_present_is_ambiguous(self):
        assert aw._classify_reply("approve but stop until I see X") == "ambiguous"

    def test_neither_signal_present_is_ambiguous(self):
        assert aw._classify_reply("Thanks for the update on the report.") == "ambiguous"

    def test_empty_text_is_ambiguous(self):
        assert aw._classify_reply("") == "ambiguous"


# ── Find approval walk ───────────────────────────────────────────


def _comment(id, body, *, creator="Ali Muwwakkil", created_at="2026-06-09T11:00:00Z"):
    return {
        "id": id,
        "content": body,
        "creator": {"name": creator},
        "created_at": created_at,
    }


class TestFindApproval:

    def test_returns_none_when_no_comment_after_autopickup(self):
        comments = [_comment(1, "<p>autopickup_id: ap-001</p>",
                                            creator="CB System",
                                            created_at="2026-06-09T10:00:00Z")]
        out = aw._find_approval("ap-001", 1, comments)
        assert out is None

    def test_returns_none_when_only_cb_comments_after(self):
        comments = [
            _comment(1, "<p>autopickup_id: ap-001</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T10:00:00Z"),
            _comment(2, "<p>Status update from CB</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T11:00:00Z"),
        ]
        out = aw._find_approval("ap-001", 1, comments)
        assert out is None

    def test_detects_approval_from_human_reply(self):
        comments = [
            _comment(1, "<p>autopickup_id: ap-001</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T10:00:00Z"),
            _comment(2, "<p>approved</p>",
                                  creator="Ali Muwwakkil",
                                  created_at="2026-06-09T11:00:00Z"),
        ]
        out = aw._find_approval("ap-001", 1, comments)
        assert out is not None
        assert out.status == "approved"
        assert out.signal_source == "reply"
        assert out.signal_author == "Ali Muwwakkil"
        assert out.signal_comment_id == 2
        assert "approved" in out.signal_text.lower()

    def test_detects_rejection_from_human_reply(self):
        comments = [
            _comment(1, "<p>autopickup_id: ap-001</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T10:00:00Z"),
            _comment(2, "<p>do not proceed -- wrong file</p>",
                                  creator="Ali Muwwakkil",
                                  created_at="2026-06-09T11:00:00Z"),
        ]
        out = aw._find_approval("ap-001", 1, comments)
        assert out.status == "rejected"

    def test_unrelated_chatter_classified_as_ambiguous(self):
        comments = [
            _comment(1, "<p>autopickup_id: ap-001</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T10:00:00Z"),
            _comment(2, "<p>Thanks for the heads up.</p>",
                                  creator="Karun",
                                  created_at="2026-06-09T11:00:00Z"),
        ]
        out = aw._find_approval("ap-001", 1, comments)
        assert out.status == "ambiguous"

    def test_walks_in_chronological_order(self):
        # Out-of-order list -- the function should sort by created_at
        comments = [
            _comment(3, "<p>approve</p>",
                                  creator="Ali",
                                  created_at="2026-06-09T12:00:00Z"),
            _comment(1, "<p>autopickup_id: ap-001</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T10:00:00Z"),
            _comment(2, "<p>Will look at this</p>",
                                  creator="Ali",
                                  created_at="2026-06-09T11:00:00Z"),
        ]
        out = aw._find_approval("ap-001", 1, comments)
        # First HUMAN reply after the autopickup comment is the 'Will look...'
        # one which is ambiguous; classifier should return that first.
        assert out is not None
        assert out.signal_comment_id == 2
        assert out.status == "ambiguous"


# ── Audit log walk ───────────────────────────────────────────────


class TestLoadRecentAutopickupAudit:

    def test_reads_drafted_rows_only(self, tmp_paths):
        (tmp_paths / "_autopickup").mkdir(parents=True, exist_ok=True)
        p = tmp_paths / "_autopickup" / "2026-06-09.jsonl"
        p.write_text("\n".join([
            json.dumps({"autopickup_id": "a", "status": "drafted",
                                "comment_id": 1, "bucket": 1, "todo_id": 1}),
            json.dumps({"autopickup_id": "b", "status": "failed",
                                "comment_id": 2, "bucket": 1, "todo_id": 2}),
            json.dumps({"autopickup_id": "c", "status": "drafted",
                                "comment_id": 3, "bucket": 1, "todo_id": 3}),
        ]) + "\n", encoding="utf-8")
        rows = aw._load_recent_autopickup_audit()
        ids = [r["autopickup_id"] for r in rows]
        assert ids == ["a", "c"]

    def test_drops_drafted_rows_without_comment_id(self, tmp_paths):
        (tmp_paths / "_autopickup").mkdir(parents=True, exist_ok=True)
        p = tmp_paths / "_autopickup" / "2026-06-09.jsonl"
        p.write_text(
            json.dumps({"autopickup_id": "a", "status": "drafted",
                                "comment_id": None}) + "\n",
            encoding="utf-8",
        )
        rows = aw._load_recent_autopickup_audit()
        assert rows == []

    def test_empty_dir_returns_empty(self):
        assert aw._load_recent_autopickup_audit() == []


# ── Processed set ────────────────────────────────────────────────


class TestProcessedSet:

    def test_roundtrip(self):
        assert aw._processed() == set()
        s = {"ap-1", "ap-2"}
        aw._save_processed(s)
        assert aw._processed() == s

    def test_caps_at_10000(self):
        s = {f"ap-{i}" for i in range(11000)}
        aw._save_processed(s)
        assert len(aw._processed()) <= 10000


# ── scan_for_user end-to-end ─────────────────────────────────────


class TestScanForUser:

    def _seed_audit(self, tmp_paths, rows: list[dict]):
        (tmp_paths / "_autopickup").mkdir(parents=True, exist_ok=True)
        p = tmp_paths / "_autopickup" / "2026-06-09.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                              encoding="utf-8")

    def test_no_token_returns_no_token_status(self, monkeypatch):
        monkeypatch.setattr(aw.tokens, "get_user_token",
                                       lambda email: (None, "vault"))
        r = aw.scan_for_user("x@y.com")
        assert r["error"] == "no_token"

    def test_marks_approved_when_reply_says_approve(self, tmp_paths, monkeypatch):
        monkeypatch.setattr(aw.tokens, "get_user_token",
                                       lambda email: ("TOK", "vault"))
        self._seed_audit(tmp_paths, [{
            "autopickup_id": "ap-1", "status": "drafted",
            "comment_id": 100, "bucket": 7463955, "todo_id": 9000,
        }])
        monkeypatch.setattr(aw, "_fetch_comments", lambda b, t, tok: [
            _comment(100, "<p>autopickup_id: ap-1</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T10:00:00Z"),
            _comment(101, "<p>approve</p>",
                                  creator="Ali Muwwakkil",
                                  created_at="2026-06-09T11:00:00Z"),
        ])
        r = aw.scan_for_user("ali@colaberry.com")
        assert r["newly_approved"] == 1
        assert r["newly_rejected"] == 0
        assert "ap-1" in aw._processed()

    def test_idempotent_does_not_reprocess(self, tmp_paths, monkeypatch):
        monkeypatch.setattr(aw.tokens, "get_user_token",
                                       lambda email: ("TOK", "vault"))
        self._seed_audit(tmp_paths, [{
            "autopickup_id": "ap-1", "status": "drafted",
            "comment_id": 100, "bucket": 7463955, "todo_id": 9000,
        }])
        # Pre-seed processed set
        aw._save_processed({"ap-1"})
        monkeypatch.setattr(aw, "_fetch_comments", lambda b, t, tok: [])
        r = aw.scan_for_user("ali@colaberry.com")
        assert r["skipped_already_processed"] == 1
        assert r["newly_approved"] == 0

    def test_pending_when_no_human_reply_yet(self, tmp_paths, monkeypatch):
        monkeypatch.setattr(aw.tokens, "get_user_token",
                                       lambda email: ("TOK", "vault"))
        self._seed_audit(tmp_paths, [{
            "autopickup_id": "ap-1", "status": "drafted",
            "comment_id": 100, "bucket": 7463955, "todo_id": 9000,
        }])
        # Only the CB-System autopickup comment exists -- no human reply yet
        monkeypatch.setattr(aw, "_fetch_comments", lambda b, t, tok: [
            _comment(100, "<p>autopickup_id: ap-1</p>",
                                  creator="CB System",
                                  created_at="2026-06-09T10:00:00Z"),
        ])
        r = aw.scan_for_user("ali@colaberry.com")
        assert r["still_pending"] == 1
        assert r["newly_approved"] == 0
        assert "ap-1" not in aw._processed()  # not marked yet, can re-scan


# ── Disabled by default ──────────────────────────────────────────


class TestEnabledFlag:

    def test_scan_all_users_noop_when_disabled(self, monkeypatch):
        monkeypatch.setattr(aw, "ENABLED", False)
        r = aw.scan_all_users()
        assert r["status"] == "disabled"
