"""Per-list + per-project rollups for the My Day daily-briefing view.

The user view doctrine: "Am I on time, what's late, where am I in jeopardy"
before you start working. This module computes the aggregates that answer
those questions in one glance.

Per-list health 0-100 (higher = healthier):
    100 - 12*overdue - 6*red - 2*amber - 1*open_count, floored at 0.
Labels:
    >= 80    ON TRACK   (green)
    60-79    AI RISK    (amber)
    < 60     AT RISK    (red)

Tier mix per list:
    H  — todos with category == 'human_required' (need your decision/action)
    AI — everything else (waitable, delegatable, or low-urgency)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from . import bc_urls
from .store import OpsTodo


def _days_until(due_on: str | None) -> int | None:
    if not due_on:
        return None
    try:
        d = datetime.strptime(due_on, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - datetime.now(timezone.utc).date()).days


def is_overdue(t: OpsTodo) -> bool:
    d = _days_until(t.due_on)
    return d is not None and d < 0


def tier(t: OpsTodo) -> str:
    """Classify a todo as 'H' (human-needed) or 'AI' (delegatable / waitable)."""
    return "H" if t.category == "human_required" else "AI"


@dataclass
class ListRollup:
    list_id: int
    list_name: str
    project_id: int
    project_name: str
    open_count: int = 0
    overdue_count: int = 0
    red_count: int = 0
    amber_count: int = 0
    human_count: int = 0      # tier H
    ai_count: int = 0         # tier AI
    score: int = 100
    score_label: str = "ON TRACK"
    next_blocking: OpsTodo | None = None   # most-urgent open todo
    open_todos: list[OpsTodo] = None        # sorted by urgency desc
    sample_app_url: str = ""                # any todo's BC URL (to derive list URL)
    bc_list_url: str = ""                   # Basecamp todolist deep link

    def __post_init__(self):
        if self.open_todos is None:
            self.open_todos = []


def _label_for_score(s: int) -> str:
    if s >= 80:
        return "ON TRACK"
    if s >= 60:
        return "AI RISK"
    return "AT RISK"


# 5-band health scale (replaces the older 4-class red/green split for the
# Heat map). Spreads the same 0-100 score across five buckets so the eye gets
# a real gradient red -> orange -> gold -> lime -> green instead of a binary.
# Returned `key` maps to a CSS class (.heat-b1 ... .heat-b5) and a Plotly
# marker color; `label` is the human chip text; `color` is the canonical hex
# (used by the scatter plot so SVG markers match the cards exactly).
_BANDS = [
    (40, "b1", "CRITICAL", "#cf222e"),   # < 40
    (55, "b2", "AT RISK",  "#e8590c"),   # 40-54
    (70, "b3", "WATCH",    "#f0c000"),   # 55-69
    (85, "b4", "STEADY",   "#74b816"),   # 70-84
    (10_000, "b5", "ON TRACK", "#1a7f37"),  # 85+
]


def score_band(score: int) -> dict:
    """Map a 0-100 health score to one of 5 bands (red -> green).

    Returns {'key', 'label', 'color'}. Used by the Heat map cards and the
    scatter plot so both render the same color for the same score.
    """
    for ceiling, key, label, color in _BANDS:
        if score < ceiling:
            return {"key": key, "label": label, "color": color}
    # Unreachable (last ceiling is huge) but keep mypy + the type checker calm.
    return {"key": "b5", "label": "ON TRACK", "color": "#1a7f37"}


# URL derivation lives in the shared `bc_urls` module so the per-todo prompt
# renderers (suggestions.py, llm_suggest.py via OpsTodo.list_url) and this
# rollup derive identical links. These thin wrappers preserve the existing
# call sites + test names.
def _bc_list_url(sample_app_url: str, list_id: int) -> str:
    return bc_urls.list_url(sample_app_url, list_id)


def _bc_project_url(sample_app_url: str) -> str:
    return bc_urls.project_url(sample_app_url)


def per_list(todos: Iterable[OpsTodo]) -> list[ListRollup]:
    """Group active todos by list and compute per-list rollups."""
    by_list: dict[int, ListRollup] = {}
    for t in todos:
        if t.status != "active":
            continue
        if t.is_dismissed:
            continue
        r = by_list.get(t.bc_todolist_id)
        if r is None:
            r = ListRollup(
                list_id=t.bc_todolist_id, list_name=t.bc_todolist_name,
                project_id=t.bc_project_id, project_name=t.bc_project_name,
            )
            by_list[t.bc_todolist_id] = r
        r.open_count += 1
        if is_overdue(t):
            r.overdue_count += 1
        if t.urgency_score >= 70:
            r.red_count += 1
        elif t.urgency_score >= 40:
            r.amber_count += 1
        if tier(t) == "H":
            r.human_count += 1
        else:
            r.ai_count += 1
        if not r.sample_app_url and t.bc_app_url:
            r.sample_app_url = t.bc_app_url
        r.open_todos.append(t)

    for r in by_list.values():
        raw = 100 - 12 * r.overdue_count - 6 * r.red_count - 2 * r.amber_count - 1 * r.open_count
        r.score = max(0, min(100, raw))
        r.score_label = _label_for_score(r.score)
        r.bc_list_url = _bc_list_url(r.sample_app_url, r.list_id)
        r.open_todos.sort(key=lambda x: (-x.urgency_score, x.due_on or "9999-12-31"))
        r.next_blocking = next(
            (t for t in r.open_todos if tier(t) == "H"),
            r.open_todos[0] if r.open_todos else None,
        )

    # Sort: most-late lists first, then by score (worst first within tied
    # overdue counts), then by closest next-blocking due date (today before
    # future). _earliest_due_key turns None into a far-future sentinel so
    # lists with no due dates fall to the bottom of their score bucket.
    def _earliest_due_key(r: ListRollup) -> str:
        if r.next_blocking and r.next_blocking.due_on:
            return r.next_blocking.due_on
        # Then check any todo in the list
        for t in r.open_todos:
            if t.due_on:
                return t.due_on
        return "9999-12-31"

    return sorted(
        by_list.values(),
        key=lambda r: (-r.overdue_count, r.score, _earliest_due_key(r)),
    )


@dataclass
class OverallStatus:
    state: str          # "on_track" | "needs_attention" | "jeopardy"
    headline: str       # short status (1 line)
    subhead: str        # supporting detail (1 line)
    overdue: int
    red: int
    amber: int
    human: int
    ai: int
    open_count: int


@dataclass
class ProjectRollup:
    project_id: int
    project_name: str
    open_count: int = 0
    overdue_count: int = 0
    red_count: int = 0
    amber_count: int = 0
    human_count: int = 0
    ai_count: int = 0
    list_count: int = 0
    score: int = 100
    score_label: str = "ON TRACK"
    next_blocking: OpsTodo | None = None   # most-urgent human todo in the project
    sample_app_url: str = ""
    bc_project_url: str = ""               # Basecamp project (bucket) deep link
    _open_todos: list[OpsTodo] = None       # internal: for next_blocking selection

    def __post_init__(self):
        if self._open_todos is None:
            self._open_todos = []


def per_project(todos: Iterable[OpsTodo]) -> list[ProjectRollup]:
    """Aggregate active todos by project. Used by the Heat map view."""
    by_proj: dict[int, ProjectRollup] = {}
    list_ids_per_proj: dict[int, set[int]] = {}
    for t in todos:
        if t.status != "active" or t.is_dismissed:
            continue
        r = by_proj.get(t.bc_project_id)
        if r is None:
            r = ProjectRollup(project_id=t.bc_project_id, project_name=t.bc_project_name)
            by_proj[t.bc_project_id] = r
            list_ids_per_proj[t.bc_project_id] = set()
        r.open_count += 1
        if is_overdue(t):
            r.overdue_count += 1
        if t.urgency_score >= 70:
            r.red_count += 1
        elif t.urgency_score >= 40:
            r.amber_count += 1
        if tier(t) == "H":
            r.human_count += 1
        else:
            r.ai_count += 1
        if not r.sample_app_url and t.bc_app_url:
            r.sample_app_url = t.bc_app_url
        r._open_todos.append(t)
        list_ids_per_proj[t.bc_project_id].add(t.bc_todolist_id)

    for r in by_proj.values():
        r.list_count = len(list_ids_per_proj[r.project_id])
        raw = 100 - 12 * r.overdue_count - 6 * r.red_count - 2 * r.amber_count - 1 * r.open_count
        r.score = max(0, min(100, raw))
        r.score_label = _label_for_score(r.score)
        r.bc_project_url = _bc_project_url(r.sample_app_url)
        r._open_todos.sort(key=lambda x: (-x.urgency_score, x.due_on or "9999-12-31"))
        r.next_blocking = next(
            (t for t in r._open_todos if tier(t) == "H"),
            r._open_todos[0] if r._open_todos else None,
        )

    return sorted(by_proj.values(), key=lambda r: (r.score, -r.overdue_count))


@dataclass
class PersonRollup:
    """Per-assignee rollup over OPEN work. Third group in the Heat map.

    Mirrors ListRollup/ProjectRollup so the same scoring + 5-band coloring
    applies. A todo with N assignees counts toward all N people (each person
    is on the hook for it), so person counts can exceed the global open count.
    """
    name: str
    open_count: int = 0
    overdue_count: int = 0
    red_count: int = 0
    amber_count: int = 0
    human_count: int = 0
    ai_count: int = 0
    project_ids: set = None
    score: int = 100
    score_label: str = "ON TRACK"
    next_blocking: OpsTodo | None = None
    _open_todos: list[OpsTodo] = None

    def __post_init__(self):
        if self.project_ids is None:
            self.project_ids = set()
        if self._open_todos is None:
            self._open_todos = []


def per_person(todos: Iterable[OpsTodo]) -> list[PersonRollup]:
    """Aggregate active todos by assignee. Used by the Heat map People group.

    Unassigned todos are skipped (no one to attribute them to). Same health
    formula as lists/projects so the bands line up across all three groups.
    """
    by_person: dict[str, PersonRollup] = {}
    for t in todos:
        if t.status != "active" or t.is_dismissed:
            continue
        for name in (t.assignee_names or []):
            if not name:
                continue
            r = by_person.get(name)
            if r is None:
                r = PersonRollup(name=name)
                by_person[name] = r
            r.open_count += 1
            if is_overdue(t):
                r.overdue_count += 1
            if t.urgency_score >= 70:
                r.red_count += 1
            elif t.urgency_score >= 40:
                r.amber_count += 1
            if tier(t) == "H":
                r.human_count += 1
            else:
                r.ai_count += 1
            r.project_ids.add(t.bc_project_id)
            r._open_todos.append(t)

    for r in by_person.values():
        raw = 100 - 12 * r.overdue_count - 6 * r.red_count - 2 * r.amber_count - 1 * r.open_count
        r.score = max(0, min(100, raw))
        r.score_label = _label_for_score(r.score)
        r._open_todos.sort(key=lambda x: (-x.urgency_score, x.due_on or "9999-12-31"))
        r.next_blocking = next(
            (t for t in r._open_todos if tier(t) == "H"),
            r._open_todos[0] if r._open_todos else None,
        )

    return sorted(by_person.values(), key=lambda r: (r.score, -r.overdue_count))


@dataclass
class CompleterStats:
    """Per-person completion rollup: count + cycle-time stats in days."""
    name: str
    count: int
    avg_cycle_days: float
    median_cycle_days: float
    fastest_cycle_days: float
    slowest_cycle_days: float


def completions_summary(todos: Iterable[OpsTodo], limit: int = 50) -> tuple[list[CompleterStats], list[OpsTodo]]:
    """Returns (per-completer stats sorted by count desc, recent completed todos sorted by completed_at desc).

    Only completed todos with cycle_seconds > 0 contribute. Names without
    cycle data still appear in the list but with avg=0.
    """
    completed = [t for t in todos if t.status == "completed" and t.completed_at]
    completed.sort(key=lambda x: x.completed_at, reverse=True)

    by_person: dict[str, list[int]] = {}
    for t in completed:
        if not t.completed_by_name:
            continue
        by_person.setdefault(t.completed_by_name, []).append(t.cycle_seconds)

    stats: list[CompleterStats] = []
    for name, cycles in by_person.items():
        valid = sorted(c / 86400 for c in cycles if c > 0)
        if valid:
            mid = len(valid) // 2
            median = (valid[mid] if len(valid) % 2 == 1
                      else (valid[mid - 1] + valid[mid]) / 2)
            stats.append(CompleterStats(
                name=name,
                count=len(cycles),
                avg_cycle_days=round(sum(valid) / len(valid), 1),
                median_cycle_days=round(median, 1),
                fastest_cycle_days=round(min(valid), 1),
                slowest_cycle_days=round(max(valid), 1),
            ))
        else:
            stats.append(CompleterStats(
                name=name, count=len(cycles),
                avg_cycle_days=0, median_cycle_days=0,
                fastest_cycle_days=0, slowest_cycle_days=0,
            ))
    stats.sort(key=lambda s: -s.count)
    return stats, completed[:limit]


def overall_health(rollups: list[ListRollup]) -> dict:
    """Aggregate score across lists, weighted by open_count.
    Returns {'score': 0-100, 'label': 'ON TRACK|AI RISK|AT RISK', 'list_count': N,
             'total_open': N, 'total_overdue': N}.
    """
    if not rollups:
        return {"score": 100, "label": "NO DATA", "list_count": 0,
                "total_open": 0, "total_overdue": 0}
    total_weight = sum(r.open_count for r in rollups) or 1
    weighted_sum = sum(r.score * r.open_count for r in rollups)
    score = int(weighted_sum / total_weight)
    return {
        "score": score,
        "label": _label_for_score(score),
        "list_count": len(rollups),
        "total_open": sum(r.open_count for r in rollups),
        "total_overdue": sum(r.overdue_count for r in rollups),
    }


def kanban_columns(todos: Iterable[OpsTodo]) -> dict[str, list[OpsTodo]]:
    """Bucket active todos into 4 Kanban columns by urgency window."""
    cols: dict[str, list[OpsTodo]] = {
        "now": [], "soon": [], "later": [], "waiting": [],
    }
    for t in todos:
        if t.status != "active" or t.is_dismissed:
            continue
        if t.category == "waiting_dependency":
            cols["waiting"].append(t)
            continue
        dd = _days_until(t.due_on)
        if dd is None:
            cols["later"].append(t)
        elif dd <= 0:
            cols["now"].append(t)
        elif dd <= 7:
            cols["soon"].append(t)
        else:
            cols["later"].append(t)
    for col_todos in cols.values():
        col_todos.sort(key=lambda t: (-t.urgency_score, t.due_on or "9999-12-31"))
    return cols


def overall(todos: Iterable[OpsTodo]) -> OverallStatus:
    """One-line status of the day. The first thing the user sees."""
    open_todos = [t for t in todos if t.status == "active" and not t.is_dismissed]
    overdue = sum(1 for t in open_todos if is_overdue(t))
    red = sum(1 for t in open_todos if t.urgency_score >= 70)
    amber = sum(1 for t in open_todos if 40 <= t.urgency_score < 70)
    human = sum(1 for t in open_todos if tier(t) == "H")
    ai = sum(1 for t in open_todos if tier(t) == "AI")

    if overdue >= 3 or red >= 5:
        state = "jeopardy"
        headline = "In jeopardy."
        if overdue:
            subhead = f"{overdue} overdue · {red} red · clear the overdue first."
        else:
            subhead = f"{red} red items piling up. Triage before they slip."
    elif overdue or red:
        state = "needs_attention"
        if overdue:
            headline = f"{overdue} overdue · {human} need you."
        else:
            headline = f"{red} red · {human} need you."
        subhead = f"Most of the queue ({ai}) is delegatable. Clear the red first."
    elif open_todos:
        state = "on_track"
        headline = "On track."
        if human:
            headline = f"On track · {human} decisions waiting."
        subhead = f"{len(open_todos)} open · 0 overdue. Pick the highest-urgency next."
    else:
        state = "on_track"
        headline = "All clear."
        subhead = "Nothing in your queue. Click Sync to refresh."

    return OverallStatus(
        state=state, headline=headline, subhead=subhead,
        overdue=overdue, red=red, amber=amber,
        human=human, ai=ai, open_count=len(open_todos),
    )
