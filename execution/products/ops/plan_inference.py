"""Given a Magic Input + a collected context bundle, produce an
execution plan with confidence + a Claude Code-ready prompt.

The Magic Input contract:
    user_feedback     str   — what the user actually said they need
    basecamp_url      str   — pointer to the BC ticket
    output_type       str   — pptx | docx | pdf | email | code | text | other | ""
    success_criteria  str   — newline-separated criteria, or empty

The output:
    {
      "anticipated_goal":      "single sentence — what 'done' looks like",
      "inferred_output_type":  "pptx|docx|pdf|email|code|text|other",
      "inferred_success_criteria": ["...", "..."],
      "execution_plan": [
        {"step": 1, "action": "<verb + specific thing>", "estimated_minutes": int}
      ],
      "missing_information": ["...", "..."],
      "confidence_pct": int,
      "claude_code_prompt": str  # complete self-contained prompt
    }
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .llm_suggest import _get_client  # reuse client + key resolution

logger = logging.getLogger(__name__)

MODEL = os.environ.get("OPS_PLAN_MODEL", "gpt-4o")
MAX_TOKENS = int(os.environ.get("OPS_PLAN_MAX_TOKENS", "3000"))

SYSTEM_PROMPT = """You are a planning agent helping Ali (busy executive) decide what \
to do on a Basecamp ticket. You receive a USER REQUEST + a CONTEXT BUNDLE assembled \
by a rabbit-hole walker. Your job: infer the goal, propose a plan, score confidence, \
and produce a self-contained Claude Code prompt that ACTUALLY DOES the work.

Respond with strict JSON matching this exact schema:

{
  "anticipated_goal": "Single sentence stating what 'done' concretely looks like — a deliverable, not an activity.",
  "inferred_output_type": "pptx | docx | pdf | email | code | text | other",
  "inferred_success_criteria": [
    "Specific testable criterion 1 (e.g. '10 slides max')",
    "Specific testable criterion 2 (e.g. 'Includes ROI section with $ figure')"
  ],
  "execution_plan": [
    {"step": 1, "action": "<verb + SPECIFIC named target from the context>", "estimated_minutes": 10}
  ],
  "missing_information": [
    "Things you'd need to be 100% confident but couldn't find in the context bundle"
  ],
  "confidence_pct": <integer 0-100>,
  "claude_code_prompt": "A complete, self-contained prompt for Claude Code, plain text, no markdown headers."
}

CRITICAL RULES:

1. ABSOLUTE BAN on generic steps. NO 'Review the existing documentation' or 'Identify \
the new features' or 'Consult with the team'. Every step must reference a SPECIFIC \
named object from the CONTEXT BUNDLE (a person, file path, BC ticket, URL, decision, \
number, deadline).

2. If output_type is provided by the user, use that. If 'infer' or empty, derive from \
the user_feedback + context. Common patterns:
   - 'PowerPoint' / 'presentation' / 'pitch' → pptx
   - 'Word doc' / 'memo' / 'spec' → docx
   - 'reply' / 'respond' / 'email' → email
   - 'code' / 'implement' / 'build' → code
   - 'investigate' / 'research' → text (a report)

3. inferred_success_criteria — if user provided them, use them as the floor and add up \
to 2 more inferred from context. If user didn't provide, infer 3-5 specific ones from \
the user_feedback + output_type.

4. confidence_pct calibration:
   - 90-100: All info present in context; plan is specific to this ticket
   - 70-89: Most info present; 1-2 small gaps; plan is mostly specific
   - 50-69: Significant gaps; some steps would need clarification
   - 30-49: Vague request OR thin context; user should clarify first
   - <30: Cannot plan responsibly without more info

5. missing_information must enumerate the gaps that prevented confidence_pct from \
being higher.

6. claude_code_prompt rules:
   - Plain text, no markdown headers (##) — single string, paste-ready.
   - Start with: 'You are completing a Basecamp ticket for Ali. Take action, do not \
just narrate. Show me each step output before continuing to the next.'
   - Include the FULL context bundle (root ticket + linked items + external URLs).
   - State the anticipated_goal explicitly.
   - State the success_criteria explicitly.
   - List execution_plan as numbered steps.
   - End with: 'If a step requires info not in the bundle, STOP and ask. Otherwise \
produce the {output_type} deliverable in your response. Begin.'
"""


def _user_message(user_feedback: str, basecamp_url: str,
                  output_type: str, success_criteria: str,
                  context_render: str) -> str:
    return (
        f"USER REQUEST:\n"
        f"  User feedback: {user_feedback}\n"
        f"  Basecamp URL: {basecamp_url}\n"
        f"  Output type (user-specified, 'infer' = derive): {output_type or 'infer'}\n"
        f"  Success criteria (user-specified):\n"
        f"{(success_criteria or '(none — infer from context)').strip()}\n"
        f"\n"
        f"CONTEXT BUNDLE (rabbit-hole crawl result):\n"
        f"{context_render}\n"
    )


def infer(user_feedback: str, basecamp_url: str, output_type: str,
          success_criteria: str, context_bundle: dict) -> dict | None:
    """Produce the plan + claude code prompt. Returns None on LLM failure."""
    client = _get_client()
    if client is None:
        logger.warning("plan_inference: OpenAI client unavailable; returning None")
        return None

    from . import context_collector
    context_render = context_collector.render_for_llm(context_bundle)
    user_msg = _user_message(
        user_feedback, basecamp_url, output_type, success_criteria, context_render,
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=MAX_TOKENS,
        )
        raw = resp.choices[0].message.content or "{}"
        out = json.loads(raw)
    except Exception:
        logger.warning("plan_inference: LLM call failed", exc_info=True)
        return None

    required = (
        "anticipated_goal", "inferred_output_type", "execution_plan",
        "confidence_pct", "claude_code_prompt",
    )
    if not all(k in out for k in required):
        logger.warning("plan_inference: malformed JSON (missing keys)")
        return None
    out.setdefault("inferred_success_criteria", [])
    out.setdefault("missing_information", [])
    return out
