"""Tests for the account-id-as-bucket guard.

A BC URL packs both the account id and the bucket id as bare numbers
(`.../<account>/buckets/<bucket>/todos/<id>`), so callers routinely pass
the account id where a bc_project_id belongs -- which Basecamp answers
with an opaque 404 on a non-existent bucket. The guard
(`_account_id_as_bucket_error`) catches that BEFORE any network call and
returns a clear, actionable error. These tests pin that behavior across
every BC write/read tool that accepts bc_project_id.

No real BC traffic: `_bc_request` is monkeypatched to explode if reached,
proving the guard short-circuits before the network.
"""
from __future__ import annotations

import pytest

from execution.products.library import mcp_tools

# The default account id (matches _bc_account()'s fallback).
ACCOUNT_ID = int(mcp_tools._bc_account())
REAL_BUCKET = 47502609


class _FakeUser:
    def __init__(self):
        self.display_name = "Swati R"
        self.email = "swati@colaberry.com"
        self.user_id = "u-swati"
        self.personal_bc_project_id = 12345
        self.personal_bc_todolist_id = 678


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("guard should short-circuit before any BC call")
    monkeypatch.setattr(mcp_tools, "_bc_request", boom)


# ── The helper itself ───────────────────────────────────────────────


def test_helper_flags_account_id():
    err = mcp_tools._account_id_as_bucket_error(ACCOUNT_ID)
    assert err is not None
    assert err["error"] == "bc_project_id_is_account_id"
    assert "colaberry_find_project" in err["remediation"]


def test_helper_passes_real_bucket():
    assert mcp_tools._account_id_as_bucket_error(REAL_BUCKET) is None


@pytest.mark.parametrize("val", [0, None, "", "not-a-number"])
def test_helper_ignores_empty_or_garbage(val):
    # Empty/garbage ids are handled by each tool's own validation, not here.
    assert mcp_tools._account_id_as_bucket_error(val) is None


def test_helper_accepts_account_id_as_string():
    assert mcp_tools._account_id_as_bucket_error(str(ACCOUNT_ID)) is not None


# ── Each tool rejects the account id without hitting the network ─────


def test_post_progress_rejects_account_id():
    res = mcp_tools._tool_post_progress(_FakeUser(), {
        "bc_project_id": ACCOUNT_ID, "ticket_id": 9946498513,
        "html_body": "<p>hi</p>",
    })
    assert res["ok"] is False
    assert res["error"] == "bc_project_id_is_account_id"


def test_create_ticket_rejects_account_id():
    res = mcp_tools._tool_create_ticket(_FakeUser(), {
        "title": "x", "bc_project_id": ACCOUNT_ID, "todolist_id": 555,
    })
    assert res["ok"] is False
    assert res["error"] == "bc_project_id_is_account_id"


def test_close_ticket_rejects_account_id():
    res = mcp_tools._tool_close_ticket(_FakeUser(), {
        "bc_project_id": ACCOUNT_ID, "ticket_id": 1, "confidence": 0.99,
    })
    assert res["ok"] is False
    assert res["error"] == "bc_project_id_is_account_id"


def test_read_ticket_rejects_account_id():
    res = mcp_tools._tool_read_ticket(_FakeUser(), {
        "bc_project_id": ACCOUNT_ID, "ticket_id": 9946498513,
    })
    assert res["ok"] is False
    assert res["error"] == "bc_project_id_is_account_id"
