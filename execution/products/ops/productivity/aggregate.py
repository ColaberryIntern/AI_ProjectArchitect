"""Pure KPI math for the productivity report — no I/O, fully testable.

What this report measures (per Ali, 2026-06-17): WHO is using the new system that
pairs each task with AI, and whether that usage is translating into MORE productivity
than how the person performed BEFORE the change. It is NOT a ticket-hygiene report.

So the headline signal is **AI Share**: of the tasks a person completed this week, how
many were AI-assisted. We then read that against their pre-launch baseline.

Attribution model (the correctness rule that was wrong before — see
tmp/productivity-diagnosis.md / directives/productivity-report.md):
  - Dedupe by bc_id (a shared-project task appears in every mirror).
  - Completions are attributed to whoever actually closed them (completed_by_name).
  - A completion is AI-assisted when ANY AI signal is present, not only when the bot
    account closed it. When someone works through Claude Code the todo is completed
    under their OWN identity, so the old "completed_by == 'CB System'" test scored the
    heaviest AI users at 0%. Signals now considered (all injected via AiSignals, gathered
    by the runner's I/O edge so this math stays pure):
      * actor close      - completed_by is an AI actor (e.g. "CB System").
      * session join     - the task's bc_id was a Claude Code session active_ticket.
      * task AI marker    - [via … Claude Code] progress prefix, @CB answer, auto-pickup,
                            ai_signals.json — any per-task AI marker.
      * human marker     - positive evidence of an unaided manual close (no AI signal).
      * ai-active person - operator had AI sessions/commits/@CB answers in the window
                            (person-level prior; gates the verdict, never silently
                            converts an unknown task into a human one).
  - Every completion lands in exactly one of three buckets: ai_assisted / human_only /
    attribution_unknown. A measurement gap (unknown) is NEVER rendered as a behavioral
    verdict ("Low AI use"); that is the core bug this module now refuses to commit.
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

# AI-adoption colour bands (share of a person's completed work that was AI-assisted).
AI_HIGH_THRESHOLD = float(os.environ.get("PRODUCTIVITY_AI_HIGH", "0.5"))
AI_LOW_THRESHOLD = float(os.environ.get("PRODUCTIVITY_AI_LOW", "0.2"))

# Below this fraction of definitively-attributed completions we refuse to render a
# behavioural verdict (the measurement-gap guard) and say "attribution incomplete".
ATTRIB_CONF_MIN = float(os.environ.get("PRODUCTIVITY_ATTRIB_CONF_MIN", "0.5"))

# Winsorize "vs before": cap the magnitude of any baseline ratio so a tiny baseline can't
# print +30775%. A baseline thinner than this many completions is treated as no baseline.
TREND_CAP_PCT = float(os.environ.get("PRODUCTIVITY_TREND_CAP", "300"))
BASELINE_MIN_SAMPLE = int(os.environ.get("PRODUCTIVITY_MIN_BASELINE_SAMPLE", "3"))

# Completions whose cycle exceeds this are "backlog cleanup", split out of the new-work
# cycle median so clearing long-dormant todos doesn't read as "slower".
DORMANT_CYCLE_DAYS = float(os.environ.get("PRODUCTIVITY_DORMANT_DAYS", "30"))

# An operator closing at least this share of all team completions is flagged an outlier so
# they cannot silently set the scale for everyone else.
OUTLIER_DOMINANCE = float(os.environ.get("PRODUCTIVITY_OUTLIER_SHARE", "0.40"))

# Projects excluded from scope (case-insensitive substring on bc_project_name).
EXCLUDE_PROJECTS = [s.strip().lower() for s in os.environ.get(
    "PRODUCTIVITY_EXCLUDE_PROJECTS", "power bi,center of excellence,rmg").split(",") if s.strip()]

SPARK_DAYS = int(os.environ.get("PRODUCTIVITY_SPARK_DAYS", "14"))
MIN_SAMPLE_FOR_VERDICT = int(os.environ.get("PRODUCTIVITY_MIN_SAMPLE", "3"))
MINUTES_SAVED_PER_AI_TASK = int(os.environ.get("PRODUCTIVITY_MIN_SAVED_PER_TASK", "15"))
DOLLARS_PER_HOUR = float(os.environ.get("PRODUCTIVITY_DOLLARS_PER_HOUR", "60"))
TREND_BAND = 0.10

# Bucket labels (one per completion).
AI_ASSISTED = "ai_assisted"
HUMAN_ONLY = "human_only"
UNKNOWN = "attribution_unknown"


@dataclass
class AiSignals:
    """Pure, injectable attribution evidence. The runner populates this from disk
    (session-state, git provenance, @CB cursor, ai_signals.json); tests build it by
    hand. Task-id sets are normalised to strings so int bc_ids and string todo_ids
    from session-state compare cleanly."""
    ai_actor_names: set = field(default_factory=set)        # completed_by => AI closed it
    session_ticket_ids: set = field(default_factory=set)    # bc_ids anchored to a CC session
    ai_marked_task_ids: set = field(default_factory=set)    # bc_ids with a per-task AI marker
    human_marked_task_ids: set = field(default_factory=set)  # bc_ids with positive human evidence
    ai_active_operators: set = field(default_factory=set)    # operators AI-active in the window
    # Per-person comment authorship in the window: {name: {"ai": n, "human": n}}. This is
    # the PRIMARY AI Share signal — of a person's BC comments, how many were AI-authored.
    comment_counts: dict = field(default_factory=dict)

    @classmethod
    def coerce(cls, value, *, ai_actors: set) -> "AiSignals":
        if isinstance(value, cls):
            sig = value
        elif isinstance(value, dict):
            sig = cls(
                ai_actor_names=set(value.get("ai_actor_names", set())),
                session_ticket_ids={_sid(x) for x in value.get("session_ticket_ids", [])},
                ai_marked_task_ids={_sid(x) for x in value.get("ai_marked_task_ids", [])},
                human_marked_task_ids={_sid(x) for x in value.get("human_marked_task_ids", [])},
                ai_active_operators=set(value.get("ai_active_operators", set())),
                comment_counts=dict(value.get("comment_counts", {})),
            )
        else:
            sig = cls()
        # Actor names always include the configured AI actors (back-compat: "CB System").
        sig.ai_actor_names = set(sig.ai_actor_names) | set(ai_actors)
        sig.session_ticket_ids = {_sid(x) for x in sig.session_ticket_ids}
        sig.ai_marked_task_ids = {_sid(x) for x in sig.ai_marked_task_ids}
        sig.human_marked_task_ids = {_sid(x) for x in sig.human_marked_task_ids}
        return sig


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
    # Comment-based AI Share (PRIMARY): of this person's BC comments in the window, the
    # fraction that were AI-authored (Claude Code / system) vs typed by hand.
    comment_ai_count: int
    comment_human_count: int
    comment_ai_share: float | None
    ai_share_source: str                     # comments | completions | none
    # Three-bucket attribution over the operator's own completions this week.
    ai_assisted_count: int
    human_only_count: int
    attribution_unknown_count: int
    attribution_confidence: float | None     # (ai_assisted + human_only) / completed_7d
    ai_touched_share: float | None           # point share: ai_assisted / completed_7d
    ai_share_attributable: float | None      # ai_assisted / (ai_assisted + human_only)
    ai_share_upper: float | None             # (ai_assisted + unknown) / completed_7d
    ai_signal_tally: dict                    # which signals fired, by name
    ai_active: bool
    delegated_ai_count: int                  # assigned tasks an AI actor closed for them
    human_required_count: int
    delegatable_count: int
    median_cycle_days: float | None          # new-work median (dormant backlog excluded)
    backlog_cycle_days: float | None
    cycle_vs_baseline_pct: float | None
    throughput_vs_baseline_pct: float | None
    baseline_too_small: bool
    volume_tier: str                         # heavy | core | occasional | none
    is_outlier: bool
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
    unknown_completions_7d: int
    comment_ai_count: int                    # team comment authorship (AI vs human)
    comment_human_count: int
    ai_touched_share: float | None           # outlier-robust headline: MEDIAN of per-op shares
    ai_share_weighted: float | None          # old completion-weighted ratio, kept as secondary
    ai_share_p25: float | None
    ai_share_p75: float | None
    attribution_confidence: float | None
    open_count: int
    overdue_count: int
    median_cycle_days: float | None
    throughput_vs_baseline_pct: float | None
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


def _sid(value) -> str:
    return str(value).strip()


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


def _quantile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 3)
    pos = q * (len(vals) - 1)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * frac, 3)


def _pct_change(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base == 0:
        return None
    return round((current - base) / base * 100.0, 1)


def _winsorize(pct: float | None, cap: float = TREND_CAP_PCT) -> float | None:
    """Clamp a percent delta to ±cap so a thin baseline can't print a runaway ratio."""
    if pct is None:
        return None
    return round(max(-cap, min(cap, pct)), 1)


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


