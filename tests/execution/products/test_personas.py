"""Unit tests for the prompt delivery personas."""
from execution.products.ops import personas as P


def test_personas_each_complete():
    assert len(P.PERSONAS) == 8
    ids = [p["id"] for p in P.PERSONAS]
    assert len(set(ids)) == 8  # unique
    for p in P.PERSONAS:
        for k in ("id", "label", "emoji", "blurb", "working_block"):
            assert p.get(k), f"persona {p.get('id')} missing {k}"
        # The working block must carry the section header so the prompt
        # structure stays stable when the persona changes.
        assert p["working_block"].startswith("## How I want you to work")


def test_default_persona_is_registered_and_is_copilot():
    assert P.DEFAULT_PERSONA == "copilot"
    assert any(p["id"] == "copilot" for p in P.PERSONAS)


def test_get_falls_back_to_default_for_unknown_or_none():
    assert P.get(None)["id"] == "copilot"
    assert P.get("")["id"] == "copilot"
    assert P.get("nope")["id"] == "copilot"
    assert P.get("visual")["id"] == "visual"


def test_is_valid():
    assert P.is_valid("visual")
    assert not P.is_valid("nope")
    assert not P.is_valid(None)


def test_visual_persona_requests_diagrams_and_browser():
    # The accessibility requirement (Karun): visuals auto-opened in a browser.
    block = P.working_block("visual").lower()
    assert "mermaid" in block
    assert "browser" in block


def test_visual_persona_is_an_interactive_decision_sheet():
    """The visual-first delivery is a fill-in-the-blanks HTML form that
    round-trips a ready-to-run prompt back into Claude Code — radios/toggles
    for choices, text boxes for open answers, checkboxes for Basecamp moves,
    and a 'Copy Claude Code prompt' button at top AND bottom. Locks in the
    2026-06-18 upgrade so a future edit can't quietly drop a gadget."""
    block = P.working_block("visual").lower()
    # Input gadgets for questions.
    assert "radio" in block          # two-answer toggle / segmented select
    assert "dropdown" in block       # few-answer select
    assert "text box" in block       # open-ended
    # Basecamp action checkboxes.
    assert "checkbox" in block
    assert "basecamp actions" in block
    assert "tag people" in block
    assert "add people" in block
    # The round-trip: a copy button at top and bottom, then paste-back executes.
    assert "copy claude code prompt" in block
    assert "top" in block and "bottom" in block
    assert "clipboard" in block
    assert "immediately" in block
    # It must NOT silently make changes on the first pass.
    assert "do not make any" in block or "do not make" in block


def test_visual_persona_is_professional_and_pre_decides():
    """The 2026-06-19 refinement: the sheet must read like a professional
    business one-pager (not a colorful kids' app), lead with a plain-English
    summary of the ticket, surface only consequential decisions (defaulting the
    rest), always let the operator type their OWN answer alongside the offered
    ones, and PRE-DECIDE the Basecamp actions instead of asking each time."""
    block = P.working_block("visual").lower()
    # Professional, not childish.
    assert "professional" in block
    assert "no neon" in block          # explicit guard against the old palette
    # Summary-first: a plain-English brief at the top.
    assert "what this is" in block
    assert "what you need to do" in block
    # Fewer questions; recommend a default the operator can override.
    assert "recommend" in block
    assert "assumed defaults" in block
    # Always offer a write-your-own escape hatch on preset questions.
    assert "other" in block
    assert "my own answer" in block or "type my own" in block
    # Pre-decide Basecamp actions rather than interrogating per move.
    assert "pre-tick" in block
    assert "pre-fill" in block


def test_checklist_persona_tags_decisions():
    assert "[DECISION]" in P.working_block("checklist")


def test_social_persona_drafts_only_and_is_channel_aware():
    """The Social Media delivery persona produces a publishable content package
    but never posts on its own, and refuses to fabricate claims."""
    assert P.is_valid("social")
    block = P.working_block("social").lower()
    # Draft-only safety: never auto-publish, never invent claims.
    assert "draft only" in block
    assert "never actually post" in block
    assert "[needs approval]" in block
    # Real content: per-channel copy with hooks, hashtags, and a calendar.
    assert "hook" in block
    assert "hashtag" in block
    assert "calendar" in block


def test_design_persona_delivers_a_spec_not_code():
    """The UI/UX Designer delivery persona returns a design spec — flows,
    wireframe descriptions, components, and an accessibility checklist — rather
    than finished code or claimed pixels."""
    assert P.is_valid("design")
    block = P.working_block("design").lower()
    assert "design spec" in block
    assert "not" in block and "code" in block  # explicitly not finished code
    assert "wireframe" in block
    assert "user flow" in block
    assert "accessibility" in block


def test_plain_persona_is_nontechnical_and_conversational():
    """The non-technical 'vibe' persona: plain language, no jargon, the AI
    handles the technical work, and it reads like a friendly conversation rather
    than a coding session — for the many non-coders going through the program."""
    assert P.is_valid("plain")
    assert P.get("plain")["id"] == "plain"
    block = P.working_block("plain").lower()
    # Plain, jargon-free, conversational tone.
    assert "plain language" in block
    assert "jargon" in block
    assert "conversation" in block or "conversational" in block
    # The AI does the technical work; the operator isn't asked to run commands.
    assert "run commands" in block
    assert "technical" in block
