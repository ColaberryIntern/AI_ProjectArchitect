"""Tests for [Workflow 2] cross-company visibility + follow-author.

Covers:
  - Default same-company-only behaviour for new tenants
  - Cross-company opt-in via allow_cross_company_shares flag
  - upgrade_item_visibility (admin bulk action) guarded by company flag
  - can_publish_cross_company permission check
  - can_follow_author rules (same-co always; cross-co requires inbound_follows)
  - follow_author / unfollow_author append-only log
  - is_following / followers_of state collapse
"""

from __future__ import annotations

import pytest

from execution.products.library import tenancy, inventory


@pytest.fixture
def tenancy_root(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    return tmp_path


@pytest.fixture
def co_open(tenancy_root):
    """Colaberry-the-public: allows cross-co shares + inbound follows."""
    tenancy.upsert_company(tenancy.Company(
        company_id="colaberry", display_name="Colaberry", plan="enterprise",
        allow_cross_company_shares=True, allow_inbound_follows=True,
    ))
    tenancy.upsert_user(tenancy.User(
        user_id="u_ali", email="ali@colaberry.com",
        company_id="colaberry", display_name="Ali", roles=["admin"],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
    ))
    return tenancy_root


@pytest.fixture
def co_closed(co_open):
    """Patriot: defaults (no cross-co shares, no inbound follows)."""
    tenancy.upsert_company(tenancy.Company(
        company_id="patriot", display_name="Patriot Mfg", plan="standard",
        allow_cross_company_shares=False, allow_inbound_follows=False,
    ))
    tenancy.upsert_user(tenancy.User(
        user_id="u_pat", email="pat@patriot.com",
        company_id="patriot", display_name="Pat", roles=["admin"],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
    ))
    tenancy.upsert_user(tenancy.User(
        user_id="u_joe", email="joe@patriot.com",
        company_id="patriot", display_name="Joe", roles=["contributor"],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
    ))
    return co_open


# ── Default tenant visibility ────────────────────────────────────


def test_new_company_defaults_to_same_company_only(co_closed):
    co = tenancy.get_company("patriot")
    assert co.default_visibility == "same-company-only"
    # Both new flags default to safe values
    assert co.allow_cross_company_shares is False
    assert co.allow_inbound_follows is False


def test_can_publish_cross_company_respects_company_flag(co_closed):
    open_co = tenancy.get_company("colaberry")
    closed_co = tenancy.get_company("patriot")
    assert tenancy.can_publish_cross_company(open_co) is True
    assert tenancy.can_publish_cross_company(closed_co) is False
    assert tenancy.can_publish_cross_company(None) is False


# ── upgrade_item_visibility ──────────────────────────────────────


def _seed_approval(item_id, company_id, user_id, visibility="same-company-only"):
    return tenancy.record_approval(
        item_kind="library_asset", item_id=item_id, category="skills",
        company_id=company_id, approved_by_user_id=user_id,
        status="approved", visibility=visibility,
    )


def test_upgrade_to_shared_public_when_allowed(co_closed):
    _seed_approval("skill-x", "colaberry", "u_ali")
    ev = tenancy.upgrade_item_visibility(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="colaberry", admin_user_id="u_ali",
        new_visibility="shared-public", notes="useful for everyone",
    )
    assert ev.visibility == "shared-public"


def test_upgrade_blocked_when_company_disallows(co_closed):
    """Patriot has allow_cross_company_shares=False — upgrade must fail."""
    _seed_approval("skill-x", "patriot", "u_pat")
    with pytest.raises(PermissionError) as exc:
        tenancy.upgrade_item_visibility(
            item_kind="library_asset", item_id="skill-x", category="skills",
            company_id="patriot", admin_user_id="u_pat",
            new_visibility="shared-public",
        )
    assert "cross-company shares" in str(exc.value)


def test_upgrade_blocks_unapproved_items(co_closed):
    """You can't share what's not yet approved."""
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="colaberry", author_user_id="u_ali",
    )
    with pytest.raises(ValueError) as exc:
        tenancy.upgrade_item_visibility(
            item_kind="library_asset", item_id="skill-x", category="skills",
            company_id="colaberry", admin_user_id="u_ali",
            new_visibility="shared-public",
        )
    assert "must be approved" in str(exc.value)


def test_downgrade_to_same_company_does_not_need_flag(co_closed):
    """Going from shared-public back to same-company-only is always allowed."""
    _seed_approval("skill-x", "patriot", "u_pat")
    # Even though Patriot disallows cross-co, going TIGHTER is fine
    ev = tenancy.upgrade_item_visibility(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", admin_user_id="u_pat",
        new_visibility="same-company-only", notes="walking it back",
    )
    assert ev.visibility == "same-company-only"


