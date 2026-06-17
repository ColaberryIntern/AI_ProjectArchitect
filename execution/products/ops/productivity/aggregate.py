"""Pure KPI math for the productivity report — no I/O, fully testable.

Attribution model (corrected 2026-06-17):
  - Todos are deduped by bc_id first: the same Basecamp task appears in every
    operator's mirror for a shared project, so counting per-mirror triple-counts.
  - A completion is attributed to whoever actually closed it (`completed_by_name`),
    NOT to everyone who mirrors the project.
  - The AI bot account ("CB System") is a first-class actor. Tasks it completed are
    the real, already-stored "AI did this" signal — that is the AI-vs-human ratio,
    no log-harvesting or extra instrumentation required.

Per human operator:
  - throughput  = tasks THEY personally completed (completed_by == them)
  - backlog     = open/overdue/stale tasks ASSIGNED to them
  - AI leverage = of their assigned tasks completed this window, the share the AI
                  closed (assigned + completed_by AI / assigned + completed)

Team headline AI leverage = AI completions / all completions across the org.

The verdict (GREEN/AMBER/RED/BASELINE) guards the productivity paradox: faster per
task does not by itself mean more output.
"""
from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# Launch of the AI-assisted operating system. Completions before it are the
# baseline; on/after are the "after" we compare against.
LAUNCH_DATE = date(2026, 6, 14)

# The AI bot account(s) whose completions count as AI-done work. Override via
# PRODUCTIVITY_AI_ACTORS (comma-separated display names).
AI_ACTORS = {n.strip() for n in os.environ.get("PRODUCTIVITY_AI_ACTORS", "CB System").split(",") if n.strip()}

MIN_SAMPLE_FOR_VERDICT = int(os.environ.get("PRODUCTIVITY_MIN_SAMPLE", "3"))
# Labor-saving estimate per AI-completed task assigned to the operator. Explicit,
# conservative, surfaced in the report footer — we do not silently mint a figure.
MINUTES_SAVED_PER_AI_TASK = int(os.environ.get("PRODUCTIVITY_MIN_SAVED_PER_TASK", "15"))
DOLLARS_PER_HOUR = float(os.environ.get("PRODUCTIVITY_DOLLARS_PER_HOUR", "60"))
OVERDUE_RED_THRESHOLD = float(os.environ.get("PRODUCTIVITY_OVERDUE_RED", "0.30"))
TREND_BAND = 0.10


@dataclass
class OperatorScorecard:
    display_name: str
    # adoption / throughput (personal — what THEY closed)
    active_days_7d: int
    completed_today: int
    completed_7d: int
    completed_prior_7d: int
    # backlog (assigned to them)
    open_count: int
    overdue_count: int
    stale_count: int
    net_flow_7d: int
    # AI leverage (on their assigned workload)
    assigned_completed_7d: int
    ai_assisted_count: int
    ai_touched_share: float | None
    human_required_count: int
    delegatable_count: int
    # speed
    median_cycle_days: float | None
    cycle_vs_baseline_pct: float | None
    throughput_vs_baseline_pct: float | None
    est_hours_saved_7d: float
    est_dollars_saved_7d: float
    # quality
    overdue_rate: float
    stale_rate: float
    # verdict
    verdict: str
    verdict_reason: str


@dataclass
class TeamRollup:
    people: int
    active_operators_7d: int
    completed_today: int
    completed_7d: int                  # all actors, unique tasks
    completed_prior_7d: int
    ai_completions_7d: int
    human_completions_7d: int
    ai_touched_share: float | None     # AI's share of all completions (the headline)
    open_count: int
    overdue_count: int
    median_cycle_days: float | None
    est_hours_saved_7d: float
    est_dollars_saved_7d: float
    verdict: str
    verdict_reason: str


@dataclass
class ProductivityScorecard:
    generated_at: str
    window_days: int
    launch_date: str
    operators: list
    team: TeamRollup
    low_confidence: bool
    assumptions: dict


# ── helpers ─────────────────────────────────────────────────────────


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _completed_dt(todo) -> datetime | None:
    if getattr(todo, "status", "") != "completed":
        return None
    return _parse_dt(getattr(todo, "completed_at", "") or "")


