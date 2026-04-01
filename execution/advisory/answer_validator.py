"""LLM-powered answer validation for advisory question flow.

Validates whether user answers actually address the question asked,
extracts structured info, detects if other questions were already answered,
and generates conversational follow-ups when answers are off-topic.
"""

import json
import logging

from execution.advisory.question_engine import ADVISORY_QUESTIONS

logger = logging.getLogger(__name__)

# Question IDs for cross-referencing
_QUESTION_IDS = [q["id"] for q in ADVISORY_QUESTIONS]
_QUESTION_LABELS = {q["id"]: q["text"] for q in ADVISORY_QUESTIONS}

_SYSTEM_PROMPT = """You are an AI business advisor validating a user's answer during an intake conversation.

You are asking structured questions to understand their business. The user may:
- Give a direct, complete answer (good)
- Give a partial answer (acceptable, note what's missing)
- Answer a different question than what was asked (off-topic)
- Include information that answers FUTURE questions too (great, flag those)

Given the QUESTION and the user's ANSWER, return ONLY valid JSON:

{
  "addresses_question": true or false,
  "quality": "complete" or "partial" or "off_topic",
  "extracted_info": "brief summary of useful info from the answer",
  "follow_up": "a friendly, conversational follow-up if the answer doesn't address the question. null if the answer is good.",
  "already_answered": ["list of question_ids from FUTURE questions that this answer also covers"]
}

RULES:
- Be generous. If the answer contains ANY relevant info for the question, mark as "partial" not "off_topic".
- Only mark "off_topic" if the answer has ZERO relevance to what was asked.
- For "already_answered", ONLY include question IDs from the provided list that haven't been asked yet.
- Follow-ups should be warm and conversational, not robotic. Like a consultant chatting.
- Keep follow_up under 2 sentences.

AVAILABLE QUESTION IDS (for already_answered):
{question_ids}
"""


def validate_answer(
    question: dict,
    answer_text: str,
    previous_answers: list[dict],
    remaining_question_ids: list[str],
) -> dict:
    """Validate a user's answer using the LLM.

    Args:
        question: The current question dict (id, text, help_text).
        answer_text: The user's raw answer text.
        previous_answers: List of previous answer dicts for context.
        remaining_question_ids: IDs of questions not yet answered.

    Returns:
        Validation result dict. On LLM failure, returns a permissive default.
    """
    # Fallback if LLM unavailable
    try:
        from execution.llm_client import chat, is_available
        if not is_available():
            return _default_result()
    except Exception:
        return _default_result()

    # Build context
    remaining_labels = {qid: _QUESTION_LABELS.get(qid, qid) for qid in remaining_question_ids}

    system = _SYSTEM_PROMPT.format(
        question_ids=json.dumps(remaining_labels, indent=2),
    )

    user_message = f"""QUESTION: {question['text']}
HELP TEXT: {question.get('help_text', '')}

USER'S ANSWER: {answer_text}

PREVIOUS CONVERSATION:
{_format_previous(previous_answers)}"""

    try:
        response = chat(
            system_prompt=system,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=300,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        result = json.loads(response.content)

        # Sanitize already_answered to only include valid remaining IDs
        already = result.get("already_answered", [])
        result["already_answered"] = [qid for qid in already if qid in remaining_question_ids]

        # Ensure required fields
        result.setdefault("addresses_question", True)
        result.setdefault("quality", "complete")
        result.setdefault("extracted_info", "")
        result.setdefault("follow_up", None)

        logger.info(f"[AnswerValidator] Q={question['id']}: quality={result['quality']}, already_answered={result['already_answered']}")
        return result

    except Exception as e:
        logger.warning(f"[AnswerValidator] LLM failed: {e}")
        return _default_result()


def _format_previous(answers: list[dict]) -> str:
    """Format previous Q&A pairs for context."""
    if not answers:
        return "(none yet)"
    lines = []
    for a in answers[-5:]:  # Last 5 for context window efficiency
        lines.append(f"Q: {a.get('question_text', '?')}")
        lines.append(f"A: {a.get('answer_text', '?')}")
    return "\n".join(lines)


def _default_result() -> dict:
    """Permissive default when LLM is unavailable."""
    return {
        "addresses_question": True,
        "quality": "complete",
        "extracted_info": "",
        "follow_up": None,
        "already_answered": [],
    }
