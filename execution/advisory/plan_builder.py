"""Assemble a ``project-plan.json`` from a generated Build Guide.

Ties the spine parser + per-chapter task generator together:
  chapters → initiatives, generated features → lists, generated todos → todos.
Then spreads ``dueOffsetDays`` across the pace window by build order (so every
todo gets a due date), assigns deterministic IDs (the ID Law), and writes
``output/{slug}/project-plan.json``. The orchestrator validates the result
(the "verify" gate) before any Basecamp write.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.advisory import build_guide_parser, feature_task_generator, project_plan

PLAN_FILENAME = "project-plan.json"
PACE_WINDOW = {"sprint": 7, "standard": 30, "relaxed": 90}


def _plan_path(slug: str) -> Path:
    return OUTPUT_DIR / slug / PLAN_FILENAME


def load_plan(slug: str) -> dict | None:
    p = _plan_path(slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_plan(slug: str, plan: dict) -> Path:
    path = _plan_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=PLAN_FILENAME + ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False, default=str)
        Path(tmp_path).replace(path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return path


def _spread_due_offsets(plan: dict, pace: str) -> None:
    """Assign each todo a dueOffsetDays spread across the pace window by build
    order (document order). Guarantees every todo has a due offset ≥ 1."""
    window = PACE_WINDOW.get(pace, PACE_WINDOW["standard"])
    todos = [n for lvl, n, _ in project_plan.iter_nodes(plan) if lvl == "todo"]
    n = len(todos)
    for i, t in enumerate(todos):
        t["dueOffsetDays"] = window if n <= 1 else max(1, round((i + 1) / n * window))


def build_plan(slug: str, md: str, idea: str = "", *, project_name: str = "",
               pace: str = "standard", source_doc: str = "") -> dict:
    """Build (and assign IDs to) a workstream-shaped project-plan.

    The Build Guide's chapter titles are context; the plan itself is a tight set
    of WORKSTREAMS (one initiative → workstreams as feature-groups → robust
    tasks as todos), so Basecamp gets ONE list with ~6-9 task groups instead of
    a per-chapter explosion. Does NOT validate or persist — the caller does.
    """
    chapters = build_guide_parser.parse_build_guide(md)
    area_titles = [c["title"] for c in chapters]
    workstreams = feature_task_generator.generate_workstreams(idea, area_titles)

    lists: list[dict] = []
    for wi, ws in enumerate(workstreams, 1):
        todos = [{
            "title": t["title"], "phase": t["phase"], "kind": t.get("kind", "ai"),
            "acceptance": t["acceptance"], "steps": t.get("steps", []),
            "order": ti, "status": "active", "deps": [],
        } for ti, t in enumerate(ws["tasks"], 1)]
        lists.append({
            "title": ws["title"], "order": wi, "status": "active",
            "designs": [], "todos": todos,
        })

    # One initiative wraps the workstreams (reconciler renders them as the
    # groups of a single project list).
    initiative = {
        "title": (project_name or slug) + " — Build Plan", "order": 1, "status": "active",
        "charter": build_guide_parser.first_sentence(md) or "Build plan.",
        "lists": lists,
        "sourceBodyHash": build_guide_parser.source_sha256(md),
    }

    plan = {
        "$schema": project_plan.SCHEMA,
        "projectSlug": slug,
        "projectName": project_name or slug,
        "sourceDoc": source_doc,
        "sourceDocVersion": "v1",
        "sourceDocSha256": build_guide_parser.source_sha256(md),
        "planRevision": 1,
        "peopleMap": {},
        "designs": [],
        "initiatives": [initiative],
    }
    _spread_due_offsets(plan, pace)
    project_plan.assign_ids(plan)
    return plan
