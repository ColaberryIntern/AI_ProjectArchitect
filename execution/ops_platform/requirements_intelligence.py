"""Requirements intelligence — extracts reusable patterns from runs.

This is the bridge from the AI Operations Platform to the existing Project
Requirements Generation system. Every successful workflow run is mined for:

  - reusable_requirements      → can be promoted to a Requirement
  - architecture_decisions     → can lock the frozen_architecture stack
  - reusable_components        → seeds the component catalog
  - successful_prompts         → eligible for promotion to a prompt_pack
  - common_workflows           → identifies candidates for new workflow plugins

Extracts are persisted to output/ops_platform/intelligence/extracts.jsonl
(append-only, one extract per run) plus a per-capability aggregate at
output/ops_platform/intelligence/aggregate.json.

The aggregate is the thing the existing requirements_writer reads to enrich
a new project's requirements set. We don't modify requirements_writer — we
expose ``feed_into_project(slug)`` that pulls the aggregate and produces
extra Requirement-shaped suggestions that the chapter writer / outline
generator can use as candidate features.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from config.settings import OUTPUT_DIR, SCHEMAS_DIR
from execution.ops_platform.workflow_runner import RunRecord

logger = logging.getLogger(__name__)

_INTELLIGENCE_DIR = OUTPUT_DIR / "ops_platform" / "intelligence"
_EXTRACTS_PATH = _INTELLIGENCE_DIR / "extracts.jsonl"
_AGGREGATE_PATH = _INTELLIGENCE_DIR / "aggregate.json"
_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "intelligence_extract.schema.json"


# ── Public API ──────────────────────────────────────────────────────────


def extract_from_run(run: RunRecord) -> dict | None:
    """Extract patterns from a single succeeded run. Persist + return them.

    Returns None when the run did not succeed or when its response lacks
    the contract fields we mine. Failures are logged, never raised — this
    is a non-blocking side effect of workflow_runner.
    """
    if run.status != "succeeded" or not run.response:
        return None

    response = run.response

    extract = {
        "run_id": run.run_id,
        "capability_id": run.capability_id,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "patterns": {
            "reusable_requirements": _mine_requirements(response),
            "architecture_decisions": _mine_architecture(response),
            "reusable_components": _mine_components(response),
            "successful_prompts": _mine_prompts(response, run.capability_id),
            "common_workflows": _mine_workflows(response),
        },
    }

    errors = _validate(extract)
    if errors:
        logger.warning("intelligence extract failed schema for run %s: %s", run.run_id, errors)
        return None

    _append_extract(extract)
    _recompute_aggregate()
    return extract


def feed_into_project(slug: str, *, top_n: int = 10) -> list[dict]:
    """Produce candidate Requirement objects for a project from the aggregate.

    These are SUGGESTIONS — the requirements_writer / outline generator can
    optionally include them. We never mutate an existing project state from
    here.

    The shape matches the existing feature.schema.json Requirement shape so
    they can be promoted directly into state.features.core or .optional.
    """
    aggregate = _load_aggregate()
    suggestions: list[dict] = []
    seen_actions: set[str] = set()

    ranked = sorted(
        aggregate.get("reusable_requirements", []),
        key=lambda r: r.get("seen_count", 0),
        reverse=True,
    )
    for entry in ranked[:top_n]:
        action_key = (entry.get("action") or entry.get("name") or "").lower().strip()
        if not action_key or action_key in seen_actions:
            continue
        seen_actions.add(action_key)
        suggestions.append(_to_feature_shape(entry, slug=slug))

    return suggestions


def load_aggregate() -> dict:
    """Expose the aggregate for the UI."""
    return _load_aggregate()


# ── Mining helpers ──────────────────────────────────────────────────────


def _mine_requirements(response: dict) -> list[dict]:
    """Turn 'components_added' + 'routes_added' + 'next_recommended_tasks'
    into reusable_requirements entries.
    """
    out: list[dict] = []

    for comp in response.get("components_added") or []:
        name = comp.get("name", "")
        if not name:
            continue
        out.append({
            "name": name,
            "description": comp.get("purpose") or f"Component '{name}'",
            "actor": "system",
            "action": f"provide {name}",
            "value": comp.get("purpose", ""),
            "suggested_priority": "should",
            "source": "components_added",
        })

    for route in response.get("routes_added") or []:
        path = route.get("path", "")
        method = route.get("method", "GET")
        if not path:
            continue
        out.append({
            "name": f"{method} {path}",
            "description": route.get("purpose") or f"HTTP endpoint {method} {path}",
            "actor": "client",
            "action": f"call {method} {path}",
            "value": route.get("purpose", ""),
            "suggested_priority": "must",
            "source": "routes_added",
        })

    for task in response.get("next_recommended_tasks") or []:
        text = task.get("task", "")
        if not text:
            continue
        priority_map = {"low": "could", "medium": "should", "high": "must"}
        out.append({
            "name": text[:80],
            "description": text,
            "actor": "team",
            "action": text,
            "value": "",
            "suggested_priority": priority_map.get(task.get("priority", "medium"), "should"),
            "source": "next_recommended_tasks",
        })

    return out


def _mine_architecture(response: dict) -> list[dict]:
    """Each dependency_added + database_change is a frozen-architecture signal."""
    out: list[dict] = []
    for dep in response.get("dependencies_added") or []:
        name = dep.get("name", "")
        if not name:
            continue
        out.append({
            "decision": f"Use {name}",
            "category": dep.get("ecosystem", "library"),
            "rationale": dep.get("reason", ""),
        })
    for change in response.get("database_changes") or []:
        target = change.get("target", "")
        kind = change.get("change_type", "")
        if not target:
            continue
        out.append({
            "decision": f"Database: {kind} on {target}",
            "category": "database",
            "rationale": change.get("description", ""),
        })
    return out


def _mine_components(response: dict) -> list[dict]:
    out: list[dict] = []
    for comp in response.get("components_added") or []:
        name = comp.get("name", "")
        if not name:
            continue
        out.append({
            "name": name,
            "kind": comp.get("kind", "component"),
            "purpose": comp.get("purpose", ""),
            "path_hint": "",
        })
    for f in response.get("files_created") or []:
        path = f.get("path", "")
        if not path:
            continue
        out.append({
            "name": Path(path).stem,
            "kind": "file",
            "purpose": f.get("purpose", ""),
            "path_hint": path,
        })
    return out


def _mine_prompts(response: dict, capability_id: str) -> list[dict]:
    """The summary, if present, is treated as a candidate prompt-pack entry."""
    summary = response.get("summary") or ""
    if not summary or len(summary) < 40:
        return []
    return [{
        "context": f"capability:{capability_id}",
        "summary": summary[:500],
        "plugin_id": capability_id,
    }]


def _mine_workflows(response: dict) -> list[dict]:
    """next_recommended_tasks pointing at a suggested plugin = candidate new workflow."""
    out: list[dict] = []
    for task in response.get("next_recommended_tasks") or []:
        if task.get("suggested_plugin"):
            out.append({
                "name": task.get("task", "")[:80],
                "trigger": "follow-up to previous run",
                "steps_seen": 1,
            })
    return out


# ── Persistence + aggregation ───────────────────────────────────────────


def _append_extract(extract: dict) -> None:
    _INTELLIGENCE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_EXTRACTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(extract, ensure_ascii=False) + "\n")


def _load_extracts() -> list[dict]:
    if not _EXTRACTS_PATH.exists():
        return []
    out: list[dict] = []
    with open(_EXTRACTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _recompute_aggregate() -> None:
    extracts = _load_extracts()

    requirement_counter: Counter = Counter()
    requirement_index: dict[str, dict] = {}
    decision_counter: Counter = Counter()
    decision_index: dict[str, dict] = {}
    component_counter: Counter = Counter()
    component_index: dict[str, dict] = {}

    for ex in extracts:
        for r in ex.get("patterns", {}).get("reusable_requirements", []):
            key = (r.get("action") or r.get("name") or "").lower().strip()
            if not key:
                continue
            requirement_counter[key] += 1
            requirement_index.setdefault(key, dict(r))
        for d in ex.get("patterns", {}).get("architecture_decisions", []):
            key = (d.get("decision") or "").lower().strip()
            if not key:
                continue
            decision_counter[key] += 1
            decision_index.setdefault(key, dict(d))
        for c in ex.get("patterns", {}).get("reusable_components", []):
            key = (c.get("name") or "").lower().strip()
            if not key:
                continue
            component_counter[key] += 1
            component_index.setdefault(key, dict(c))

    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "extract_count": len(extracts),
        "reusable_requirements": _rank(requirement_index, requirement_counter),
        "architecture_decisions": _rank(decision_index, decision_counter),
        "reusable_components": _rank(component_index, component_counter),
    }

    _INTELLIGENCE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(_INTELLIGENCE_DIR), suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(aggregate, f, indent=2)
        tmp.replace(_AGGREGATE_PATH)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _rank(index: dict[str, dict], counter: Counter) -> list[dict]:
    out: list[dict] = []
    for key, count in counter.most_common():
        entry = dict(index[key])
        entry["seen_count"] = count
        out.append(entry)
    return out


def _load_aggregate() -> dict:
    if not _AGGREGATE_PATH.exists():
        return {
            "generated_at": None,
            "extract_count": 0,
            "reusable_requirements": [],
            "architecture_decisions": [],
            "reusable_components": [],
        }
    with open(_AGGREGATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_feature_shape(entry: dict, *, slug: str) -> dict:
    """Convert an aggregate reusable_requirement into a Requirement candidate
    that matches the existing feature.schema.json shape.
    """
    return {
        "id": f"REQ-OPS-{abs(hash(entry.get('action', '')))%10000:04d}",
        "name": entry.get("name", "Reusable Requirement"),
        "description": entry.get("description", ""),
        "rationale": (
            f"Suggested by the Operations Platform — seen {entry.get('seen_count', 0)} "
            f"times across workflow executions. Source: {entry.get('source', 'unknown')}."
        ),
        "type": "optional",
        "actor": entry.get("actor", "system"),
        "action": entry.get("action", ""),
        "value": entry.get("value", ""),
        "priority": entry.get("suggested_priority", "should"),
        "acceptance_criteria": [],
        "nfr": [],
        "traces_to": {
            "outline_section_id": None,
            "chapter_ids": [],
            "problem_id": f"ops_platform_intelligence:{slug}",
        },
    }


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    try:
        return _SCHEMA_CACHE
    except NameError:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
        return _SCHEMA_CACHE


def _validate(extract: dict) -> list[str]:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(extract), key=lambda e: e.absolute_path)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    ]
