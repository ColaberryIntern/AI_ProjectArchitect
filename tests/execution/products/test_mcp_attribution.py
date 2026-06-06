"""Tests for the body-attribution helper that prefixes BC writes with
'via {Name}'s Claude Code'. Must be idempotency-marker-safe: Op 3
HTML-comment markers (<!-- step:KIND:HASH -->) stay at the very top
of the body so upstream scanners find them."""
from __future__ import annotations

import pytest

from execution.products.library import mcp_tools


class _FakeUser:
    def __init__(self, display_name="", email="x@colaberry.com"):
        self.display_name = display_name
        self.email = email


def test_attribution_prepends_to_empty_body_returns_empty():
    user = _FakeUser(display_name="Karun")
    assert mcp_tools._with_attribution(user, "") == ""
    assert mcp_tools._with_attribution(user, None) is None


def test_attribution_uses_display_name():
    user = _FakeUser(display_name="Karun Vellanki")
    out = mcp_tools._with_attribution(user, "<p>hello</p>")
    assert out.startswith("<p><em>via Karun Vellanki's Claude Code</em></p>\n")
    assert out.endswith("<p>hello</p>")


def test_attribution_falls_back_to_email_local_part():
    user = _FakeUser(display_name="", email="ram@colaberry.com")
    out = mcp_tools._with_attribution(user, "body")
    assert "via ram's Claude Code" in out


def test_attribution_falls_back_to_unknown_when_no_name_or_email():
    user = _FakeUser(display_name="", email="")
    out = mcp_tools._with_attribution(user, "body")
    assert "via Unknown's Claude Code" in out


def test_attribution_preserves_op3_marker_at_top():
    """Op 3 idempotency: '<!-- step:KIND:HASH -->' must remain at the
    very top of the body. The prefix slots in AFTER the marker."""
    user = _FakeUser(display_name="Karun")
    body = "<!-- step:DEMO:abc123 -->\n<p>card content</p>"
    out = mcp_tools._with_attribution(user, body)
    assert out.startswith("<!-- step:DEMO:abc123 -->\n")
    # The prefix appears right after the marker, before card content
    marker_end = len("<!-- step:DEMO:abc123 -->\n")
    assert out[marker_end:marker_end + len("<p><em>via Karun's Claude Code")] \
        == "<p><em>via Karun's Claude Code"
    # Original content survives
    assert "<p>card content</p>" in out


def test_attribution_preserves_multiple_leading_comments():
    user = _FakeUser(display_name="Kes")
    body = "<!-- step:A:1 --><!-- step:B:2 -->\n<p>body</p>"
    out = mcp_tools._with_attribution(user, body)
    assert out.startswith("<!-- step:A:1 --><!-- step:B:2 -->\n")
    assert "<p><em>via Kes's Claude Code" in out


def test_attribution_handles_comment_containing_gt_chars():
    """HTML comments can contain '>'; our regex must use non-greedy DOTALL
    matching so a marker like <!-- step:KIND:HASH -->  followed by content
    doesn't accidentally eat into card body."""
    user = _FakeUser(display_name="Kes")
    body = "<!-- step:DEMO:a>b -->\n<p>body</p>"
    out = mcp_tools._with_attribution(user, body)
    assert out.startswith("<!-- step:DEMO:a>b -->")
    assert "<p>body</p>" in out


def test_attribution_is_idempotent_safe_on_repost():
    """If somebody runs the helper twice (e.g. middleware bug), the prefix
    appears twice but the Op 3 marker still anchors the top — upstream
    dedupe still works. This is documentation-as-test."""
    user = _FakeUser(display_name="Karun")
    body = "<!-- step:X:1 -->\n<p>x</p>"
    once = mcp_tools._with_attribution(user, body)
    twice = mcp_tools._with_attribution(user, once)
    assert twice.startswith("<!-- step:X:1 -->\n")
    assert twice.count("via Karun's Claude Code") == 2