# ── attribution: classify one completion into a bucket ──────────────


def _ai_signals_fired(todo, sig: AiSignals) -> list[str]:
    """Names of the task-level AI signals present on this completion (may be empty)."""
    fired = []
    if _completed_by(todo) in sig.ai_actor_names:
        fired.append("actor_close")
    bid = _sid(getattr(todo, "bc_id", ""))
    if bid in sig.session_ticket_ids:
        fired.append("session")
    if bid in sig.ai_marked_task_ids:
        fired.append("marker")
    return fired


def _classify(todo, sig: AiSignals) -> tuple[str, list[str]]:
    """Return (bucket, signals_fired). ai_assisted on any AI signal; human_only only on
    positive human evidence; attribution_unknown when there is no signal either way."""
    fired = _ai_signals_fired(todo, sig)
    if fired:
        return AI_ASSISTED, fired
    if _sid(getattr(todo, "bc_id", "")) in sig.human_marked_task_ids:
        return HUMAN_ONLY, ["human_marker"]
    return UNKNOWN, []


# ── verdict (gated on attribution confidence) ───────────────────────


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


def _verdict(*, ai_share: float | None, attribution_confidence: float | None,
            ai_active: bool, throughput_vs_base: float | None,
            cycle_vs_base: float | None, has_activity: bool,
            source: str = "completions") -> tuple[str, str]:
    """Colour by AI adoption, but ONLY after the work is attributable. When too much of
    the week's completions carry no AI/human signal we refuse to call it "low AI use" —
    that would render a measurement gap as a behavioural verdict (the core bug). Instead
    we return UNKNOWN ("attribution incomplete"). RED is reached only when attribution is
    confident AND the attributed share is genuinely low (real human-only work dominating).
    """
    if not has_activity:
        return ("NODATA", "No completed work in scope this week to assess.")

    if attribution_confidence is None or attribution_confidence < ATTRIB_CONF_MIN:
        conf_pct = "0" if attribution_confidence is None else str(round(attribution_confidence * 100))
        active_note = " Recent AI sessions/commits are present, so this is not a low-use call." \
            if ai_active else ""
        return ("UNKNOWN",
                f"Attribution incomplete (only {conf_pct}% of this week's completions could be "
                f"attributed); baseline-building.{active_note}")

    if ai_share is None:
        return ("NODATA", "No completed work in scope this week to assess.")
    prod = _productivity_phrase(throughput_vs_base, cycle_vs_base)
    pct = round(ai_share * 100)
    basis = "comments" if source == "comments" else "attributed work"
    if ai_share >= AI_HIGH_THRESHOLD:
        return ("GREEN", f"Heavy use of the AI system ({pct}% of {basis}), {prod}.")
    if ai_share >= AI_LOW_THRESHOLD:
        return ("AMBER", f"Partial use of the AI system ({pct}% of {basis}), {prod}.")
    return ("RED", f"Low use of the AI system ({pct}% of {basis}), {prod}.")


