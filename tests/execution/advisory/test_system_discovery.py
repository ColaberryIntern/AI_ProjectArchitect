"""Tests for the 9-phase AI System Discovery framework."""

import json
import types

from execution.advisory.system_discovery import (
    MIN_ANSWERS,
    PHASE_KEYS,
    PHASES,
    _coerce_questions,
    _run_discovery,
    generate_discovery_questions,
    refine_idea,
)


def _canned_llm(idea_keyword="salon"):
    """A well-formed 9-question response for the mocked LLM (compact shape)."""
    qs = []
    for p in PHASES:
        qs.append({
            "phase": p["key"],
            "question": f"For your {idea_keyword}, {p['axis'].lower()}?",
            "A": f"A-level for the {idea_keyword}.",
            "B": f"B-level for the {idea_keyword}.",
            "C": f"C-level for the {idea_keyword}.",
        })
    return json.dumps({"questions": qs})


# ─── framework shape ────────────────────────────────────────────────

def test_nine_phases_in_canonical_order():
    assert len(PHASES) == 9
    assert PHASE_KEYS == [
        "control", "intelligence", "data", "decision", "execution",
        "agents", "governance", "strategy", "differentiators",
    ]


# ─── generate (mocked LLM) ──────────────────────────────────────────

def test_generate_returns_nine_in_order_with_three_options(monkeypatch):
    import execution.llm_client as llm
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda **_kw: types.SimpleNamespace(content=_canned_llm()))

    qs = generate_discovery_questions("A booking and payments app for my hair salon")
    assert [q["phase"] for q in qs] == PHASE_KEYS
    for q in qs:
        assert q["question"]
        assert [o["letter"] for o in q["options"]] == ["A", "B", "C"]
        assert all(o["description"] for o in q["options"])
    # domain reference made it through
    assert "salon" in qs[0]["question"].lower()


def test_generate_falls_back_when_llm_off(monkeypatch):
    import execution.llm_client as llm
    monkeypatch.setattr(llm, "is_available", lambda: False)

    qs = generate_discovery_questions("A booking and payments app for my hair salon")
    assert [q["phase"] for q in qs] == PHASE_KEYS          # still exactly 9, in order
    assert all(len(q["options"]) == 3 for q in qs)


def test_generate_short_idea_uses_fallback(monkeypatch):
    import execution.llm_client as llm
    # Even if the LLM is "up", an idea under 20 chars never calls it.
    called = {"n": 0}

    def chat(**_kw):
        called["n"] += 1
        return types.SimpleNamespace(content=_canned_llm())

    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", chat)

    qs = generate_discovery_questions("too short")
    assert called["n"] == 0
    assert len(qs) == 9


# ─── _coerce: partial / malformed LLM output ────────────────────────

def test_coerce_fills_missing_and_malformed_phases():
    raw = {
        "questions": [
            # valid control question (compact shape)
            {"phase": "control", "question": "Who decides for your salon?",
             "A": "You approve each.", "B": "AI handles routine bookings.", "C": "AI runs it all."},
            # no descriptions -> nothing usable -> falls back entirely
            {"phase": "intelligence", "question": "How smart?"},
            # unknown phase -> ignored
            {"phase": "not_a_phase", "question": "?", "A": "x"},
        ]
    }
    qs = _coerce_questions(raw, "A booking app for my hair salon and spa")
    assert [q["phase"] for q in qs] == PHASE_KEYS          # all 9 present, in order
    assert qs[0]["question"] == "Who decides for your salon?"   # kept the valid LLM one
    assert qs[0]["options"][0]["description"] == "You approve each."
    assert qs[0]["options"][0]["label"] == "AI recommends"      # label from canonical anchor
    # intelligence had no usable descriptions -> full fallback question
    assert qs[1]["question"] == "How smart should the system be?"
    assert len(qs[1]["options"]) == 3


def test_coerce_none_is_all_fallback():
    qs = _coerce_questions(None, "An idea long enough to pass")
    assert len(qs) == 9
    assert all(len(q["options"]) == 3 for q in qs)


# ─── refine_idea ────────────────────────────────────────────────────

def test_refine_idea_folds_chosen_levels():
    questions = _coerce_questions(None, "A booking app for my hair salon")
    answers = {"control": "B", "execution": "C", "agents": "A", "data": "B", "governance": "C"}
    refined = refine_idea("A booking app for my hair salon", answers, questions)
    assert refined.startswith("Original idea: A booking app for my hair salon")
    assert "Control Model" in refined
    assert "Execution Level" in refined
    # only answered phases appear
    assert "Strategy Layer" not in refined


def test_refine_idea_handles_no_answers():
    refined = refine_idea("Some idea", {}, [])
    assert "Original idea: Some idea" in refined


def test_min_answers_is_five():
    assert MIN_ANSWERS == 5


# ─── background worker ──────────────────────────────────────────────

def test_run_discovery_persists_questions_and_flips_status(monkeypatch, tmp_path):
    import execution.advisory.advisory_state_manager as asm
    import execution.llm_client as llm

    monkeypatch.setattr(asm, "ADVISORY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda **_kw: types.SimpleNamespace(content=_canned_llm()))

    session = asm.initialize_session("A booking and payments app for my hair salon")
    session["discovery_status"] = "pending"
    asm.save_session(session)
    sid = session["session_id"]

    _run_discovery(sid, "A booking and payments app for my hair salon")

    reloaded = asm.load_session(sid)
    assert reloaded["discovery_status"] == "ready"
    assert len(reloaded["discovery_questions"]) == 9
    assert [q["phase"] for q in reloaded["discovery_questions"]] == PHASE_KEYS
