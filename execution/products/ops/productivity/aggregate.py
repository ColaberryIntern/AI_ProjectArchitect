"""Pure KPI math for the productivity report — no I/O, fully testable.

What this report measures (per Ali, 2026-06-17): WHO is using the new system that
pairs each task with AI, and whether that usage is translating into MORE productivity
than how the person performed BEFORE the change. It is NOT a ticket-hygiene report.

So the headline signal — and the color of every verdict — is the **AI Share**: of the
tasks completed in a person's scope this week, how many the AI closed. High AI share =
heavy use of the new system. We then read that against their pre-launch baseline to say
whether the usage is paying off (more / same / less than before).

Attribution rules:
  - Dedupe by bc_id (a shared-project task appears in every mirror).
  - Completions are attributed to whoever actually closed them (completed_by_name).
  - AI = completed_by one of AI_ACTORS (default "CB System", the bot).
  - Scope is filtered to employee + Gov-Contract work (EXCLUDE_PROJECTS drops Power BI /
    Center of Excellence / RMG).
"""
from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

LAUNCH_DATE = date(2026, 6, 14)

AI_ACTORS = {n.strip() for n in os.environ.get("PRODUCTIVITY_AI_ACTORS", "CB System").split(",") if n.strip()}

# AI-adoption colour bands (share of a person's completed work the AI closed).
AI_HIGH_THRESHOLD = float(os.environ.get("PRODUCTIVITY_AI_HIGH", "0.5"))
AI_LOW_THRESHOLD = float(os.environ.get("PRODUCTIVITY_AI_LOW", "0.2"))

# Projects excluded from scope (case-insensitive substring on bc_project_name).
EXCLUDE_PROJECTS = [s.strip().lower() for s in os.environ.get(
    "PRODUCTIVITY_EXCLUDE_PROJECTS", "power bi,center of excellence,rmg").split(",") if s.strip()]

SPARK_DAYS = int(os.environ.get("PRODUCTIVITY_SPARK_DAYS", "14"))
MIN_SAMPLE_FOR_VERDICT = int(os.environ.get("PRODUCTIVITY_MIN_SAMPLE", "3"))
MINUTES_SAVED_PER_AI_TASK = int(os.environ.get("PRODUCTIVITY_MIN_SAVED_PER_TASK", "15"))
DOLLARS_PER_HOUR = float(os.environ.get("PRODUCTIVITY_DOLLARS_PER_HOUR", "60"))
TREND_BAND = 0.10


@dataclass
class OperatorScorecard:
    display_name: str
    active_days_7d: int
    completed_today: int
    completed_7d: int
    completed_prior_7d: int
    open_count: int
    overdue_count: int
    stale_count: int
    net_flow_7d: int
    assigned_completed_7d: int
    ai_assisted_count: int
    ai_touched_share: float | None
    human_required_count: int
    delegatable_count: int
    median_cycle_days: float | None
    cycle_vs_baseline_pct: float | None
    throughput_vs_baseline_pct: float | None
    est_hours_saved_7d: float
    est_dollars_saved_7d: float
    overdue_rate: float
    stale_rate: float
    verdict: str
    verdict_reason: str
    spark_completed: list = field(default_factory=list)


@dataclass
class TeamRollup:
    people: int
    active_operators_7d: int
    completed_today: int
    completed_7d: int
    completed_prior_7d: int
    ai_completions_7d: int
    human_completions_7d: int
    ai_touched_share: float | None
    open_count: int
    overdue_count: int
    median_cycle_days: float | None
    est_hours_saved_7d: float
    est_dollars_saved_7d: float
    verdict: str
    verdict_reason: str
    spark_completed: list = field(default_factory=list)


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
    by_id: dict = {}
    for t in todos:
        bid = getattr(t, "bc_id", None)
        if bid is None:
            continue
        if bid not in by_id or (_completed_by(t) and not _completed_by(by_id[bid])):
            by_id[bid] = t
    return list(by_id.values())


def _cycle_days(todos) -> list[float]:
    return [getattr(t, "cycle_seconds", 0) / 86400 for t in todos if getattr(t, "cycle_seconds", 0) > 0]


def _daily_counts(dts: list, now: datetime, days: int) -> list[int]:
    """Counts per calendar day for the last `days`, oldest first / newest last."""
    base = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    counts = [0] * days
    for dt in dts:
        d0 = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        idx = days - 1 - (base - d0).days
        if 0 <= idx < days:
            counts[idx] += 1
    return counts


def filter_scope(todos: list, exclude_projects: list | None = None) -> list:
    """Drop todos whose project name matches an excluded substring (Power BI /
    Center of Excellence / RMG by default), keeping employee + Gov-Contract work."""
    ex = exclude_projects if exclude_projects is not None else EXCLUDE_PROJECTS
    if not ex:
        return list(todos)
    out = []
    for t in todos:
        name = (getattr(t, "bc_project_name", "") or "").lower()
        if any(term in name for term in ex):
            continue
        out.append(t)
    return out


# ── verdict (coloured by AI adoption, read against the before-baseline) ──


