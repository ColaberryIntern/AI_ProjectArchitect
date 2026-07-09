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
and produce a SHORT Claude Code prompt.

Respond with strict JSON matching this exact schema:

{
  "anticipated_goal": "Single sentence: what 'done' concretely looks like. A deliverable.",
  "summary_paragraph": "ONE flowing paragraph (3-5 sentences) explaining what to do and how, and PREDICTING the file type(s) the final deliverable will need. There may be MORE THAN ONE (e.g. a .pptx deck plus a .docx leave-behind, or a .xlsx model plus a .pdf summary); name the concrete file extension(s) you expect to hand back. Mention specifics from the bundle (file paths, names, deadlines, numbers). NO bullet points, NO 'Step 1, Step 2'. This is what Ali reads at a glance to decide whether to proceed.",
  "inferred_output_type": "pptx | docx | pdf | email | code | text | other",
  "inferred_success_criteria": [
    "Specific testable criterion 1 (e.g. '10 slides max')",
    "Specific testable criterion 2"
  ],
  "execution_plan": [
    {"step": 1, "action": "<verb + SPECIFIC named target from the context>", "estimated_minutes": 10}
  ],
  "missing_information": [
    "Things needed to be 100% confident but couldn't find in the bundle"
  ],
  "confidence_pct": <integer 0-100>,
  "claude_code_prompt": "A FULL-CONTEXT, ACTION-ORIENTED prompt the user pastes into a fresh Claude Code session. NOT short. Inline EVERYTHING from the CONTEXT BUNDLE (root ticket + linked items + external URLs). The session may not have BC fetch capability so do NOT rely on the BC URL alone. Use REAL newline characters between sections (encode as literal \\n\\n in the JSON). See the MUST list (rule 6 below) for the exact structure."
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

3. inferred_success_criteria. If user provided them, use them as the floor and add up \
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

6. claude_code_prompt rules. The string must use REAL newline characters between sections (encode each section break as a literal \\n\\n in the JSON, so json.loads produces a string with actual blank lines). The structure is FIVE sections separated by blank lines.

Section 1: A short SUMMARY (2 to 3 sentences) that is the INITIAL ANALYSIS of the request: what this ticket is and what the deliverable(s) are, naming the predicted file type(s) for the final output (there may be more than one). Put this at the very top, before any detail; then get into the meat below.

Section 2: A "CONTEXT:" block with all known ticket metadata (title, project, list, due date, BC URL) each on its own line, followed by a blank line, then "BUNDLE:" on its own line, then the full context bundle verbatim (root ticket description, linked items, external URLs that the rabbit-hole walker collected).

Section 3: A "SUCCESS CRITERIA:" block listing inferred_success_criteria, one per line, prefixed with "- ".

Section 4: A "STEPS:" block listing execution_plan as numbered actions.

Section 5: This EXACT closing block, byte-for-byte (replace nothing): "Attempt the full deliverable on the FIRST pass: if you have what you need, produce the complete output now instead of stopping to ask permission part-way. Save every file you create or download into the Downloads folder, and attach it to the Basecamp ticket FROM the Downloads folder; use that same path every time. When the work is done, STATE your confidence as a percentage (0 to 100) that the deliverable is complete and correct, then ASK before you post anything to Basecamp, send any outbound message, or mark the todo complete: do not auto-post, auto-send, or auto-close (this overrides any standing order to close automatically). If something is genuinely missing, do NOT guess: list exactly what input you need from Ali so he can answer in one round. Begin."

Length: do not constrain. A thin bundle produces a short prompt; a rich bundle produces a long one. Treat the prompt as self-sufficient: if Claude Code has zero ability to fetch external resources, it must still be able to act from this prompt alone.

Forbidden in the claude_code_prompt:
- em-dashes (use a colon or hyphen)
- "as needed" / "where applicable" / "and so on"
- "Review the situation" / "Understand what they need" without a named target
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
        f"{(success_criteria or '(none: infer from context)').strip()}\n"
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
        try:
            from execution.ops_platform import cost_ledger
            cost_ledger.record(model=getattr(resp, "model", MODEL), source="ops_plan",
                               prompt_tokens=resp.usage.prompt_tokens,
                               completion_tokens=resp.usage.completion_tokens)
        except Exception:
            pass
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
    out.setdefault("summary_paragraph", "")

    # Append standing orders deterministically.
    from .standing_orders import append_orders
    out["claude_code_prompt"] = append_orders(out["claude_code_prompt"])
    return out
