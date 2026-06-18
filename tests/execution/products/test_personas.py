"""Unit tests for the prompt delivery personas."""
from execution.products.ops import personas as P


def test_five_personas_each_complete():
    assert len(P.PERSONAS) == 5
    ids = [p["id"] for p in P.PERSONAS]
    assert len(set(ids)) == 5  # unique
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


def test_checklist_persona_tags_decisions():
    assert "[DECISION]" in P.working_block("checklist")
