"""Tests for [Workflow 1] per-company publish workflow + moderation queue.

Covers:
  - State machine transitions (legal + illegal)
  - submit_for_review / claim_for_review / decide_review semantics
  - Per-company queue isolation (Patriot author's draft does NOT
    appear in Colaberry's queue)
  - One item independently approved by N companies
  - Notification fan-out to admins
  - Daily-digest renderer roll-up
  - can_transition / can_review guards
"""

from __future__ import annotations

import pytest

from execution.products.library import tenancy, notifications


@pytest.fixture
def tenancy_root(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    return tmp_path


@pytest.fixture
def two_co(tenancy_root):
    """Colaberry (with admin Ali + contributor Karun) + Patriot (with admin Pat + author Joe)."""
    tenancy.upsert_company(tenancy.Company(
        company_id="colaberry", display_name="Colaberry", plan="enterprise",
    ))
    tenancy.upsert_company(tenancy.Company(
        company_id="patriot", display_name="Patriot Mfg", plan="standard",
    ))
    tenancy.upsert_user(tenancy.User(
        user_id="u_ali", email="ali@colaberry.com",
        company_id="colaberry", display_name="Ali", roles=["admin", "contributor"],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
    ))
    tenancy.upsert_user(tenancy.User(
        user_id="u_karun", email="karun@colaberry.com",
        company_id="colaberry", display_name="Karun", roles=["contributor"],
        google_subject=None, workspace_repo=None, created_at=tenancy._now(),
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
    return tenancy_root


# ── State machine ────────────────────────────────────────────────


def test_can_transition_legal_moves(two_co):
    # brand-new
    assert tenancy.can_transition(None, "submitted") is True
    assert tenancy.can_transition(None, "draft") is True
    # normal flow
    assert tenancy.can_transition("submitted", "under_review") is True
    assert tenancy.can_transition("under_review", "approved") is True
    assert tenancy.can_transition("under_review", "rejected") is True
    assert tenancy.can_transition("under_review", "changes_requested") is True
    # cycles
    assert tenancy.can_transition("changes_requested", "submitted") is True
    assert tenancy.can_transition("rejected", "draft") is True
    assert tenancy.can_transition("approved", "withdrawn") is True


def test_can_transition_blocks_illegal_moves(two_co):
    # cannot skip review
    assert tenancy.can_transition("draft", "approved") is False
    assert tenancy.can_transition("submitted", "approved") is False
    # cannot resurrect deprecated
    assert tenancy.can_transition("deprecated", "draft") is False
    # cannot self-loop on submitted (it's a no-op via the helper, not a transition)
    assert tenancy.can_transition("submitted", "submitted") is False


# ── submit_for_review ────────────────────────────────────────────


def test_submit_creates_first_approval_row(two_co):
    ev = tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe", notes="ready for review",
    )
    assert ev.status == "submitted"
    assert ev.company_id == "patriot"
    assert ev.approved_by_user_id == "u_joe"
    assert ev.notes == "ready for review"
    # transition logged
    history = tenancy.list_transitions(item_id="skill-x", company_id="patriot")
    assert len(history) == 1
    assert history[0]["from_status"] is None
    assert history[0]["to_status"] == "submitted"


def test_submit_is_idempotent_on_already_submitted(two_co):
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    # Second submit should be a no-op (idempotent), not raise
    ev2 = tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    assert ev2.status == "submitted"
    # Only one transition logged
    history = tenancy.list_transitions(item_id="skill-x", company_id="patriot")
    assert len(history) == 1


def test_submit_after_changes_requested_is_resubmission(two_co):
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.claim_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
    )
    tenancy.decide_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
        decision="changes_requested", notes="add docstring",
    )
    # Author resubmits — legal: changes_requested → submitted
    ev = tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe", notes="docstring added",
    )
    assert ev.status == "submitted"


# ── claim_for_review + decide_review ─────────────────────────────


def test_claim_moves_submitted_to_under_review(two_co):
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    ev = tenancy.claim_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
    )
    assert ev.status == "under_review"
    assert ev.approved_by_user_id == "u_pat"


def test_claim_blocked_on_already_under_review(two_co):
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.claim_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
    )
    with pytest.raises(ValueError):
        tenancy.claim_for_review(
            item_kind="library_asset", item_id="skill-x", category="skills",
            company_id="patriot", reviewer_user_id="u_pat",
        )


def test_decide_approve_sets_default_visibility(two_co):
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.claim_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
    )
    ev = tenancy.decide_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
        decision="approved",
    )
    assert ev.status == "approved"
    assert ev.visibility == "same-company-only"


def test_decide_approve_with_shared_public_visibility(two_co):
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.claim_for_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
    )
    ev = tenancy.decide_review(
        item_kind="library_asset", item_id="skill-x", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
        decision="approved", visibility="shared-public",
    )
    assert ev.visibility == "shared-public"


# ── Per-company queue isolation ─────────────────────────────────


def test_patriot_queue_does_not_include_colaberry_submissions(two_co):
    """Critical: queues are PER-COMPANY (not a shared queue)."""
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="patriot-skill", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="colaberry-skill", category="skills",
        company_id="colaberry", author_user_id="u_karun",
    )

    patriot_queue = tenancy.queue_for_company("patriot")
    patriot_ids = {a.item_id for a in patriot_queue}
    assert "patriot-skill" in patriot_ids
    assert "colaberry-skill" not in patriot_ids

    colaberry_queue = tenancy.queue_for_company("colaberry")
    colaberry_ids = {a.item_id for a in colaberry_queue}
    assert "colaberry-skill" in colaberry_ids
    assert "patriot-skill" not in colaberry_ids


