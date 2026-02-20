"""Full pipeline orchestrator: from raw idea to completed document.

Takes a raw project idea (or detailed campaign text), runs the complete
8-phase pipeline, and produces a final assembled document. Designed
to run in a background thread.

Reuses all existing execution scripts — no logic is duplicated.

Phases:
    1. Idea Intake         (0-5%)   — Initialize state, record idea
    2. Profile Generation  (5-10%)  — LLM generates project profile
    3. Feature Discovery   (10-20%) — LLM generates + auto-selects features
    4. Outline Generation  (20-25%) — LLM generates outline from profile
    5. Outline Approval    (25-28%) — Lock outline, set depth mode
    6-9. Auto-Build        (28-100%) — Delegated to auto_builder.run_auto_build()
"""

import logging
import re
import threading
from typing import Generator

from execution.auto_builder import BuildEvent, run_auto_build
from execution.build_depth import resolve_depth_mode
from execution.feature_catalog import generate_catalog, generate_catalog_from_profile
from execution.outline_generator import generate_outline_from_profile
from execution.profile_generator import generate_profile
from execution.state_manager import (
    PROFILE_REQUIRED_FIELDS,
    add_feature,
    advance_phase,
    approve_features,
    confirm_all_profile_fields,
    get_project_profile,
    initialize_state,
    is_profile_complete,
    load_state,
    lock_outline,
    record_idea,
    save_state,
    set_build_depth_mode,
    set_outline_sections,
    set_profile_derived,
    set_profile_field,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-safe progress store (mirrors auto_builder._build_progress pattern)
# ---------------------------------------------------------------------------

_pipeline_progress: dict[str, list[BuildEvent]] = {}
_pipeline_lock = threading.Lock()


def get_pipeline_progress(job_id: str) -> list[BuildEvent]:
    """Get all progress events for a pipeline job."""
    with _pipeline_lock:
        return list(_pipeline_progress.get(job_id, []))


def _append_pipeline_event(job_id: str, event: BuildEvent) -> None:
    """Thread-safe append of a pipeline event."""
    with _pipeline_lock:
        if job_id not in _pipeline_progress:
            _pipeline_progress[job_id] = []
        _pipeline_progress[job_id].append(event)


def clear_pipeline_progress(job_id: str) -> None:
    """Clear progress events for a completed pipeline."""
    with _pipeline_lock:
        _pipeline_progress.pop(job_id, None)


def is_pipeline_running(job_id: str) -> bool:
    """Check if a pipeline is currently in progress for this job."""
    events = get_pipeline_progress(job_id)
    if not events:
        return False
    last = events[-1]
    return last.event_type not in ("complete", "error")


def _slugify(name: str) -> str:
    """Mirror state_manager._slugify for slug prediction."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# ---------------------------------------------------------------------------
# Pipeline generator
# ---------------------------------------------------------------------------


def run_full_pipeline(
    project_name: str,
    raw_idea: str,
    depth_mode: str = "professional",
) -> Generator[BuildEvent, None, None]:
    """Run the complete pipeline from idea to assembled document.

    Yields BuildEvent for each significant step. Can be consumed
    directly or via run_full_pipeline_sync().

    Args:
        project_name: Human-readable project name.
        raw_idea: Detailed idea/campaign text.
        depth_mode: Build depth (default "professional").

    Yields:
        BuildEvent for each pipeline step.
    """
    try:
        # Validate depth mode early
        resolved_depth = resolve_depth_mode(depth_mode)

        # ── Phase 1: Idea Intake ────────────────────────────────
        yield BuildEvent("phase", "Initializing project...", 0, 0, 2)

        state = initialize_state(project_name)
        slug = state["project"]["slug"]
        record_idea(state, raw_idea)
        save_state(state, slug)

        # ── Profile Generation ──────────────────────────────────
        yield BuildEvent("phase", "Generating project profile...", 0, 0, 5)

        profile_data = generate_profile(raw_idea)

        for field in PROFILE_REQUIRED_FIELDS:
            field_data = profile_data["fields"].get(field, {})
            set_profile_field(
                state,
                field,
                field_data.get("options", []),
                field_data.get("recommended"),
                field_data.get("confidence", 0.0),
            )

        derived = profile_data.get("derived", {})
        set_profile_derived(
            state,
            technical_constraints=derived.get("technical_constraints", []),
            nfrs=derived.get("non_functional_requirements", []),
            success_metrics=derived.get("success_metrics", []),
            risk_assessment=derived.get("risk_assessment", []),
            core_use_cases=derived.get("core_use_cases", []),
        )

        # Auto-confirm all profile fields using recommended values
        profile = get_project_profile(state)
        selections = {}
        for field in PROFILE_REQUIRED_FIELDS:
            selections[field] = profile[field].get("selected", "")
        confirm_all_profile_fields(state, selections)
        save_state(state, slug)

        yield BuildEvent("phase", "Profile generated and confirmed", 0, 0, 10)

        # ── Advance: idea_intake → feature_discovery ────────────
        advance_phase(state, "feature_discovery")
        save_state(state, slug)

        # ── Phase 2: Feature Discovery ──────────────────────────
        yield BuildEvent("phase", "Generating feature catalog...", 0, 0, 12)

        profile = get_project_profile(state)
        if is_profile_complete(state):
            catalog = generate_catalog_from_profile(profile)
        else:
            catalog = generate_catalog(raw_idea)

        state["features"]["catalog"] = catalog

        # Auto-select all features from catalog
        for i, feat in enumerate(catalog, 1):
            add_feature(
                state,
                "core",
                feat["id"],
                feat["name"],
                feat["description"],
                "Auto-selected during one-shot generation",
                problem_mapped_to="core problem",
                build_order=i,
            )

        profile["selected_features"] = [f["id"] for f in catalog]
        approve_features(state)
        save_state(state, slug)

        yield BuildEvent(
            "phase",
            f"Feature catalog generated: {len(catalog)} features selected",
            0, 0, 18,
        )

        # ── Advance: feature_discovery → outline_generation ─────
        advance_phase(state, "outline_generation")
        save_state(state, slug)

        # ── Phase 3: Outline Generation ─────────────────────────
        yield BuildEvent("phase", "Generating document outline...", 0, 0, 20)

        features = state["features"]["core"]
        sections = generate_outline_from_profile(
            profile, features, depth_mode=resolved_depth,
        )
        set_outline_sections(state, sections)
        save_state(state, slug)

        yield BuildEvent(
            "phase",
            f"Outline generated: {len(sections)} sections",
            0, 0, 24,
        )

        # ── Advance: outline_generation → outline_approval ──────
        advance_phase(state, "outline_approval")

        # ── Phase 4: Outline Approval (auto-approve) ────────────
        yield BuildEvent("phase", "Locking outline and setting depth mode...", 0, 0, 26)
        set_build_depth_mode(state, resolved_depth)
        lock_outline(state)
        save_state(state, slug)

        # ── Advance: outline_approval → chapter_build ───────────
        advance_phase(state, "chapter_build")
        save_state(state, slug)

        # ── Phase 5-8: Delegate to auto_builder ─────────────────
        yield BuildEvent("phase", "Starting chapter auto-build...", 0, 0, 28)

        for event in run_auto_build(state, slug):
            # Remap auto-build 0-100% → pipeline 28-100%
            adjusted_percent = 28 + int(event.percent * 0.72)
            event.percent = min(adjusted_percent, 100)
            yield event

    except Exception as e:
        logger.exception("Full pipeline failed: %s", e)
        yield BuildEvent("error", f"Pipeline failed: {e}", 0, 0, 0)


# ---------------------------------------------------------------------------
# Synchronous wrapper for background threads
# ---------------------------------------------------------------------------


def run_full_pipeline_sync(
    project_name: str,
    raw_idea: str,
    depth_mode: str = "professional",
) -> str:
    """Run the full pipeline synchronously, storing events in progress store.

    Designed to be called from a background thread.

    Args:
        project_name: Human-readable project name.
        raw_idea: Detailed idea/campaign text.
        depth_mode: Build depth (default "professional").

    Returns:
        The project slug (job_id).
    """
    slug = _slugify(project_name)
    clear_pipeline_progress(slug)

    try:
        for event in run_full_pipeline(project_name, raw_idea, depth_mode):
            _append_pipeline_event(slug, event)
            logger.info("Pipeline [%s]: %s", slug, event.message)
    except Exception as e:
        logger.exception("Full pipeline sync failed for %s: %s", slug, e)
        error_event = BuildEvent("error", f"Pipeline failed: {e}", 0, 0, 0)
        _append_pipeline_event(slug, error_event)

    return slug