# ── per-person scoring ──────────────────────────────────────────────


def _score_person(todos: list, person: str, baseline_entry: dict | None,
                  now: datetime, sig: AiSignals) -> OperatorScorecard:
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

    # Three-bucket attribution over the operator's OWN completions this week.
    buckets = {AI_ASSISTED: 0, HUMAN_ONLY: 0, UNKNOWN: 0}
    tally: dict = {}
    for t in personal_completed_7d:
        bucket, fired = _classify(t, sig)
        buckets[bucket] += 1
        for s in fired:
            tally[s] = tally.get(s, 0) + 1
    total = len(personal_completed_7d)
    ai_n, human_n, unknown_n = buckets[AI_ASSISTED], buckets[HUMAN_ONLY], buckets[UNKNOWN]
    attributable = ai_n + human_n
    completion_confidence = round(attributable / total, 3) if total else None
    ai_share_upper = round((ai_n + unknown_n) / total, 3) if total else None

    # PRIMARY AI Share = comment authorship (AI comments vs human comments). When comment
    # data exists for this person it is direct evidence and drives the headline + verdict;
    # completions stay as supporting throughput. Falls back to the completion attribution
    # only when we have no comment data for the person.
    cc = sig.comment_counts.get(person) or {}
    c_ai, c_human = int(cc.get("ai", 0)), int(cc.get("human", 0))
    c_total = c_ai + c_human
    comment_ai_share = round(c_ai / c_total, 3) if c_total else None
    if c_total:
        ai_share_source = "comments"
        ai_share_point = comment_ai_share
        ai_share_attributable = comment_ai_share
        attribution_confidence = 1.0          # comments are direct authorship evidence
    elif total:
        ai_share_source = "completions"
        ai_share_point = round(ai_n / total, 3)
        ai_share_attributable = round(ai_n / attributable, 3) if attributable else None
        attribution_confidence = completion_confidence
    else:
        ai_share_source = "none"
        ai_share_point = None
        ai_share_attributable = None
        attribution_confidence = None
    ai_active = (person in sig.ai_active_operators) or ai_n > 0 or c_ai > 0
    has_activity = bool(personal_completed_7d) or c_total > 0

    assigned_open, assigned_completed_7d, delegated_ai = [], [], 0
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
            if _completed_by(t) in sig.ai_actor_names:
                delegated_ai += 1

    overdue = [t for t in assigned_open if _is_overdue(t, today)]
    stale = []
    for t in assigned_open:
        udt = _parse_dt(getattr(t, "bc_updated_at", "") or "")
        if udt is not None and (now - udt) > timedelta(days=7):
            stale.append(t)
    human_required = [t for t in assigned_open if getattr(t, "category", "") == "human_required"]

    # Cycle: split new-work from long-dormant backlog cleanup; new-work median drives colour.
    cycles = _cycle_days(personal_completed_7d)
    new_cycles = [c for c in cycles if c <= DORMANT_CYCLE_DAYS]
    backlog_cycles = [c for c in cycles if c > DORMANT_CYCLE_DAYS]
    median_cycle = _median(new_cycles)
    backlog_cycle = _median(backlog_cycles)

    base_cycle = (baseline_entry or {}).get("median_cycle_days")
    base_weekly = (baseline_entry or {}).get("weekly_throughput")
    # Floor the baseline: an explicitly low-confidence or under-sampled baseline is too thin
    # to ratio against. A baseline that carries no sample_count at all is trusted (real
    # baselines from baseline.compute_baseline always include it).
    base_sample = (baseline_entry or {}).get("sample_count")
    baseline_too_small = bool(baseline_entry) and (
        bool((baseline_entry or {}).get("low_confidence"))
        or (base_sample is not None and base_sample < BASELINE_MIN_SAMPLE))

    if baseline_too_small:
        cycle_vs_base = None
        throughput_vs_base = None
    else:
        cycle_vs_base = _winsorize(_pct_change(median_cycle, base_cycle))
        throughput_vs_base = _winsorize(_pct_change(float(len(personal_completed_7d)), base_weekly))

    ai_for_savings = ai_n + delegated_ai
    est_hours = round(ai_for_savings * MINUTES_SAVED_PER_AI_TASK / 60.0, 1)
    est_dollars = round(est_hours * DOLLARS_PER_HOUR, 2)
    overdue_rate = round(len(overdue) / len(assigned_open), 3) if assigned_open else 0.0
    stale_rate = round(len(stale) / len(assigned_open), 3) if assigned_open else 0.0

    verdict, reason = _verdict(
        ai_share=ai_share_attributable, attribution_confidence=attribution_confidence,
        ai_active=ai_active, throughput_vs_base=throughput_vs_base,
        cycle_vs_base=cycle_vs_base, has_activity=has_activity, source=ai_share_source)

    return OperatorScorecard(
        display_name=person,
        active_days_7d=len(active_days),
        completed_today=completed_today,
        completed_7d=total,
        completed_prior_7d=personal_prior_7d,
        open_count=len(assigned_open),
        overdue_count=len(overdue),
        stale_count=len(stale),
        net_flow_7d=total - created_7d,
        assigned_completed_7d=len(assigned_completed_7d),
        comment_ai_count=c_ai,
        comment_human_count=c_human,
        comment_ai_share=comment_ai_share,
        ai_share_source=ai_share_source,
        ai_assisted_count=ai_n,
        human_only_count=human_n,
        attribution_unknown_count=unknown_n,
        attribution_confidence=attribution_confidence,
        ai_touched_share=ai_share_point,
        ai_share_attributable=ai_share_attributable,
        ai_share_upper=ai_share_upper,
        ai_signal_tally=tally,
        ai_active=ai_active,
        delegated_ai_count=delegated_ai,
        human_required_count=len(human_required),
        delegatable_count=len(assigned_open) - len(human_required),
        median_cycle_days=median_cycle,
        backlog_cycle_days=backlog_cycle,
        cycle_vs_baseline_pct=cycle_vs_base,
        throughput_vs_baseline_pct=throughput_vs_base,
        baseline_too_small=baseline_too_small,
        volume_tier="none",
        is_outlier=False,
        est_hours_saved_7d=est_hours,
        est_dollars_saved_7d=est_dollars,
        overdue_rate=overdue_rate,
        stale_rate=stale_rate,
        verdict=verdict,
        verdict_reason=reason,
        spark_completed=_daily_counts(spark_dts, now, SPARK_DAYS),
    )


