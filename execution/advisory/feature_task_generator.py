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


_WS_SYSTEM = (
    "You produce a concise, executable software build plan for ONE project. Return STRICT "
    "JSON only. Create 6 to 9 WORKSTREAMS — major feature areas or build phases, NOT document "
    "sections. Each workstream has 4 to 7 SUBSTANTIAL tasks (quality over quantity; no trivial "
    "one-line steps as their own task). Every workstream MUST include at least one task with "
    "phase 'BUILD' and at least one with phase 'BREAK' (the failure / edge / negative path). Use "
    "'HARDEN' for security, performance, or observability work where it fits. For each task give: "
    "a clear imperative title; phase BUILD|BREAK|HARDEN; kind 'ai' if Claude Code can build it "
    "autonomously or 'human' if it needs a person's decision/approval/credentials; an "
    "'acceptance' line stating the happy-path result AND the failure/edge behavior; and 'steps' = "
    "2 to 5 concrete sub-steps."
)

_WS_USER = (
    "Project idea:\n{idea}\n\n"
    "Key areas from the design doc (context, not a required structure):\n{areas}\n\n"
    'Return JSON exactly: {{"workstreams": [{{"title": "<workstream>", "tasks": ['
    '{{"title": "<task>", "phase": "BUILD|BREAK|HARDEN", "kind": "ai|human", '
    '"acceptance": "<happy + failure>", "steps": ["<step>", "..."]}}]}}]}}'
)

_MAX_WORKSTREAMS = 9
_MAX_TASKS = 7


def _norm_ws_task(t: dict) -> dict | None:
    title = (t.get("title") or "").strip()
    if not title:
        return None
    phase = (t.get("phase") or "BUILD").upper()
    if phase not in _VALID_PHASES:
        phase = "BUILD"
    kind = "human" if (t.get("kind") or "").lower() == "human" else "ai"
    acceptance = (t.get("acceptance") or "").strip() or f"{title} works and handles its failure path."
    steps = [str(s).strip() for s in (t.get("steps") or []) if str(s).strip()][:6]
    return {"title": title, "phase": phase, "kind": kind, "acceptance": acceptance, "steps": steps}


def _ensure_ws_build_break(workstreams: list[dict]) -> list[dict]:
    for ws in workstreams:
        phases = {t["phase"] for t in ws["tasks"]}
        name = ws["title"]
        if "BUILD" not in phases:
            ws["tasks"].insert(0, {"title": f"Build {name}", "phase": "BUILD", "kind": "ai",
                                   "acceptance": f"{name} works end to end.",
                                   "steps": ["Design", "Implement", "Test"]})
        if "BREAK" not in phases:
            ws["tasks"].append({"title": f"Harden the failure path for {name}", "phase": "BREAK",
                                "kind": "ai",
                                "acceptance": f"Invalid input, limits, and errors in {name} are handled gracefully.",
                                "steps": ["Enumerate edge cases", "Add validation + error handling", "Test failures"]})
    return workstreams


def _fallback_workstreams(idea: str, areas: list[str]) -> list[dict]:
    """Deterministic ~6 workstreams when the LLM is unavailable."""
    titles = [a for a in (areas or []) if a][:6] or [
        "Foundations & Data Model", "Core Features", "Integrations",
        "Workflow & Automation", "Reliability & Security", "Launch & Handoff"]
    out = []
    for t in titles:
        out.append({"title": t, "tasks": [
            {"title": f"Build {t}", "phase": "BUILD", "kind": "ai",
             "acceptance": f"{t} works end to end per the design.",
             "steps": ["Design", "Implement", "Test"]},
            {"title": f"Handle failures and edge cases for {t}", "phase": "BREAK", "kind": "ai",
             "acceptance": f"Invalid input, limits, and errors in {t} are handled gracefully.",
             "steps": ["Enumerate edge cases", "Add validation + error handling", "Test failures"]},
        ]})
    return out


def generate_workstreams(idea: str, areas: list[str] | None = None) -> list[dict]:
    """Return a tight, robust build plan: ~6-9 workstreams, each with ~4-7 tasks
    (BUILD/BREAK/HARDEN, kind, acceptance covering happy+failure, sub-steps).
    Always returns ≥1 workstream, each guaranteed a BUILD and a BREAK task."""
    workstreams: list[dict] = []
    if is_available() and (idea or areas):
        try:
            resp = chat(
                system_prompt=_WS_SYSTEM,
                messages=[{"role": "user", "content": _WS_USER.format(
                    idea=(idea or "")[:3000],
                    areas="\n".join(f"- {a}" for a in (areas or [])[:25]) or "(none)")}],
                temperature=0.4,
                max_tokens=6000,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.content)
            for ws in (data.get("workstreams") or [])[:_MAX_WORKSTREAMS]:
                wtitle = (ws.get("title") or "").strip()
                tasks = [nt for nt in (_norm_ws_task(t) for t in (ws.get("tasks") or [])) if nt][:_MAX_TASKS]
                if wtitle and tasks:
                    workstreams.append({"title": wtitle, "tasks": tasks})
        except (LLMUnavailableError, LLMClientError, json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning("workstream generation LLM call failed: %s", e)
        except Exception:  # noqa: BLE001
            logger.warning("workstream generation unexpected error", exc_info=True)

    if not workstreams:
        workstreams = _fallback_workstreams(idea, areas or [])
    return _ensure_ws_build_break(workstreams)


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
                # Generous budget: a chapter can yield several features, each with
                # multiple BUILD/BREAK/HARDEN todos + acceptance. 1024 (the default)
                # truncates the JSON → parse failure → generic fallback.
                max_tokens=4000,
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
