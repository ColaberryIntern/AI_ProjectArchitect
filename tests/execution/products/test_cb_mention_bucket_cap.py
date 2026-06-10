"""Tests for the bucket-cap ranking + rotated-out surfacing.

Cap raised 50 → 100 and projects are now sorted by `updated_at` desc
before truncation, so the most-active buckets always rank first. Rotated-
out buckets are surfaced in the heartbeat (`buckets_rotated_out`) and
logged at WARNING. With per-bucket cursors, rotated-out buckets keep
their cursor and catch up on the next tick they're scanned — they don't
permanently lose mentions, just see higher latency.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from execution.products.ops import cb_mention_worker as cb


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.setattr(cb, "HEARTBEAT_PATH", tmp_path / "heartbeat.json")
    yield


def _project(i: int, updated_at: str = "") -> dict:
    return {"id": i, "name": f"P{i}", "updated_at": updated_at}


# ── ranking ───────────────────────────────────────────────────────


def test_buckets_ranked_by_updated_at_desc(monkeypatch):
    """Most-recently-active bucket is scanned first."""
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [
            _project(1, "2026-01-01T00:00:00Z"),  # oldest
            _project(2, "2026-06-09T00:00:00Z"),  # newest
            _project(3, "2026-03-15T00:00:00Z"),
        ],
    )
    scanned_order: list[int] = []
    def _capture(bucket, token, cutoff):
        scanned_order.append(bucket)
        return []
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions", _capture)

    cb.scan_for_user("ali@colaberry.com", max_buckets=5)
    assert scanned_order == [2, 3, 1], "expected most-recent-first ordering"


def test_buckets_missing_updated_at_sort_to_bottom(monkeypatch):
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [
            _project(1, ""),                       # no signal
            _project(2, "2026-06-09T00:00:00Z"),
            _project(3, None),                     # explicit None
        ],
    )
    scanned_order: list[int] = []
    def _capture(bucket, token, cutoff):
        scanned_order.append(bucket)
        return []
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions", _capture)

    cb.scan_for_user("ali@colaberry.com", max_buckets=5)
    assert scanned_order[0] == 2, "bucket with updated_at scans first"


# ── rotated-out reporting ────────────────────────────────────────


def test_rotated_out_buckets_are_reported_with_metadata(monkeypatch):
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("tok", "vault-oauth"))
    # 5 buckets, cap=3 → 2 should be rotated out.
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [
            _project(1, "2026-06-09T00:00:00Z"),
            _project(2, "2026-06-08T00:00:00Z"),
            _project(3, "2026-06-07T00:00:00Z"),
            _project(4, "2026-06-06T00:00:00Z"),
            _project(5, "2026-06-05T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions",
                        lambda bucket, token, cutoff: [])

    r = cb.scan_for_user("ali@colaberry.com", max_buckets=3)
    assert r["buckets_truncated"] == 2
    rotated = r["buckets_rotated_out"]
    assert len(rotated) == 2
    rotated_ids = {b["id"] for b in rotated}
    assert rotated_ids == {4, 5}
    # Each entry has name + updated_at
    for b in rotated:
        assert "name" in b and "updated_at" in b


def test_no_rotated_out_when_under_cap(monkeypatch):
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [_project(1, "2026-06-09T00:00:00Z"),
                       _project(2, "2026-06-08T00:00:00Z")],
    )
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions",
                        lambda bucket, token, cutoff: [])
    r = cb.scan_for_user("ali@colaberry.com", max_buckets=100)
    assert r["buckets_truncated"] == 0
    assert r["buckets_rotated_out"] == []


def test_rotated_out_payload_capped_at_25(monkeypatch):
    """Don't blow up the heartbeat file for an operator with 1000+ buckets."""
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("tok", "vault-oauth"))
    many = [_project(i, f"2026-06-{(i % 28) + 1:02d}T00:00:00Z")
            for i in range(200)]
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: many,
    )
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions",
                        lambda bucket, token, cutoff: [])

    r = cb.scan_for_user("ali@colaberry.com", max_buckets=10)
    # 190 truncated but only 25 reported in detail
    assert r["buckets_truncated"] == 190
    assert len(r["buckets_rotated_out"]) == 25


def test_warning_log_lists_first_rotated_buckets(monkeypatch, caplog):
    monkeypatch.setattr(cb.tokens, "get_user_token",
                        lambda uid: ("tok", "vault-oauth"))
    monkeypatch.setattr(
        "execution.products.ops.sync.discover_projects",
        lambda token: [
            _project(1, "2026-06-09T00:00:00Z"),
            _project(99, "2026-01-01T00:00:00Z"),  # rotated out
        ],
    )
    monkeypatch.setattr(cb, "_scan_bucket_for_mentions",
                        lambda bucket, token, cutoff: [])
    with caplog.at_level("WARNING"):
        cb.scan_for_user("ali@colaberry.com", max_buckets=1)
    msg = " | ".join(r.message for r in caplog.records)
    assert "99(2026-01-01)" in msg


# ── default cap raised from 50 to 100 ─────────────────────────────


def test_default_max_buckets_is_100():
    assert cb.MAX_BUCKETS == 100
