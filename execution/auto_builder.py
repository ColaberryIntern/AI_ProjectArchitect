"""Auto-build pipeline: generates all chapters, runs quality gates, and assembles the final document.

Orchestrates the full post-outline-approval pipeline without human interaction.
Uses a generator pattern to yield progress events that can be streamed to the
browser via SSE.

Supports enterprise depth modes with scoring, post-build validation, and
auto-complete navigation.
"""

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from config.settings import OUTPUT_DIR
from execution.build_depth import estimate_pages, get_depth_config
from execution.chapter_writer import (
    _fallback_chapter,
    generate_chapter,
    generate_chapter_enterprise,
    generate_chapter_enterprise_with_retry,
    generate_chapter_enterprise_with_retry_and_usage,
    generate_chapter_enterprise_with_usage,
    generate_chapter_with_retry,
    generate_chapter_with_retry_and_usage,
    generate_chapter_with_usage,
)
from execution.document_assembler import assemble_full_document
from execution.quality_gate_runner import (
    run_chapter_gates,
    run_final_gates,
    score_chapter,
    score_document,
)
from execution.state_manager import (
    advance_phase,
    all_chapters_approved,
    get_build_depth_mode,
    get_project_profile,
    record_chapter_quality,
    record_chapter_score,
    record_chapter_status,
    record_document_assembly,
    record_final_quality,
    save_state,
)
from execution.template_renderer import render_chapter, render_chapter_enterprise

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


@dataclass
class BuildEvent:
    """A progress event from the auto-build pipeline."""

    event_type: str       # "phase", "chapter", "gate", "retry", "scoring", "validation", "regenerating", "error", "complete"
    message: str          # Human-readable status
    chapter_index: int    # 0 for non-chapter events
    total_chapters: int
    percent: int          # 0-100
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# GPT-4o-mini pricing (per 1M tokens)
_INPUT_COST_PER_M = 0.15
_OUTPUT_COST_PER_M = 0.60


@dataclass
class BuildMetrics:
    """Accumulates per-chapter and total build metrics."""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_llm_calls: int = 0
    total_retries: int = 0
    chapter_metrics: dict = field(default_factory=dict)

    def add_chapter_call(
        self,
        chapter_index: int,
        usage: dict,
        latency_ms: int,
        attempt: int = 1,
    ) -> None:
        """Record one LLM call for a chapter.

        Args:
            chapter_index: 1-based chapter number.
            usage: Dict with prompt_tokens and completion_tokens.
            latency_ms: Wall-clock time in milliseconds.
            attempt: Attempt number (1 = first try, 2+ = retries).
        """
        prompt_tok = usage.get("prompt_tokens", 0)
        completion_tok = usage.get("completion_tokens", 0)

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok
        self.total_llm_calls += 1
        if attempt > 1:
            self.total_retries += 1

        if chapter_index not in self.chapter_metrics:
            self.chapter_metrics[chapter_index] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "calls": 0,
                "attempts": 0,
                "latency_ms": 0,
            }

        cm = self.chapter_metrics[chapter_index]
        cm["prompt_tokens"] += prompt_tok
        cm["completion_tokens"] += completion_tok
        cm["calls"] += 1
        cm["attempts"] = max(cm["attempts"], attempt)
        cm["latency_ms"] += latency_ms

    def estimate_cost(self) -> float:
        """Estimate cost in USD based on GPT-4o-mini pricing."""
        input_cost = (self.total_prompt_tokens / 1_000_000) * _INPUT_COST_PER_M
        output_cost = (self.total_completion_tokens / 1_000_000) * _OUTPUT_COST_PER_M
        return round(input_cost + output_cost, 4)

    def to_summary_dict(self) -> dict:
        """Return a summary dict suitable for BuildEvent.data."""
        return {
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "total_llm_calls": self.total_llm_calls,
            "total_retries": self.total_retries,
            "estimated_cost_usd": self.estimate_cost(),
        }


