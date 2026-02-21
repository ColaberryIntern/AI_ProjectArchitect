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
import time
from pathlib import Path
from typing import Generator

from config.settings import OUTPUT_DIR
from execution.auto_builder import BuildEvent, run_auto_build
from execution.build_depth import get_depth_config, get_scoring_thresholds, resolve_depth_mode
from execution.chapter_writer import generate_chapter_enterprise_with_retry_and_usage
from execution.document_assembler import assemble_full_document
from execution.feature_catalog import generate_catalog, generate_catalog_from_profile
from execution.outline_generator import generate_outline_from_profile
from execution.profile_generator import generate_profile
from execution.quality_gate_runner import run_final_gates, score_chapter
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
    record_chapter_score,
    record_document_assembly,
    record_final_quality,
    record_idea,
    save_state,
    set_build_depth_mode,
    set_outline_sections,
    set_profile_derived,
    set_profile_field,
)
from execution.template_renderer import render_chapter_enterprise

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

        # Intercept auto_build events — hold the "complete" event for verification
        last_complete_event = None
        for event in run_auto_build(state, slug):
            # Remap auto-build 0-100% → pipeline 28-100%
            adjusted_percent = 28 + int(event.percent * 0.72)
            event.percent = min(adjusted_percent, 100)
            if event.event_type == "complete":
                last_complete_event = event  # Hold — verify before yielding
            else:
                yield event

        # ── Post-build verification + retry ───────────────────
        if last_complete_event:
            yield from _verify_and_retry(slug, resolved_depth, last_complete_event)
        else:
            yield BuildEvent("error", "Auto-build did not produce a completion event", 0, 0, 0)

    except Exception as e:
        logger.exception("Full pipeline failed: %s", e)
        yield BuildEvent("error", f"Pipeline failed: {e}", 0, 0, 0)


# ---------------------------------------------------------------------------
# Post-build verification + retry
# ---------------------------------------------------------------------------


def _check_document_quality(state: dict, depth_mode: str) -> dict:
    """Check whether the document meets quality thresholds.

    Returns:
        Dict with 'passed', 'final_gates_passed', 'deficient_chapters',
        'average_score', and 'details'.
    """
    thresholds = get_scoring_thresholds(depth_mode)
    complete_threshold = thresholds["complete_threshold"]

    # Check final quality gates
    final_report = state.get("quality", {}).get("final_report", {})
    final_gates_passed = final_report.get("all_passed", False)

    # Check individual chapter scores
    deficient = []
    scores = []
    for ch in state.get("chapters", []):
        ch_score = ch.get("chapter_score", {})
        total = ch_score.get("total_score", 0)
        scores.append(total)
        if total < complete_threshold:
            deficient.append({
                "index": ch["index"],
                "score": total,
                "status": ch_score.get("status", "unknown"),
            })

    avg_score = sum(scores) // max(len(scores), 1) if scores else 0
    passed = final_gates_passed and len(deficient) == 0

    return {
        "passed": passed,
        "final_gates_passed": final_gates_passed,
        "deficient_chapters": deficient,
        "average_score": avg_score,
        "complete_threshold": complete_threshold,
    }


