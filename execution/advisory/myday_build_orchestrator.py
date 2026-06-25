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
              name_prefix: str = "") -> None:
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

        # ── Phase b: full Build Guide → project-plan.json ───────────
        build_status.write_status(
            slug, phase="tasks", message="Generating your Build Guide and build plan…",
            session_id=session_id, bc_project_id=bc_project_id,
            pace=pace, operator_email=operator_email,
        )
        from execution.full_pipeline import run_full_pipeline_sync
        from execution.state_manager import load_state
        state = load_state(slug)
        project_name = (state.get("project") or {}).get("name") or slug
        raw_idea = state.get("advisory_prefill") or idea_text or project_name
        run_full_pipeline_sync(project_name, raw_idea, depth_mode="professional", blueprint=blueprint)

        guide_path = _locate_build_guide(slug)
        if not guide_path:
            build_status.write_status(slug, phase="error",
                                      error="Build Guide generation produced no document")
            return
        with open(guide_path, encoding="utf-8") as f:
            md = f.read()
        plan = plan_builder.build_plan(slug, md, raw_idea, project_name=project_name,
                                       pace=pace, source_doc=guide_path)
        plan_builder.save_plan(slug, plan)

        # ── Verify gate: the plan must validate before any BC write ──
        errors = project_plan.validate_plan(plan, doc_anchors=build_guide_parser.doc_anchors(md))
        if errors:
            build_status.write_status(
                slug, phase="error",
                message="Build plan failed verification.",
                error="; ".join(errors[:5]),
                violations=errors,
            )
            return

        # ── Phase c: reconcile the plan into Basecamp ───────────────
        n_todos = sum(1 for lvl, _n, _p in project_plan.iter_nodes(plan) if lvl == "todo")
        build_status.write_status(
            slug, phase="basecamp",
            message=f"Creating {n_todos} tasks across the project plan in Basecamp…",
        )
        creator_id = basecamp_build_writer.resolve_operator_bc_person_id(user, bc_project_id)
        summary = project_plan_reconciler.reconcile(
            plan, slug, user, bc_project_id, creator_id=creator_id, name_prefix=name_prefix,
            project_list_name=project_name,
        )

        # ── Phase d: resync so My Day reflects the new lists ────────
        try:
            from execution.products.ops import scorer, sync
            sync.pull_todos_for_project(operator_email, bc_project_id)
            scorer.score_all_todos(operator_email)
        except Exception:
            logger.warning("post-build My Day sync failed (non-blocking)", exc_info=True)

        build_status.write_status(
            slug, phase="done",
            message=f"Created {summary['created']} tasks in Basecamp.",
            tasks_created=summary["created"],
            updated=summary.get("updated", 0),
            reconcile_errors=summary.get("errors", []),
            bc_project_id=bc_project_id,
        )
    except Exception as e:  # noqa: BLE001 — background job: record, don't crash
        logger.error("My-Day build failed for slug=%s: %s", slug, e, exc_info=True)
        build_status.write_status(slug, phase="error", error=str(e)[:300])


def kick_build(session_id: str, bc_project_id, pace: str, operator_email: str,
               slug: str, idea_text: str, *, blueprint: str | None = None,
               name_prefix: str = "") -> None:
    """Spawn run_build in a daemon thread (mirrors my_day._kick_bg_full_sync)."""
    threading.Thread(
        target=run_build,
        args=(session_id, bc_project_id, pace, operator_email, slug, idea_text),
        kwargs={"blueprint": blueprint, "name_prefix": name_prefix},
        daemon=True,
        name=f"myday-build-{slug}",
    ).start()
