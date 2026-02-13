"""Core state engine for the AI Project Architect system.

Manages the project state JSON file: load, save, initialize, phase transitions,
outline locking/unlocking, chapter tracking, and approval recording.

This is the single most critical script — every other component depends on it.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config.settings import MAX_CHAPTER_REVISIONS, OUTPUT_DIR, PHASE_ORDER
from execution.build_depth import DEFAULT_DEPTH_MODE, DEPTH_MODES, resolve_depth_mode

PROFILE_REQUIRED_FIELDS = [
    "problem_definition",
    "target_user",
    "value_proposition",
    "deployment_type",
    "ai_depth",
    "monetization_model",
    "mvp_scope",
]


def _now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def _state_path(project_slug: str) -> Path:
    """Return the path to a project's state file."""
    return OUTPUT_DIR / project_slug / "project_state.json"


def _blank_profile_field() -> dict:
    """Return a blank profile field dict."""
    return {"selected": None, "confidence": None, "confirmed": False, "options": []}


def _blank_profile() -> dict:
    """Return a blank project_profile structure."""
    profile = {}
    for field in PROFILE_REQUIRED_FIELDS:
        profile[field] = _blank_profile_field()
    profile["technical_constraints"] = []
    profile["non_functional_requirements"] = []
    profile["success_metrics"] = []
    profile["risk_assessment"] = []
    profile["core_use_cases"] = []
    profile["selected_features"] = []
    profile["intelligence_goals"] = []
    profile["generated_at"] = None
    profile["confirmed_at"] = None
    return profile


def _ensure_project_profile(state: dict) -> dict:
    """Ensure project_profile exists in state (backward compat for old states)."""
    if "project_profile" not in state:
        state["project_profile"] = _blank_profile()
    return state["project_profile"]


def get_project_profile(state: dict) -> dict:
    """Return the project_profile dict, creating it if missing.

    Args:
        state: The project state dictionary.

    Returns:
        The project_profile dictionary.
    """
    return _ensure_project_profile(state)


def set_profile_field(
    state: dict,
    field: str,
    options: list[dict],
    recommended: str | None,
    confidence: float,
) -> dict:
    """Store LLM-generated options with recommended selection for one field.

    Args:
        state: The project state dictionary.
        field: One of the PROFILE_REQUIRED_FIELDS.
        options: List of option dicts (each has 'value', 'label', 'description').
        recommended: The recommended option value (pre-selected).
        confidence: Confidence score 0.0-1.0.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If field is not a valid profile field.
    """
    if field not in PROFILE_REQUIRED_FIELDS:
        raise ValueError(
            f"Invalid profile field: {field}. Must be one of {PROFILE_REQUIRED_FIELDS}"
        )
    profile = _ensure_project_profile(state)
    profile[field] = {
        "selected": recommended,
        "confidence": confidence,
        "confirmed": False,
        "options": options,
    }
    return state


def confirm_profile_field(state: dict, field: str, selected_value: str) -> dict:
    """Record user's confirmed selection for one field.

    Args:
        state: The project state dictionary.
        field: One of the PROFILE_REQUIRED_FIELDS.
        selected_value: The value the user selected.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If field is not a valid profile field.
    """
    if field not in PROFILE_REQUIRED_FIELDS:
        raise ValueError(
            f"Invalid profile field: {field}. Must be one of {PROFILE_REQUIRED_FIELDS}"
        )
    profile = _ensure_project_profile(state)
    profile[field]["selected"] = selected_value
    profile[field]["confirmed"] = True
    return state


def confirm_all_profile_fields(state: dict, selections: dict) -> dict:
    """Bulk-confirm all 7 required fields from form submission.

    Args:
        state: The project state dictionary.
        selections: Dict mapping field names to selected values.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If any required field is missing from selections.
    """
    missing = [f for f in PROFILE_REQUIRED_FIELDS if f not in selections or not selections[f]]
    if missing:
        raise ValueError(f"Missing required profile fields: {missing}")

    profile = _ensure_project_profile(state)
    for field in PROFILE_REQUIRED_FIELDS:
        profile[field]["selected"] = selections[field]
        profile[field]["confirmed"] = True
    profile["confirmed_at"] = _now()
    return state