# ── Visibility behaviour end-to-end via filter_for_company ──────


def test_shared_public_item_appears_in_other_tenant_library(co_closed):
    """The actual cross-tenant payoff: Colaberry approves shared-public,
    Patriot user sees it via filter_for_company."""
    _seed_approval("shared-skill", "colaberry", "u_ali",
                          visibility="shared-public")
    rows = inventory.filter_for_company(
        [{"name": "shared-skill"}], "skills", "patriot",
    )
    assert len(rows) == 1


def test_same_co_item_does_not_leak_across_tenants(co_closed):
    _seed_approval("private-skill", "colaberry", "u_ali",
                          visibility="same-company-only")
    rows = inventory.filter_for_company(
        [{"name": "private-skill"}], "skills", "patriot",
    )
    assert rows == []


def test_allowlist_visibility_lets_listed_tenants_see(co_closed):
    tenancy.record_approval(
        item_kind="library_asset", item_id="ally-skill", category="skills",
        company_id="colaberry", approved_by_user_id="u_ali",
        status="approved", visibility="shared-with-allowlist",
        shared_with=["patriot"],
    )
    assert len(inventory.filter_for_company(
        [{"name": "ally-skill"}], "skills", "patriot",
    )) == 1
    # An unlisted tenant doesn't see it
    tenancy.upsert_company(tenancy.Company(
        company_id="outsider", display_name="Outsider Co", plan="free",
    ))
    assert inventory.filter_for_company(
        [{"name": "ally-skill"}], "skills", "outsider",
    ) == []


# ── Follow-author rules ──────────────────────────────────────────


def test_can_follow_same_company_always(co_closed):
    karun = tenancy.upsert_user(tenancy.User(
        user_id="u_karun", email="karun@colaberry.com",
        company_id="colaberry", display_name="Karun", roles=["contributor"],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
    ))
    ali = tenancy.get_user("ali@colaberry.com")
    provenance = {"author_email": ali.email, "author_company": "colaberry"}
    # Karun (colaberry) following Ali (colaberry) — same-co, always OK
    assert tenancy.can_follow_author(karun, provenance) is True


def test_can_follow_cross_co_requires_inbound_follows(co_closed):
    pat = tenancy.get_user("pat@patriot.com")
    ali = tenancy.get_user("ali@colaberry.com")
    # Pat (patriot) following Ali (colaberry, allow_inbound_follows=True) — OK
    provenance_ali = {"author_email": ali.email, "author_company": "colaberry"}
    assert tenancy.can_follow_author(pat, provenance_ali) is True
    # Ali following Pat (patriot, allow_inbound_follows=False) — blocked
    provenance_pat = {"author_email": pat.email, "author_company": "patriot"}
    assert tenancy.can_follow_author(ali, provenance_pat) is False


def test_can_follow_anon_viewer_blocked(co_closed):
    provenance = {"author_email": "ali@colaberry.com", "author_company": "colaberry"}
    assert tenancy.can_follow_author(None, provenance) is False


# ── Follow event log ────────────────────────────────────────────


def test_follow_then_unfollow_collapses_to_unfollow(co_closed):
    tenancy.follow_author("u_pat", "ali@colaberry.com")
    assert tenancy.is_following("u_pat", "ali@colaberry.com") is True
    tenancy.unfollow_author("u_pat", "ali@colaberry.com")
    assert tenancy.is_following("u_pat", "ali@colaberry.com") is False


def test_is_following_collapse_most_recent_wins(co_closed):
    """Follow, unfollow, follow → currently following."""
    tenancy.follow_author("u_pat", "ali@colaberry.com")
    tenancy.unfollow_author("u_pat", "ali@colaberry.com")
    tenancy.follow_author("u_pat", "ali@colaberry.com")
    assert tenancy.is_following("u_pat", "ali@colaberry.com") is True


def test_followers_of_returns_active_followers_only(co_closed):
    tenancy.follow_author("u_pat", "ali@colaberry.com")
    tenancy.follow_author("u_joe", "ali@colaberry.com")
    tenancy.unfollow_author("u_pat", "ali@colaberry.com")
    followers = tenancy.followers_of("ali@colaberry.com")
    assert followers == ["u_joe"]


def test_follow_email_normalisation_case_insensitive(co_closed):
    tenancy.follow_author("u_pat", "Ali@Colaberry.COM")
    assert tenancy.is_following("u_pat", "ali@colaberry.com") is True
    assert tenancy.is_following("u_pat", "ALI@COLABERRY.COM") is True