def _productivity_phrase(throughput_vs_base: float | None, cycle_vs_base: float | None) -> str:
    band = TREND_BAND * 100
    more = (throughput_vs_base is not None and throughput_vs_base >= band) or \
           (cycle_vs_base is not None and cycle_vs_base <= -band)
    less = (throughput_vs_base is not None and throughput_vs_base <= -band) and \
           not (cycle_vs_base is not None and cycle_vs_base <= -band)
    if more:
        return "and producing more than before"
    if less:
        return "but producing less than before"
    return "at about the same output as before"


def _verdict(*, ai_share: float | None, throughput_vs_base: float | None,
            cycle_vs_base: float | None, has_activity: bool) -> tuple[str, str]:
    """Colour by how much they USE the new system (AI share), with a read on whether
    the usage is paying off versus their pre-change baseline."""
    if not has_activity or ai_share is None:
        return ("NODATA", "No completed work in scope this week to assess.")
    prod = _productivity_phrase(throughput_vs_base, cycle_vs_base)
    pct = round(ai_share * 100)
    if ai_share >= AI_HIGH_THRESHOLD:
        return ("GREEN", f"Heavy use of the AI system ({pct}% of completed work), {prod}.")
    if ai_share >= AI_LOW_THRESHOLD:
        return ("AMBER", f"Partial use of the AI system ({pct}% of completed work), {prod}.")
    return ("RED", f"Low use of the AI system ({pct}% of completed work), {prod}.")


# ── per-person scoring ──────────────────────────────────────────────


def _score_person(todos: list, person: str, baseline_entry: dict | None,
                  now: datetime, ai_actors: set) -> OperatorScorecard:
    today = now.date()
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    win7 = now - timedelta(days=7)
    win14 = now - timedelta(days=14)
    win_spark = now - timedelta(days=SPARK_DAYS)

    personal_completed_7d, personal_prior_7d = [], 0
    completed_today = 0
    active_days: set[date] = set()
    spark_dts: list = []
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
        if cdt >= win_spark:
            spark_dts.append(cdt)

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
        ai_share=ai_share, throughput_vs_base=throughput_vs_base,
        cycle_vs_base=cycle_vs_base, has_activity=bool(assigned_completed_7d))

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
        spark_completed=_daily_counts(spark_dts, now, SPARK_DAYS),
    )


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
    win_spark = now - timedelta(days=SPARK_DAYS)

    completed_7d, prior_7d, today_n = [], 0, 0
    ai_n = human_n = 0
    spark_dts: list = []
    for t in todos:
        cdt = _completed_dt(t)
        if cdt is None:
            continue
        if cdt >= win_spark:
            spark_dts.append(cdt)
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
    median_cycle = _median(_cycle_days(completed_7d))
    throughput_vs_base = _pct_change(float(len(completed_7d)), float(prior_7d) if prior_7d else None)

    verdict, reason = _verdict(
        ai_share=ai_share, throughput_vs_base=throughput_vs_base,
        cycle_vs_base=None, has_activity=bool(completed_7d))

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
        spark_completed=_daily_counts(spark_dts, now, SPARK_DAYS),
    )


def build_scorecard(todos: list, *, baseline: dict | None = None, now: datetime | None = None,
                    ai_actors: set | None = None, exclude_projects: list | None = None) -> ProductivityScorecard:
    """Build the report from a flat list of OpsTodo-like rows (any number of mirrors).

    Dedupes by bc_id, filters out excluded projects, attributes completions by
    completed_by, treats `ai_actors` (default {"CB System"}) as AI-done work.
    Pure + deterministic.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    baseline = baseline or {}
    ai_actors = ai_actors if ai_actors is not None else set(AI_ACTORS)

    todos = _dedupe(todos)
    if exclude_projects is not None:
        todos = filter_scope(todos, exclude_projects)
    people = _people(todos, ai_actors)
    cards = [_score_person(todos, p, baseline.get(p), now, ai_actors) for p in people]
    # Rank by AI adoption (the report's headline), then by throughput.
    cards.sort(key=lambda c: ((c.ai_touched_share or 0), c.completed_7d, c.ai_assisted_count), reverse=True)
    team = _team_rollup(todos, cards, now, ai_actors)

    # Low-confidence when no person has a usable before-baseline to compare against
    # (the "vs before" reads are the part that needs post-launch history to firm up).
    low_confidence = not any(
        c.throughput_vs_baseline_pct is not None or c.cycle_vs_baseline_pct is not None
        for c in cards)

    return ProductivityScorecard(
        generated_at=now.isoformat(),
        window_days=7,
        launch_date=LAUNCH_DATE.isoformat(),
        operators=cards,
        team=team,
        low_confidence=low_confidence,
        assumptions={
            "ai_actors": sorted(ai_actors),
            "ai_high_pct": round(AI_HIGH_THRESHOLD * 100),
            "ai_low_pct": round(AI_LOW_THRESHOLD * 100),
            "excluded_projects": EXCLUDE_PROJECTS,
            "minutes_saved_per_ai_task": MINUTES_SAVED_PER_AI_TASK,
            "dollars_per_hour": DOLLARS_PER_HOUR,
            "spark_days": SPARK_DAYS,
            "attribution": "completions counted by completed_by, deduped by task id",
        },
    )