def is_profile_complete(state: dict) -> bool:
    """Check if all 7 required profile fields have been confirmed.

    Args:
        state: The project state dictionary.

    Returns:
        True if all required fields have confirmed=True.
    """
    profile = _ensure_project_profile(state)
    return all(
        profile.get(field, {}).get("confirmed", False)
        for field in PROFILE_REQUIRED_FIELDS
    )


def set_profile_derived(
    state: dict,
    technical_constraints: list[str],
    nfrs: list[str],
    success_metrics: list[str],
    risk_assessment: list[str],
    core_use_cases: list[str],
) -> dict:
    """Store LLM-derived list fields in the project profile.

    Args:
        state: The project state dictionary.
        technical_constraints: List of technical constraint strings.
        nfrs: List of non-functional requirement strings.
        success_metrics: List of success metric strings.
        risk_assessment: List of risk assessment strings.
        core_use_cases: List of core use case strings.

    Returns:
        The updated state dictionary.
    """
    profile = _ensure_project_profile(state)
    profile["technical_constraints"] = technical_constraints
    profile["non_functional_requirements"] = nfrs
    profile["success_metrics"] = success_metrics
    profile["risk_assessment"] = risk_assessment
    profile["core_use_cases"] = core_use_cases
    profile["generated_at"] = _now()
    return state


def normalize_goal_data(goal: dict) -> dict:
    """Normalize an intelligence goal dict to the canonical field names.

    Handles backward compatibility: maps old field names (label, type,
    confidence_level) to new names (user_facing_label, goal_type,
    confidence_required). Passes through already-correct data unchanged.

    Args:
        goal: A single goal dict (old or new format).

    Returns:
        Goal dict with canonical field names.
    """
    if not isinstance(goal, dict):
        return goal
    return {
        "id": goal.get("id", ""),
        "user_facing_label": goal.get("user_facing_label") or goal.get("label", ""),
        "description": goal.get("description", ""),
        "goal_type": goal.get("goal_type") or goal.get("type", "recommendation"),
        "confidence_required": goal.get("confidence_required") or goal.get("confidence_level") or None,
        "impact_level": goal.get("impact_level") or None,
    }


def set_intelligence_goals(state: dict, goals: list[dict]) -> dict:
    """Store selected intelligence goals in the project profile.

    Normalizes field names on write so stored data always uses the
    canonical format (user_facing_label, goal_type, confidence_required).

    Args:
        state: The project state dictionary.
        goals: List of goal dicts (old or new format accepted).

    Returns:
        The updated state dictionary.
    """
    profile = _ensure_project_profile(state)
    profile["intelligence_goals"] = [normalize_goal_data(g) for g in goals]
    return state


def get_intelligence_goals(state: dict) -> list[dict]:
    """Return the list of intelligence goals from the profile.

    Normalizes field names on read so downstream code always sees
    the canonical format regardless of what was stored.

    Args:
        state: The project state dictionary.

    Returns:
        List of intelligence goal dicts with canonical field names.
    """
    profile = _ensure_project_profile(state)
    raw = profile.get("intelligence_goals", [])
    return [normalize_goal_data(g) for g in raw]