def _verify_and_retry(
    slug: str,
    depth_mode: str,
    complete_event: BuildEvent,
) -> Generator[BuildEvent, None, None]:
    """Verify document quality and retry deficient chapters if needed.

    Intercepts the auto_builder "complete" event. If quality verification
    passes, yields the complete event. Otherwise, retries deficient chapters
    once and re-verifies.

    Args:
        slug: Project slug.
        depth_mode: Resolved depth mode.
        complete_event: The "complete" event from auto_builder.

    Yields:
        BuildEvent for verification progress, ending with "complete" or "error".
    """
    yield BuildEvent("verification", "Verifying document quality...", 0, 0, 96)

    state = load_state(slug)
    quality = _check_document_quality(state, depth_mode)

    if quality["passed"]:
        logger.info("Document quality verified for %s (avg score: %d)", slug, quality["average_score"])
        yield BuildEvent("verification", "Quality verification passed", 0, 0, 98,
                         data={"quality": quality})
        yield complete_event
        return

    # Quality failed — attempt retry pass on deficient chapters
    deficient = quality["deficient_chapters"]
    logger.warning(
        "Document quality verification failed for %s: %d deficient chapters, final_gates=%s",
        slug, len(deficient), quality["final_gates_passed"],
    )

    yield BuildEvent(
        "verification",
        f"Quality below threshold — retrying {len(deficient)} deficient chapter(s)...",
        0, 0, 96,
        data={"quality": quality},
    )

    # Retry each deficient chapter
    profile = get_project_profile(state)
    features = state.get("features", {}).get("core", [])
    sections = state.get("outline", {}).get("sections", [])
    section_by_index = {s["index"]: s for s in sections}
    N = len(state["chapters"])

    # Build previous summaries for context (from existing chapter files)
    prev_summaries = []
    for ch in state["chapters"]:
        content_path = ch.get("content_path")
        if content_path and Path(content_path).exists():
            text = Path(content_path).read_text(encoding="utf-8")
            prev_summaries.append(text[:300])
        else:
            prev_summaries.append("")

    for deficient_ch in deficient:
        chapter_idx = deficient_ch["index"]
        section = section_by_index.get(chapter_idx, {})
        title = section.get("title", f"Chapter {chapter_idx}")
        summary = section.get("summary", "")

        # Find the chapter's current score for retry context
        ch_obj = next((c for c in state["chapters"] if c["index"] == chapter_idx), None)
        ch_score = ch_obj.get("chapter_score", {}) if ch_obj else {}

        yield BuildEvent(
            "regenerating",
            f"Verification retry: chapter {chapter_idx} ({title}) — score {deficient_ch['score']}/{quality['complete_threshold']}",
            chapter_idx, N, 97,
        )

        t0 = time.monotonic()
        content_dict, usage = generate_chapter_enterprise_with_retry_and_usage(
            profile, features, title, summary,
            chapter_idx, N, prev_summaries, depth_mode,
            score_result=ch_score,
        )
        rendered = render_chapter_enterprise(chapter_idx, title, content_dict["content"])
        latency_ms = int((time.monotonic() - t0) * 1000)

        # Write to disk
        chapter_path = OUTPUT_DIR / slug / "chapters" / f"ch{chapter_idx}.md"
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text(rendered, encoding="utf-8")

        # Re-score
        new_score = score_chapter(rendered, title, depth_mode)
        record_chapter_score(state, chapter_idx, new_score)

        yield BuildEvent(
            "scoring",
            f"Verification retry: chapter {chapter_idx} now {new_score['total_score']}/100 ({new_score['status']})",
            chapter_idx, N, 97,
            data={
                "score": new_score["total_score"],
                "word_count": new_score["word_count"],
                "status": new_score["status"],
                "latency_ms": latency_ms,
            },
        )

    save_state(state, slug)

    # Re-assemble the document with updated chapters
    yield BuildEvent("phase", "Re-assembling document after verification retry...", 0, N, 98)

    chapter_paths = []
    chapter_titles = []
    for ch in state["chapters"]:
        chapter_paths.append(ch["content_path"])
        sec = section_by_index.get(ch["index"], {})
        chapter_titles.append(sec.get("title", f"Chapter {ch['index']}"))

    result = assemble_full_document(
        chapter_paths=chapter_paths,
        chapter_titles=chapter_titles,
        project_name=state["project"]["name"],
        project_slug=slug,
        version=state["document"]["version"],
    )
    record_document_assembly(state, result["filename"], result["output_path"])

    # Re-run final quality gates
    all_text = ""
    for ch in state["chapters"]:
        content_path = ch.get("content_path")
        if content_path and Path(content_path).exists():
            all_text += Path(content_path).read_text(encoding="utf-8") + "\n\n"

    final_results = run_final_gates(all_text)
    record_final_quality(state, final_results)
    save_state(state, slug)

    # Re-verify
    quality_after = _check_document_quality(state, depth_mode)

    if quality_after["passed"]:
        logger.info("Document quality verified after retry for %s (avg score: %d)",
                     slug, quality_after["average_score"])
        yield BuildEvent("verification", "Quality verification passed after retry", 0, 0, 99,
                         data={"quality": quality_after})
        # Update complete event data with retry info
        complete_event.data["verification_retried"] = True
        complete_event.data["average_score"] = quality_after["average_score"]
        yield complete_event
    else:
        # Still failing after retry — report error with details
        still_deficient = quality_after["deficient_chapters"]
        ch_list = ", ".join(f"ch{d['index']}({d['score']})" for d in still_deficient)
        msg = (
            f"Document failed quality verification after retry. "
            f"Average score: {quality_after['average_score']}/100. "
            f"Final gates: {'passed' if quality_after['final_gates_passed'] else 'failed'}. "
        )
        if still_deficient:
            msg += f"Deficient chapters: {ch_list}."

        logger.error("Quality verification failed after retry for %s: %s", slug, msg)
        yield BuildEvent("error", msg, 0, 0, 99, data={"quality": quality_after})


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
