"""Background orchestration for a My-Day "create a new project" build.

Phase a (the 9-stage advisory generation + project creation) runs synchronously
in the request before this is kicked, so the org buildout page can render and
the slug exists. This module runs the remaining phases in a daemon thread,
JOINED into one automatic flow (no human gate on the first build):

    b. tasks    — generate the full Build Guide, parse its spine, and generate
                  the BUILD/BREAK/HARDEN tasks per feature → project-plan.json
    verify      — validate_plan() gate (the "requirements created AND verified")
    c. basecamp — reconcile the plan into Basecamp (assigned, due-dated todos)
    d. resync   — pull the new lists into My Day and re-score

Progress is written to ``output/{slug}/build_status.json`` (see ``build_status``)
so the buildout page can poll it and My Day can show a banner.
"""
from __future__ import annotations

import glob
import logging
import threading

from config.settings import OUTPUT_DIR
from execution.advisory import (
    basecamp_build_writer,
    build_guide_parser,
    build_status,
    plan_builder,
    project_plan,
    project_plan_reconciler,
)

logger = logging.getLogger(__name__)


def _locate_build_guide(slug: str) -> str | None:
    """Newest ``*_Build_Guide_*.md`` written for this project, if any."""
    matches = sorted(glob.glob(str(OUTPUT_DIR / slug / "*_Build_Guide_*.md")))
    return matches[-1] if matches else None


def run_build(session_id: str, bc_project_id, pace: str, operator_email: str,
              slug: str, idea_text: str, *, blueprint: str | None = None,
              name_prefix: str = "", publisher_target: str = "basecamp") -> None:
    """Run phases b → verify → c → d. Writes status at each step; never raises.

    ``blueprint`` (standard|autonomous) selects the Build Guide blueprint;
    ``name_prefix`` labels the Basecamp todolists (used to segregate test builds).
    """
    try:
        bc_project_id = int(bc_project_id)
    except (TypeError, ValueError):
        build_status.write_status(slug, phase="error", error="invalid bc_project_id")
        return

    try:
        from execution.products.library import tenancy
        user = tenancy.get_user(operator_email)
        if user is None:
            build_status.write_status(slug, phase="error", error=f"unknown operator {operator_email}")
            return

        # ── Phase b: deep plan (maker/checker loop) → docs + tickets ──
        build_status.write_status(
            slug, phase="tasks", message="Designing your deep build plan (maker/checker loop on a strong model)…",
            session_id=session_id, bc_project_id=bc_project_id,
            pace=pace, operator_email=operator_email,
        )
        from execution.state_manager import load_state
        from execution.advisory.advisory_state_manager import load_session
        from execution.advisory import deep_plan, deep_plan_publisher, deep_plan_targets
        from config.settings import COHORT_START_MONDAY

        state = load_state(slug)
        project_name = (state.get("project") or {}).get("name") or slug
        raw_idea = state.get("advisory_prefill") or idea_text or project_name

        # The discovery choices that shape this product (My-Day discovery flow).
        try:
            answers = (load_session(session_id).get("discovery") or {}).get("answers") or []
        except Exception:
            answers = []
        choices = "\n".join(
            f"- {a.get('label', '')}: {(a.get('choice') or {}).get('label', '')} — {(a.get('choice') or {}).get('description', '')}".rstrip(" —")
            for a in answers if (a.get("choice") or {}).get("label")
        ) or "(discovery choices not recorded)"

        # Generate (heavy) → store to files first, so a heavy run never strands us.
        plan = deep_plan.generate_deep_plan(raw_idea, choices, project_name)
        deep_plan.store_deep_plan(slug, plan)

        # ── Phase c: publish (pluggable target: basecamp | accelerator) ──
        if publisher_target == "accelerator":
            build_status.write_status(
                slug, phase="publish",
                message=f"Publishing {plan['ticket_count']} tasks to the student platform…",
            )
            result = deep_plan_targets.publish_plan(
                "accelerator", plan, operator_email=operator_email,
                project_ref=(name_prefix or slug))
            if not result.get("ok"):
                build_status.write_status(
                    slug, phase="error",
                    error=f"student-platform publish failed: {result.get('error') or result.get('status')}")
                return
            counts = result.get("response") if isinstance(result.get("response"), dict) else {}
            build_status.write_status(
                slug, phase="done",
                message=f"Published {plan.get('story_count', 0)} tasks to the student platform.",
                tasks_created=counts.get("tasks", plan.get("story_count", 0)),
                target="accelerator",
            )
            return

        # Default target: Basecamp (employees) — docs + due-dated tickets.
        build_status.write_status(
            slug, phase="basecamp",
            message=f"Publishing {plan['ticket_count']} tickets + 3 documents to Basecamp…",
        )
        anchor = deep_plan_publisher.anchor_from_cohort_start(COHORT_START_MONDAY)
        list_name = f"{name_prefix}{project_name} - Sprint Build Plan"
        summary = deep_plan_targets.publish_plan(
            "basecamp", plan, user=user, bc_project_id=bc_project_id,
            anchor_monday=anchor, list_name=list_name, project_name=project_name)

        # ── Phase d: resync so My Day reflects the new list ─────────
        try:
            from execution.products.ops import scorer, sync
            sync.pull_todos_for_project(operator_email, bc_project_id)
            scorer.score_all_todos(operator_email)
        except Exception:
            logger.warning("post-build My Day sync failed (non-blocking)", exc_info=True)

        build_status.write_status(
            slug, phase="done",
            message=f"Created {summary['created']} tasks + {len(summary.get('docs', []))} documents in Basecamp.",
            tasks_created=summary["created"],
            docs=len(summary.get("docs", [])),
            bc_project_id=bc_project_id,
        )
    except Exception as e:  # noqa: BLE001 — background job: record, don't crash
        logger.error("My-Day build failed for slug=%s: %s", slug, e, exc_info=True)
        build_status.write_status(slug, phase="error", error=str(e)[:300])


def kick_build(session_id: str, bc_project_id, pace: str, operator_email: str,
               slug: str, idea_text: str, *, blueprint: str | None = None,
               name_prefix: str = "", publisher_target: str = "basecamp") -> None:
    """Spawn run_build in a daemon thread (mirrors my_day._kick_bg_full_sync)."""
    threading.Thread(
        target=run_build,
        args=(session_id, bc_project_id, pace, operator_email, slug, idea_text),
        kwargs={"blueprint": blueprint, "name_prefix": name_prefix, "publisher_target": publisher_target},
        daemon=True,
        name=f"myday-build-{slug}",
    ).start()
