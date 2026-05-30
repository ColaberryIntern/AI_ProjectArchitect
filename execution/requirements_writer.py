"""Requirements artifact writer.

Serializes the project's Requirement objects (promoted features) to
``output/{slug}/specs/requirements.json``. This is the spec-driven layer's
single source of truth for downstream gates and any external consumer.

Traceability and acceptance criteria live INSIDE this file as fields on
each Requirement — there is no separate Gherkin export, no separate
traceability CSV. See plan note in
``C:/Users/ali_m/.claude/plans/look-at-the-requirement-synthetic-bear.md``.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.feature_classifier import promote_to_requirement

logger = logging.getLogger(__name__)

REQUIREMENTS_SCHEMA_VERSION = "1.0"


def _specs_dir(slug: str) -> Path:
    return OUTPUT_DIR / slug / "specs"


def _requirements_path(slug: str) -> Path:
    return _specs_dir(slug) / "requirements.json"


def collect_requirements(state: dict) -> list[dict]:
    """Pull core + optional features from state and promote each to a Requirement.

    Promotion is idempotent: features already containing Requirement
    fields keep them; missing fields are seeded with defaults.

    The ``type`` field (``core`` / ``optional``) is injected here based on
    which bucket the feature came from. This is necessary because
    features stored under ``state.features.core[]`` don't carry the
    ``type`` field — their MVP class is implied by the bucket. Without
    this, ``promote_to_requirement`` would default core features to
    ``priority: should`` instead of ``must``.

    Args:
        state: The project state dict (as returned by state_manager).

    Returns:
        List of Requirement dicts. Order: core first (by build_order), then
        optional. The order matches the build sequence.
    """
    features = state.get("features") or {}
    core = sorted(
        features.get("core") or [],
        key=lambda f: f.get("build_order", 999),
    )
    optional = features.get("optional") or []
    promoted_core = [promote_to_requirement({"type": "core", **f}) for f in core]
    promoted_optional = [
        promote_to_requirement({"type": "optional", **f}) for f in optional
    ]
    return promoted_core + promoted_optional


def build_requirements_document(state: dict) -> dict:
    """Build the JSON document that gets written to disk.

    Includes a small envelope so downstream consumers can detect schema
    drift without parsing the inner array.

    Args:
        state: The project state dict.

    Returns:
        Dict with keys ``schema_version``, ``project``, ``generated_at``,
        ``requirements`` (list), and ``summary`` (counts by priority/type).
    """
    requirements = collect_requirements(state)

    summary = {
        "total": len(requirements),
        "by_priority": _count_by(requirements, "priority"),
        "by_type": _count_by(requirements, "requirement_type"),
        "by_mvp_class": _count_by(requirements, "type"),
        "with_acceptance_criteria": sum(
            1 for r in requirements if r.get("acceptance_criteria")
        ),
        "with_nfr": sum(1 for r in requirements if r.get("nfr")),
    }

    return {
        "schema_version": REQUIREMENTS_SCHEMA_VERSION,
        "project": {
            "name": (state.get("project") or {}).get("name", ""),
            "slug": (state.get("project") or {}).get("slug", ""),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "requirements": requirements,
    }


def _count_by(requirements: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in requirements:
        v = r.get(key) or "unspecified"
        counts[v] = counts.get(v, 0) + 1
    return counts


def write_requirements(state: dict, slug: str | None = None) -> Path:
    """Write the Requirements artifact for a project, atomically.

    Atomicity: writes to a temp file in the same directory, then renames.
    A partial write cannot leave a corrupt requirements.json.

    Args:
        state: The project state dict.
        slug: Project slug. If None, read from ``state['project']['slug']``.

    Returns:
        The absolute path to the written file.
    """
    if slug is None:
        slug = (state.get("project") or {}).get("slug", "")
        if not slug:
            raise ValueError("slug is required (not present in state)")

    target_dir = _specs_dir(slug)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = _requirements_path(slug)

    document = build_requirements_document(state)

    fd, tmp_path = tempfile.mkstemp(dir=str(target_dir), suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(document, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(target_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    logger.info(
        "Wrote %d requirements to %s", len(document["requirements"]), target_path
    )
    return target_path


def read_requirements(slug: str) -> dict | None:
    """Read the Requirements artifact for a project, if it exists.

    Args:
        slug: Project slug.

    Returns:
        The parsed document dict, or None if the file does not exist.
    """
    path = _requirements_path(slug)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
