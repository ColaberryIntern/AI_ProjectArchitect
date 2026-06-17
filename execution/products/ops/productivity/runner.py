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
from .aggregate import OperatorInput

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


def _display_name(user_id: str) -> str:
    local = user_id.split("@", 1)[0]
    return " ".join(p.capitalize() for p in local.replace(".", " ").replace("_", " ").split())


def _global_ai_touched_ids() -> set:
    """Best-available set of bc_ids AI demonstrably touched, unioned across the
    auto-pickup audit and the @CB-mention seen-set. Parsed leniently so an
    unexpected schema degrades to 'no signal' rather than crashing. Per the plan,
    AI attribution is explicitly labelled 'estimated' in the report.
    """
    ids: set = set()
    candidates = [
        OPS_ROOT / "_autopickup" / "seen.json",
        OPS_ROOT / "_cb_mentions" / "seen.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ids |= _harvest_ids(raw)
    return ids


def _harvest_ids(obj) -> set:
    """Pull anything that looks like a todo/bc id out of a loosely-typed blob."""
    found: set = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("todo_id", "bc_id", "ticket_id", "id") and isinstance(v, (int, str)):
                try:
                    found.add(int(v))
                except (ValueError, TypeError):
                    pass
            else:
                found |= _harvest_ids(v)
    elif isinstance(obj, list):
        for item in obj:
            found |= _harvest_ids(item)
    return found


def _operator_signals(user_id: str) -> dict:
    """Optional per-operator override for AI activity counts (instrumentation
    drops these in later). Shape: {ai_action_count, human_action_count,
    ai_touched_ids}. Absent -> activity ratio stays n/a, never fabricated."""
    p = OPS_ROOT / user_id / "ai_signals.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _build_inputs(user_ids: list[str]) -> list[OperatorInput]:
    from execution.products.ops import store

    global_ai_ids = _global_ai_touched_ids()
    inputs = []
    for uid in user_ids:
        todos = store.load_todos(uid)
        state = store.load_state(uid)
        sig = _operator_signals(uid)

        their_ids = {getattr(t, "bc_id", None) for t in todos}
        ai_touched = (global_ai_ids & their_ids) | set(sig.get("ai_touched_ids", []))

        inputs.append(OperatorInput(
            user_id=uid,
            display_name=_display_name(uid),
            todos=todos,
            ai_touched_ids=ai_touched,
            ai_action_count=int(sig.get("ai_action_count", 0)),
            human_action_count=int(sig.get("human_action_count", 0)),
            syncs=int(getattr(state, "todos_synced", 0) or 0),
        ))
    return inputs


def run(*, now: datetime | None = None, rebuild_baseline: bool = False) -> ReportResult:
    started = (now or datetime.now(timezone.utc)).isoformat()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = started[:10]
    out_path = OUTPUT_DIR / f"{date_str}.html"

    try:
        user_ids = _discover_operators()
        if rebuild_baseline or not baseline.BASELINE_PATH.exists():
            baseline.build_and_save(user_ids)
        base = baseline.load_baseline()

        inputs = _build_inputs(user_ids)
        scorecard = aggregate.build_scorecard(inputs, baseline=base, now=now)

        from . import render
        html = render.render_html(scorecard)
        out_path.write_text(html, encoding="utf-8")

        sidecar = {
            "ran_at": started,
            "window_days": scorecard.window_days,
            "launch_date": scorecard.launch_date,
            "low_confidence": scorecard.low_confidence,
            "team_verdict": scorecard.team.verdict,
            "operators": [
                {
                    "user_id": c.user_id, "completed_7d": c.completed_7d,
                    "open_count": c.open_count, "ai_touched_share": c.ai_touched_share,
                    "ai_action_share": c.ai_action_share, "median_cycle_days": c.median_cycle_days,
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