def initialize_state(project_name: str) -> dict:
    """Create a new blank project state with all required fields.

    Args:
        project_name: Human-readable project name.

    Returns:
        The initialized state dictionary.
    """
    now = _now()
    slug = _slugify(project_name)

    state = {
        "project": {
            "name": project_name,
            "slug": slug,
            "created_at": now,
            "updated_at": now,
        },
        "current_phase": "idea_intake",
        "idea": {
            "original_raw": "",
            "captured_at": None,
        },
        "project_profile": _blank_profile(),
        "ideation": {
            "business_model": {"status": "open", "responses": [], "summary": None},
            "user_problem": {"status": "open", "responses": [], "summary": None},
            "differentiation": {"status": "open", "responses": [], "summary": None},
            "ai_leverage": {"status": "open", "responses": [], "summary": None},
            "ideation_summary": None,
            "approved": False,
            "extracted_features": [],
        },
        "features": {
            "core": [],
            "optional": [],
            "approved": False,
            "catalog": [],
        },
        "outline": {
            "version": 1,
            "status": "draft",
            "locked_at": None,
            "locked_hash": None,
            "sections": [],
            "approval_history": [],
        },
        "chapters": [],
        "quality": {
            "final_report": {
                "ran_at": None,
                "all_passed": False,
                "details": [],
            }
        },
        "document": {
            "version": "v1",
            "filename": None,
            "assembled_at": None,
            "output_path": None,
        },
        "version_history": [
            {
                "version": 1,
                "created_at": now,
                "change_summary": "Initial project creation",
            }
        ],
        "chat": {
            "messages": [],
            "current_step": "idea_intake.welcome",
            "context": {},
        },
    }

    # Create project output directory and save
    project_dir = OUTPUT_DIR / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    save_state(state, slug)

    return state


def delete_project(project_slug: str) -> bool:
    """Delete a project's output directory and state file.

    Args:
        project_slug: The project's URL-safe identifier.

    Returns:
        True if the project was deleted, False if it didn't exist.

    Raises:
        ValueError: If the slug contains path traversal characters.
        OSError: If deletion fails due to locked files or permissions.
    """
    if ".." in project_slug or "/" in project_slug or "\\" in project_slug:
        raise ValueError(f"Invalid project slug: {project_slug}")

    project_dir = OUTPUT_DIR / project_slug
    state_file = project_dir / "project_state.json"

    if not project_dir.exists() or not state_file.exists():
        return False

    try:
        shutil.rmtree(project_dir)
    except PermissionError as e:
        raise OSError(
            f"Cannot delete project '{project_slug}': files are locked. "
            f"Stop any active builds and try again."
        ) from e

    return True


