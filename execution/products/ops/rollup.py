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

    def __post_init__(self):
        if self.open_todos is None:
            self.open_todos = []


def _label_for_score(s: int) -> str:
    if s >= 80:
        return "ON TRACK"
    if s >= 60:
        return "AI RISK"
    return "AT RISK"


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
        r.open_todos.append(t)

    for r in by_list.values():
        raw = 100 - 12 * r.overdue_count - 6 * r.red_count - 2 * r.amber_count - 1 * r.open_count
        r.score = max(0, min(100, raw))
        r.score_label = _label_for_score(r.score)
        r.open_todos.sort(key=lambda x: (-x.urgency_score, x.due_on or "9999-12-31"))
        r.next_blocking = next(
            (t for t in r.open_todos if tier(t) == "H"),
            r.open_todos[0] if r.open_todos else None,
        )

    return sorted(by_list.values(), key=lambda r: (r.score, -r.overdue_count))


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
