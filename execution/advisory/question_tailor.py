"""Idea-aware tailoring of the advisory intake questions.

The 10 intake questions in ``question_engine`` are a fixed, universal spine —
stable IDs + categories that downstream mappers and tests depend on. Their
default example chips, however, are generic (logistics / SaaS / healthcare) and
feel disconnected from whatever the user actually typed on the "new project"
page. This module produces idea-specific *overrides* — tailored example chips
(and a tailored framing for the opening question) — that the route overlays on
top of the static questions before rendering and recording.

Pure + fallback-safe: ``tailor_questions`` returns ``{}`` whenever the LLM is
unavailable or returns anything malformed, so the flow silently falls back to
the static examples (current behavior). ``apply_tailoring`` is a pure overlay.
"""

import json
import logging
import threading

logger = logging.getLogger(__name__)

# Which static questions get idea-specific tailoring, and what each one asks.
# Only ``q1`` has its *text* reworded — it is the one that feels most redundant
# once the user has already described their idea ("what industry are you in?"
# right after they said "a booking app for my salon"). The rest keep their
# universal wording and only receive tailored example chips. The option-based
# questions (company size, budget, tools, …) are intentionally left untouched —
# their fixed options are already universal.
_TAILORABLE = [
    {"id": "q1_business_overview", "asks": "what the business does and the context it operates in", "tailor_text": True},
    {"id": "q4_bottlenecks",       "asks": "the biggest day-to-day bottlenecks or pain points",     "tailor_text": False},
    {"id": "q5_customer_journey",  "asks": "how customers find them and how they get supported",     "tailor_text": False},
    {"id": "q8_manual_processes",  "asks": "the manual, repetitive work they'd most want automated", "tailor_text": False},
    {"id": "q10_success_vision",   "asks": "what success looks like ~12 months from now",            "tailor_text": False},
]

_TAILOR_IDS = {q["id"] for q in _TAILORABLE}
_TAILOR_TEXT_IDS = {q["id"] for q in _TAILORABLE if q["tailor_text"]}

_SYSTEM_PROMPT = """You personalize a short business-intake questionnaire so every prompt and example clearly relates to ONE specific business idea the user just described.

You are given the user's idea and a list of questions (each with an id and what it asks). For EACH question id, write three short, realistic example answers, written in the user's first person ("We ..." / "I ..."), that a person running THIS specific business would plausibly give. Examples must be concrete to the idea's domain — never generic filler, never about a different industry.

For any question marked "tailor_text": true, also rewrite its question text and a one-line help text so they speak directly about THIS business instead of asking generically. Keep the question's underlying intent identical.

Return ONLY valid JSON of this exact shape (include an entry for every id):
{
  "<question_id>": {
    "text": "<reworded question — ONLY when tailor_text is true, else omit>",
    "help_text": "<one short line — ONLY when tailor_text is true, else omit>",
    "examples": ["<example 1>", "<example 2>", "<example 3>"]
  }
}

RULES:
- Examples: max ~12 words each, plain language, no jargon, no emojis.
- Make the three examples specific and varied (different angles), all believable for this exact business.
- Never invent a different business than the one described.
- Output JSON only — no prose, no markdown."""


def tailor_questions(business_idea: str) -> dict:
    """Generate idea-specific overrides for the tailorable intake questions.

    Args:
        business_idea: The raw idea text the user typed on the start page.

    Returns:
        A map ``{question_id: {"text"?, "help_text"?, "examples": [...]}}``.
        Empty dict when the idea is too short, the LLM is unavailable, or the
        response is malformed — callers then fall back to the static questions.
    """
    idea = (business_idea or "").strip()
    if len(idea) < 3:
        return {}

    try:
        from execution.llm_client import chat, is_available
        if not is_available():
            return {}
    except Exception:
        return {}

    spec = [{"id": q["id"], "asks": q["asks"], "tailor_text": q["tailor_text"]} for q in _TAILORABLE]
    user_message = (
        f"USER'S IDEA:\n{idea[:600]}\n\n"
        f"QUESTIONS TO PERSONALIZE:\n{json.dumps(spec, indent=2)}"
    )

    try:
        response = chat(
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=900,
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.content)
    except Exception as e:  # pragma: no cover - exercised via monkeypatch
        logger.warning(f"[QuestionTailor] LLM failed: {e}")
        return {}

    result = _sanitize(raw)
    logger.info(f"[QuestionTailor] tailored {len(result)} question(s) for idea: {idea[:60]!r}")
    return result


def _sanitize(raw) -> dict:
    """Keep only well-formed overrides for known tailorable question IDs."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for qid, payload in raw.items():
        if qid not in _TAILOR_IDS or not isinstance(payload, dict):
            continue
        entry: dict = {}

        examples = payload.get("examples")
        if isinstance(examples, list):
            clean = [str(e).strip() for e in examples if isinstance(e, str) and str(e).strip()]
            clean = [e for e in clean if len(e) <= 160][:3]
            if clean:
                entry["examples"] = clean

        if qid in _TAILOR_TEXT_IDS:
            text = payload.get("text")
            help_text = payload.get("help_text")
            if isinstance(text, str) and text.strip():
                entry["text"] = text.strip()[:200]
            if isinstance(help_text, str) and help_text.strip():
                entry["help_text"] = help_text.strip()[:160]

        if entry:
            out[qid] = entry
    return out


def is_tailorable(question_id: str | None) -> bool:
    """True when a question receives idea-specific tailoring."""
    return question_id in _TAILOR_IDS


def _run_tailoring(session_id: str, business_idea: str) -> None:
    """Generate tailoring, then merge it into the session and flip the status.

    Generation (the slow part) happens first; the session is (re)loaded only
    immediately before writing, so a concurrent answer-save is never clobbered.
    Always marks the status "done" — even on failure — so the page's poller
    stops and falls back to the static questions.
    """
    result: dict = {}
    try:
        result = tailor_questions(business_idea)
    except Exception as e:  # pragma: no cover - defensive; tailor_questions is already guarded
        logger.warning(f"[QuestionTailor] background generation failed: {e}")

    try:
        from execution.advisory.advisory_state_manager import load_session, save_session
        session = load_session(session_id)
        session["tailored_questions"] = result
        session["tailoring_status"] = "done"
        save_session(session)
    except Exception as e:  # pragma: no cover - session may be gone (e.g. test teardown)
        logger.warning(f"[QuestionTailor] could not persist tailoring for {session_id}: {e}")


def kick_tailoring(session_id: str, business_idea: str) -> None:
    """Run ``_run_tailoring`` in a daemon thread so the request returns instantly."""
    threading.Thread(
        target=_run_tailoring,
        args=(session_id, business_idea),
        name=f"tailor-{session_id[:8]}",
        daemon=True,
    ).start()


def apply_tailoring(question: dict | None, tailored: dict | None) -> dict | None:
    """Overlay a tailored override onto a static question dict (pure).

    Returns the question unchanged when there is no override for it, so it is
    always safe to call regardless of whether tailoring succeeded.
    """
    if not question or not tailored:
        return question
    override = tailored.get(question.get("id"))
    if not override:
        return question
    merged = dict(question)
    for key, value in override.items():
        if value:
            merged[key] = value
    return merged
