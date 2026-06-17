"""Pure KPI math for the productivity report — no I/O, fully testable.

Everything here operates on plain in-memory inputs (OpsTodo-like objects,
duck-typed via getattr exactly like dash_runner does) so the runner can wire
in real disk/BC data while tests drive it with SimpleNamespace fixtures.

The five pillars (see directives/productivity-report.md):
  1. Adoption    — syncs, active days
  2. Throughput  — completions, net flow, backlog
  3. AI leverage — AI-touched task share (outcome) + AI action share (activity)
  4. Speed       — median cycle time, vs baseline, AI vs human cohort
  5. Quality     — overdue/stale rate (the paradox guard)

The verdict turns pillars 2+4+5 into a GREEN / AMBER / RED / BASELINE call that
directly answers "are they slowing down because they can work faster?".
"""
from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# The new AI-assisted operating system went live on this date. Completions
# before it are "before"; completions on/after are "after". Used to split the
# baseline (baseline.py) and to label the comparison window.
LAUNCH_DATE = date(2026, 6, 14)

# A verdict needs enough post-launch completions to mean anything. Below this,
# we say so ("baseline building") rather than over-claiming a trend.
MIN_SAMPLE_FOR_VERDICT = int(os.environ.get("PRODUCTIVITY_MIN_SAMPLE", "3"))

# Labor-saving estimate per AI-touched completion. Deliberately conservative
# and surfaced as an explicit assumption in the report footer — we do NOT
# silently mint a dollar figure. Calendar cycle-time reduction is NOT the same
# as labor hours, so we model saved labor separately and transparently.
MINUTES_SAVED_PER_AI_TASK = int(os.environ.get("PRODUCTIVITY_MIN_SAVED_PER_TASK", "15"))
DOLLARS_PER_HOUR = float(os.environ.get("PRODUCTIVITY_DOLLARS_PER_HOUR", "60"))

# Quality gate: above this overdue share, speed is read as costing quality.
OVERDUE_RED_THRESHOLD = float(os.environ.get("PRODUCTIVITY_OVERDUE_RED", "0.30"))

# Trend sensitivity: a change must clear +/-10% to count as up/down/faster/slower.
TREND_BAND = 0.10


# ── Inputs ──────────────────────────────────────────────────────────


@dataclass
class OperatorInput:
    """Everything the math needs for one operator. The runner builds these
    from the on-disk ops store + AI-signal stores; tests build them directly."""
    user_id: str                       # output/ops/<user_id>/  (an email)
    display_name: str
    todos: list = field(default_factory=list)   # OpsTodo-like objects
    # Outcome signal: bc_ids of tasks AI demonstrably touched (auto-pickup
    # proposal, @CB answer, [via ... Claude Code] progress). Best-available,
    # labelled "estimated" in the report.
    ai_touched_ids: set = field(default_factory=set)
    # Activity signal: discrete work-events we can attribute to AI vs human.
    ai_action_count: int = 0
    human_action_count: int = 0
    # Adoption signal from state.json.
    syncs: int = 0


@dataclass
class OperatorScorecard:
    user_id: str
    display_name: str
    # Pillar 1 — adoption
    syncs: int
    active_days_7d: int
    # Pillar 2 — throughput
    completed_today: int
    completed_7d: int
    completed_prior_7d: int
    open_count: int
    overdue_count: int
    stale_count: int
    net_flow_7d: int                   # completed - created, last 7d
    # Pillar 3 — AI leverage (two views)
    ai_touched_share: float | None     # outcome: % of completions AI touched
    ai_action_share: float | None      # activity: % of work-events AI-driven
    human_required_count: int
    delegatable_count: int
    # Pillar 4 — speed
    median_cycle_days: float | None
    median_cycle_ai_days: float | None
    median_cycle_human_days: float | None
    cycle_vs_baseline_pct: float | None
    throughput_vs_baseline_pct: float | None
    est_hours_saved_7d: float
    est_dollars_saved_7d: float
    # Pillar 5 — quality
    overdue_rate: float
    stale_rate: float
    # Verdict
    verdict: str                       # GREEN | AMBER | RED | BASELINE
    verdict_reason: str


@dataclass
class TeamRollup:
    operators: int
    active_operators_7d: int
    completed_today: int
    completed_7d: int
    completed_prior_7d: int
    open_count: int
    overdue_count: int
    ai_touched_share: float | None
    ai_action_share: float | None
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
    operators: list                    # list[OperatorScorecard]
    team: TeamRollup
    low_confidence: bool               # True until enough post-launch data
    assumptions: dict


# ── Date helpers ────────────────────────────────────────────────────


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _completed_dt(todo) -> datetime | None:
    if getattr(todo, "status", "") != "completed":
        return None
    return _parse_dt(getattr(todo, "completed_at", "") or "")