# In-memory progress store (process-level singleton, thread-safe)
_build_progress: dict[str, list[BuildEvent]] = {}
_build_lock = threading.Lock()


def get_build_progress(slug: str) -> list[BuildEvent]:
    """Get all progress events for a build."""
    with _build_lock:
        return list(_build_progress.get(slug, []))


def _append_event(slug: str, event: BuildEvent) -> None:
    """Thread-safe append of a build event."""
    with _build_lock:
        if slug not in _build_progress:
            _build_progress[slug] = []
        _build_progress[slug].append(event)


def clear_build_progress(slug: str) -> None:
    """Clear progress events after completion."""
    with _build_lock:
        _build_progress.pop(slug, None)


def is_build_running(slug: str) -> bool:
    """Check if a build is currently in progress for this slug."""
    events = get_build_progress(slug)
    if not events:
        return False
    last = events[-1]
    return last.event_type not in ("complete", "error")


def run_auto_build(state: dict, slug: str) -> Generator[BuildEvent, None, None]:
    """Run the full build pipeline, yielding progress events.

    Phases:
    1. Generate chapters with enterprise prompts (0-70%)
    2. Post-build validation & auto-regeneration (72-80%)
    3. Final quality gates (82%)
    4. Document assembly (90%)
    5. Auto-complete navigation (95%)
    6. Done (100%)

    Args:
        state: The project state dict (mutated in place).
        slug: The project slug.

    Yields:
        BuildEvent for each significant step.
    """
    chapters = state["chapters"]
    N = len(chapters)
    if N == 0:
        yield BuildEvent("error", "No chapters found in state", 0, 0, 0)
        return

    depth_mode = get_build_depth_mode(state)
    depth_config = get_depth_config(depth_mode)
    profile = get_project_profile(state)
    features = state.get("features", {}).get("core", [])
    sections = state.get("outline", {}).get("sections", [])
    section_by_index = {s["index"]: s for s in sections}

    use_enterprise = depth_mode != "light"
    prev_summaries: list[str] = []
    chapter_scores: list[dict] = []
    metrics = BuildMetrics()

    yield BuildEvent("phase",
                     f"Starting {depth_config['label']} build ({depth_config['target_pages']} pages target)",
                     0, N, 0, data={"depth_mode": depth_mode})

    # Phase 1: Generate and gate all chapters (0-70%)
    for i, chapter in enumerate(chapters):
        chapter_idx = chapter["index"]
        section = section_by_index.get(chapter_idx, {})
        title = section.get("title", chapter.get("outline_section", f"Chapter {chapter_idx}"))
        summary = section.get("summary", "")

        base_percent = int((i / N) * 70)
        yield BuildEvent("chapter", f"Writing chapter {chapter_idx} of {N}: {title}",
                         chapter_idx, N, base_percent)

        # Generate chapter content (enterprise or legacy) with usage tracking
        t0 = time.monotonic()
        if use_enterprise:
            content_dict, usage = generate_chapter_enterprise_with_usage(
                profile, features, title, summary,
                chapter_idx, N, prev_summaries, depth_mode,
            )
            rendered = render_chapter_enterprise(chapter_idx, title, content_dict["content"])
        else:
            content_dict, usage = generate_chapter_with_usage(
                profile, features, title, summary,
                chapter_idx, N, prev_summaries,
            )
            rendered = render_chapter(
                chapter_idx, title,
                content_dict["purpose"],
                content_dict["design_intent"],
                content_dict["implementation_guidance"],
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        if usage:
            metrics.add_chapter_call(chapter_idx, usage, latency_ms, attempt=1)

        # Save to disk
        chapter_dir = OUTPUT_DIR / slug / "chapters"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        chapter_path = chapter_dir / f"ch{chapter_idx}.md"
        chapter_path.write_text(rendered, encoding="utf-8")
        record_chapter_status(state, chapter_idx, "draft", str(chapter_path))

        # Run quality gates
        gate_results = run_chapter_gates(rendered, title)
        record_chapter_quality(state, chapter_idx, gate_results)

        # Score chapter
        ch_score = score_chapter(rendered, title, depth_mode)
        record_chapter_score(state, chapter_idx, ch_score)
        chapter_scores.append(ch_score)

        score_pct = base_percent + int(70 / N / 2)
        ch_metrics = metrics.chapter_metrics.get(chapter_idx, {})
        total_ch_tokens = ch_metrics.get("prompt_tokens", 0) + ch_metrics.get("completion_tokens", 0)
        yield BuildEvent("scoring",
                         f"Chapter {chapter_idx}: {ch_score['total_score']}/100 ({ch_score['status']}), {ch_score['word_count']} words",
                         chapter_idx, N, score_pct,
                         data={"score": ch_score["total_score"], "word_count": ch_score["word_count"],
                               "status": ch_score["status"],
                               "tokens_used": total_ch_tokens,
                               "attempt_number": ch_metrics.get("attempts", 1),
                               "latency_ms": ch_metrics.get("latency_ms", 0)})

        if gate_results["all_passed"]:
            record_chapter_status(state, chapter_idx, "approved")
        else:
            # Auto-retry up to MAX_RETRIES times based on gate failures
            approved = False
            for retry in range(1, MAX_RETRIES + 1):
                failures = _extract_gate_failures(gate_results)
                yield BuildEvent("retry",
                                 f"Retrying chapter {chapter_idx} (attempt {retry + 1}/{MAX_RETRIES + 1})...",
                                 chapter_idx, N, score_pct)

                t0 = time.monotonic()
                if use_enterprise:
                    content_dict, usage = generate_chapter_enterprise_with_retry_and_usage(
                        profile, features, title, summary,
                        chapter_idx, N, prev_summaries, depth_mode,
                        score_result=ch_score,
                    )
                    rendered = render_chapter_enterprise(chapter_idx, title, content_dict["content"])
                else:
                    content_dict, usage = generate_chapter_with_retry_and_usage(
                        profile, features, title, summary,
                        chapter_idx, N, prev_summaries,
                        gate_failures=failures,
                    )
                    rendered = render_chapter(
                        chapter_idx, title,
                        content_dict["purpose"],
                        content_dict["design_intent"],
                        content_dict["implementation_guidance"],
                    )
                retry_latency = int((time.monotonic() - t0) * 1000)
                if usage:
                    metrics.add_chapter_call(chapter_idx, usage, retry_latency, attempt=retry + 1)

                chapter_path.write_text(rendered, encoding="utf-8")
                record_chapter_status(state, chapter_idx, f"revision_{retry}", str(chapter_path))

                gate_results = run_chapter_gates(rendered, title)
                record_chapter_quality(state, chapter_idx, gate_results)

                ch_score = score_chapter(rendered, title, depth_mode)
                record_chapter_score(state, chapter_idx, ch_score)
                chapter_scores[i] = ch_score

                if gate_results["all_passed"]:
                    record_chapter_status(state, chapter_idx, "approved")
                    approved = True
                    yield BuildEvent("gate", f"Chapter {chapter_idx} passed on retry {retry + 1}",
                                     chapter_idx, N, score_pct)
                    break

            if not approved:
                record_chapter_status(state, chapter_idx, "approved")
                logger.warning("Chapter %d force-approved after %d retries", chapter_idx, MAX_RETRIES)
                yield BuildEvent("error",
                                 f"Chapter {chapter_idx} approved with warnings after {MAX_RETRIES} retries",
                                 chapter_idx, N, score_pct)

        save_state(state, slug)

        # Capture summary for next chapter's context
        if use_enterprise:
            prev_summaries.append(content_dict["content"][:300])
        else:
            prev_summaries.append(content_dict["purpose"][:200])

    # Phase 2: Post-build validation (verify only, no regeneration)
    yield BuildEvent("validation", "Running post-build validation...", 0, N, 72)

    from execution.build_depth import get_scoring_thresholds
    complete_threshold = get_scoring_thresholds(depth_mode)["complete_threshold"]

    deficient = [
        (i, chapters[i]["index"])
        for i, sc in enumerate(chapter_scores)
        if sc.get("total_score", 0) < complete_threshold
    ]

    if deficient:
        deficient_info = ", ".join(
            f"ch{chapters[idx]['index']}({chapter_scores[idx]['total_score']}/100)"
            for idx, _ in deficient
        )
        yield BuildEvent("validation",
                         f"{len(deficient)} chapter(s) below threshold: {deficient_info}",
                         0, N, 78)
    else:
        yield BuildEvent("validation", "All chapters meet quality threshold", 0, N, 80)

    # Document-level score summary
    doc_score = score_document(chapter_scores, depth_mode)
    total_words = doc_score["total_word_count"]
    est_pages = doc_score["estimated_pages"]

    metrics_summary = metrics.to_summary_dict()
    yield BuildEvent("scoring",
                     f"Document: {doc_score['average_score']}/100 avg, {total_words} words, ~{est_pages} pages",
                     0, N, 80,
                     data={"average_score": doc_score["average_score"],
                           "total_word_count": total_words,
                           "estimated_pages": est_pages,
                           **metrics_summary})

    # Phase 3: Final quality gates (82%)
    yield BuildEvent("phase", "Running final quality gates...", 0, N, 82)
    advance_phase(state, "quality_gates")
    save_state(state, slug)

    all_text = _combine_all_chapters(state, slug)
    final_results = run_final_gates(all_text)
    record_final_quality(state, final_results)
    save_state(state, slug)

    if not final_results["all_passed"]:
        logger.warning("Final quality gates had failures, proceeding anyway")
        yield BuildEvent("error", "Final quality gates had some warnings", 0, N, 85)

    # Phase 4: Assemble document (90%)
    yield BuildEvent("phase", "Assembling final document...", 0, N, 90)
    advance_phase(state, "final_assembly")
    save_state(state, slug)

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
    save_state(state, slug)

    # Phase 5: Auto-complete navigation (95%)
    yield BuildEvent("phase", "Auto-completing project...", 0, N, 95)
    advance_phase(state, "complete")
    save_state(state, slug)

    yield BuildEvent("complete",
                     f"Build guide ready: {result['filename']} (~{est_pages} pages)",
                     0, N, 100,
                     data={"filename": result["filename"],
                           "estimated_pages": est_pages,
                           "total_word_count": total_words,
                           "average_score": doc_score["average_score"],
                           **metrics_summary})


def run_auto_build_sync(slug: str) -> None:
    """Run the auto-build pipeline synchronously, storing events in the progress store.

    Designed to be called from a background thread.
    """
    from execution.state_manager import load_state

    clear_build_progress(slug)

    try:
        state = load_state(slug)
        for event in run_auto_build(state, slug):
            _append_event(slug, event)
            logger.info("Build [%s]: %s", slug, event.message)
    except Exception as e:
        logger.exception("Auto-build failed for %s: %s", slug, e)
        error_event = BuildEvent(
            "error", f"Build failed: {e}", 0, 0, 0,
        )
        _append_event(slug, error_event)


def _extract_gate_failures(gate_results: dict) -> list[str]:
    """Extract issue strings from quality gate results."""
    failures = []
    for key, value in gate_results.items():
        if isinstance(value, dict) and not value.get("passed", True):
            failures.extend(value.get("issues", []))
    return failures


def _combine_all_chapters(state: dict, slug: str) -> str:
    """Read and combine all chapter files into a single text."""
    parts = []
    for chapter in state["chapters"]:
        content_path = chapter.get("content_path")
        if content_path:
            path = Path(content_path)
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)
