"""End-to-end: discover operators -> gather AI signals -> aggregate -> render -> write -> deliver.

Mirrors execution/products/pilot/dash_runner.run(): always returns a result and
always writes the HTML to disk (the file is the source of truth); delivery is a
separate, gated concern. Delivery defaults OFF (PRODUCTIVITY_REPORT_DELIVERY).

The impure edges live here; the math (aggregate.py) and the before-baseline
(baseline.py) stay pure and unit-tested. Attribution signals are gathered here from
whatever exists on disk (Claude Code session-state, an optional curated ai_signals.json,
the @CB-mention cursor, git provenance) and injected into the pure scorer as AiSignals.
Every source is optional and best-effort: a missing source just yields more
"attribution_unknown", which the report shows honestly rather than mislabelling as
"low AI use".
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import PROJECT_ROOT

from . import aggregate, baseline
from .aggregate import AI_ACTORS, AiSignals

logger = logging.getLogger(__name__)

OPS_ROOT = PROJECT_ROOT / "output" / "ops"
OUTPUT_DIR = OPS_ROOT / "_productivity"
SIGNAL_WINDOW_DAYS = int(os.environ.get("PRODUCTIVITY_SIGNAL_WINDOW_DAYS", "7"))


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


# ── AI-signal harvest (best-effort, every source optional) ──────────


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _session_state_paths(user_ids: list[str]) -> list[Path]:
    """Candidate .claude/session-state.json locations: the repo root and any per-operator
    workspace mirror. Each anchors a Claude Code session to one Basecamp ticket."""
    candidates = [PROJECT_ROOT / ".claude" / "session-state.json"]
    for uid in user_ids:
        base = OPS_ROOT / uid
        candidates.append(base / ".claude" / "session-state.json")
        candidates.append(base / "workspace" / ".claude" / "session-state.json")
    return [p for p in candidates if p.exists()]


def _session_ticket_ids(user_ids: list[str], window_start: datetime) -> set[str]:
    ids: set[str] = set()
    for p in _session_state_paths(user_ids):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        at = data.get("active_ticket")
        if not isinstance(at, dict) or not at.get("todo_id"):
            continue
        started = _parse_iso(at.get("started_at"))
        if started is None or started >= window_start:
            ids.add(str(at["todo_id"]).strip())
    return ids


def _cb_active_operators(window_start: datetime) -> set[str]:
    """Operators whose AI answered an @CB mention in the window (a pull-based AI signal).
    Keys are user_ids/emails; aggregate also matches these against display names, and the
    curated sidecar can add display-name aliases."""
    path = OPS_ROOT / "_cb_mentions" / "cursor.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    active: set[str] = set()
    for user, seen in (data.get("per_user", {}) or {}).items():
        for _mid, ts in (seen or {}).items():
            dt = _parse_iso(ts)
            if dt is not None and dt >= window_start:
                active.add(user)
                break
    return active


def _git_ai_active(window_start: datetime) -> set[str]:
    """Authors of commits in the window carrying a Co-Authored-By: Claude trailer or a
    Session ID (CC-YYYYMMDD-xxxx). Best-effort: returns author names + emails, which the
    curated ai_signals.json can map onto operator display names where they differ."""
    since = window_start.strftime("%Y-%m-%d")
    try:
        out = subprocess.run(
            ["git", "log", f"--since={since}", "--pretty=%an%x1f%ae%x1f%H%x1f%b%x1e"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    import re
    session_re = re.compile(r"CC-\d{8}-\w{3,}")
    active: set[str] = set()
    for record in out.stdout.split("\x1e"):
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 4:
            continue
        an, ae, _h, body = parts[0].strip(), parts[1].strip(), parts[2], parts[3]
        if "co-authored-by: claude" in body.lower() or session_re.search(body):
            if an:
                active.add(an)
            if ae:
                active.add(ae)
    return active


def _curated_signals() -> dict:
    """Optional operator-curated sidecar (output/ops/_productivity/ai_signals.json). The
    escape hatch for signals we can't infer locally (progress-prefix joins, name aliases)."""
    path = OUTPUT_DIR / "ai_signals.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def gather_ai_signals(user_ids: list[str], *, now: datetime) -> AiSignals:
    window_start = now - timedelta(days=SIGNAL_WINDOW_DAYS)
    curated = _curated_signals()

    session_ids = _session_ticket_ids(user_ids, window_start)
    session_ids |= {str(x).strip() for x in curated.get("session_ticket_ids", [])}

    ai_marked = {str(x).strip() for x in curated.get("ai_marked_task_ids", [])}
    human_marked = {str(x).strip() for x in curated.get("human_marked_task_ids", [])}

    ai_active = set(curated.get("ai_active_operators", []))
    ai_active |= _cb_active_operators(window_start)
    ai_active |= _git_ai_active(window_start)

    # Comment authorship (the PRIMARY AI Share signal) from the comment-scan sidecar.
    from . import comment_scan
    comment_counts = comment_scan.load_comment_counts()
    comment_counts.update(curated.get("comment_counts", {}))

    return AiSignals(
        ai_actor_names=set(AI_ACTORS),
        session_ticket_ids=session_ids,
        ai_marked_task_ids=ai_marked,
        human_marked_task_ids=human_marked,
        ai_active_operators=ai_active,
        comment_counts=comment_counts,
    )


