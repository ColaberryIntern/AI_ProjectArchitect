"""Generate features + BUILD/BREAK/HARDEN todos for a Build Guide chapter.

The "spine + generate" path: the parser gives us a chapter (initiative); this
module turns that chapter into one or more **features** (lists), each with
concrete todos carrying a phase (BUILD/BREAK/HARDEN), an AI/human ``kind``, and
an acceptance criterion. A single schema-constrained LLM call per chapter, with
a deterministic fallback so the build still produces a valid plan when the LLM
is unavailable.

Failure-First is guaranteed structurally: every feature is post-processed to
have at least one active BUILD and one active BREAK todo, so the resulting plan
satisfies ``project_plan.validate_plan`` rule 7 and the build can proceed
automatically (no human promotion gate on the first build).
"""
from __future__ import annotations

import json
import logging

from execution.llm_client import LLMClientError, LLMUnavailableError, chat, is_available

logger = logging.getLogger(__name__)

_VALID_PHASES = {"BUILD", "BREAK", "HARDEN"}

_SYSTEM = (
    "You convert one chapter of a software Build Guide into a concrete build plan. "
    "Return STRICT JSON only. Identify the real FEATURES the chapter describes, and for "
    "each feature produce buildable todos. Every feature MUST include at least one todo "
    "with phase 'BUILD' (make it work) and at least one with phase 'BREAK' (the failure / "
    "edge-case path — invalid input, limits, errors). Use 'HARDEN' for security/perf/"
    "observability follow-ups where relevant. Mark each todo's kind 'ai' if Claude Code can "
    "build it autonomously, or 'human' if it needs a person's decision/approval/credentials. "
    "Each todo needs a one-line acceptance criterion."
)

_USER_TMPL = (
    "Chapter: {title}\n\n"
    "Chapter content:\n{body}\n\n"
    "Return JSON of this exact shape:\n"
    '{{"features": [{{"title": "<feature name>", "todos": ['
    '{{"title": "<imperative task>", "phase": "BUILD|BREAK|HARDEN", '
    '"kind": "ai|human", "acceptance": "<one line>"}}]}}]}}'
)


def _norm_todo(t: dict) -> dict | None:
    title = (t.get("title") or "").strip()
    if not title:
        return None
    phase = (t.get("phase") or "BUILD").upper()
    if phase not in _VALID_PHASES:
        phase = "BUILD"
    kind = "human" if (t.get("kind") or "").lower() == "human" else "ai"
    acceptance = (t.get("acceptance") or "").strip() or f"{title} works as intended."
    return {"title": title, "phase": phase, "kind": kind, "acceptance": acceptance}


def _build_todo(feature: str) -> dict:
    return {"title": f"Build {feature}", "phase": "BUILD", "kind": "ai",
            "acceptance": f"{feature} works as described in the Build Guide."}


def _break_todo(feature: str) -> dict:
    return {"title": f"Handle failures and edge cases for {feature}", "phase": "BREAK", "kind": "ai",
            "acceptance": f"Invalid input, limits, and error paths for {feature} are handled gracefully."}


def _ensure_build_break(features: list[dict]) -> list[dict]:
    """Guarantee each feature has an active BUILD and BREAK todo (rule 7)."""
    for f in features:
        phases = {t["phase"] for t in f["todos"]}
        if "BUILD" not in phases:
            f["todos"].insert(0, _build_todo(f["title"]))
        if "BREAK" not in phases:
            f["todos"].append(_break_todo(f["title"]))
    return features


def _fallback_features(chapter_title: str) -> list[dict]:
    feat = chapter_title.strip() or "Core feature"
    return [{"title": feat, "todos": [_build_todo(feat), _break_todo(feat)]}]


def generate_features(chapter_title: str, chapter_body: str, max_features: int = 6) -> list[dict]:
    """Return ``[{title, todos:[{title, phase, kind, acceptance}]}]`` for a chapter.

    Always returns at least one feature, each with a BUILD and a BREAK todo.
    """
    features: list[dict] = []
    if is_available() and (chapter_body or "").strip():
        try:
            resp = chat(
                system_prompt=_SYSTEM,
                messages=[{"role": "user", "content": _USER_TMPL.format(
                    title=chapter_title, body=(chapter_body or "")[:6000])}],
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.content)
            for f in (data.get("features") or [])[:max_features]:
                ftitle = (f.get("title") or "").strip()
                todos = [nt for nt in (_norm_todo(t) for t in (f.get("todos") or [])) if nt]
                if ftitle and todos:
                    features.append({"title": ftitle, "todos": todos})
        except (LLMUnavailableError, LLMClientError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning("feature generation LLM call failed for %r: %s", chapter_title, e)
        except Exception:  # noqa: BLE001 — never let generation crash the build
            logger.warning("feature generation unexpected error for %r", chapter_title, exc_info=True)

    if not features:
        features = _fallback_features(chapter_title)
    return _ensure_build_break(features)