def _created_dt(todo) -> datetime | None:
    return _parse_dt(getattr(todo, "bc_created_at", "") or "")


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(statistics.median(vals), 1)


def _pct_change(current: float | None, base: float | None) -> float | None:
    """Signed % change of current vs base; None if base is missing/zero."""
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


# ── Per-operator scoring ────────────────────────────────────────────


def _score_operator(op: OperatorInput, baseline_entry: dict | None, now: datetime) -> OperatorScorecard:
    today = now.date()
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    win7_start = now - timedelta(days=7)
    win14_start = now - timedelta(days=14)

    completed_today = 0
    completed_7d: list = []
    completed_prior_7d = 0
    created_7d = 0
    active_days: set[date] = set()

    open_todos = [t for t in op.todos if _is_open(t)]

    for t in op.todos:
        cdt = _completed_dt(t)
        if cdt is not None:
            if cdt >= day_start:
                completed_today += 1
            if cdt >= win7_start:
                completed_7d.append(t)
                active_days.add(cdt.date())
            elif cdt >= win14_start:
                completed_prior_7d += 1
        crt = _created_dt(t)
        if crt is not None and crt >= win7_start:
            created_7d += 1

    overdue = [t for t in open_todos if _is_overdue(t, today)]
    stale = []
    for t in open_todos:
        udt = _parse_dt(getattr(t, "bc_updated_at", "") or "")
        if udt is not None and (now - udt) > timedelta(days=7):
            stale.append(t)

    human_required = [t for t in open_todos if getattr(t, "category", "") == "human_required"]

    # Pillar 3 — AI leverage
    ai_touched_completed = [t for t in completed_7d if getattr(t, "bc_id", None) in op.ai_touched_ids]
    ai_touched_share = (
        round(len(ai_touched_completed) / len(completed_7d), 3) if completed_7d else None
    )
    action_total = op.ai_action_count + op.human_action_count
    ai_action_share = round(op.ai_action_count / action_total, 3) if action_total else None

    # Pillar 4 — speed
    def _cycle_days(todos):
        return [getattr(t, "cycle_seconds", 0) / 86400 for t in todos
                if getattr(t, "cycle_seconds", 0) > 0]

    median_cycle = _median(_cycle_days(completed_7d))
    median_cycle_ai = _median(_cycle_days(ai_touched_completed))
    human_completed = [t for t in completed_7d if getattr(t, "bc_id", None) not in op.ai_touched_ids]
    median_cycle_human = _median(_cycle_days(human_completed))

    base_cycle = (baseline_entry or {}).get("median_cycle_days")
    base_weekly = (baseline_entry or {}).get("weekly_throughput")
    cycle_vs_baseline = _pct_change(median_cycle, base_cycle)
    throughput_vs_baseline = _pct_change(float(len(completed_7d)), base_weekly)

    est_hours = round(len(ai_touched_completed) * MINUTES_SAVED_PER_AI_TASK / 60.0, 1)
    est_dollars = round(est_hours * DOLLARS_PER_HOUR, 2)

    overdue_rate = round(len(overdue) / len(open_todos), 3) if open_todos else 0.0
    stale_rate = round(len(stale) / len(open_todos), 3) if open_todos else 0.0

    verdict, reason = _verdict(
        completed_7d=len(completed_7d),
        completed_prior_7d=completed_prior_7d,
        base_weekly=base_weekly,
        cycle_vs_baseline_pct=cycle_vs_baseline,
        overdue_rate=overdue_rate,
    )

    return OperatorScorecard(
        user_id=op.user_id,
        display_name=op.display_name,
        syncs=op.syncs,
        active_days_7d=len(active_days),
        completed_today=completed_today,
        completed_7d=len(completed_7d),
        completed_prior_7d=completed_prior_7d,
        open_count=len(open_todos),
        overdue_count=len(overdue),
        stale_count=len(stale),
        net_flow_7d=len(completed_7d) - created_7d,
        ai_touched_share=ai_touched_share,
        ai_action_share=ai_action_share,
        human_required_count=len(human_required),
        delegatable_count=len(open_todos) - len(human_required),
        median_cycle_days=median_cycle,
        median_cycle_ai_days=median_cycle_ai,
        median_cycle_human_days=median_cycle_human,
        cycle_vs_baseline_pct=cycle_vs_baseline,
        throughput_vs_baseline_pct=throughput_vs_baseline,
        est_hours_saved_7d=est_hours,
        est_dollars_saved_7d=est_dollars,
        overdue_rate=overdue_rate,
        stale_rate=stale_rate,
        verdict=verdict,
        verdict_reason=reason,
    )


