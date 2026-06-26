"""Pure comment-scan tally: window filtering, actor exclusion, per-person AI share."""
from __future__ import annotations

from datetime import datetime, timezone

from execution.products.ops.productivity.comment_scan import tally_threads

NOW = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
SINCE = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


def _c(author, body, created):
    return {"author": author, "content_text": body, "created_at": created}


def test_window_filters_old_comments():
    comments = [
        _c("Ali Muwwakkil", "via Ali Muwwakkil's Claude Code  a", "2026-06-25T10:00:00Z"),
        _c("Ali Muwwakkil", "via Ali Muwwakkil's Claude Code  b", "2026-06-10T10:00:00Z"),  # too old
    ]
    t = tally_threads(comments, since=SINCE)
    assert t["Ali Muwwakkil"]["total"] == 1
    assert t["Ali Muwwakkil"]["ai_share"] == 1.0


def test_actor_excluded_from_rows():
    comments = [
        _c("CB System", "auto stuff", "2026-06-25T10:00:00Z"),
        _c("Sohail Syed", "posted for today", "2026-06-25T10:00:00Z"),
    ]
    t = tally_threads(comments, since=SINCE, exclude={"CB System"})
    assert "CB System" not in t
    assert t["Sohail Syed"]["ai_share"] == 0.0


def test_mixed_authorship_share_excludes_ambient():
    comments = [
        _c("Ali Muwwakkil", "via Ali Muwwakkil's Claude Code  x", "2026-06-25T10:00:00Z"),
        _c("Ali Muwwakkil", "Outbound email attached per operating doctrine", "2026-06-25T11:00:00Z"),
        _c("Ali Muwwakkil", "hand typed note to the team", "2026-06-25T12:00:00Z"),
        _c("Ali Muwwakkil", "Ali backlog snapshot at 2026-06-25T12:00 UTC", "2026-06-25T12:30:00Z"),
    ]
    row = tally_threads(comments, since=SINCE)["Ali Muwwakkil"]
    assert (row["ai"], row["human"], row["ambient"]) == (2, 1, 1)
    assert row["total"] == 3 and row["ai_share"] == round(2 / 3, 3)  # ambient excluded


def test_empty_is_empty():
    assert tally_threads([], since=SINCE) == {}
