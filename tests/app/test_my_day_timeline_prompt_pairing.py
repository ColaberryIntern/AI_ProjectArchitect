"""Invariant: every Workspace action ships with a paired Prompt button.

User rule (2026-06-11): "any time you have a workspace action, you also need
to have a prompt." The briefing PROJECT TIMELINE rows used to render a lone
``⚙ Workspace`` button with no ``📋 Prompt`` beside it — so the operator had
to open the workspace page just to grab a prompt. The router now precomputes
``seq_prompts`` for every timeline row and the template renders the pair.

These tests render the real briefing page (GET /my-day/?view=briefing) with a
stubbed data layer (no Basecamp, no DB, no LLM) and assert:

  1. count(md-btn-workspace) == count(md-btn-prompt)  — the pairing invariant.
  2. every rendered todo carries its own ``sp-<bc_id>`` prompt textarea wired
     to ``copyPrompt('sp-<bc_id>', this)``.

Strategy mirrors test_my_day_sync_redirect.py: mount ONLY the my-day router on
a fresh FastAPI app and monkeypatch the store / token / LLM seams so the
request is fast and deterministic.
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
    """One active todo assigned to the viewer, in a single project + list so the
    briefing collapses to one feasibility row with a PROJECT TIMELINE."""
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


# Three todos: two human, one AI — enough that the timeline has a NEXT HUMAN
# STEP plus upcoming rows, and the "See all" drill-down renders.
_TODOS = [
    _todo(1001, "Compile daily HUMAN ACTION QUEUE update for Ali", "human_required", "2026-06-01", 90),
    _todo(1002, "Review and approve Curriculum design visuals", "human_required", "2026-06-02", 70),
    _todo(1003, "Draft Project Marketplace governance proposal", "ai_doable", "2026-06-17", 40),
]


@pytest.fixture
def client(monkeypatch, stub_user):
    # Auth → fixed test user; no cookie/JWT path.
    monkeypatch.setattr(my_day_router, "_require_user", lambda r: stub_user)
    # Data layer → in-memory stubs. No Basecamp, no DB.
    monkeypatch.setattr(my_day_router.store, "load_todos", lambda email: list(_TODOS))
    monkeypatch.setattr(my_day_router.store, "load_projects", lambda email: [])
    monkeypatch.setattr(
        my_day_router.store, "load_state",
        lambda email: OpsState(
            user_id="usr_test",
            last_sync_at="2026-06-11T08:00:00Z",
            last_sync_status="ok",
        ),
    )
    # Token lookup → present, so the natural-flow sync sees a connected user
    # but the freshness check (status ok, recent) short-circuits any BC call.
    monkeypatch.setattr(my_day_router.tokens, "get_user_token", lambda email: ("tok", "test"))
    # LLM enhance → identity passthrough so no network round trip.
    monkeypatch.setattr(my_day_router.llm_suggest, "enhance", lambda *a, **kw: None)

    app = FastAPI()
    app.include_router(my_day_router.router)
    # Routers render via request.app.state.templates — wire a Jinja env with
    # the same custom filter main.py registers, without booting the full app.
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["days_from_today"] = _days_from_today
    app.state.templates = templates
    return TestClient(app)


def test_briefing_pairs_every_workspace_with_a_prompt(client):
    """The pairing invariant: as many Prompt buttons as Workspace buttons."""
    r = client.get("/my-day/?view=briefing&tier=all")
    assert r.status_code == 200, r.text
    html = r.text
    n_workspace = html.count('md-btn-workspace"')
    n_prompt = html.count('md-btn-prompt"')
    assert n_workspace >= 1, "expected at least one Workspace button on the page"
    assert n_workspace == n_prompt, (
        "every Workspace action must have a paired Prompt button "
        f"(workspace={n_workspace}, prompt={n_prompt})"
    )


def test_timeline_rows_carry_a_seq_prompt_textarea(client):
    """Each rendered todo gets its own copy-prompt textarea + handler."""
    r = client.get("/my-day/?view=briefing&tier=all")
    assert r.status_code == 200, r.text
    html = r.text
    for t in _TODOS:
        assert f'id="sp-{t.bc_id}"' in html, f"missing seq-prompt textarea for todo {t.bc_id}"
        assert f"copyPrompt('sp-{t.bc_id}', this)" in html
