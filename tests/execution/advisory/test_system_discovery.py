"""Tests for the sequential, adaptive AI System Discovery."""

import json
import types

from execution.advisory.system_discovery import (
    PHASE_KEYS,
    PHASES,
    TOTAL_QUESTIONS,
    _clean_question,
    _fallback_question,
    generate_question,
    refine_idea,
)

_IDEA = "A booking and payments app for my hair salon that cuts no-shows"


def _canned():
    return json.dumps({
        "question": "How should your salon app confirm appointments?",
        "options": [
            {"label": "Manual confirm", "description": "Staff confirm each booking by hand."},
            {"label": "Auto reminders", "description": "App texts reminders and logs replies."},
            {"label": "Full auto-booking", "description": "App books, confirms, and rebooks no-shows."},
        ],
    })


# ─── framework shape ────────────────────────────────────────────────

def test_nine_dimensions_in_canonical_order():
    assert TOTAL_QUESTIONS == 9
    assert PHASE_KEYS == [
        "control", "intelligence", "data", "decision", "execution",
        "agents", "governance", "strategy", "differentiators",
    ]
    assert all(p.get("label") and p.get("focus") and p.get("fallback") for p in PHASES)


# ─── generate_question ──────────────────────────────────────────────

def test_generate_question_builds_from_llm(monkeypatch):
    import execution.llm_client as llm
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda **_kw: types.SimpleNamespace(content=_canned()))

    q = generate_question(_IDEA, PHASES[0], [])
    assert q["phase"] == "control"
    assert q["label"] == "Control & autonomy"
    assert "salon" in q["question"].lower()
    assert [o["letter"] for o in q["options"]] == ["A", "B", "C"]
    assert q["options"][0]["label"] == "Manual confirm"
    assert all(o["description"] for o in q["options"])


def test_generate_question_is_adaptive_passes_prior_choices(monkeypatch):
    """Prior answers are fed to the model so the next question builds on them."""
    import execution.llm_client as llm
    captured = {}

    def chat(**kw):
        captured["msg"] = kw["messages"][0]["content"]
        return types.SimpleNamespace(content=_canned())

    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", chat)

    prior = [{"label": "Automation & actions",
              "choice": {"label": "Full auto-booking", "description": "Books and rebooks automatically."}}]
    generate_question(_IDEA, PHASES[5], prior)
    assert "Full auto-booking" in captured["msg"]
    assert "Automation & actions" in captured["msg"]


def test_generate_question_falls_back_when_llm_off(monkeypatch):
    import execution.llm_client as llm
    monkeypatch.setattr(llm, "is_available", lambda: False)
    q = generate_question(_IDEA, PHASES[4], [])
    assert q["phase"] == "execution"
    assert len(q["options"]) == 3
    # uses the dimension's fallback question text
    assert q["question"] == PHASES[4]["fallback"][0]


def test_generate_question_short_idea_skips_llm(monkeypatch):
    import execution.llm_client as llm
    called = {"n": 0}

    def chat(**_kw):
        called["n"] += 1
        return types.SimpleNamespace(content=_canned())

    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", chat)

    q = generate_question("too short", PHASES[0], [])
    assert called["n"] == 0
    assert q["phase"] == "control"


# ─── _clean_question ────────────────────────────────────────────────

def test_clean_question_valid():
    raw = json.loads(_canned())
    q = _clean_question(raw, PHASES[0])
    assert q["question"].startswith("How should")
    assert [o["letter"] for o in q["options"]] == ["A", "B", "C"]


def test_clean_question_rejects_too_few_options():
    raw = {"question": "Q?", "options": [{"label": "x", "description": "y"}]}
    assert _clean_question(raw, PHASES[0]) is None


def test_clean_question_rejects_blank_description():
    raw = {"question": "Q?", "options": [
        {"label": "a", "description": ""},
        {"label": "b", "description": "ok"},
        {"label": "c", "description": "ok"},
    ]}
    assert _clean_question(raw, PHASES[0]) is None


def test_fallback_question_shape():
    q = _fallback_question(PHASES[2])
    assert q["phase"] == "data"
    assert [o["letter"] for o in q["options"]] == ["A", "B", "C"]
    assert all(o["label"] and o["description"] for o in q["options"])


# ─── refine_idea ────────────────────────────────────────────────────

def test_refine_idea_folds_chosen_capabilities():
    answers = [
        {"label": "Control & autonomy", "choice": {"label": "Act with guardrails", "description": "Handles routine, asks on risk."}},
        {"label": "Automation & actions", "choice": {"label": "Full auto-booking", "description": "Books and rebooks automatically."}},
    ]
    refined = refine_idea(_IDEA, answers)
    assert refined.startswith("Original idea: " + _IDEA)
    assert "Control & autonomy: Act with guardrails" in refined
    assert "Full auto-booking" in refined


def test_refine_idea_handles_empty():
    assert refine_idea("Some idea", []).startswith("Original idea: Some idea")
