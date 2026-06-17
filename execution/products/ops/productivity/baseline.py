"""Pre-launch baseline — the "before" half of the before/after comparison.

The new operating system went live 2026-06-14 (aggregate.LAUNCH_DATE). To tell
whether work actually sped up / people got more productive, the report compares
post-launch numbers against a per-operator baseline of how fast and how much
they completed BEFORE launch.

`compute_baseline()` is pure: given a list of completed OpsTodo-like objects it
returns {median_cycle_days, weekly_throughput, sample_count, window_weeks} for
the pre-launch window. The runner sources the completed todos from the local ops
mirror (store.list_completed_for_user pulls completed todos already) and persists
the result to output/ops/_productivity/baseline.json so the daily run is cheap.

Honest limitation: the local mirror only retains recently-completed todos
(freshness purge drops items >30d old with no future due date), so a brand-new
baseline may be thin. When sample_count is below MIN_BASELINE_SAMPLE the entry is
flagged low_confidence and the verdict stays in BASELINE mode rather than
over-claiming. A deeper walk of full Basecamp completion history (paging the BC
API) is a Phase-3 enrichment that drops in behind this same interface.
"""
from __future__ import annotations

import json
import os
import statistics
from datetime import date, datetime, timezone
from pathlib import Path

from config.settings import PROJECT_ROOT

from .aggregate import AI_ACTORS, LAUNCH_DATE, _completed_by, _completed_dt, _dedupe

BASELINE_DIR = PROJECT_ROOT / "output" / "ops" / "_productivity"
BASELINE_PATH = BASELINE_DIR / "baseline.json"

# Cap the look-back so a few very old completions don't dominate the weekly rate.
MAX_BASELINE_WEEKS = int(os.environ.get("PRODUCTIVITY_BASELINE_WEEKS", "8"))
# Below this many pre-launch completions, the baseline is too thin to trust.
MIN_BASELINE_SAMPLE = int(os.environ.get("PRODUCTIVITY_MIN_BASELINE_SAMPLE", "3"))


def compute_baseline(completed_todos: list, *, launch: date = LAUNCH_DATE) -> dict:
    """Pure: pre-launch {median_cycle_days, weekly_throughput, ...} for one operator.

    Considers only completions strictly before `launch`, within MAX_BASELINE_WEEKS
    of it. weekly_throughput = pre-launch completions / observed window weeks.
    """
    launch_dt = datetime(launch.year, launch.month, launch.day, tzinfo=timezone.utc)
    earliest = launch_dt - _weeks(MAX_BASELINE_WEEKS)

    pre: list = []
    for t in completed_todos:
        cdt = _completed_dt(t)
        if cdt is None or cdt >= launch_dt or cdt < earliest:
            continue
        pre.append((cdt, t))

    if not pre:
        return {"median_cycle_days": None, "weekly_throughput": None,
                "sample_count": 0, "window_weeks": 0, "low_confidence": True}

    cycles = [t.cycle_seconds / 86400 for _, t in pre if getattr(t, "cycle_seconds", 0) > 0]
    median_cycle = round(statistics.median(cycles), 1) if cycles else None

    span_days = (launch_dt - min(c for c, _ in pre)).days
    window_weeks = max(1, round(span_days / 7))
    weekly_throughput = round(len(pre) / window_weeks, 2)

    return {
        "median_cycle_days": median_cycle,
        "weekly_throughput": weekly_throughput,
        "sample_count": len(pre),
        "window_weeks": window_weeks,
        "low_confidence": len(pre) < MIN_BASELINE_SAMPLE,
    }


def _weeks(n: int):
    from datetime import timedelta
    return timedelta(weeks=n)


# ── Disk layer (thin; the math above is what tests exercise) ─────────


def load_baseline() -> dict:
    """Return the persisted {user_id: baseline_entry} map, or {} if none yet."""
    if not BASELINE_PATH.exists():
        return {}
    try:
        payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # Stored as {"_built_at": ..., "operators": {uid: entry}}.
    return payload.get("operators", {}) if isinstance(payload, dict) else {}


def save_baseline(by_user: dict) -> Path:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"_built_at": datetime.now(timezone.utc).isoformat(), "operators": by_user}
    BASELINE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return BASELINE_PATH


def build_and_save(user_ids: list[str], *, ai_actors: set | None = None) -> dict:
    """Compute + persist a per-PERSON baseline from the local ops mirrors.

    Unions every operator's todos, dedupes by task id, then groups pre-launch
    completions by who actually closed them (completed_by_name). Keyed by person
    display name so it lines up with the scorecard. AI actors are excluded — the
    baseline is human throughput. No BC calls. Returns {person: baseline_entry}.
    """
    from execution.products.ops import store

    ai_actors = ai_actors if ai_actors is not None else set(AI_ACTORS)
    all_todos: list = []
    for uid in user_ids:
        all_todos.extend(store.load_todos(uid))

    by_person: dict[str, list] = {}
    for t in _dedupe(all_todos):
        person = _completed_by(t)
        if not person or person in ai_actors:
            continue
        if _completed_dt(t) is None:
            continue
        by_person.setdefault(person, []).append(t)

    baseline = {person: compute_baseline(rows) for person, rows in by_person.items()}
    save_baseline(baseline)
    return baseline
