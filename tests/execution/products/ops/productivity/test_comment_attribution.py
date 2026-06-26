"""Comment classifier: AI-posted (markers/automation/attachments) vs genuinely hand-typed.
Specimens are the real patterns observed in live BC threads (2026-06-26)."""
from __future__ import annotations

from execution.products.ops.productivity.comment_attribution import (
    AI, HUMAN, classify_comment, tally_comments)


def test_via_claude_code_prefix_is_ai():
    assert classify_comment("Ali Muwwakkil", "<em>via Ali Muwwakkil's Claude Code</em> done") == AI
    assert classify_comment("Kes Delele", "via Kes's Claude Code  Epic 1 complete") == AI


def test_cb_system_author_is_ai():
    assert classify_comment("CB System", "anything at all") == AI


def test_doctrine_work_cards_are_ai():
    assert classify_comment("Ali Muwwakkil", "Outbound email attached per operating doctrine") == AI
    assert classify_comment("Ali Muwwakkil", "Auto-attached by sendWithBcAttach. Mandrill: <x>") == AI
    assert classify_comment("Ali Muwwakkil", "<!-- step:progress:abc123 --> update") == AI


def test_automation_posted_under_human_accounts_is_ai():
    # The system posts these THROUGH operators' accounts (no AI account exists for comments).
    for author, body in (
        ("Ali Muwwakkil", "Ali backlog snapshot at 2026-06-26T16:00 UTC. Counts: 71 total open."),
        ("Ali Muwwakkil", "CRITICAL RISK - this task is 7 days overdue. Marking on Launch Readiness Dashboard."),
        ("Ali Muwwakkil", "Reminder - this was due 2026-06-23. Quick status check: where are we on this?"),
        ("Ram Katamaraja", "CB System: automated response Anticipated goal: a refined email draft."),
        ("Ram Katamaraja", "CB System is starting this task now. Drafting a first-pass deliverable for Ali."),
        ("Ali Muwwakkil", "Friday, June 26, 2026 - daily dashboard snapshot"),
    ):
        assert classify_comment(author, body) == AI


def test_bare_attachment_dump_is_ai():
    assert classify_comment("Ali Muwwakkil", "design-e-colaberry-ds-LATEST-2026-06-24.html") == AI
    assert classify_comment("Ali Muwwakkil", "PROJECT_BUILDER_FLOW_MOCKUP.html  WEEK1_BRIEF.pdf") == AI


def test_hand_typed_comments_are_human():
    assert classify_comment("Aleem", "CB please explain this to me") == HUMAN
    assert classify_comment("Ali Muwwakkil", "Aleem Can we get this done today.") == HUMAN
    assert classify_comment("Ram Katamaraja", "Ali - Is there any action item for me?") == HUMAN
    assert classify_comment("Sohail Syed", "https://colaberry.online/p/x posted for today") == HUMAN
    # prose that mentions a file but is clearly typed (>=8 words) stays human
    assert classify_comment("Ali Muwwakkil",
                            "PROJECT_BUILDER_FLOW_MOCKUP.html uses Aleem's design and I am approving it now") == HUMAN
    assert classify_comment("Vinay Shankar", "The EDI option in SAM.gov is usually No [1][2]") == HUMAN


def test_custom_ai_actor_set():
    assert classify_comment("MyBot", "hi", ai_actors={"MyBot"}) == AI
    assert classify_comment("CB System", "hi", ai_actors={"MyBot"}) == HUMAN


def test_tally_per_person_ai_share():
    comments = [
        {"author": "Ali Muwwakkil", "content_text": "via Ali Muwwakkil's Claude Code  a"},
        {"author": "Ali Muwwakkil", "content_text": "CB System: automated response Anticipated goal: x"},
        {"author": "Ali Muwwakkil", "content_text": "Kes can you take a look at this please"},  # human
    ]
    row = tally_comments(comments)["Ali Muwwakkil"]
    assert row["ai"] == 2 and row["human"] == 1 and row["total"] == 3
    assert row["ai_share"] == round(2 / 3, 3)


def test_pure_manual_worker_is_zero():
    comments = [{"author": "Ram", "content_text": "Pls add this info to the form"} for _ in range(5)]
    assert tally_comments(comments)["Ram"]["ai_share"] == 0.0


def test_tally_accepts_objects_and_html_bodies():
    from types import SimpleNamespace
    comments = [SimpleNamespace(author="Kes Delele", content_html="<em>via Kes's Claude Code</em> x")]
    assert tally_comments(comments)["Kes Delele"]["ai_share"] == 1.0
