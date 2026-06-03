"""Standing orders appended to every Claude Code prompt this product generates.

Deterministic. Lives once here so it can be tweaked in one place without
re-running every LLM call. Imported by anything that emits a prompt
(llm_suggest, plan_inference, future tools).
"""
from __future__ import annotations

STANDING_ORDERS = """\

----- STANDING ORDERS (always follow) -----
1. POST PROGRESS — After each meaningful step, post a one-line progress
   comment on the BC ticket. Use the Basecamp MCP if you have it; otherwise
   tell me what to paste.
2. POST YOUR ANSWER — Your final response must be a complete answer suitable
   for pasting on the BC ticket as a comment. Lead with the verdict / output;
   put rationale below.
3. CLOSE IF DONE — If, after your final response, you are >85% confident the
   ticket is genuinely complete, mark the BC todo complete. Use the BC MCP
   or tell me explicitly to mark it done.
4. ASK IF UNSURE — If you are not confident on ANY step, do not guess. Post
   a focused question on the BC ticket and STOP. List exactly what you'd need
   to proceed.
5. NEVER NARRATE — Don't summarize what you're about to do. Do it and report
   the result. If a step produces a file, attach it (or paste its content).
6. SCOPE GUARD — If you discover the work needs >2× the estimated time or
   touches anything outside this ticket's scope, STOP and ask before
   continuing. Avoid scope creep without explicit approval.
"""


def append_orders(prompt: str) -> str:
    """Append standing orders to a base prompt. Idempotent — won't double-add."""
    if not prompt:
        return STANDING_ORDERS.strip()
    if "STANDING ORDERS" in prompt:
        return prompt
    return prompt.rstrip() + "\n" + STANDING_ORDERS