def _verdict(*, completed_7d: int, completed_prior_7d: int, base_weekly: float | None,
            cycle_vs_baseline_pct: float | None, overdue_rate: float) -> tuple[str, str]:
    """The assessment. Answers: more productive, just faster, or slipping?

    Precedence:
      BASELINE — too little post-launch data, or no baseline to compare to.
      RED      — overdue share high (speed costing quality), or slower AND
                 completing less than baseline.
      GREEN    — completing more than baseline without slowing down.
      AMBER    — faster per task but NOT completing more (the paradox), or any
                 other mixed signal.
    """
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
        return ("RED",
                "Slower per task AND completing less than the pre-launch baseline. The process "
                "is not paying off here.")
    if throughput_up and not cycle_slower:
        return ("GREEN",
                "Completing more than the pre-launch baseline without slowing down. Genuinely "
                "more productive, not just faster.")
    if cycle_faster and not throughput_up:
        return ("AMBER",
                "Faster per task but NOT completing more than baseline. Check whether scope "
                "shrank, rework rose, or AI is only taking the easy items. Speed is not yet "
                "translating into more output.")
    return ("AMBER",
            "Mixed signal: no clear productivity gain or loss versus the pre-launch baseline yet.")


# ── Team rollup + public entry ──────────────────────────────────────


def _team_rollup(cards: list[OperatorScorecard]) -> TeamRollup:
    completed_7d = sum(c.completed_7d for c in cards)
    completed_prior = sum(c.completed_prior_7d for c in cards)
    open_count = sum(c.open_count for c in cards)
    overdue = sum(c.overdue_count for c in cards)

    # Outcome AI share, weighted by completions.
    ai_touched = sum((c.ai_touched_share or 0) * c.completed_7d for c in cards)
    ai_touched_share = round(ai_touched / completed_7d, 3) if completed_7d else None

    # Activity share at team level: average of per-operator shares that exist.
    shares = [c.ai_action_share for c in cards if c.ai_action_share is not None]
    ai_action_share = round(sum(shares) / len(shares), 3) if shares else None

    cycles = [c.median_cycle_days for c in cards if c.median_cycle_days is not None]
    median_cycle = round(statistics.median(cycles), 1) if cycles else None

    hours = round(sum(c.est_hours_saved_7d for c in cards), 1)
    dollars = round(sum(c.est_dollars_saved_7d for c in cards), 2)

    overdue_rate = overdue / open_count if open_count else 0.0
    verdict, reason = _verdict(
        completed_7d=completed_7d,
        completed_prior_7d=completed_prior,
        base_weekly=float(completed_prior) if completed_prior else None,
        cycle_vs_baseline_pct=None,
        overdue_rate=overdue_rate,
    )

    return TeamRollup(
        operators=len(cards),
        active_operators_7d=sum(1 for c in cards if c.completed_7d > 0 or c.syncs > 0),
        completed_today=sum(c.completed_today for c in cards),
        completed_7d=completed_7d,
        completed_prior_7d=completed_prior,
        open_count=open_count,
        overdue_count=overdue,
        ai_touched_share=ai_touched_share,
        ai_action_share=ai_action_share,
        median_cycle_days=median_cycle,
        est_hours_saved_7d=hours,
        est_dollars_saved_7d=dollars,
        verdict=verdict,
        verdict_reason=reason,
    )


def build_scorecard(operators: list[OperatorInput], *,
                   baseline: dict[str, dict] | None = None,
                   now: datetime | None = None) -> ProductivityScorecard:
    """Build the full report from in-memory operator inputs. Pure + deterministic.

    baseline: {user_id: {"median_cycle_days": float, "weekly_throughput": float, ...}}
    now: injectable clock (UTC); defaults to current time.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    baseline = baseline or {}

    cards = [_score_operator(op, baseline.get(op.user_id), now) for op in operators]
    cards.sort(key=lambda c: (c.completed_7d, c.open_count), reverse=True)
    team = _team_rollup(cards)

    low_confidence = all(c.verdict == "BASELINE" for c in cards) if cards else True

    return ProductivityScorecard(
        generated_at=now.isoformat(),
        window_days=7,
        launch_date=LAUNCH_DATE.isoformat(),
        operators=cards,
        team=team,
        low_confidence=low_confidence,
        assumptions={
            "minutes_saved_per_ai_task": MINUTES_SAVED_PER_AI_TASK,
            "dollars_per_hour": DOLLARS_PER_HOUR,
            "min_sample_for_verdict": MIN_SAMPLE_FOR_VERDICT,
            "trend_band_pct": round(TREND_BAND * 100),
            "ai_attribution": "estimated (auto-pickup + @CB-mention + Claude-Code progress signals)",
        },
    )
