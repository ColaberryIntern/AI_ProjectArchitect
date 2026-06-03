"""Tests for [Auth 1] multi-tenant data model.

Companies, users, item_approvals, access_scopes, and the per-tenant
filter on inventory.load_category.

Each test points TENANT_ROOT at a tmp_path so the file-backed store
is isolated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from execution.products.library import tenancy, inventory


@pytest.fixture
def isolated_tenancy(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    yield tmp_path


# ── Companies CRUD ────────────────────────────────────────────────


def test_seed_creates_colaberry_and_demo_tenant(isolated_tenancy):
    out = tenancy.seed_initial_companies_and_users()
    assert out["companies"] == 2
    assert tenancy.get_company("colaberry") is not None
    assert tenancy.get_company("demo-tenant") is not None


def test_seed_is_idempotent(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    out2 = tenancy.seed_initial_companies_and_users()
    assert out2["companies"] == 0
    assert out2["users"] == 0


def test_upsert_company_updates_existing(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    c = tenancy.get_company("colaberry")
    c.plan = "enterprise-plus"
    tenancy.upsert_company(c)
    assert tenancy.get_company("colaberry").plan == "enterprise-plus"


def test_deactivate_hides_company_by_default(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    tenancy.deactivate_company("demo-tenant")
    active = [c.company_id for c in tenancy.list_companies()]
    assert "demo-tenant" not in active
    inactive = [c.company_id for c in tenancy.list_companies(active_only=False)]
    assert "demo-tenant" in inactive


# ── Users ─────────────────────────────────────────────────────────


def test_seed_creates_ali_ram_karun_kes_and_demo(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    emails = [u.email for u in tenancy.list_users()]
    for e in ["ali@colaberry.com", "ram@colaberry.com",
                  "karun@colaberry.com", "kes@colaberry.com",
                  "demo@demo-tenant.local"]:
        assert e in emails


def test_user_lookup_by_email_case_insensitive(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    assert tenancy.get_user("Ali@Colaberry.COM") is not None


def test_list_users_filtered_by_company(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    colaberry_users = tenancy.list_users(company_id="colaberry")
    demo_users = tenancy.list_users(company_id="demo-tenant")
    assert len(colaberry_users) == 4
    assert len(demo_users) == 1


def test_has_role(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    assert tenancy.has_role(ali.user_id, "admin")
    assert not tenancy.has_role(ali.user_id, "platform_root")


def test_record_login_updates_timestamp(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    assert ali.last_login_at is None
    tenancy.record_login(ali.user_id)
    assert tenancy.get_user(ali.user_id).last_login_at is not None


# ── Item approvals ────────────────────────────────────────────────


def test_record_approval_persists(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    a = tenancy.record_approval(
        item_kind="library_asset", item_id="MCP Filesystem Server",
        category="mcp", company_id="colaberry",
        approved_by_user_id=ali.user_id, status="approved",
        visibility="same-company-only",
    )
    assert a.status == "approved"
    assert tenancy.get_approval("library_asset", "MCP Filesystem Server",
                                          "mcp", "colaberry") is not None


def test_two_companies_can_approve_same_item_independently(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    demo = tenancy.get_user("demo@demo-tenant.local")
    tenancy.record_approval("library_asset", "x", "skills", "colaberry",
                                    ali.user_id)
    tenancy.record_approval("library_asset", "x", "skills", "demo-tenant",
                                    demo.user_id)
    approvals = tenancy.list_approvals(item_id="x", category="skills")
    company_ids = sorted(a.company_id for a in approvals)
    assert company_ids == ["colaberry", "demo-tenant"]


def test_revoke_marks_withdrawn(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    tenancy.record_approval("library_asset", "y", "skills", "colaberry",
                                    ali.user_id)
    tenancy.revoke_approval("library_asset", "y", "skills", "colaberry",
                                    ali.user_id, notes="oops")
    a = tenancy.get_approval("library_asset", "y", "skills", "colaberry")
    assert a.status == "withdrawn"


# ── Visibility check ──────────────────────────────────────────────


def test_companies_with_access_same_company_only(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    tenancy.record_approval("library_asset", "z", "skills", "colaberry",
                                    ali.user_id, visibility="same-company-only")
    assert tenancy.companies_with_access("library_asset", "z", "skills", "colaberry")
    assert not tenancy.companies_with_access("library_asset", "z", "skills", "demo-tenant")


def test_companies_with_access_shared_public(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    tenancy.record_approval("library_asset", "z", "skills", "colaberry",
                                    ali.user_id, visibility="shared-public")
    assert tenancy.companies_with_access("library_asset", "z", "skills", "demo-tenant")


def test_companies_with_access_shared_with_allowlist(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    tenancy.record_approval("library_asset", "z", "skills", "colaberry",
                                    ali.user_id,
                                    visibility="shared-with-allowlist",
                                    shared_with=["demo-tenant"])
    assert tenancy.companies_with_access("library_asset", "z", "skills", "demo-tenant")
    assert not tenancy.companies_with_access("library_asset", "z", "skills", "other-tenant")


def test_withdrawn_approval_does_not_grant_access(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    tenancy.record_approval("library_asset", "z", "skills", "colaberry",
                                    ali.user_id, visibility="shared-public")
    tenancy.revoke_approval("library_asset", "z", "skills", "colaberry",
                                    ali.user_id)
    assert not tenancy.companies_with_access("library_asset", "z", "skills", "demo-tenant")


# ── Access scopes ────────────────────────────────────────────────


def test_grant_revoke_scope_round_trip(isolated_tenancy):
    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")
    demo = tenancy.get_user("demo@demo-tenant.local")
    tenancy.grant_scope(demo.user_id, "gmail", ali.user_id, "for testing")
    tenancy.grant_scope(demo.user_id, "calendar", ali.user_id)
    assert tenancy.current_scopes(demo.user_id) == {"gmail", "calendar"}
    tenancy.revoke_scope(demo.user_id, "calendar", ali.user_id, "rotated")
    assert tenancy.current_scopes(demo.user_id) == {"gmail"}


# ── Inventory filter by company ───────────────────────────────────


def test_filter_for_company_none_passes_all_rows(isolated_tenancy):
    rows = [{"name": "a"}, {"name": "b"}]
    assert inventory.filter_for_company(rows, "skills", None) == rows


def test_filter_for_company_includes_owned_and_approved(tmp_path, monkeypatch):
    # Isolate tenancy + the asset store
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    from execution.products.library import store
    monkeypatch.setattr(store, "LIB_ROOT", tmp_path / "lib")

    tenancy.seed_initial_companies_and_users()
    ali = tenancy.get_user("ali@colaberry.com")

    # asset_owned_by_colaberry: should be visible to colaberry
    store.upsert_metadata("global", "skills", "asset_owned_by_colaberry",
                                  owning_company_id="colaberry")
    # asset_owned_by_demo: should NOT be visible to colaberry by default
    store.upsert_metadata("global", "skills", "asset_owned_by_demo",
                                  owning_company_id="demo-tenant")
    # asset_shared_public: owned by demo but approved with shared-public
    store.upsert_metadata("global", "skills", "asset_shared_public",
                                  owning_company_id="demo-tenant")
    tenancy.record_approval("library_asset", "asset_shared_public", "skills",
                                    "demo-tenant", "demo-uid", "approved",
                                    visibility="shared-public")

    rows = [{"name": "asset_owned_by_colaberry"},
              {"name": "asset_owned_by_demo"},
              {"name": "asset_shared_public"}]
    visible_to_colaberry = [r["name"] for r in
                                     inventory.filter_for_company(rows, "skills", "colaberry")]
    assert "asset_owned_by_colaberry" in visible_to_colaberry
    assert "asset_owned_by_demo" not in visible_to_colaberry
    assert "asset_shared_public" in visible_to_colaberry


# ── Backfill ─────────────────────────────────────────────────────


def test_backfill_idempotent_and_seeds_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    from execution.products.library import tenancy_backfill, store
    monkeypatch.setattr(store, "LIB_ROOT", tmp_path / "lib")
    # Run twice — second run should be a no-op
    r1 = tenancy_backfill.run(dry_run=False)
    assert r1["companies_seeded"] == 2
    r2 = tenancy_backfill.run(dry_run=False)
    assert r2["companies_seeded"] == 0
    assert r2["users_seeded"] == 0
