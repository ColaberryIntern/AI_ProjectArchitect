"""LLM-enhanced per-ticket action plan.

Replaces the regex-based recipes from `suggestions.py` with a per-ticket
LLM analysis that produces SPECIFIC steps and a Claude Code-ready prompt
grounded in the ticket's title + description + recent comments.

Cached on disk under output/ops/{user_id}/_llm_cache.json keyed by
(bc_id, bc_updated_at, comments_hash) so we only re-LLM when the ticket
actually changes. Cache survives container restarts; rebuild it by
deleting the file.

Falls back to None silently when OPENAI_API_KEY is unset or the API
errors — callers degrade to the deterministic recipe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT

from .store import OpsTodo

logger = logging.getLogger(__name__)

MODEL = os.environ.get("OPS_LLM_MODEL", "gpt-4o")
MAX_TOKENS = int(os.environ.get("OPS_LLM_MAX_TOKENS", "2800"))
CACHE_FILENAME = "_llm_cache.json"

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
        # Per-client default timeout; per-request override on .create() too.
        # Keeps /my-day/ page render under 20s end-to-end so the dim
        # overlay never sticks waiting for the API.
        _client = OpenAI(api_key=key, timeout=12.0)
        return _client
    except Exception:
        logger.warning("Failed to init OpenAI client", exc_info=True)
        return None


def _cache_path(user_id: str) -> Path:
    return PROJECT_ROOT / "output" / "ops" / user_id / CACHE_FILENAME


def _load_cache(user_id: str) -> dict[str, Any]:
    p = _cache_path(user_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(user_id: str, cache: dict[str, Any]) -> None:
    p = _cache_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp", prefix=p.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        Path(tmp).replace(p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Bump PROMPT_VERSION when SYSTEM_PROMPT or claude_code_prompt rules change
# so existing cache entries do not serve stale prompts. v3 = v2 + structured
# section newlines (the LLM was emitting one wall-of-text line; v3 adds
# explicit \n\n between TITLE / DESCRIPTION / COMMENTS / STEPS / CLOSE
# sections so the prompt reads as a document).
# v4 = v3 + PROJECT URL / LIST URL in the CONTEXT block and Depends-on/Artifact
# lift (approval-task-dependency-linking.md).
PROMPT_VERSION = "v4"


def _cache_key(todo: OpsTodo, comments: str) -> str:
    h = hashlib.sha1(comments.encode("utf-8")).hexdigest()[:10]
    return f"{todo.bc_id}|{todo.bc_updated_at}|{h}|{PROMPT_VERSION}"


SYSTEM_PROMPT = """You are helping Ali (a busy CEO/CTO) clear a Basecamp ticket. \
Produce a Claude Code-ready action plan SPECIFIC to THIS ticket using ONLY \
the title, description, and comments provided.

Respond with strict JSON matching this exact schema:

{
  "action_kind": "decision|reply|email|build|research|meeting|schedule|review|default",
  "goal_line": "One sentence: what 'done' concretely looks like. A deliverable, not an activity.",
  "summary_paragraph": "ONE flowing paragraph (3-5 sentences) describing what to do AND how to do it. Mention specifics from the ticket (file paths, named people, deadlines, numbers). NO bullet points, NO 'Step 1, Step 2'. Just a clear paragraph a smart human can read in 15 seconds and know exactly what to do.",
  "specific_steps": [
    "<verb> <specific named thing>",
    "<verb> <specific named thing>",
    "..."
  ],
  "stop_conditions": [
    "Specific named conditions that should pause the work and get a human in the loop"
  ],
  "claude_code_prompt": "A FULL-CONTEXT, ACTION-ORIENTED prompt the user pastes into a fresh Claude Code session. NOT short. Inline EVERYTHING Claude Code needs to either solve the ticket or write a precise list of missing inputs. The session may not have BC fetch capability so do NOT rely on the BC URL alone. See the MUST list below for required content."
}

ABSOLUTE RULES. These are violations of the contract:

❌ BANNED step shapes (generic "thinking" verbs without a specific named target):
   - "Review the existing documentation"
   - "Identify the new features"
   - "Draft a list of requirements"
   - "Consult with the development team"
   - "Read the thread"
   - "Understand what they need"

✅ REQUIRED step shapes (verb + SPECIFIC named target from the ticket):

   IF action_kind == 'build':
     - "Open <specific file path inferred from description or repo convention>"
     - "Add function <name> to <file> that <behavior>"
     - "Write a pytest case asserting <specific condition from ticket>"
     - "Update <docs path> with the new <thing> from this commit"

   IF action_kind == 'reply' or 'email':
     - The FIRST step contains the draft reply text in full, in quotes.
       Example: 'Send this reply: "Karun, attached is the stager training data export per our May 28 call. Columns A-E are pivot-tagged per your spec. Let me know if anything is off. Ali"'
     - Subsequent steps confirm or adjust the draft.

   IF action_kind == 'decision':
     - Step 1 lists the 2-3 specific verdict options derived from the ticket, each with a one-line tradeoff.
       Example: 'Choose ONE: (a) Approve and ship Friday: fastest, but skips integration testing. (b) Approve conditional on <specific test>. (c) Hold until <named blocker> resolves.'

   IF action_kind == 'meeting':
     - Step 1 contains the proposed agenda in 3-5 named bullets.
     - Step 2 decides who to invite or whether to replace with async + a 1-page brief.

   IF action_kind == 'research':
     - Name 2-3 specific sources (file paths, doc URLs, BC tickets, repo names) to look at first.
     - Step 1: "Read <specific source>"
     - Step 2: "Cross-check against <specific other source>"

   IF action_kind == 'review':
     - Name what's being reviewed and a specific decision criterion.