def test_queue_sorted_by_submitted_at(two_co):
    import time
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="first", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    time.sleep(1.1)  # ensure different timestamp
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="second", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    queue = tenancy.queue_for_company("patriot")
    assert [a.item_id for a in queue] == ["first", "second"]


def test_queue_counts_breakdown(two_co):
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="a", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="b", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.claim_for_review(
        item_kind="library_asset", item_id="a", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
    )
    counts = tenancy.queue_counts("patriot")
    assert counts["submitted"] == 1
    assert counts["under_review"] == 1


# ── One item approved by multiple companies independently ───────


def test_one_item_approved_independently_by_two_companies(two_co):
    """Per [Workflow 1] criterion: same item, multiple ItemApproval rows."""
    # Colaberry's path
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="shared-skill", category="skills",
        company_id="colaberry", author_user_id="u_karun",
    )
    tenancy.claim_for_review(
        item_kind="library_asset", item_id="shared-skill", category="skills",
        company_id="colaberry", reviewer_user_id="u_ali",
    )
    tenancy.decide_review(
        item_kind="library_asset", item_id="shared-skill", category="skills",
        company_id="colaberry", reviewer_user_id="u_ali",
        decision="approved",
    )
    # Patriot's independent path
    tenancy.submit_for_review(
        item_kind="library_asset", item_id="shared-skill", category="skills",
        company_id="patriot", author_user_id="u_joe",
    )
    tenancy.claim_for_review(
        item_kind="library_asset", item_id="shared-skill", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
    )
    tenancy.decide_review(
        item_kind="library_asset", item_id="shared-skill", category="skills",
        company_id="patriot", reviewer_user_id="u_pat",
        decision="approved",
    )

    approvals = tenancy.list_approvals(item_id="shared-skill", status="approved")
    company_ids = sorted(a.company_id for a in approvals)
    assert company_ids == ["colaberry", "patriot"]


# ── can_review guard ─────────────────────────────────────────────


def test_can_review_requires_admin_role(two_co):
    karun = tenancy.get_user("karun@colaberry.com")
    ali = tenancy.get_user("ali@colaberry.com")
    assert tenancy.can_review(karun) is False  # contributor, not admin
    assert tenancy.can_review(ali) is True


def test_can_review_blocks_none(two_co):
    assert tenancy.can_review(None) is False


# ── Notification fan-out ─────────────────────────────────────────


def test_notify_submission_fans_out_to_company_admins(two_co):
    # Patriot has 1 admin (Pat), Joe submits → notify Pat (not Joe himself)
    n = notifications.notify_submission(
        company_id="patriot", author_user_id="u_joe",
        item_kind="library_asset", item_id="skill-x", category="skills",
    )
    assert n == 1
    unread = notifications.unread_for_user("u_pat", "patriot")
    assert len(unread) == 1
    assert unread[0]["kind"] == "submission"


def test_notify_submission_does_not_notify_self(two_co):
    """An admin submitting their own item shouldn't ping themselves."""
    n = notifications.notify_submission(
        company_id="patriot", author_user_id="u_pat",  # Pat is the admin
        item_kind="library_asset", item_id="skill-x", category="skills",
    )
    assert n == 0
    unread = notifications.unread_for_user("u_pat", "patriot")
    assert unread == []


def test_notify_decision_pings_author(two_co):
    notifications.notify_decision(
        company_id="patriot", reviewer_user_id="u_pat",
        author_user_id="u_joe", item_kind="library_asset",
        item_id="skill-x", category="skills",
        decision="approved", notes="great work",
    )
    unread = notifications.unread_for_user("u_joe", "patriot")
    assert len(unread) == 1
    assert "approved" in unread[0]["summary"]


def test_mark_all_read_clears_unread_count(two_co):
    notifications.notify_decision(
        company_id="patriot", reviewer_user_id="u_pat",
        author_user_id="u_joe", item_kind="library_asset",
        item_id="x", category="skills", decision="approved",
    )
    assert notifications.unread_count_for_user("u_joe", "patriot") == 1
    n = notifications.mark_all_read("u_joe", "patriot")
    assert n == 1
    assert notifications.unread_count_for_user("u_joe", "patriot") == 0


# ── Daily-digest renderer ───────────────────────────────────────


def test_render_daily_digest_groups_by_target(two_co):
    notifications.notify_submission(
        company_id="patriot", author_user_id="u_joe",
        item_kind="library_asset", item_id="a", category="skills",
    )
    notifications.notify_decision(
        company_id="patriot", reviewer_user_id="u_pat",
        author_user_id="u_joe", item_kind="library_asset",
        item_id="b", category="skills", decision="approved",
    )
    import time
    today = time.strftime("%Y-%m-%d", time.gmtime())
    out_path = notifications.render_daily_digest("patriot", today)
    assert out_path  # file written
    body = open(out_path, encoding="utf-8").read()
    assert "Patriot Mfg" not in body  # uses company_id in header
    assert "patriot" in body
    assert "For Pat" in body or "For Joe" in body