def load_state(project_slug: str) -> dict:
    """Load project state from the JSON file.

    Args:
        project_slug: The project's URL-safe identifier.

    Returns:
        The state dictionary.

    Raises:
        FileNotFoundError: If the state file does not exist.
        json.JSONDecodeError: If the state file contains invalid JSON.
    """
    path = _state_path(project_slug)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, project_slug: str) -> None:
    """Write state to JSON file with atomic write (write to temp, then rename).

    Args:
        state: The state dictionary to save.
        project_slug: The project's URL-safe identifier.
    """
    # Update the timestamp
    state["project"]["updated_at"] = _now()

    path = _state_path(project_slug)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix="state_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        # On Windows, need to remove target first if it exists
        if path.exists():
            path.unlink()
        os.rename(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def get_current_phase(state: dict) -> str:
    """Return the current pipeline phase.

    Args:
        state: The project state dictionary.

    Returns:
        The current phase string.
    """
    return state["current_phase"]


def advance_phase(state: dict, to_phase: str) -> dict:
    """Move to the next phase with validation.

    Phase transitions must follow the defined order. You cannot skip phases
    or go backward.

    Args:
        state: The project state dictionary.
        to_phase: The phase to transition to.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If the transition is invalid.
    """
    current = state["current_phase"]

    if to_phase not in PHASE_ORDER:
        raise ValueError(f"Invalid phase: {to_phase}")

    current_index = PHASE_ORDER.index(current)
    target_index = PHASE_ORDER.index(to_phase)

    if target_index != current_index + 1:
        raise ValueError(
            f"Invalid transition from '{current}' to '{to_phase}'. "
            f"Next valid phase is '{PHASE_ORDER[current_index + 1]}'"
            if current_index + 1 < len(PHASE_ORDER)
            else f"Cannot advance from '{current}' — already at final phase."
        )

    state["current_phase"] = to_phase
    return state


def record_idea(state: dict, raw_idea: str) -> dict:
    """Record the user's original idea verbatim.

    Args:
        state: The project state dictionary.
        raw_idea: The user's idea as provided.

    Returns:
        The updated state dictionary.
    """
    state["idea"]["original_raw"] = raw_idea
    state["idea"]["captured_at"] = _now()
    return state


def record_ideation_response(
    state: dict, dimension: str, question: str, answer: str
) -> dict:
    """Record a question-answer pair for an ideation dimension.

    Args:
        state: The project state dictionary.
        dimension: One of 'business_model', 'user_problem', 'differentiation', 'ai_leverage'.
        question: The question that was asked.
        answer: The user's response.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If the dimension is invalid.
    """
    valid_dimensions = [
        "business_model",
        "user_problem",
        "differentiation",
        "ai_leverage",
    ]
    if dimension not in valid_dimensions:
        raise ValueError(
            f"Invalid dimension: {dimension}. Must be one of {valid_dimensions}"
        )

    state["ideation"][dimension]["responses"].append(
        {"question": question, "answer": answer}
    )
    return state


def complete_ideation_dimension(state: dict, dimension: str, summary: str) -> dict:
    """Mark an ideation dimension as answered with a summary.

    Args:
        state: The project state dictionary.
        dimension: The dimension to complete.
        summary: Summary of the dimension's findings.

    Returns:
        The updated state dictionary.
    """
    valid_dimensions = [
        "business_model",
        "user_problem",
        "differentiation",
        "ai_leverage",
    ]
    if dimension not in valid_dimensions:
        raise ValueError(
            f"Invalid dimension: {dimension}. Must be one of {valid_dimensions}"
        )

    state["ideation"][dimension]["status"] = "answered"
    state["ideation"][dimension]["summary"] = summary
    return state


def approve_ideation(state: dict, summary: str) -> dict:
    """Approve the ideation phase with a synthesis summary.

    Args:
        state: The project state dictionary.
        summary: The ideation synthesis (who, what, why, AI fit, what NOT).

    Returns:
        The updated state dictionary.
    """
    state["ideation"]["ideation_summary"] = summary
    state["ideation"]["approved"] = True
    return state


def get_extracted_features(state: dict) -> list[dict]:
    """Return the list of features extracted during ideation.

    Args:
        state: The project state dictionary.

    Returns:
        List of extracted feature dicts (each has 'name' and 'description').
    """
    return state.get("ideation", {}).get("extracted_features", [])


def add_extracted_features(state: dict, features: list[dict]) -> dict:
    """Append extracted features from ideation, deduplicating by name.

    Args:
        state: The project state dictionary.
        features: List of dicts with 'name' and 'description' keys.

    Returns:
        The updated state dictionary.
    """
    existing = state.get("ideation", {}).get("extracted_features")
    if existing is None:
        state.setdefault("ideation", {})["extracted_features"] = []
        existing = state["ideation"]["extracted_features"]

    existing_names = {f["name"].lower() for f in existing}
    for feat in features:
        name = feat.get("name", "").strip()
        if not name:
            continue
        if name.lower() not in existing_names:
            existing.append({
                "name": name,
                "description": feat.get("description", "").strip(),
            })
            existing_names.add(name.lower())

    return state


def add_feature(
    state: dict,
    feature_type: str,
    feature_id: str,
    name: str,
    description: str,
    rationale: str,
    problem_mapped_to: str = "",
    build_order: int = 0,
    deferred: bool = False,
    defer_reason: str | None = None,
) -> dict:
    """Add a feature to the core or optional list.

    Args:
        state: The project state dictionary.
        feature_type: 'core' or 'optional'.
        feature_id: Unique identifier for the feature.
        name: Feature name.
        description: What the feature does.
        rationale: Why it exists.
        problem_mapped_to: (core only) Which problem this addresses.
        build_order: (core only) Build priority order.
        deferred: (optional only) Whether deferred.
        defer_reason: (optional only) Why deferred.

    Returns:
        The updated state dictionary.
    """
    if feature_type not in ("core", "optional"):
        raise ValueError(f"feature_type must be 'core' or 'optional', got '{feature_type}'")

    if feature_type == "core":
        feature = {
            "id": feature_id,
            "name": name,
            "description": description,
            "rationale": rationale,
            "problem_mapped_to": problem_mapped_to,
            "build_order": build_order,
        }
    else:
        feature = {
            "id": feature_id,
            "name": name,
            "description": description,
            "rationale": rationale,
            "deferred": deferred,
            "defer_reason": defer_reason,
        }

    state["features"][feature_type].append(feature)
    return state


def approve_features(state: dict) -> dict:
    """Mark feature discovery as approved.

    Args:
        state: The project state dictionary.

    Returns:
        The updated state dictionary.
    """
    state["features"]["approved"] = True
    return state


def set_outline_sections(state: dict, sections: list[dict]) -> dict:
    """Set the outline sections.

    Args:
        state: The project state dictionary.
        sections: List of section dicts with index, title, type, summary.

    Returns:
        The updated state dictionary.
    """
    state["outline"]["sections"] = sections
    return state


def _hash_outline(sections: list[dict]) -> str:
    """Compute SHA256 hash of outline sections for immutability checking."""
    content = json.dumps(sections, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def lock_outline(state: dict) -> dict:
    """Mark the outline as approved and immutable.

    Stores a hash of the outline content to detect unauthorized changes.
    Creates chapter entries for each outline section.

    Args:
        state: The project state dictionary.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If outline has no sections.
    """
    sections = state["outline"]["sections"]
    if not sections:
        raise ValueError("Cannot lock an outline with no sections")

    now = _now()
    state["outline"]["status"] = "approved"
    state["outline"]["locked_at"] = now
    state["outline"]["locked_hash"] = _hash_outline(sections)
    state["outline"]["approval_history"].append(
        {
            "version": state["outline"]["version"],
            "decision": "approved",
            "timestamp": now,
            "notes": None,
        }
    )

    # Create chapter entries for each section
    state["chapters"] = [
        {
            "index": section["index"],
            "outline_section": section["title"],
            "status": "pending",
            "revision_count": 0,
            "content_path": None,
            "quality_report": None,
            "approved_at": None,
        }
        for section in sections
    ]

    return state


def unlock_outline(state: dict, reason: str) -> dict:
    """Unlock the outline for modification, increment version.

    Args:
        state: The project state dictionary.
        reason: Why the outline is being unlocked.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If outline is not currently locked.
    """
    if state["outline"]["status"] != "approved":
        raise ValueError("Cannot unlock an outline that is not approved")

    now = _now()
    state["outline"]["status"] = "unlocked"
    state["outline"]["locked_at"] = None
    state["outline"]["locked_hash"] = None
    state["outline"]["version"] += 1
    state["outline"]["approval_history"].append(
        {
            "version": state["outline"]["version"],
            "decision": "revise",
            "timestamp": now,
            "notes": reason,
        }
    )

    # Add to version history
    state["version_history"].append(
        {
            "version": state["outline"]["version"],
            "created_at": now,
            "change_summary": f"Outline unlocked: {reason}",
        }
    )

    return state


def record_outline_decision(
    state: dict, decision: str, notes: str | None = None
) -> dict:
    """Record an outline approval decision (revise, expand, reduce).

    Args:
        state: The project state dictionary.
        decision: One of 'revise', 'expand', 'reduce'.
        notes: Optional notes about the decision.

    Returns:
        The updated state dictionary.
    """
    valid_decisions = ["revise", "expand", "reduce"]
    if decision not in valid_decisions:
        raise ValueError(
            f"Invalid decision: {decision}. Must be one of {valid_decisions}"
        )

    state["outline"]["approval_history"].append(
        {
            "version": state["outline"]["version"],
            "decision": decision,
            "timestamp": _now(),
            "notes": notes,
        }
    )
    return state


def get_chapter(state: dict, chapter_index: int) -> dict | None:
    """Get a chapter by its index.

    Args:
        state: The project state dictionary.
        chapter_index: The 1-based chapter index.

    Returns:
        The chapter dict, or None if not found.
    """
    for chapter in state["chapters"]:
        if chapter["index"] == chapter_index:
            return chapter
    return None


def record_chapter_status(
    state: dict, chapter_index: int, status: str, content_path: str | None = None
) -> dict:
    """Update a chapter's status and optionally its content path.

    Args:
        state: The project state dictionary.
        chapter_index: The 1-based chapter index.
        status: The new status.
        content_path: Path to the chapter's Markdown file.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If chapter not found or revision limit exceeded.
    """
    chapter = get_chapter(state, chapter_index)
    if chapter is None:
        raise ValueError(f"Chapter {chapter_index} not found")

    valid_statuses = ["pending", "draft", "revision_1", "revision_2", "approved"]
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

    # Track revision count
    if status.startswith("revision_"):
        revision_num = int(status.split("_")[1])
        if revision_num > MAX_CHAPTER_REVISIONS:
            raise ValueError(
                f"Chapter {chapter_index} has reached the maximum of "
                f"{MAX_CHAPTER_REVISIONS} revisions"
            )
        chapter["revision_count"] = revision_num

    if status == "approved":
        chapter["approved_at"] = _now()

    chapter["status"] = status
    if content_path is not None:
        chapter["content_path"] = content_path

    return state


def get_revision_count(state: dict, chapter_index: int) -> int:
    """Return how many revisions a chapter has had.

    Args:
        state: The project state dictionary.
        chapter_index: The 1-based chapter index.

    Returns:
        The revision count.

    Raises:
        ValueError: If chapter not found.
    """
    chapter = get_chapter(state, chapter_index)
    if chapter is None:
        raise ValueError(f"Chapter {chapter_index} not found")
    return chapter["revision_count"]


def record_chapter_quality(state: dict, chapter_index: int, report: dict) -> dict:
    """Record quality gate results for a chapter.

    Args:
        state: The project state dictionary.
        chapter_index: The 1-based chapter index.
        report: Quality report dict with gate results.

    Returns:
        The updated state dictionary.
    """
    chapter = get_chapter(state, chapter_index)
    if chapter is None:
        raise ValueError(f"Chapter {chapter_index} not found")
    chapter["quality_report"] = report
    return state


def record_final_quality(state: dict, report: dict) -> dict:
    """Record the final document quality gate results.

    Args:
        state: The project state dictionary.
        report: Final quality report dict.

    Returns:
        The updated state dictionary.
    """
    state["quality"]["final_report"] = {
        "ran_at": _now(),
        "all_passed": report.get("all_passed", False),
        "details": report.get("details", []),
    }
    return state


def record_document_assembly(
    state: dict, filename: str, output_path: str
) -> dict:
    """Record that the final document has been assembled.

    Args:
        state: The project state dictionary.
        filename: The document filename.
        output_path: Path to the assembled document.

    Returns:
        The updated state dictionary.
    """
    state["document"]["filename"] = filename
    state["document"]["output_path"] = output_path
    state["document"]["assembled_at"] = _now()
    return state


def all_chapters_approved(state: dict) -> bool:
    """Check if all chapters have been approved.

    Args:
        state: The project state dictionary.

    Returns:
        True if all chapters are approved, False otherwise.
    """
    if not state["chapters"]:
        return False
    return all(ch["status"] == "approved" for ch in state["chapters"])


def is_outline_locked(state: dict) -> bool:
    """Check if the outline is currently locked (approved).

    Args:
        state: The project state dictionary.

    Returns:
        True if the outline is locked.
    """
    return state["outline"]["status"] == "approved"


def verify_outline_integrity(state: dict) -> bool:
    """Verify that the outline has not been modified since locking.

    Args:
        state: The project state dictionary.

    Returns:
        True if the outline matches its locked hash, False otherwise.
    """
    if not is_outline_locked(state):
        return False
    current_hash = _hash_outline(state["outline"]["sections"])
    return current_hash == state["outline"]["locked_hash"]


def set_build_depth_mode(state: dict, mode: str) -> dict:
    """Set the build depth mode in the project profile.

    Args:
        state: The project state dictionary.
        mode: One of 'light', 'standard', 'professional', 'enterprise'
              (or legacy aliases 'lite', 'architect').

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If mode is not valid.
    """
    resolved = resolve_depth_mode(mode)
    profile = _ensure_project_profile(state)
    profile["build_depth_mode"] = resolved
    return state


def get_build_depth_mode(state: dict) -> str:
    """Return the build depth mode, resolving legacy aliases.

    Args:
        state: The project state dictionary.

    Returns:
        The canonical depth mode string.
    """
    profile = _ensure_project_profile(state)
    raw_mode = profile.get("build_depth_mode", DEFAULT_DEPTH_MODE)
    try:
        return resolve_depth_mode(raw_mode)
    except ValueError:
        return DEFAULT_DEPTH_MODE


def record_chapter_score(state: dict, chapter_index: int, score: dict) -> dict:
    """Record quality scoring results for a chapter.

    Args:
        state: The project state dictionary.
        chapter_index: The 1-based chapter index.
        score: Scoring dict with total_score, word_count, status, etc.

    Returns:
        The updated state dictionary.

    Raises:
        ValueError: If chapter not found.
    """
    chapter = get_chapter(state, chapter_index)
    if chapter is None:
        raise ValueError(f"Chapter {chapter_index} not found")
    chapter["chapter_score"] = score
    return state


def _ensure_chat(state: dict) -> dict:
    """Ensure the chat key exists in state (for backwards compatibility)."""
    if "chat" not in state:
        state["chat"] = {
            "messages": [],
            "current_step": f"{state['current_phase']}.welcome",
            "context": {},
        }
    return state["chat"]


def append_chat_message(
    state: dict,
    role: str,
    text: str,
    options: list[str] | None = None,
    options_mode: str | None = None,
) -> dict:
    """Append a message to the chat history.

    Args:
        state: The project state dictionary.
        role: 'bot' or 'user'.
        text: The message text.
        options: Optional list of clickable options (bot messages only).
        options_mode: 'single' or 'multi' (when options are present).

    Returns:
        The updated state dictionary.
    """
    if role not in ("bot", "user"):
        raise ValueError(f"Invalid chat role: {role}. Must be 'bot' or 'user'")
    chat = _ensure_chat(state)
    msg = {
        "role": role,
        "text": text,
        "timestamp": _now(),
    }
    if options is not None and role == "bot":
        msg["options"] = options
        msg["options_mode"] = options_mode or "single"
    chat["messages"].append(msg)
    return state


def get_chat_step(state: dict) -> str:
    """Return the current chat conversation step.

    Args:
        state: The project state dictionary.

    Returns:
        The current step identifier (e.g. 'idea_intake.welcome').
    """
    chat = _ensure_chat(state)
    return chat["current_step"]


def set_chat_step(state: dict, step_id: str) -> dict:
    """Set the current chat conversation step.

    Args:
        state: The project state dictionary.
        step_id: The new step identifier.

    Returns:
        The updated state dictionary.
    """
    chat = _ensure_chat(state)
    chat["current_step"] = step_id
    return state
