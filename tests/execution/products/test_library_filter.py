"""Tests for [Library 1] per-tenant approval filter
and [Library 2] identity + workspace switcher.

[Library 1] guarantees:
  - filter_for_company narrows by (owning_company OR has-approval OR shared-public)
  - approved-by-company query narrows to items a named company has approved
  - per-item _approving_companies enrichment lists every company that approved

[Library 2] guarantees:
  - _scope() defaults: anon → "all", logged-in → "my-company"
  - explicit ?scope= overrides
  - _viewer_company_id() returns None for anon and for scope=all
  - _viewer_company_id() returns user.company_id for my-company / mine

Tests isolate tenancy + store roots via tmp_path so they never touch
the developer's real workspace data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from execution.products.library import inventory, tenancy


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def tenancy_root(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    return tmp_path


@pytest.fixture
def two_companies(tenancy_root):
    """colaberry + patriot tenants, each with one user."""
    tenancy.upsert_company(tenancy.Company(
        company_id="colaberry", display_name="Colaberry", plan="enterprise",
        default_visibility="same-company-only", is_active=True,
    ))
    tenancy.upsert_company(tenancy.Company(
        company_id="patriot", display_name="Patriot Mfg", plan="standard",
        default_visibility="same-company-only", is_active=True,
    ))
    tenancy.upsert_user(tenancy.User(
        user_id="u_ali", email="ali@colaberry.com",
        company_id="colaberry", display_name="Ali", roles=["admin"],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
        is_active=True,
    ))
    tenancy.upsert_user(tenancy.User(
        user_id="u_pat", email="ops@patriot.com",
        company_id="patriot", display_name="Pat", roles=[],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
        is_active=True,
    ))
    return tenancy_root


# ── [Library 1] filter_for_company ────────────────────────────────


def test_filter_returns_all_rows_for_anon_viewer(two_companies):
    """No viewer_company_id (anon) → no filter applied (legacy/open mode)."""
    rows = [{"name": "skill-a"}, {"name": "skill-b"}]
    out = inventory.filter_for_company(rows, "skills", None)
    assert out == rows


def test_filter_keeps_only_items_company_can_see(two_companies, monkeypatch):
    """Approval-gated items: patriot user only sees items patriot has approved
    OR items approved with visibility=shared-public."""
    # patriot has approved skill-a; skill-b is colaberry-only
    tenancy.record_approval(
        item_kind="library_asset", item_id="skill-a", category="skills",
        company_id="patriot", approved_by_user_id="u_pat",
        status="approved", visibility="same-company-only",
    )
    tenancy.record_approval(
        item_kind="library_asset", item_id="skill-b", category="skills",
        company_id="colaberry", approved_by_user_id="u_ali",
        status="approved", visibility="same-company-only",
    )

    # store.get_metadata defaults owning_company_id="colaberry"
    rows = [{"name": "skill-a"}, {"name": "skill-b"}]
    out = inventory.filter_for_company(rows, "skills", "patriot")
    names = [r["name"] for r in out]
    assert "skill-a" in names  # patriot has own approval row
    assert "skill-b" not in names  # only colaberry approved


def test_filter_includes_shared_public_items(two_companies):
    """Items approved with visibility=shared-public are visible to all tenants."""
    tenancy.record_approval(
        item_kind="library_asset", item_id="skill-public", category="skills",
        company_id="colaberry", approved_by_user_id="u_ali",
        status="approved", visibility="shared-public",
    )
    rows = [{"name": "skill-public"}]
    out = inventory.filter_for_company(rows, "skills", "patriot")
    assert len(out) == 1


def test_filter_respects_shared_with_allowlist(two_companies):
    """shared-with-allowlist visibility narrows to listed companies only."""
    tenancy.record_approval(
        item_kind="library_asset", item_id="skill-list", category="skills",
        company_id="colaberry", approved_by_user_id="u_ali",
        status="approved", visibility="shared-with-allowlist",
        shared_with=["patriot"],
    )
    rows = [{"name": "skill-list"}]
    assert len(inventory.filter_for_company(rows, "skills", "patriot")) == 1
    # A third tenant not in allowlist sees nothing
    tenancy.upsert_company(tenancy.Company(
        company_id="other", display_name="Other Co", plan="free",
        default_visibility="same-company-only", is_active=True,
    ))
    assert len(inventory.filter_for_company(rows, "skills", "other")) == 0


def test_filter_keeps_items_owned_by_viewer_company(two_companies, monkeypatch):
    """Owning company always sees its own items even without approval rows."""
    # Patch store.get_metadata to claim skill-pat is owned by patriot
    from execution.products.library import store

    @dataclass
    class FakeMeta:
        owning_company_id: str = "patriot"
        vetted: bool = False

    monkeypatch.setattr(store, "get_metadata",
                                  lambda ws, cat, aid: FakeMeta())
    rows = [{"name": "skill-pat"}]
    out = inventory.filter_for_company(rows, "skills", "patriot")
    assert len(out) == 1


# ── [Library 1] approval enumeration ──────────────────────────────


def test_list_approvals_filters_by_company(two_companies):
    tenancy.record_approval(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="colaberry", approved_by_user_id="u_ali",
        status="approved",
    )
    tenancy.record_approval(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", approved_by_user_id="u_pat",
        status="approved",
    )
    approvals = tenancy.list_approvals(
        item_kind="library_asset", item_id="skill-x", category="skills",
        status="approved",
    )
    company_ids = sorted(a.company_id for a in approvals)
    assert company_ids == ["colaberry", "patriot"]


def test_revoked_approval_no_longer_in_status_approved(two_companies):
    tenancy.record_approval(
        item_kind="library_asset", item_id="skill-y", category="skills",
        company_id="patriot", approved_by_user_id="u_pat",
        status="approved",
    )
    tenancy.revoke_approval(
        item_kind="library_asset", item_id="skill-y", category="skills",
        company_id="patriot", revoked_by_user_id="u_pat",
    )
    rows = inventory.filter_for_company([{"name": "skill-y"}], "skills", "patriot")
    assert rows == []


# ── [Library 2] _scope() + _viewer_company_id() resolvers ─────────


def _import_router_helpers():
    """Import the router resolvers without running the FastAPI app setup."""
    from app.routers import library as lib_router
    return lib_router


class _FakeRequest:
    def __init__(self, qp: dict | None = None):
        self.query_params = qp or {}
        self.cookies = {}


def test_scope_defaults_to_all_for_anon(two_companies):
    r = _import_router_helpers()
    req = _FakeRequest()
    assert r._scope(req, None) == "all"


def test_scope_defaults_to_my_company_for_logged_in_user(two_companies):
    r = _import_router_helpers()
    req = _FakeRequest()
    user = tenancy.get_user("ali@colaberry.com")
    assert r._scope(req, user) == "my-company"


def test_scope_explicit_query_overrides_default(two_companies):
    r = _import_router_helpers()
    user = tenancy.get_user("ali@colaberry.com")
    assert r._scope(_FakeRequest({"scope": "all"}), user) == "all"
    assert r._scope(_FakeRequest({"scope": "mine"}), user) == "mine"
    assert r._scope(_FakeRequest({"scope": "my-company"}), user) == "my-company"


def test_scope_ignores_unknown_values(two_companies):
    r = _import_router_helpers()
    user = tenancy.get_user("ali@colaberry.com")
    # garbage value falls back to default (logged-in → my-company)
    assert r._scope(_FakeRequest({"scope": "everything"}), user) == "my-company"


def test_viewer_company_id_none_for_anon(two_companies):
    r = _import_router_helpers()
    assert r._viewer_company_id(None, "all") is None
    assert r._viewer_company_id(None, "my-company") is None


def test_viewer_company_id_none_for_scope_all(two_companies):
    """scope=all explicitly opts OUT of company filtering even when logged in."""
    r = _import_router_helpers()
    user = tenancy.get_user("ali@colaberry.com")
    assert r._viewer_company_id(user, "all") is None


def test_viewer_company_id_uses_user_company_for_my_company(two_companies):
    r = _import_router_helpers()
    user = tenancy.get_user("ali@colaberry.com")
    assert r._viewer_company_id(user, "my-company") == "colaberry"
    assert r._viewer_company_id(user, "mine") == "colaberry"
