"""Deterministic urgency scorer — 0-100, no LLM, fully auditable.

Five signals (sum capped at 100):
  1. Due-date proximity              max 40
  2. Staleness since last update     max 20
  3. Title/description keyword tier  max 15
  4. Assignee present                max 15
  5. Project signal                  max 10  (currently flat 5; later: weight*10)

Categories derived from urgency + state:
  human_required        urgency >= 60 AND has assignees
  waiting_dependency    no due_on AND staleness >= 7d AND keyword_tier == 0
  unscored              everything else

The Phase 1 doctrine: this is deterministic by design so the operator
trusts the page within minutes. LLM-based scorers are additive only —
they augment, never replace.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .store import OpsTodo

URGENT_RE = re.compile(r"\b(URGENT|ASAP|CRITICAL|EMERGENCY)\b", re.IGNORECASE)
HOT_RE = re.compile(r"\b(HOT|PRIORITY|P0|P1|ESCALATE|BLOCK(?:ER|ING|ED)?)\b", re.IGNORECASE)
DECIDE_RE = re.compile(r"\b(REVIEW|APPROVE|DECIDE|SIGN[- ]?OFF|CONFIRM)\b", re.IGNORECASE)


def _days_until(due_on: str | None) -> int | None:
    if not due_on:
        return None
    try:
        d = datetime.strptime(due_on, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = datetime.now(timezone.utc).date()
    return (d - today).days


def _staleness_days(updated_at: str) -> int | None:
    if not updated_at:
        return None
    try:
        ts = updated_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.days
    except (ValueError, TypeError):
        return None


def _due_score(days: int | None) -> int:
    if days is None:
        return 0
    if days < 0:                # overdue
        return 40
    if days == 0:               # due today
        return 35
    if days <= 1:
        return 28
    if days <= 3:
        return 20
    if days <= 7:
        return 12
    if days <= 14:
        return 6
    return 0


def _staleness_score(days: int | None) -> int:
    if days is None:
        return 0
    if days > 14:
        return 20
    if days >= 7:
        return 12
    if days >= 3:
        return 6
    return 0


def _keyword_score(title: str, desc: str) -> tuple[int, str]:
    text = f"{title}\n{desc}"
    if URGENT_RE.search(text):
        return 15, "urgent"
    if HOT_RE.search(text):
        return 8, "hot"
    if DECIDE_RE.search(text):
        return 5, "decide"
    return 0, "none"


def score_todo(todo: OpsTodo, project_weight: float = 1.0) -> dict[str, Any]:
    """Return a {urgency, category, breakdown} dict. Does NOT mutate `todo`."""
    due_days = _days_until(todo.due_on)
    stale_days = _staleness_days(todo.bc_updated_at)
    kw_pts, kw_tier = _keyword_score(todo.title, todo.description)

    due_pts = _due_score(due_days)
    stale_pts = _staleness_score(stale_days)
    assignee_pts = 15 if todo.assignee_ids else 0
    project_pts = 5  # flat for Phase A; Phase 1.4b scales by project_weight

    raw = due_pts + stale_pts + kw_pts + assignee_pts + project_pts
    weighted = min(100, int(round(raw * project_weight)))

    # Category
    if weighted >= 60 and todo.assignee_ids:
        category = "human_required"
    elif not todo.due_on and (stale_days or 0) >= 7 and kw_pts == 0:
        category = "waiting_dependency"
    else:
        category = "unscored"

    return {
        "urgency": weighted,
        "category": category,
        "breakdown": {
            "due_days": due_days,
            "stale_days": stale_days,
            "keyword_tier": kw_tier,
            "components": {
                "due": due_pts,
                "staleness": stale_pts,
                "keyword": kw_pts,
                "assignee": assignee_pts,
                "project_signal": project_pts,
            },
            "raw": raw,
            "project_weight": project_weight,
            "weighted": weighted,
        },
    }


def score_all_todos(user_id: str, project_weights: dict[int, float] | None = None) -> int:
    """Re-score every todo for a user and persist. Returns count rescored.

    project_weights: optional {bc_project_id: weight} override per project.
    """
    from . import store

    weights = project_weights or {}
    todos = store.load_todos(user_id)
    if not todos:
        return 0
    for t in todos:
        weight = weights.get(t.bc_project_id, 1.0)
        s = score_todo(t, project_weight=weight)
        t.urgency_score = s["urgency"]
        t.category = s["category"]
        t.score_breakdown = s["breakdown"]
    store.save_todos(user_id, todos)
    return len(todos)
