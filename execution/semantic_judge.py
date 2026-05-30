"""LLM-as-judge wrapper for spec-driven quality gates.

Uses ``execution.llm_client`` (the project's existing OpenAI wrapper) to
score acceptance criteria for testability and to evaluate whether a
chapter answers the four spec questions:
  (a) inputs, (b) outputs, (c) one runnable test scenario, (d) DoD.

Design notes:
- All judge calls run at temperature 0.0 for stability.
- Results are cached in-process by SHA256 hash of the prompt input so a
  re-run during the same Python session is free.
- The judge degrades gracefully: if the LLM is not configured, the
  function returns a structured "skipped" result rather than raising.
  Callers (the gate runner) treat skipped as advisory, not failing.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from execution import llm_client

logger = logging.getLogger(__name__)

JUDGE_MODEL = "gpt-4o-mini"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 800

_CACHE: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


AC_TESTABILITY_SYSTEM_PROMPT = """You are a senior QA engineer evaluating acceptance criteria.

For each acceptance criterion, score testability on a 0-3 scale:

- 0 = Untestable. The "then" clause is subjective ("works correctly",
  "is good", "handles errors appropriately") with no observable, measurable
  outcome.
- 1 = Weakly testable. The outcome is concrete in spirit but the AC is missing
  inputs, expected values, or a precise observable signal a developer could
  assert against.
- 2 = Testable. A developer could write a runnable test, but may need to make
  one or two reasonable assumptions about exact values, timeouts, or formats.
- 3 = Fully testable. The AC names concrete preconditions, a single trigger,
  and a measurable outcome (status code, value, threshold, persistent state).

Respond with strict JSON: {"results": [{"id": "...", "score": <0-3>, "reason": "..."}]}.
Do not add any text outside the JSON.
"""


CHAPTER_INTERN_SYSTEM_PROMPT = """You are an experienced engineering intern evaluating whether you could
execute work from a build-guide chapter alone.

Given the chapter text and the linked Requirements, decide for each of these
four questions whether the chapter, taken with the linked Requirements, gives
you enough specifics to act on it. For each:

- inputs: Can you list the concrete inputs the system or component will accept?
- outputs: Can you list the concrete outputs it produces?
- test_scenario: Can you describe one runnable test scenario (with concrete
  inputs and an assertable outcome)?
- definition_of_done: Can you state when this chapter's work is complete in a
  way another engineer could check?

Respond with strict JSON:
{
  "inputs":      {"answered": true|false, "evidence": "<short quote or null>"},
  "outputs":     {"answered": true|false, "evidence": "<short quote or null>"},
  "test_scenario":      {"answered": true|false, "evidence": "<short quote or null>"},
  "definition_of_done": {"answered": true|false, "evidence": "<short quote or null>"}
}

Do not add any text outside the JSON.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class JudgeResult:
    """Wrapper for a judge call. ``status`` is one of:

    - ``ok`` — judge ran and returned a structured payload in ``data``.
    - ``skipped`` — LLM unavailable; ``reason`` explains.
    - ``error`` — LLM call or JSON parsing failed; ``reason`` explains.
    """

    status: str
    data: dict | None = None
    reason: str | None = None


def score_acceptance_criteria(criteria: list[dict]) -> JudgeResult:
    """Score a list of acceptance criteria for testability.

    Args:
        criteria: List of AC dicts with at least ``id``, ``given``, ``when``,
            ``then``.

    Returns:
        JudgeResult. On ``ok`` the ``data`` shape is::

            {
                "results": [
                    {"id": "AC-001-1", "score": 2, "reason": "..."},
                    ...
                ]
            }

        On ``skipped`` (no LLM configured), ``data`` is None.
    """
    if not criteria:
        return JudgeResult(status="ok", data={"results": []})

    payload = json.dumps(
        [
            {
                "id": ac.get("id", ""),
                "given": ac.get("given", ""),
                "when": ac.get("when", ""),
                "then": ac.get("then", ""),
            }
            for ac in criteria
        ],
        ensure_ascii=False,
    )

    return _judge(
        system_prompt=AC_TESTABILITY_SYSTEM_PROMPT,
        user_content=payload,
        cache_key=("ac_testability", payload),
    )


def evaluate_chapter_intern_test(
    chapter_text: str,
    linked_requirements: list[dict],
) -> JudgeResult:
    """Evaluate whether a chapter answers the four intern-test questions.

    Args:
        chapter_text: Full rendered chapter text.
        linked_requirements: Requirements traced to this chapter (their
            ``traces_to.chapter_ids`` includes this chapter's id).

    Returns:
        JudgeResult. On ``ok`` the ``data`` is the four-key dict from
        CHAPTER_INTERN_SYSTEM_PROMPT.
    """
    requirements_summary = json.dumps(
        [
            {
                "id": r.get("id", ""),
                "actor": r.get("actor"),
                "action": r.get("action"),
                "value": r.get("value"),
                "priority": r.get("priority"),
                "acceptance_criteria_count": len(r.get("acceptance_criteria") or []),
            }
            for r in linked_requirements
        ],
        ensure_ascii=False,
    )

    user_content = (
        "Linked Requirements (JSON):\n"
        f"{requirements_summary}\n\n"
        "Chapter text:\n"
        f"{chapter_text}"
    )

    return _judge(
        system_prompt=CHAPTER_INTERN_SYSTEM_PROMPT,
        user_content=user_content,
        cache_key=("chapter_intern", user_content),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _judge(system_prompt: str, user_content: str, cache_key: tuple) -> JudgeResult:
    """Run an LLM judge call with caching, JSON parsing, and graceful errors."""
    key = _hash_cache_key(cache_key)
    if key in _CACHE:
        return JudgeResult(status="ok", data=_CACHE[key])

    if not llm_client.is_available():
        return JudgeResult(
            status="skipped",
            reason="LLM not configured (OPENAI_API_KEY unset)",
        )

    try:
        response = llm_client.chat(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            model=JUDGE_MODEL,
            max_tokens=JUDGE_MAX_TOKENS,
            temperature=JUDGE_TEMPERATURE,
            response_format={"type": "json_object"},
        )
    except llm_client.LLMUnavailableError as e:
        return JudgeResult(status="skipped", reason=str(e))
    except llm_client.LLMClientError as e:
        logger.warning("Judge LLM call failed: %s", e)
        return JudgeResult(status="error", reason=str(e))

    try:
        data = json.loads(response.content)
    except json.JSONDecodeError as e:
        logger.warning("Judge returned non-JSON content: %s", e)
        return JudgeResult(
            status="error",
            reason=f"Could not parse judge response as JSON: {e}",
        )

    _CACHE[key] = data
    return JudgeResult(status="ok", data=data)


def _hash_cache_key(parts: tuple) -> str:
    h = hashlib.sha256()
    for part in parts:
        if isinstance(part, str):
            h.update(part.encode("utf-8"))
        else:
            h.update(json.dumps(part, sort_keys=True).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def clear_cache() -> None:
    """Clear the in-process judge cache. Useful for tests."""
    _CACHE.clear()
