"""End-to-end: discover operators -> aggregate -> render -> write -> deliver.

Mirrors execution/products/pilot/dash_runner.run(): always returns a result and
always writes the HTML to disk (the file is the source of truth); delivery is a
separate, gated concern. Delivery defaults OFF (PRODUCTIVITY_REPORT_DELIVERY).

The impure edges live here; the math (aggregate.py) and the before-baseline
(baseline.py) stay pure and unit-tested.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.settings import PROJECT_ROOT

from . import aggregate, baseline

logger = logging.getLogger(__name__)

OPS_ROOT = PROJECT_ROOT / "output" / "ops"
OUTPUT_DIR = OPS_ROOT / "_productivity"


@dataclass
class ReportResult:
    ran_at: str
    output_path: str
    status: str                  # ok | error
    operators: int = 0
    delivery_status: str = "not_attempted"
    error: str = ""
    verdict: str = ""


def _discover_operators() -> list[str]:
    """Every operator with an ops store: output/ops/<user_id>/ (skip _internal)."""
    if not OPS_ROOT.exists():
        return []
    out = []
    for child in sorted(OPS_ROOT.iterdir()):
        if child.is_dir() and not child.name.startswith("_") and (child / "todos.json").exists():
            out.append(child.name)
    return out


def _gather_todos(user_ids: list[str]) -> list:
    """Union every operator's mirror into one list. aggregate dedupes by task id
    and attributes completions by completed_by, so cross-mirror duplicates of a
    shared-project task collapse to one row."""
    from execution.products.ops import store

    todos: list = []
    for uid in user_ids:
        todos.extend(store.load_todos(uid))
    return todos


def run(*, now: datetime | None = None, rebuild_baseline: bool = False) -> ReportResult:
    started = (now or datetime.now(timezone.utc)).isoformat()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = started[:10]
    out_path = OUTPUT_DIR / f"{date_str}.html"

    try:
        user_ids = _discover_operators()
        todos = aggregate.filter_scope(_gather_todos(user_ids))  # employees + Gov Contracts only
        if rebuild_baseline or not baseline.BASELINE_PATH.exists():
            baseline.build_and_save(todos)
        base = baseline.load_baseline()

        scorecard = aggregate.build_scorecard(todos, baseline=base, now=now)

        from . import render
        html = render.render_html(scorecard)
        out_path.write_text(html, encoding="utf-8")

        sidecar = {
            "ran_at": started,
            "window_days": scorecard.window_days,
            "launch_date": scorecard.launch_date,
            "low_confidence": scorecard.low_confidence,
            "team_verdict": scorecard.team.verdict,
            "team": {
                "verdict": scorecard.team.verdict,
                "completed_7d": scorecard.team.completed_7d,
                "ai_completions_7d": scorecard.team.ai_completions_7d,
                "human_completions_7d": scorecard.team.human_completions_7d,
                "ai_touched_share": scorecard.team.ai_touched_share,
            },
            "operators": [
                {
                    "name": c.display_name, "completed_7d": c.completed_7d,
                    "open_count": c.open_count, "assigned_completed_7d": c.assigned_completed_7d,
                    "ai_assisted_count": c.ai_assisted_count, "ai_touched_share": c.ai_touched_share,
                    "median_cycle_days": c.median_cycle_days,
                    "cycle_vs_baseline_pct": c.cycle_vs_baseline_pct, "verdict": c.verdict,
                }
                for c in scorecard.operators
            ],
            "assumptions": scorecard.assumptions,
        }
        (OUTPUT_DIR / f"{date_str}.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

        delivery_status = "not_attempted"
        if os.environ.get("PRODUCTIVITY_REPORT_DELIVERY", "0") == "1":
            from . import delivery
            delivery_status = delivery.send_report(str(out_path), started).status

        logger.info(
            "productivity report: operators=%d team_verdict=%s delivery=%s path=%s",
            len(scorecard.operators), scorecard.team.verdict, delivery_status, out_path,
        )
        return ReportResult(
            ran_at=started, output_path=str(out_path), status="ok",
            operators=len(scorecard.operators), delivery_status=delivery_status,
            verdict=scorecard.team.verdict,
        )
    except Exception as e:
        logger.exception("productivity report run failed")
        return ReportResult(ran_at=started, output_path="", status="error", error=str(e))


def main() -> int:
    """CLI: `python -m execution.products.ops.productivity.runner [--rebuild-baseline]`.

    Phase 1 entry point for manual + OS-cron runs. Phase 2 wires run() into a
    daily APScheduler job in app/main lifespan (mirrors pilot/scheduler.py).
    """
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rebuild = "--rebuild-baseline" in sys.argv
    result = run(rebuild_baseline=rebuild)
    print(f"status={result.status} operators={result.operators} "
          f"team_verdict={result.verdict} delivery={result.delivery_status}")
    print(f"report: {result.output_path}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
