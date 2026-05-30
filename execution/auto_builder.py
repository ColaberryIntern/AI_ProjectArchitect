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
from execution.build_depth import estimate_pages, get_depth_config, get_scoring_thresholds
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
from execution.citation_injector import inject_citations
from execution.document_assembler import assemble_full_document
from execution.quality_gate_runner import (
    run_chapter_gates,
    run_final_gates,
    run_spec_gates,
    score_chapter,
    score_document,
)
from execution.requirements_writer import (
    collect_requirements,
    write_requirements,
)
from execution.state_manager import (
    advance_phase,
    all_chapters_approved,
    get_blueprint_id,
    get_build_depth_mode,
    get_project_profile,
    get_selected_skills,
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

# When the LLM call fails internally and the chapter writer returns
# template content (signalled by an empty ``usage`` dict), retry up to
# this many times before accepting the fallback. Without this, a single
# transient OpenAI hiccup silently lands placeholder content as an
# "approved" chapter that lexical gates can't distinguish from real
# output. Each retry adds ~30-60s + a small base-rate of API cost.
LLM_FALLBACK_MAX_ATTEMPTS = 3
LLM_FALLBACK_BACKOFF_SEC = 2.0


def _is_fallback(usage: dict | None) -> bool:
    """Detect a fallback chapter via empty usage dict.

    The chapter_writer returns ``({...}, {})`` when the LLM call failed
    internally and template content was substituted. A real LLM call
    returns ``({...}, {"prompt_tokens": N, "completion_tokens": M})``.
    """
    return not usage or usage == {}


def _generate_with_fallback_retry(generator_fn, *, log_label: str, **kwargs):
    """Call a chapter generate function; retry if it returns a fallback.

    Returns ``(content_dict, usage, attempts)`` where ``attempts`` is
    the number of LLM calls made (1 = succeeded first try, N = succeeded
    on Nth try, MAX = all attempts fell back).
    """
    last_content: dict = {}
    last_usage: dict = {}
    for attempt in range(1, LLM_FALLBACK_MAX_ATTEMPTS + 1):
        content_dict, usage = generator_fn(**kwargs)
        last_content, last_usage = content_dict, usage
        if not _is_fallback(usage):
            if attempt > 1:
                logger.info(
                    "%s recovered on LLM attempt %d/%d",
                    log_label, attempt, LLM_FALLBACK_MAX_ATTEMPTS,
                )
            return content_dict, usage, attempt
        if attempt < LLM_FALLBACK_MAX_ATTEMPTS:
            backoff = LLM_FALLBACK_BACKOFF_SEC * attempt
            logger.warning(
                "%s LLM call returned fallback (attempt %d/%d) — sleeping %.1fs before retry",
                log_label, attempt, LLM_FALLBACK_MAX_ATTEMPTS, backoff,
            )
            time.sleep(backoff)
    logger.warning(
        "%s LLM fallback after %d attempts — accepting template content",
        log_label, LLM_FALLBACK_MAX_ATTEMPTS,
    )
    return last_content, last_usage, LLM_FALLBACK_MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# Requirement traceability helpers (Phase A/C wiring)
# ---------------------------------------------------------------------------


def _build_requirements_lookup(
    requirements: list[dict],
    sections: list[dict],
) -> dict[str, list[dict]]:
    """Build a section-title -> [requirement, ...] lookup.

    Matches by either ``traces_to.outline_section_id`` (preferred, exact)
    or by case-insensitive substring of the Requirement name in the
    section title (fallback for projects where outline_section_id has
    not been populated yet — common during the rollout window).

    Args:
        requirements: List of Requirement dicts (post-promotion).
        sections: Outline section dicts with at least ``title``.

    Returns:
        Dict mapping section title -> list of Requirements.
    """
    by_id = {s.get("title", ""): [] for s in sections}
    section_titles = list(by_id.keys())

    for r in requirements:
        outline_id = (r.get("traces_to") or {}).get("outline_section_id")
        if outline_id and outline_id in by_id:
            by_id[outline_id].append(r)
            continue
        # Fallback: simple substring match. Conservative — only attaches
        # the Requirement to a section whose title contains the
        # Requirement's primary keyword (problem_mapped_to, then name).
        keyword = (r.get("problem_mapped_to") or r.get("name") or "").strip().lower()
        if not keyword:
            continue
        for title in section_titles:
            if keyword and keyword in title.lower():
                by_id[title].append(r)
                break

    return by_id


def _update_chapter_traces(
    requirements: list[dict],
    chapter_id: str,
    linked: list[dict],
) -> list[dict]:
    """Add chapter_id to each linked Requirement's traces_to.chapter_ids.

    Returns a new list of Requirements with the updated traces; does NOT
    mutate the input. The matching is by Requirement id.

    Args:
        requirements: Full Requirement list.
        chapter_id: Identifier for the chapter (e.g. ``"3"`` or ``"ch3"``).
        linked: Requirements that this chapter cites.

    Returns:
        Updated Requirement list.
    """
    linked_ids = {r.get("id") for r in linked if r.get("id")}
    out: list[dict] = []
    for r in requirements:
        if r.get("id") in linked_ids:
            traces = dict(r.get("traces_to") or {})
            chapter_ids = list(traces.get("chapter_ids") or [])
            if chapter_id not in chapter_ids:
                chapter_ids.append(chapter_id)
            traces["chapter_ids"] = chapter_ids
            r = {**r, "traces_to": traces}
        out.append(r)
    return out


def _persist_chapter_traces(
    state: dict,
    chapter_id: str,
    linked: list[dict],
) -> None:
    """Mutate state['features'].core/optional to record chapter_id traces.

    Looks up each linked Requirement by id in state.features.core and
    state.features.optional and updates its traces_to.chapter_ids list
    in place. Idempotent: re-calling with the same chapter_id is a no-op.
    """
    linked_ids = {r.get("id") for r in linked if r.get("id")}
    if not linked_ids:
        return

    features = state.get("features") or {}
    for bucket_name in ("core", "optional"):
        bucket = features.get(bucket_name) or []
        for f in bucket:
            if f.get("id") not in linked_ids:
                continue
            traces = dict(f.get("traces_to") or {})
            chapter_ids = list(traces.get("chapter_ids") or [])
            if chapter_id not in chapter_ids:
                chapter_ids.append(chapter_id)
            traces["chapter_ids"] = chapter_ids
            traces.setdefault("problem_id", f.get("problem_mapped_to"))
            f["traces_to"] = traces


def _build_cross_chapter_context(
    bound_so_far: dict[str, list[str]],
) -> str:
    """Render a short summary of which Requirements are already bound.

    Used to keep context consistent across chapters: a chapter that
    depends on a prior chapter's Requirement should defer to it rather
    than re-implement.

    Args:
        bound_so_far: Mapping of chapter_id -> [requirement_id, ...].

    Returns:
        One-line-per-chapter string. Empty if nothing bound yet.
    """
    if not bound_so_far:
        return ""
    lines = ["Requirements already covered by prior chapters (do not re-implement; cite if needed):"]
    for chapter_id, req_ids in bound_so_far.items():
        if req_ids:
            lines.append(f"- Chapter {chapter_id}: {', '.join(req_ids)}")
    return "\n".join(lines) if len(lines) > 1 else ""


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
    # Copy profile and inject blueprint ID for chapter_writer context injection.
    # Using a copy so the _blueprint key is never persisted to state JSON.
    profile = {
        **get_project_profile(state),
        "_blueprint": get_blueprint_id(state),
        "_skills": get_selected_skills(state),
    }
    features = state.get("features", {}).get("core", [])
    sections = state.get("outline", {}).get("sections", [])
    section_by_index = {s["index"]: s for s in sections}

    use_enterprise = depth_mode != "light"
    prev_summaries: list[str] = []
    chapter_scores: list[dict] = []
    metrics = BuildMetrics()

    # Spec-driven: build a section-title -> Requirements lookup for this run.
    # Requirements come from promoting features (idempotent — see
    # requirements_writer.collect_requirements). Empty list when no features
    # have been upgraded to Requirements yet (backward compatible).
    requirements = collect_requirements(state)
    requirements_by_section = _build_requirements_lookup(requirements, sections)
    bound_so_far: dict[str, list[str]] = {}

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

        # Resolve Requirements traced to this section (may be empty).
        linked_requirements = requirements_by_section.get(title, [])

        # Build cross-chapter context: which Requirements were cited in
        # prior chapters? Prepended to prev_summaries so the writer can
        # avoid re-implementing what an earlier chapter already covered.
        cross_chapter = _build_cross_chapter_context(bound_so_far)
        effective_prev_summaries = (
            ([cross_chapter] + prev_summaries) if cross_chapter else prev_summaries
        )

        # Generate chapter content (enterprise or legacy) with usage tracking.
        # Wrapped in fallback-retry: when the LLM call fails internally,
        # the chapter writer returns template content with empty usage.
        # We retry the LLM up to LLM_FALLBACK_MAX_ATTEMPTS times before
        # accepting the placeholder content.
        t0 = time.monotonic()
        if use_enterprise:
            content_dict, usage, llm_attempts = _generate_with_fallback_retry(
                generate_chapter_enterprise_with_usage,
                log_label=f"ch{chapter_idx}",
                profile=profile, features=features,
                section_title=title, section_summary=summary,
                chapter_index=chapter_idx, total_chapters=N,
                previous_summaries=effective_prev_summaries,
                depth_mode=depth_mode,
                linked_requirements=linked_requirements,
            )
            rendered = render_chapter_enterprise(chapter_idx, title, content_dict["content"])
        else:
            content_dict, usage, llm_attempts = _generate_with_fallback_retry(
                generate_chapter_with_usage,
                log_label=f"ch{chapter_idx}",
                profile=profile, features=features,
                section_title=title, section_summary=summary,
                chapter_index=chapter_idx, total_chapters=N,
                previous_summaries=effective_prev_summaries,
                linked_requirements=linked_requirements,
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
        if llm_attempts > 1:
            yield BuildEvent(
                "retry",
                f"Chapter {chapter_idx} LLM recovered on attempt {llm_attempts}",
                chapter_idx, N, base_percent,
            )

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

        complete_threshold = get_scoring_thresholds(depth_mode)["complete_threshold"]
        min_words = get_scoring_thresholds(depth_mode)["min_words"]
        word_count_floor = int(min_words * 0.35)  # 35% of min_words — catches truly short chapters
        meets_word_floor = ch_score["word_count"] >= word_count_floor

        score_ok = gate_results["all_passed"] or ch_score["total_score"] >= complete_threshold
        if score_ok and meets_word_floor:
            record_chapter_status(state, chapter_idx, "approved")
        else:
            # Auto-retry: score below threshold OR word count below floor
            retry_reason = []
            if not score_ok:
                retry_reason.append(f"score {ch_score['total_score']}<{complete_threshold}")
            if not meets_word_floor:
                retry_reason.append(f"words {ch_score['word_count']}<{word_count_floor}")
            approved = False
            for retry in range(1, MAX_RETRIES + 1):
                failures = _extract_gate_failures(gate_results)
                yield BuildEvent("retry",
                                 f"Retrying chapter {chapter_idx} ({', '.join(retry_reason)})...",
                                 chapter_idx, N, score_pct)

                t0 = time.monotonic()
                if use_enterprise:
                    content_dict, usage = generate_chapter_enterprise_with_retry_and_usage(
                        profile, features, title, summary,
                        chapter_idx, N, effective_prev_summaries, depth_mode,
                        score_result=ch_score,
                        linked_requirements=linked_requirements,
                    )
                    rendered = render_chapter_enterprise(chapter_idx, title, content_dict["content"])
                else:
                    content_dict, usage = generate_chapter_with_retry_and_usage(
                        profile, features, title, summary,
                        chapter_idx, N, effective_prev_summaries,
                        gate_failures=failures,
                        linked_requirements=linked_requirements,
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

                retry_score_ok = gate_results["all_passed"] or ch_score["total_score"] >= complete_threshold
                retry_meets_floor = ch_score["word_count"] >= word_count_floor
                if retry_score_ok and retry_meets_floor:
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

        # Spec-driven: inject [REQ-NNN] / [AC-NNN-N] citations into the
        # approved chapter file deterministically. The chapter writer
        # is asked to cite via the prompt, but gpt-4o-mini compliance
        # is inconsistent (typical: 0–2 citations across 5 chapters).
        # The injector reads the rendered file, finds first plausible
        # mention of each linked Requirement, and inserts a bracketed
        # ID. Idempotent — running again is a no-op. Failures are
        # logged but do not block the build.
        if linked_requirements and chapter_path.exists():
            try:
                original = chapter_path.read_text(encoding="utf-8")
                injected, report = inject_citations(original, linked_requirements)
                if injected != original:
                    chapter_path.write_text(injected, encoding="utf-8")
                    logger.info(
                        "Citation injector: ch%d: +%d REQ, +%d AC (already cited: %d, unmatched: %d)",
                        chapter_idx,
                        len(report.injected),
                        len(report.ac_injected),
                        len(report.already_cited),
                        len(report.unmatched),
                    )
            except Exception as e:
                logger.warning(
                    "Citation injector failed on chapter %d: %s",
                    chapter_idx, e,
                )

        # Spec-driven: record this chapter on each linked Requirement's
        # traces_to.chapter_ids, then re-emit requirements.json so the
        # Requirement Coverage gate (run later in run_spec_gates) sees
        # the up-to-date trace. Idempotent.
        if linked_requirements:
            chapter_id = str(chapter_idx)
            _persist_chapter_traces(state, chapter_id, linked_requirements)
            bound_so_far[chapter_id] = [r["id"] for r in linked_requirements if r.get("id")]
            try:
                write_requirements(state, slug)
            except Exception as e:
                # Requirements artifact write must never block the build
                # — log and continue. The build still finishes; the
                # spec gates may flag missing trace data downstream.
                logger.warning(
                    "Failed to write requirements.json for chapter %d: %s",
                    chapter_idx, e,
                )

        save_state(state, slug)

        # Capture summary for next chapter's context
        if use_enterprise:
            prev_summaries.append(content_dict["content"][:300])
        else:
            prev_summaries.append(content_dict["purpose"][:200])

    # Phase 2: Post-build validation (verify only, no regeneration)
    yield BuildEvent("validation", "Running post-build validation...", 0, N, 72)

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

    # Spec-driven gates: requirement coverage + AC testability + per-chapter
    # semantic intern test. Run only when Requirements exist (otherwise this
    # is a no-op that returns passed=True). Results are stored on state
    # under ``quality.spec_report`` so the directive's verification step
    # can inspect them without re-running judges.
    final_requirements = collect_requirements(state)
    if final_requirements:
        chapter_payloads = []
        for ch in state["chapters"]:
            ch_path = ch.get("content_path")
            if ch_path and Path(ch_path).exists():
                chapter_payloads.append({
                    "id": str(ch["index"]),
                    "text": Path(ch_path).read_text(encoding="utf-8"),
                })
        try:
            spec_results = run_spec_gates(final_requirements, chapter_payloads)
            state.setdefault("quality", {})["spec_report"] = {
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "all_passed": spec_results.get("all_passed", False),
                "requirement_coverage": spec_results.get("requirement_coverage"),
                "ac_testability": spec_results.get("ac_testability"),
                "chapter_intern_semantic": spec_results.get("chapter_intern_semantic"),
            }
            if not spec_results.get("all_passed"):
                logger.info("Spec gates reported issues; build continues with warnings")
        except Exception as e:
            # Spec gates must never block the build — they are advisory at
            # this stage of the rollout. Record the error and continue.
            logger.warning("Spec gates errored: %s", e)
            state.setdefault("quality", {})["spec_report"] = {
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "all_passed": False,
                "error": str(e),
            }
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