# ── team rollup + public entry ──────────────────────────────────────


def _people(todos: list, ai_actors: set, extra: set | None = None) -> list[str]:
    names: set[str] = set(extra or set())
    for t in todos:
        cb = _completed_by(t)
        if cb:
            names.add(cb)
        names.update(_assignees(t))
    return sorted(n for n in names if n and n not in ai_actors)


def _assign_tiers(cards: list) -> None:
    """Bucket operators into heavy / core / occasional by completion volume and flag the
    dominant outlier(s). Tiers are relative to the team median so a power user and a light
    user are not judged on the same yardstick; the outlier flag marks anyone who closed
    >= OUTLIER_DOMINANCE of all completions so they can be read AS an outlier, not the scale.
    """
    counts = [c.completed_7d for c in cards if c.completed_7d > 0]
    total = sum(c.completed_7d for c in cards)
    med = statistics.median(counts) if counts else 0
    for c in cards:
        if c.completed_7d <= 0:
            c.volume_tier = "none"
        elif med and c.completed_7d >= 2 * med:
            c.volume_tier = "heavy"
        elif c.completed_7d >= max(1, 0.5 * med):
            c.volume_tier = "core"
        else:
            c.volume_tier = "occasional"
        c.is_outlier = bool(total) and (c.completed_7d / total) >= OUTLIER_DOMINANCE