def run(*, now: datetime | None = None, rebuild_baseline: bool = False) -> ReportResult:
    started = (now or datetime.now(timezone.utc)).isoformat()
    from execution.ops_platform import runtime_controls
    if runtime_controls.is_paused("productivity_report"):
        return ReportResult(ran_at=started, output_path="", status="paused_by_operator")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = started[:10]
    out_path = OUTPUT_DIR / f"{date_str}.html"

    try:
        run_now = now or datetime.now(timezone.utc)
        user_ids = _discover_operators()
        todos = aggregate.filter_scope(_gather_todos(user_ids))  # employees + Gov Contracts only
        if rebuild_baseline or not baseline.BASELINE_PATH.exists():
            baseline.build_and_save(todos)
        base = baseline.load_baseline()

        # Refresh comment-authorship stats (the AI Share basis) before scoring. Best-effort:
        # a missing BC token / network just leaves the prior stats (or none) in place and the
        # report falls back to completion-based attribution. Gate via PRODUCTIVITY_COMMENT_SCAN.
        if os.environ.get("PRODUCTIVITY_COMMENT_SCAN", "1") == "1":
            try:
                from . import comment_scan
                stats = comment_scan.build_comment_stats(now=run_now)
                logger.info("comment scan: %d comments, %d people",
                            stats.get("scanned", 0), len(stats.get("per_person", {})))
            except Exception:
                logger.warning("comment scan skipped", exc_info=True)

        signals = gather_ai_signals(user_ids, now=run_now)
        scorecard = aggregate.build_scorecard(todos, baseline=base, now=now, ai_signals=signals)

        from . import render
        html = render.render_html(scorecard)
        out_path.write_text(html, encoding="utf-8")

        sidecar = {
            "ran_at": started,
            "window_days": scorecard.window_days,
            "launch_date": scorecard.launch_date,
            "low_confidence": scorecard.low_confidence,
            "team_verdict": scorecard.team.verdict,
            "signals": {
                "session_ticket_ids": len(signals.session_ticket_ids),
                "ai_marked_task_ids": len(signals.ai_marked_task_ids),
                "human_marked_task_ids": len(signals.human_marked_task_ids),
                "ai_active_operators": len(signals.ai_active_operators),
            },
            "team": {
                "verdict": scorecard.team.verdict,
                "completed_7d": scorecard.team.completed_7d,
                "ai_completions_7d": scorecard.team.ai_completions_7d,
                "human_completions_7d": scorecard.team.human_completions_7d,
                "unknown_completions_7d": scorecard.team.unknown_completions_7d,
                "ai_touched_share": scorecard.team.ai_touched_share,
                "ai_share_weighted": scorecard.team.ai_share_weighted,
                "attribution_confidence": scorecard.team.attribution_confidence,
            },
            "operators": [
                {
                    "name": c.display_name, "completed_7d": c.completed_7d,
                    "open_count": c.open_count,
                    "ai_assisted": c.ai_assisted_count, "human_only": c.human_only_count,
                    "attribution_unknown": c.attribution_unknown_count,
                    "attribution_confidence": c.attribution_confidence,
                    "ai_touched_share": c.ai_touched_share,
                    "ai_share_attributable": c.ai_share_attributable,
                    "ai_signal_tally": c.ai_signal_tally,
                    "volume_tier": c.volume_tier, "is_outlier": c.is_outlier,
                    "median_cycle_days": c.median_cycle_days,
                    "throughput_vs_baseline_pct": c.throughput_vs_baseline_pct,
                    "baseline_too_small": c.baseline_too_small, "verdict": c.verdict,
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
            "productivity report: operators=%d team_verdict=%s signals(session=%d,active=%d) delivery=%s path=%s",
            len(scorecard.operators), scorecard.team.verdict,
            len(signals.session_ticket_ids), len(signals.ai_active_operators),
            delivery_status, out_path,
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
