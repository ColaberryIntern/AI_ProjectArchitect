"""Tests for idea-aware tailoring of the advisory intake questions."""

import json
import types

from execution.advisory.question_tailor import (
    _sanitize,
    apply_tailoring,
    tailor_questions,
)


# ─── tailor_questions: fallback safety ──────────────────────────────

def test_tailor_returns_empty_for_short_idea():
    assert tailor_questions("") == {}
    assert tailor_questions("  ") == {}
    assert tailor_questions("ab") == {}


def test_tailor_returns_empty_when_llm_unavailable(monkeypatch):
    import execution.llm_client as llm
    monkeypatch.setattr(llm, "is_available", lambda: False)
    assert tailor_questions("A booking app for my salon") == {}


def test_tailor_returns_empty_when_chat_raises(monkeypatch):
    import execution.llm_client as llm

    def boom(**_kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", boom)
    assert tailor_questions("A booking app for my salon") == {}


# ─── tailor_questions: happy path (mocked LLM) ──────────────────────

def test_tailor_questions_with_mocked_chat(monkeypatch):
    import execution.llm_client as llm

    canned = json.dumps({
        "q1_business_overview": {
            "text": "Tell us about your salon and how booking works today",
            "help_text": "Your salon, in your words",
            "examples": [
                "We run a 3-chair hair salon",
                "We take walk-ins and online bookings",
                "Nail & lash studio, mostly regulars",
            ],
        },
        "q4_bottlenecks": {"examples": ["Too many no-shows", "Phone tag for appointments", "Double-booked chairs"]},
        "q5_customer_journey": {"examples": ["Instagram DMs to book", "Walk-ins", "Regulars rebook in person"]},
        "q8_manual_processes": {"examples": ["Texting reminders by hand", "Paper appointment book", "Chasing deposits"]},
        "q10_success_vision": {"examples": ["Half the no-shows", "Fully booked weeks", "Clients self-book online"]},
    })

    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat", lambda **_kw: types.SimpleNamespace(content=canned))

    out = tailor_questions("A booking + payments app for my salon")

    assert set(out) == {
        "q1_business_overview", "q4_bottlenecks", "q5_customer_journey",
        "q8_manual_processes", "q10_success_vision",
    }
    assert "salon" in out["q1_business_overview"]["text"].lower()
    assert out["q1_business_overview"]["help_text"]
    assert len(out["q4_bottlenecks"]["examples"]) == 3
    # Non-q1 questions never carry reworded text.
    assert "text" not in out["q4_bottlenecks"]


# ─── _sanitize ──────────────────────────────────────────────────────

def test_sanitize_drops_unknown_malformed_and_caps_examples():
    raw = {
        "q1_business_overview": {
            "text": "Tell us about your salon",
            "help_text": "How it runs today",
            "examples": ["We run a hair salon", "We do nails", "Barbershop", "fourth gets dropped"],
        },
        # q4 is NOT a tailor_text question -> its "text" must be discarded.
        "q4_bottlenecks": {"text": "should be dropped", "examples": ["No-shows", "Phone tag"]},
        "q_unknown": {"examples": ["x"]},          # unknown id -> dropped
        "q5_customer_journey": "not a dict",         # malformed -> dropped
        "q8_manual_processes": {"examples": []},     # empty -> no usable fields -> dropped
    }
    out = _sanitize(raw)

    assert set(out) == {"q1_business_overview", "q4_bottlenecks"}
    assert out["q1_business_overview"]["text"] == "Tell us about your salon"
    assert out["q1_business_overview"]["help_text"] == "How it runs today"
    assert len(out["q1_business_overview"]["examples"]) == 3
    assert "text" not in out["q4_bottlenecks"]
    assert out["q4_bottlenecks"]["examples"] == ["No-shows", "Phone tag"]


def test_sanitize_non_dict_returns_empty():
    assert _sanitize(None) == {}
    assert _sanitize(["a", "b"]) == {}
    assert _sanitize("nope") == {}


# ─── apply_tailoring (pure overlay) ─────────────────────────────────

def test_apply_tailoring_overlays_text_and_examples():
    static_q1 = {
        "id": "q1_business_overview",
        "text": "What does your business do and what industry are you in?",
        "help_text": "Tell us about your company and market.",
        "examples": ["logistics", "saas", "healthcare"],
    }
    tailored = {"q1_business_overview": {"text": "Tell us about your salon", "examples": ["x", "y", "z"]}}

    merged = apply_tailoring(static_q1, tailored)
    assert merged["text"] == "Tell us about your salon"
    assert merged["examples"] == ["x", "y", "z"]
    # help_text not overridden -> retained from the static question.
    assert merged["help_text"] == "Tell us about your company and market."
    # Original dict is not mutated.
    assert static_q1["text"].startswith("What does")


def test_apply_tailoring_no_override_is_passthrough():
    q = {"id": "q2_company_size", "options": ["1-10", "11-50"]}
    tailored = {"q1_business_overview": {"examples": ["x"]}}
    assert apply_tailoring(q, tailored) == q


def test_apply_tailoring_handles_none():
    q = {"id": "q1_business_overview", "text": "t"}
    assert apply_tailoring(q, None) is q
    assert apply_tailoring(None, {"q1_business_overview": {"text": "t"}}) is None
