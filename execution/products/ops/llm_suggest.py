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

MODEL = os.environ.get("OPS_LLM_MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.environ.get("OPS_LLM_MAX_TOKENS", "1400"))
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
        _client = OpenAI(api_key=key)
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


def _cache_key(todo: OpsTodo, comments: str) -> str:
    h = hashlib.sha1(comments.encode("utf-8")).hexdigest()[:10]
    return f"{todo.bc_id}|{todo.bc_updated_at}|{h}"


SYSTEM_PROMPT = """You are helping a busy executive triage a Basecamp ticket. \
Produce a Claude Code-ready action plan SPECIFIC to THIS ticket. Do not give \
generic advice ("read the thread", "identify what they need"). Read what's \
already provided and write steps a human or Claude Code can directly execute.

Respond with strict JSON matching this exact schema:

{
  "action_kind": "decision|reply|email|build|research|meeting|schedule|review|default",
  "goal_line": "One sentence: what 'done' concretely looks like on this ticket.",
  "specific_steps": [
    "Step 1: a verb + specific object — what to do, not how to think",
    "Step 2: ...",
    "Step 3: ..."
  ],
  "stop_conditions": [
    "Specific things that should pause the work and get a human in the loop"
  ],
  "claude_code_prompt": "A complete, self-contained prompt the user can paste into a fresh Claude Code session. It must include the full task context, the goal, the steps, the expected output, and a clear directive to TAKE ACTION (do the work) not just narrate. End with 'Begin.'"
}

Rules:
- Steps are concrete and SHORT. 3-6 steps total. Each is something a person or Claude Code could complete in 5-30 min.
- If the ticket is genuinely too vague to be specific, the steps should be how to clarify it (who to ask, what specific question).
- The claude_code_prompt must be a single string, no markdown headings, ready to paste into Claude Code as-is. Include the BC URL, due date, full description, and recent comments at the top of the prompt.
- For reply/email tickets, include the proposed first-draft text inside the prompt so Claude Code has a starting point.
- For build/research tickets, suggest specific files/repos/paths to look at if you can infer them.
- For decision tickets, propose 2-3 verdict options the user can pick from, with the tradeoff for each.
"""


def _build_user_message(todo: OpsTodo, comments_text: str) -> str:
    return (
        f"TICKET TITLE: {todo.title}\n"
        f"PROJECT: {todo.bc_project_name}\n"
        f"LIST: {todo.bc_todolist_name}\n"
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
        resp = client.chat.completions.create(
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

    cache[key] = out
    try:
        _save_cache(user_id, cache)
    except Exception:
        logger.warning("Failed to write LLM cache", exc_info=True)
    return out
