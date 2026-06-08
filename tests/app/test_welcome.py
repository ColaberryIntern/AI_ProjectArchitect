"""Unit tests for app/routers/welcome.py completeness helpers.

The page itself is HTML and exercised end-to-end manually; what matters
for correctness here is that the helpers (also used by the auth gate)
return the right answer for every combination of (MCP installed?,
Google grant?, BC grant?).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.routers import welcome
from execution.products.library import basecamp_oauth_token, google_oauth_token


def _user(*, mcp_token_issued_at=None, mcp_tokens=None,
          mcp_token_last_used_at=None,
          email="x@colaberry.com", display_name="X",
          user_id="usr-test"):
    return SimpleNamespace(
        user_id=user_id, email=email, display_name=display_name,
        mcp_token_issued_at=mcp_token_issued_at,
        mcp_tokens=list(mcp_tokens or []),
        mcp_token_last_used_at=mcp_token_last_used_at,
    )


# ── has_mcp_installed ──────────────────────────────────────────────────


def test_mcp_no_token_at_all():
    u = _user()
    assert welcome.has_mcp_installed(u) is False


def test_mcp_token_minted_no_device_seen():
    """Minted token but no device has phoned home -> NOT installed.
    Key requirement: catches users who minted but never finished install."""
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z",
        mcp_tokens=[{"hash": "a", "issued_at": "2026-06-06T12:00:00Z",
                     "last_used_at": None}],
    )
    assert welcome.has_mcp_installed(u) is False


def test_mcp_token_minted_and_device_seen():
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z",
        mcp_tokens=[{"hash": "a", "issued_at": "2026-06-06T12:00:00Z",
                     "last_used_at": "2026-06-06T12:05:00Z"}],
    )
    assert welcome.has_mcp_installed(u) is True


def test_mcp_legacy_single_token_field_counts():
    """Pre-mcp_tokens-migration users have the legacy
    mcp_token_last_used_at field set directly on the user."""
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z",
        mcp_tokens=[],
        mcp_token_last_used_at="2026-06-06T12:05:00Z",
    )
    assert welcome.has_mcp_installed(u) is True


def test_mcp_modern_user_only_has_mcp_tokens_entries_no_legacy_field():
    """Regression: Kes minted his token via the per-device flow that
    populates mcp_tokens but NOT the legacy mcp_token_issued_at field.
    His Claude Code phoned home (last_used_at set on the device entry)
    so he IS installed -- the page must reflect that. Previous bug
    bailed early when legacy field was None, missing this case
    entirely."""
    u = _user(
        mcp_token_issued_at=None,            # legacy field empty
        mcp_token_last_used_at=None,         # legacy field empty
        mcp_tokens=[{                        # modern multi-device entry
            "hash": "kes-hash",
            "label": "Windows laptop",
            "issued_at": "2026-06-08T15:46:41Z",
            "last_used_at": "2026-06-08T15:49:56Z",
            "revoked_at": None,
            "hostname": "Kesetebirhan",
        }],
    )
    assert welcome.has_mcp_installed(u) is True


def test_mcp_revoked_device_does_not_count():
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z",
        mcp_tokens=[{"hash": "a", "issued_at": "2026-06-06T12:00:00Z",
                     "last_used_at": "2026-06-06T12:05:00Z",
                     "revoked_at": "2026-06-06T13:00:00Z"}],
    )
    assert welcome.has_mcp_installed(u) is False


# ── has_google_grant ───────────────────────────────────────────────────


def test_google_grant_present(monkeypatch):
    u = _user()
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(return_value="some-rt"))
    assert welcome.has_google_grant(u) is True


def test_google_grant_absent(monkeypatch):
    u = _user()
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(return_value=None))
    assert welcome.has_google_grant(u) is False


def test_google_grant_exception_treated_as_absent(monkeypatch):
    """Vault hiccup mid-page-render shouldn't crash the page; treat as
    not connected so the user can attempt to (re)connect."""
    u = _user()
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(side_effect=RuntimeError("vault dead")))
    assert welcome.has_google_grant(u) is False


# ── has_bc_grant ───────────────────────────────────────────────────────


def test_bc_grant_present(monkeypatch):
    u = _user()
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value={"legacy": False,
                                                 "bc_user_email": "x@y"}))
    assert welcome.has_bc_grant(u) is True


def test_bc_grant_absent(monkeypatch):
    u = _user()
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value=None))
    assert welcome.has_bc_grant(u) is False


def test_bc_grant_legacy_does_not_count(monkeypatch):
    """Legacy paste-only tokens have no refresh; they break in 14 days.
    Force the user to upgrade via the OAuth flow."""
    u = _user()
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value={"legacy": True,
                                                 "bc_user_email": None}))
    assert welcome.has_bc_grant(u) is False


def test_bc_grant_exception_treated_as_absent(monkeypatch):
    u = _user()
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(side_effect=ConnectionError("net dead")))
    assert welcome.has_bc_grant(u) is False


# ── needs_setup composite ──────────────────────────────────────────────


@pytest.mark.parametrize("mcp,google,bc,expected_needs", [
    (False, False, False, True),
    (True, False, False, True),
    (False, True, False, True),
    (False, False, True, True),
    (True, True, False, True),
    (True, False, True, True),
    (False, True, True, True),
    (True, True, True, False),
])
def test_needs_setup_truth_table(monkeypatch, mcp, google, bc, expected_needs):
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z" if mcp else None,
        mcp_token_last_used_at="2026-06-06T12:05:00Z" if mcp else None,
    )
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(return_value="rt" if google else None))
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value=({"legacy": False,
                                                  "bc_user_email": "x@y"}
                                                 if bc else None)))
    assert welcome.needs_setup(u) is expected_needs


# ── next_step_path / next_step_label ───────────────────────────────────


def test_next_step_when_mcp_missing(monkeypatch):
    u = _user()
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(return_value="rt"))
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value={"legacy": False, "bc_user_email": "x@y"}))
    assert welcome.next_step_path(u) == "/profile/mcp-setup?return=welcome"
    assert welcome.next_step_label(u) == "Install MCP"


def test_next_step_when_google_missing(monkeypatch):
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z",
        mcp_token_last_used_at="2026-06-06T12:05:00Z",
    )
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(return_value=None))
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value={"legacy": False, "bc_user_email": "x@y"}))
    assert welcome.next_step_path(u) == "/profile/connect-google?return=welcome"
    assert welcome.next_step_label(u) == "Connect Google"


def test_next_step_when_bc_missing(monkeypatch):
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z",
        mcp_token_last_used_at="2026-06-06T12:05:00Z",
    )
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(return_value="rt"))
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value=None))
    assert welcome.next_step_path(u) == "/profile/connect-basecamp?return=welcome"
    assert welcome.next_step_label(u) == "Connect Basecamp"


def test_next_step_when_all_done(monkeypatch):
    u = _user(
        mcp_token_issued_at="2026-06-06T12:00:00Z",
        mcp_token_last_used_at="2026-06-06T12:05:00Z",
    )
    monkeypatch.setattr(google_oauth_token, "get_refresh_token_for_operator",
                        MagicMock(return_value="rt"))
    monkeypatch.setattr(basecamp_oauth_token, "get_grant_metadata",
                        MagicMock(return_value={"legacy": False, "bc_user_email": "x@y"}))
    assert welcome.next_step_path(u) == "/my-day/?welcome=1"
    assert welcome.next_step_label(u) == "Go to My Day"


# ── Video slot ─────────────────────────────────────────────────────────


def test_video_block_empty_when_unset(monkeypatch):
    monkeypatch.delenv("ONBOARDING_VIDEO_URL", raising=False)
    assert welcome._video_block() == ""


def test_video_block_renders_iframe_for_youtube(monkeypatch):
    monkeypatch.setenv("ONBOARDING_VIDEO_URL",
                       "https://www.youtube.com/embed/abc123")
    out = welcome._video_block()
    assert "<iframe" in out
    assert "abc123" in out


def test_video_block_renders_video_tag_for_static_url(monkeypatch):
    monkeypatch.setenv("ONBOARDING_VIDEO_URL", "/static/onboarding.mp4")
    out = welcome._video_block()
    assert "<video" in out
    assert "/static/onboarding.mp4" in out