If the ticket is genuinely too vague to be specific, the steps must be:
  - "Ask <specific named person> in BC reply: '<specific question>'"
  - Not "Clarify the requirements".

Length: 3-6 steps. Total response ≤ 1000 tokens.

The claude_code_prompt MUST be a single plain-text string (no markdown headers like ##), ready to paste. CRITICAL: the string must use REAL newline characters between sections. In the JSON output, encode each section break as a literal \\n\\n (so json.loads produces a string with actual blank lines). The structure is FIVE sections separated by blank lines:

Section 1: WHAT to do (the goal_line) on a single line at the top.

Section 2: A "CONTEXT:" block with TITLE, PROJECT, PROJECT URL, LIST, LIST URL, DUE, BC URL each on its own line (copy the PROJECT URL and LIST URL through verbatim so the reader can open the list and see sibling tasks at project scale), followed by a blank line, then "DESCRIPTION:" on its own line, then the full description verbatim. If the description contains "Depends-on:" or "Artifact:" lines, lift them to the TOP of the CONTEXT block as "DRAFTING TASK:" and "ARTIFACT:" so the reader can reach the thing being approved without scrolling. If "Artifact:" is "PENDING", state plainly that the artifact does not exist yet and the next step belongs to the drafting task's owner, not this approval gate.

Section 3: A "RECENT COMMENTS:" block with each comment on its own paragraph, prefixed with "[Author Name, YYYY-MM-DD]" on its own line, then the comment body. Oldest first.

Section 4: A "STEPS:" block with each specific_step on its own numbered line. Then a "STOP IF:" block listing stop_conditions, one per line.

Section 5: This EXACT closing block, byte-for-byte (replace nothing):
"DO the work. Complete the deliverable in this session if you have everything you need. If anything is missing, do NOT guess: list exactly what input you need from Ali so he can answer in one round. Drafts of outbound communications go through Ali before send. Begin."

Length: do not constrain the prompt. Inline whatever context the ticket has. A short ticket produces a short prompt. A rich ticket produces a long one. Treat the prompt as self-sufficient: if Claude Code has zero ability to fetch external resources, it must still be able to act from this prompt alone.

Forbidden in the claude_code_prompt:
- em-dashes (use a colon or hyphen)
- "as needed" / "where applicable" / "and so on"
- "Review the situation" / "Understand what they need" without a named target
"""


def _build_user_message(todo: OpsTodo, comments_text: str) -> str:
    return (
        f"TICKET TITLE: {todo.title}\n"
        f"PROJECT: {todo.bc_project_name}\n"
        f"PROJECT URL: {todo.project_url or '(no url)'}\n"
        f"LIST: {todo.bc_todolist_name}\n"
        f"LIST URL: {todo.list_url or '(no url)'}\n"
        f"DUE: {todo.due_on or 'no due date'}\n"
        f"URGENCY SCORE (0-100): {todo.urgency_score}\n"
        f"CATEGORY: {todo.category}\n"
        f"BC URL: {todo.bc_app_url or '(no url)'}\n\n"
        f"DESCRIPTION:\n{(todo.description or '(no description)').strip()[:3000]}\n\n"
        f"RECENT COMMENTS (oldest → newest):\n{comments_text[:4000] or '(no comments yet)'}\n"
    )


def enhance(user_id: str, todo: OpsTodo, comments_text: str = "") -> dict | None:
    """Return {action_kind, goal_line, specific_steps, stop_conditions,
    claude_code_prompt} or None if LLM unavailable / failed.

    Cached on disk; re-runs only when ticket or comments change.
    """
    client = _get_client()
    if client is None:
        return None
    key = _cache_key(todo, comments_text)
    cache = _load_cache(user_id)
    if key in cache:
        return cache[key]

    try:
        resp = client.with_options(timeout=12.0).chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(todo, comments_text)},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=MAX_TOKENS,
        )
        raw = resp.choices[0].message.content or "{}"
        out = json.loads(raw)
    except Exception:
        logger.warning("LLM enhance failed for todo %s", todo.bc_id, exc_info=True)
        return None

    # Validate shape
    required = ("action_kind", "goal_line", "specific_steps", "claude_code_prompt")
    if not all(k in out for k in required):
        logger.warning("LLM returned malformed JSON for todo %s (missing keys)", todo.bc_id)
        return None
    if not isinstance(out["specific_steps"], list) or not out["specific_steps"]:
        return None
    out.setdefault("stop_conditions", [])
    out.setdefault("summary_paragraph", "")

    # Append standing orders deterministically so EVERY prompt has them,
    # regardless of whether the LLM remembered to include them.
    from .standing_orders import append_orders
    out["claude_code_prompt"] = append_orders(out["claude_code_prompt"])

    cache[key] = out
    try:
        _save_cache(user_id, cache)
    except Exception:
        logger.warning("Failed to write LLM cache", exc_info=True)
    return out
