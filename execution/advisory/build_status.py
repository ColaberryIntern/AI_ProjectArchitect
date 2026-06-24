"""Status file for a My-Day-initiated project build.

A "create a new project" build runs in a background thread (see
``myday_build_orchestrator``). Its progress is written to
``output/{slug}/build_status.json`` so the buildout page can poll it and the My
Day briefing can show a "building in the background" banner.

The file is written atomically (tempfile + replace) and ``write_status`` merges
into whatever is already there, so each phase only has to pass the fields it
changes. Mirrors the atomic-write style of ``execution.products.ops.store``.

Phases (also drive the percent the UI shows):
    advisory  → the 9-stage AI-org generation is running
    tasks     → generating the project's build tasks (requirements)
    basecamp  → creating the Basecamp list + assigned, due-dated todos
    done      → finished; tasks_created / todolist_url populated
    error     → failed; ``error`` populated
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import OUTPUT_DIR

# Phase → default percent (the orchestrator may override message/percent).
PHASE_PERCENT = {
    "starting": 5,
    "advisory": 25,
    "tasks": 55,
    "basecamp": 80,
    "done": 100,
    "error": 100,
}

_ACTIVE_PHASES = {"starting", "advisory", "tasks", "basecamp"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_path(slug: str) -> Path:
    return OUTPUT_DIR / slug / "build_status.json"


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.name + ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        Path(tmp_path).replace(path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_status(slug: str) -> dict | None:
    """Return the build status dict for a slug, or None if there is none."""
    p = _status_path(slug)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_status(slug: str, **fields: Any) -> dict:
    """Merge ``fields`` into the slug's build status and write it atomically.

    Always stamps ``updated_at`` (and ``started_at`` on first write). If
    ``phase`` is given without an explicit ``percent``, the phase's default
    percent is filled in. Returns the merged status dict.
    """
    current = read_status(slug) or {"slug": slug, "started_at": _now()}
    current.update(fields)
    phase = current.get("phase")
    if phase and "percent" not in fields and phase in PHASE_PERCENT:
        current["percent"] = PHASE_PERCENT[phase]
    current["updated_at"] = _now()
    _atomic_write(_status_path(slug), current)
    return current


def active_builds_for_operator(operator_email: str) -> list[dict]:
    """All in-progress builds owned by this operator (phase not done/error).

    Scans ``output/*/build_status.json``. Used by My Day to show a
    "project building in the background" banner.
    """
    out: list[dict] = []
    email = (operator_email or "").strip().lower()
    if not email or not OUTPUT_DIR.exists():
        return out
    for status_file in OUTPUT_DIR.glob("*/build_status.json"):
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (data.get("operator_email") or "").strip().lower() != email:
            continue
        if data.get("phase") in _ACTIVE_PHASES:
            out.append(data)
    return out