def _team_rollup(todos: list, cards: list, now: datetime, sig: AiSignals) -> TeamRollup:
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    win7 = now - timedelta(days=7)
    win14 = now - timedelta(days=14)
    win_spark = now - timedelta(days=SPARK_DAYS)

    completed_7d, prior_7d, today_n = [], 0, 0
    ai_n = human_n = unknown_n = 0
    spark_dts: list = []
    new_cycles: list = []
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
            bucket, _ = _classify(t, sig)
            if bucket == AI_ASSISTED:
                ai_n += 1
            elif bucket == HUMAN_ONLY:
                human_n += 1
            else:
                unknown_n += 1
            cs = getattr(t, "cycle_seconds", 0) / 86400
            if 0 < cs <= DORMANT_CYCLE_DAYS:
                new_cycles.append(cs)
        elif cdt >= win14:
            prior_7d += 1

    n = len(completed_7d)
    ai_share_weighted = round(ai_n / n, 3) if n else None
    attributable = ai_n + human_n
    team_confidence = round(attributable / n, 3) if n else None

    # Outlier-robust headline: MEDIAN of per-operator shares, not the completion-weighted
    # ratio one mega-operator would own. Show the spread (p25-p75) too. Only operators with
    # a RELIABLE share count — comment-authored, or completions with real attribution — so a
    # long tail of unattributed (unknown) 0s does not falsely drag the median to zero.
    def _reliable(c):
        return c.ai_share_source == "comments" or (
            c.attribution_confidence is not None and c.attribution_confidence >= ATTRIB_CONF_MIN)
    point_shares = [c.ai_touched_share for c in cards
                    if c.ai_touched_share is not None and _reliable(c)]
    attr_shares = [c.ai_share_attributable for c in cards
                   if c.ai_share_attributable is not None and _reliable(c)]
    team_comment_ai = sum(c.comment_ai_count for c in cards)
    team_comment_human = sum(c.comment_human_count for c in cards)
    ai_share_median = _quantile(point_shares, 0.5)
    ai_share_p25 = _quantile(point_shares, 0.25)
    ai_share_p75 = _quantile(point_shares, 0.75)
    median_attr_share = _quantile(attr_shares, 0.5)

    open_todos = [t for t in todos if _is_open(t)]
    overdue = [t for t in open_todos if _is_overdue(t, now.date())]
    median_cycle = _median(new_cycles)
    # Floor the team baseline: a prior week below BASELINE_MIN_SAMPLE is too thin to ratio.
    base_prior = float(prior_7d) if prior_7d >= BASELINE_MIN_SAMPLE else None
    throughput_vs_base = _winsorize(_pct_change(float(n), base_prior))

    # Colour by the median operator share (outlier-robust). When every AI completion was
    # delegated to the bot and no human has attributable personal work, the per-operator
    # median is undefined — fall back to the team's own attributable share so the banner
    # still reflects that AI did the work, instead of spuriously reading NODATA.
    team_attr_share = round(ai_n / attributable, 3) if attributable else None
    # Headline: when comment authorship exists, the team AI Share is the team-wide comment
    # ratio (AI comments / authored comments) — it mirrors the per-person metric and, unlike
    # completions, is not owned by one mega-closer. Falls back to the outlier-robust median
    # of per-operator completion shares when there is no comment data.
    has_comments = (team_comment_ai + team_comment_human) > 0
    team_comment_share = round(team_comment_ai / (team_comment_ai + team_comment_human), 3) \
        if has_comments else None
    if has_comments:
        headline_share = team_comment_share
    else:
        # Outlier-robust median of reliable per-operator POINT shares; fall back to the team
        # attributable share only when no single operator is reliably attributed.
        headline_share = ai_share_median if ai_share_median is not None else team_attr_share
    verdict_share = headline_share
    team_source = "comments" if has_comments else "completions"
    team_verdict_conf = 1.0 if has_comments else team_confidence
    verdict, reason = _verdict(
        ai_share=verdict_share, attribution_confidence=team_verdict_conf,
        ai_active=any(c.ai_active for c in cards), throughput_vs_base=throughput_vs_base,
        cycle_vs_base=None, has_activity=bool(completed_7d) or has_comments, source=team_source)

    return TeamRollup(
        people=len(cards),
        active_operators_7d=sum(1 for c in cards if c.completed_7d > 0 or
                                (c.comment_ai_count + c.comment_human_count) > 0),
        completed_today=today_n,
        completed_7d=n,
        completed_prior_7d=prior_7d,
        ai_completions_7d=ai_n,
        human_completions_7d=human_n,
        unknown_completions_7d=unknown_n,
        comment_ai_count=team_comment_ai,
        comment_human_count=team_comment_human,
        ai_touched_share=headline_share,
        ai_share_weighted=ai_share_weighted,
        ai_share_p25=ai_share_p25,
        ai_share_p75=ai_share_p75,
        attribution_confidence=team_confidence,
        open_count=len(open_todos),
        overdue_count=len(overdue),
        median_cycle_days=median_cycle,
        throughput_vs_baseline_pct=throughput_vs_base,
        est_hours_saved_7d=round(sum(c.est_hours_saved_7d for c in cards), 1),
        est_dollars_saved_7d=round(sum(c.est_dollars_saved_7d for c in cards), 2),
        verdict=verdict,
        verdict_reason=reason,
        spark_completed=_daily_counts(spark_dts, now, SPARK_DAYS),
    )


