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


def build_plan(slug: str, md: str, *, pace: str = "standard", source_doc: str = "") -> dict:
    """Build (and assign IDs to) a project-plan from a Build Guide markdown.

    Does NOT validate or persist — the caller validates (the verify gate) and
    saves. Returns the plan dict.
    """
    chapters = build_guide_parser.parse_build_guide(md)
    initiatives: list[dict] = []
    for ch in chapters:
        features = feature_task_generator.generate_features(ch["title"], ch["body"])
        lists: list[dict] = []
        for fi, feat in enumerate(features, 1):
            todos = [{
                "title": t["title"], "phase": t["phase"], "kind": t.get("kind", "ai"),
                "acceptance": t["acceptance"], "order": ti, "status": "active", "deps": [],
            } for ti, t in enumerate(feat["todos"], 1)]
            lists.append({
                "title": feat["title"], "order": fi, "status": "active",
                "designs": [], "todos": todos,
            })
        initiatives.append({
            "title": ch["title"], "order": ch["order"], "status": "active",
            "charter": build_guide_parser.first_sentence(ch["body"]),
            "docAnchor": ch["anchor"], "lists": lists,
            # metadata (not part of content_hash): lets re-parse detect which
            # chapters changed so it only regenerates those.
            "sourceBodyHash": build_guide_parser.source_sha256(ch["body"]),
        })

    plan = {
        "$schema": project_plan.SCHEMA,
        "projectSlug": slug,
        "sourceDoc": source_doc,
        "sourceDocVersion": "v1",
        "sourceDocSha256": build_guide_parser.source_sha256(md),
        "planRevision": 1,
        "peopleMap": {},
        "designs": [],
        "initiatives": initiatives,
    }
    _spread_due_offsets(plan, pace)
    project_plan.assign_ids(plan)
    return plan
