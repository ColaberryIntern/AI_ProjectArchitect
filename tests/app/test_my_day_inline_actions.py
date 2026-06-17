"""Inline Mark Done + Skip at the list level; Mark Done (no Skip) at the task level.

User ask (2026-06-17): replicate the focused card's ✓ Mark done + Skip-for-now
buttons down at the LIST level — the per-list drill-down PROJECT TIMELINE rows
and the feasibility-row tip — so the operator can clear or skip any task
(including non-tip ones) without opening it. And add a ✓ Mark done to the
single-task Workspace page, but NOT a skip there (nothing to skip on one task).

Both inline buttons POST to the pre-existing endpoints:
  POST /my-day/todo/{id}/complete   (Mark done — BC write-back + local)
  POST /my-day/todo/{id}/dismiss    (Skip for now — local soft-dismiss)
which redirect back to the Referer, so the action reloads the same filtered
view in place. No new backend; these tests assert the TEMPLATE renders the
forms.

Harness mirrors test_my_day_timeline_prompt_pairing.py: mount only the my-day
router on a fresh FastAPI app and stub the store / token / LLM seams.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from app.main import _days_from_today
from app.routers import my_day as my_day_router
from execution.products.ops.store import OpsState, OpsTodo

_TEMPLATES_DIR = Path(my_day_router.__file__).resolve().parent.parent / "templates"


@pytest.fixture
def stub_user():
    return SimpleNamespace(
        user_id="usr_test",
        email="someone@colaberry.com",
        display_name="Tester",
        company_id="colaberry",
        roles=[],
    )


def _todo(bc_id: int, title: str, category: str, due_on: str | None, urgency: int) -> OpsTodo:
    return OpsTodo(
        bc_id=bc_id,
        bc_project_id=7463955,
        bc_project_name="Colaberry System",
        bc_todolist_id=9939449052,
        bc_todolist_name="HUMAN ACTION QUEUE",
        title=title,
        status="active",
        due_on=due_on,
        assignee_names=["Ali Muwwakkil"],
        inclusion_reason="assigned",
        bc_app_url=f"https://3.basecamp.com/x/todos/{bc_id}",
        urgency_score=urgency,
        category=category,
    )


# Two human + one AI todo: the timeline renders a NEXT HUMAN STEP (the tip)
# plus upcoming rows, so we can assert both the tip and the non-tip rows carry
# the inline actions.
_TODOS = [
    _todo(1001, "Compile daily HUMAN ACTION QUEUE update for Ali", "human_required", "2026-06-01", 90),
    _todo(1002, "Review and approve Curriculum design visuals", "human_required", "2026-06-02", 70),
    _todo(1003, "Draft Project Marketplace governance proposal", "ai_doable", "2026-06-17", 40),
]


@pytest.fixture
def client(monkeypatch, stub_user):
    monkeypatch.setattr(my_day_router, "_require_user", lambda r: stub_user)
    monkeypatch.setattr(my_day_router.store, "load_todos", lambda email: list(_TODOS))
    monkeypatch.setattr(my_day_router.store, "load_projects", lambda email: [])
    monkeypatch.setattr(
        my_day_router.store, "get_todo",
        lambda email, bc_id: next((t for t in _TODOS if t.bc_id == bc_id), None),
    )
    monkeypatch.setattr(
        my_day_router.store, "load_state",
        lambda email: OpsState(
            user_id="usr_test",
            last_sync_at="2026-06-11T08:00:00Z",
            last_sync_status="ok",
        ),
    )
    monkeypatch.setattr(my_day_router.tokens, "get_user_token", lambda email: ("tok", "test"))
    monkeypatch.setattr(my_day_router.llm_suggest, "enhance", lambda *a, **kw: None)

    app = FastAPI()
    app.include_router(my_day_router.router)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["days_from_today"] = _days_from_today
    app.state.templates = templates
    return TestClient(app)


# ── List level: briefing drill-down + tip ───────────────────────────────


def test_every_open_task_has_inline_mark_done_and_skip(client):
    """Every open todo rendered in the briefing carries BOTH a Mark Done form
    and a Skip form pointing at its own bc_id — so any task (tip or not) can be
    cleared or skipped in place."""
    r = client.get("/my-day/?view=briefing&tier=all")
    assert r.status_code == 200, r.text
    html = r.text
    for t in _TODOS:
        assert f'action="/my-day/todo/{t.bc_id}/complete"' in html, (
            f"todo {t.bc_id} missing an inline Mark Done form"
        )
        assert f'action="/my-day/todo/{t.bc_id}/dismiss"' in html, (
            f"todo {t.bc_id} missing an inline Skip form"
        )


def test_mark_done_and_skip_are_paired_at_the_list_level(client):
    """Mark Done and Skip come as a pair at the list level — equal counts, and
    at least one per open todo."""
    r = client.get("/my-day/?view=briefing&tier=all")
    assert r.status_code == 200, r.text
    html = r.text
    n_done = html.count("/complete")
    n_skip = html.count("/dismiss")
    assert n_done == n_skip, f"Done/Skip not paired (done={n_done}, skip={n_skip})"
    assert n_done >= len(_TODOS), (
        f"expected >= {len(_TODOS)} inline Mark Done forms, got {n_done}"
    )


def test_inline_actions_do_not_break_workspace_prompt_pairing(client):
    """Regression guard for the sibling invariant: the new green/quiet action
    buttons must not be counted as Workspace or Prompt buttons, so the
    workspace==prompt pairing in test_my_day_timeline_prompt_pairing still holds."""
    r = client.get("/my-day/?view=briefing&tier=all")
    assert r.status_code == 200, r.text
    html = r.text
    assert html.count('md-btn-workspace"') == html.count('md-btn-prompt"')


# ── Task level: single-task Workspace page ──────────────────────────────


def test_workspace_page_has_mark_done_button(client):
    """The single-task Workspace page gains a ✓ Mark done (POST /complete)."""
    r = client.get("/my-day/todo/1001")
    assert r.status_code == 200, r.text
    html = r.text
    assert 'action="/my-day/todo/1001/complete"' in html, "Workspace page missing Mark done"
    assert "✓ Mark done" in html


def test_workspace_page_has_no_skip_for_now(client):
    """No 'Skip for now' on the single-task page — there's nothing to skip to.
    (The pre-existing local-only Dismiss is a different function and stays.)"""
    r = client.get("/my-day/todo/1001")
    assert r.status_code == 200, r.text
    assert "Skip for now" not in r.text