def _created_dt(todo) -> datetime | None:
    return _parse_dt(getattr(todo, "bc_created_at", "") or "")


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def _pct_change(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base == 0:
        return None
    return round((current - base) / base * 100.0, 1)


def _is_open(todo) -> bool:
    return getattr(todo, "status", "") == "active" and not getattr(todo, "is_dismissed", False)


def _is_overdue(todo, today: date) -> bool:
    due = getattr(todo, "due_on", None)
    if not due:
        return False
    try:
        return datetime.strptime(due, "%Y-%m-%d").date() < today
    except (ValueError, TypeError):
        return False


def _completed_by(todo) -> str:
    return (getattr(todo, "completed_by_name", "") or "").strip()


def _assignees(todo) -> list[str]:
    return [n for n in (getattr(todo, "assignee_names", []) or []) if n]


def _dedupe(todos: list) -> list:
    """One row per bc_id (shared-project todos appear in many mirrors)."""
    by_id: dict = {}
    for t in todos:
        bid = getattr(t, "bc_id", None)
        if bid is None:
            continue
        # Prefer the row that carries completion metadata.
        if bid not in by_id or (_completed_by(t) and not _completed_by(by_id[bid])):
            by_id[bid] = t
    return list(by_id.values())


def _cycle_days(todos) -> list[float]:
    return [getattr(t, "cycle_seconds", 0) / 86400 for t in todos if getattr(t, "cycle_seconds", 0) > 0]


# ── per-person scoring ──────────────────────────────────────────────


def _score_person(todos: list, person: str, baseline_entry: dict | None,
                  now: datetime, ai_actors: set) -> OperatorScorecard:
    today = now.date()
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    win7 = now - timedelta(days=7)
    win14 = now - timedelta(days=14)

    # Personal throughput: tasks THIS person closed.
    personal_completed_7d, personal_prior_7d = [], 0
    completed_today = 0
    active_days: set[date] = set()
    for t in todos:
        if _completed_by(t) != person:
            continue
        cdt = _completed_dt(t)
        if cdt is None:
            continue
        if cdt >= day_start:
            completed_today += 1
        if cdt >= win7:
            personal_completed_7d.append(t)
            active_days.add(cdt.date())
        elif cdt >= win14:
            personal_prior_7d += 1

    # Backlog + AI leverage: tasks ASSIGNED to this person.
    assigned_open, assigned_completed_7d, assigned_ai = [], [], 0
    created_7d = 0
    for t in todos:
        if person not in _assignees(t):
            continue
        if _is_open(t):
            assigned_open.append(t)
        crt = _created_dt(t)
        if crt is not None and crt >= win7:
            created_7d += 1
        cdt = _completed_dt(t)
        if cdt is not None and cdt >= win7:
            assigned_completed_7d.append(t)
            if _completed_by(t) in ai_actors:
                assigned_ai += 1

    overdue = [t for t in assigned_open if _is_overdue(t, today)]
    stale = []
    for t in assigned_open:
        udt = _parse_dt(getattr(t, "bc_updated_at", "") or "")
        if udt is not None and (now - udt) > timedelta(days=7):
            stale.append(t)
    human_required = [t for t in assigned_open if getattr(t, "category", "") == "human_required"]

    ai_share = round(assigned_ai / len(assigned_completed_7d), 3) if assigned_completed_7d else None

    median_cycle = _median(_cycle_days(personal_completed_7d))
    base_cycle = (baseline_entry or {}).get("median_cycle_days")
    base_weekly = (baseline_entry or {}).get("weekly_throughput")
    cycle_vs_base = _pct_change(median_cycle, base_cycle)
    throughput_vs_base = _pct_change(float(len(personal_completed_7d)), base_weekly)

    est_hours = round(assigned_ai * MINUTES_SAVED_PER_AI_TASK / 60.0, 1)
    est_dollars = round(est_hours * DOLLARS_PER_HOUR, 2)

    overdue_rate = round(len(overdue) / len(assigned_open), 3) if assigned_open else 0.0
    stale_rate = round(len(stale) / len(assigned_open), 3) if assigned_open else 0.0

    verdict, reason = _verdict(
        completed_7d=len(personal_completed_7d), completed_prior_7d=personal_prior_7d,
        base_weekly=base_weekly, cycle_vs_baseline_pct=cycle_vs_base, overdue_rate=overdue_rate)

    return OperatorScorecard(
        display_name=person,
        active_days_7d=len(active_days),
        completed_today=completed_today,
        completed_7d=len(personal_completed_7d),
        completed_prior_7d=personal_prior_7d,
        open_count=len(assigned_open),
        overdue_count=len(overdue),
        stale_count=len(stale),
        net_flow_7d=len(personal_completed_7d) - created_7d,
        assigned_completed_7d=len(assigned_completed_7d),
        ai_assisted_count=assigned_ai,
        ai_touched_share=ai_share,
        human_required_count=len(human_required),
        delegatable_count=len(assigned_open) - len(human_required),
        median_cycle_days=median_cycle,
        cycle_vs_baseline_pct=cycle_vs_base,
        throughput_vs_baseline_pct=throughput_vs_base,
        est_hours_saved_7d=est_hours,
        est_dollars_saved_7d=est_dollars,
        overdue_rate=overdue_rate,
        stale_rate=stale_rate,
        verdict=verdict,
        verdict_reason=reason,
    )


def _verdict(*, completed_7d: int, completed_prior_7d: int, base_weekly: float | None,
            cycle_vs_baseline_pct: float | None, overdue_rate: float) -> tuple[str, str]:
    """More productive, just faster, or slipping? Answers the paradox question."""
    sample = completed_7d + completed_prior_7d
    if sample < MIN_SAMPLE_FOR_VERDICT or base_weekly in (None, 0):
        return ("BASELINE",
                "Not enough post-launch completions (or no pre-launch baseline) to judge a "
                "trend yet; still building the baseline.")
    if overdue_rate > OVERDUE_RED_THRESHOLD:
        return ("RED",
                f"{round(overdue_rate * 100)}% of open work is overdue. Gains in speed may be "
                "coming at the cost of quality and kept commitments.")
    throughput_up = completed_7d >= base_weekly * (1 + TREND_BAND)
    throughput_down = completed_7d <= base_weekly * (1 - TREND_BAND)
    cycle_faster = cycle_vs_baseline_pct is not None and cycle_vs_baseline_pct <= -TREND_BAND * 100
    cycle_slower = cycle_vs_baseline_pct is not None and cycle_vs_baseline_pct >= TREND_BAND * 100
    if cycle_slower and throughput_down:
        return ("RED", "Slower per task AND completing less than the pre-launch baseline. "
                       "The process is not paying off here.")
    if throughput_up and not cycle_slower:
        return ("GREEN", "Completing more than the pre-launch baseline without slowing down. "
                         "Genuinely more productive, not just faster.")
    if cycle_faster and not throughput_up:
        return ("AMBER", "Faster per task but NOT completing more than baseline. Check whether "
                         "scope shrank, rework rose, or AI is only taking the easy items.")
    return ("AMBER", "Mixed signal: no clear productivity gain or loss versus the pre-launch "
                     "baseline yet.")


# ── team rollup + public entry ──────────────────────────────────────


def _people(todos: list, ai_actors: set) -> list[str]:
    names: set[str] = set()
    for t in todos:
        cb = _completed_by(t)
        if cb:
            names.add(cb)
        names.update(_assignees(t))
    return sorted(n for n in names if n and n not in ai_actors)


def _team_rollup(todos: list, cards: list, now: datetime, ai_actors: set) -> TeamRollup:
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    win7 = now - timedelta(days=7)
    win14 = now - timedelta(days=14)

    completed_7d, prior_7d, today_n = [], 0, 0
    ai_n = human_n = 0
    for t in todos:
        cdt = _completed_dt(t)
        if cdt is None:
            continue
        if cdt >= win7:
            completed_7d.append(t)
            if cdt >= day_start:
                today_n += 1
            if _completed_by(t) in ai_actors:
                ai_n += 1
            else:
                human_n += 1
        elif cdt >= win14:
            prior_7d += 1

    ai_share = round(ai_n / len(completed_7d), 3) if completed_7d else None
    open_todos = [t for t in todos if _is_open(t)]
    overdue = [t for t in open_todos if _is_overdue(t, now.date())]
    overdue_rate = len(overdue) / len(open_todos) if open_todos else 0.0
    median_cycle = _median(_cycle_days(completed_7d))

    verdict, reason = _verdict(
        completed_7d=len(completed_7d), completed_prior_7d=prior_7d,
        base_weekly=float(prior_7d) if prior_7d else None,
        cycle_vs_baseline_pct=None, overdue_rate=overdue_rate)

    return TeamRollup(
        people=len(cards),
        active_operators_7d=sum(1 for c in cards if c.completed_7d > 0),
        completed_today=today_n,
        completed_7d=len(completed_7d),
        completed_prior_7d=prior_7d,
        ai_completions_7d=ai_n,
        human_completions_7d=human_n,
        ai_touched_share=ai_share,
        open_count=len(open_todos),
        overdue_count=len(overdue),
        median_cycle_days=median_cycle,
        est_hours_saved_7d=round(sum(c.est_hours_saved_7d for c in cards), 1),
        est_dollars_saved_7d=round(sum(c.est_dollars_saved_7d for c in cards), 2),
        verdict=verdict,
        verdict_reason=reason,
    )


def build_scorecard(todos: list, *, baseline: dict | None = None,
                    now: datetime | None = None, ai_actors: set | None = None) -> ProductivityScorecard:
    """Build the report from a flat list of OpsTodo-like rows (any number of mirrors).

    Dedupes by bc_id, attributes completions by completed_by, treats `ai_actors`
    (default {"CB System"}) as AI-done work. Pure + deterministic.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    baseline = baseline or {}
    ai_actors = ai_actors if ai_actors is not None else set(AI_ACTORS)

    todos = _dedupe(todos)
    people = _people(todos, ai_actors)
    cards = [_score_person(todos, p, baseline.get(p), now, ai_actors) for p in people]
    # Rank by personal throughput, then by AI-assisted workload.
    cards.sort(key=lambda c: (c.completed_7d, c.ai_assisted_count, c.open_count), reverse=True)
    team = _team_rollup(todos, cards, now, ai_actors)

    low_confidence = all(c.verdict == "BASELINE" for c in cards) if cards else True

    return ProductivityScorecard(
        generated_at=now.isoformat(),
        window_days=7,
        launch_date=LAUNCH_DATE.isoformat(),
        operators=cards,
        team=team,
        low_confidence=low_confidence,
        assumptions={
            "ai_actors": sorted(ai_actors),
            "minutes_saved_per_ai_task": MINUTES_SAVED_PER_AI_TASK,
            "dollars_per_hour": DOLLARS_PER_HOUR,
            "min_sample_for_verdict": MIN_SAMPLE_FOR_VERDICT,
            "trend_band_pct": round(TREND_BAND * 100),
            "attribution": "completions counted by completed_by, deduped by task id",
        },
    )