def build_scorecard(todos: list, *, baseline: dict | None = None, now: datetime | None = None,
                    ai_actors: set | None = None, ai_signals=None,
                    exclude_projects: list | None = None) -> ProductivityScorecard:
    """Build the report from a flat list of OpsTodo-like rows (any number of mirrors).

    Dedupes by bc_id, filters out excluded projects, attributes completions by
    completed_by, and classifies each completion into ai_assisted / human_only /
    attribution_unknown using the injected `ai_signals` (default: only the AI actor
    close signal, i.e. {"CB System"}). Pure + deterministic.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    baseline = baseline or {}
    ai_actors = ai_actors if ai_actors is not None else set(AI_ACTORS)
    sig = AiSignals.coerce(ai_signals, ai_actors=ai_actors)

    todos = _dedupe(todos)
    if exclude_projects is not None:
        todos = filter_scope(todos, exclude_projects)
    comment_people = {p for p in sig.comment_counts if p not in sig.ai_actor_names}
    people = _people(todos, sig.ai_actor_names, extra=comment_people)
    cards = [_score_person(todos, p, baseline.get(p), now, sig) for p in people]
    _assign_tiers(cards)
    # Rank by AI adoption (the report's headline), then by throughput.
    cards.sort(key=lambda c: ((c.ai_touched_share or 0), c.completed_7d, c.ai_assisted_count), reverse=True)
    team = _team_rollup(todos, cards, now, sig)

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
            "ai_actors": sorted(sig.ai_actor_names),
            "ai_high_pct": round(AI_HIGH_THRESHOLD * 100),
            "ai_low_pct": round(AI_LOW_THRESHOLD * 100),
            "attrib_conf_min_pct": round(ATTRIB_CONF_MIN * 100),
            "trend_cap_pct": round(TREND_CAP_PCT),
            "baseline_min_sample": BASELINE_MIN_SAMPLE,
            "dormant_cycle_days": round(DORMANT_CYCLE_DAYS),
            "excluded_projects": EXCLUDE_PROJECTS,
            "minutes_saved_per_ai_task": MINUTES_SAVED_PER_AI_TASK,
            "dollars_per_hour": DOLLARS_PER_HOUR,
            "spark_days": SPARK_DAYS,
            "attribution": ("AI Share = AI-authored comments / all comments per person "
                            "(via-Claude-Code / CB System / doctrine markers); completions "
                            "deduped by task id and three-bucket classified as the fallback; "
                            "team AI share is the median of per-operator shares"),
        },
    )
