"""Regression: the single-todo Workspace page's "Copy prompt" button works.

Bug (2026-06-17): clicking "📋 Copy prompt" on /my-day/todo/<id> copied an
EMPTY string. The button flipped to "✓ Copied" but the clipboard held nothing.

Root cause: the copy read ``document.getElementById('promptText').innerText``,
but ``#promptText`` lives inside a *collapsed* ``<details>``. The ``.innerText``
getter of a non-rendered element returns '' in Chromium — so the copy source
was empty. (The sibling briefing/kanban surfaces read ``.value`` off a
``<textarea>``, which is rendering-independent, which is why they worked.)

Fix: assemble the prompt as a JS string (``rebuildFullPrompt`` returns it /
caches it in ``_fullPrompt``) and copy THAT directly, never reading back from
the hidden preview node. The button is wired to ``copyWorkspacePrompt()``.

These tests render the real workspace page with a stubbed data layer (no
Basecamp, no DB, no LLM), mirroring test_my_day_timeline_prompt_pairing.py.
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
from execution.products.ops.store import OpsTodo

_TEMPLATES_DIR = Path(my_day_router.__file__).resolve().parent.parent / "templates"


@pytest.fixture
def stub_user():
    return SimpleNamespace(
        user_id="usr_test",
        email="someone@colaberry.com",
        display_name="Tester",
        company_id="colaberry",
        roles=[],
        workspace_repo="",
    )


def _todo() -> OpsTodo:
    return OpsTodo(
        bc_id=9946498486,
        bc_project_id=7463955,
        bc_project_name="AI Systems Architect Accelerator",
        bc_todolist_id=9939449052,
        bc_todolist_name="TWC Compliance",
        title="Submit TWC registration application",
        status="active",
        due_on="2026-06-10",
        assignee_names=["Swati Raman"],
        inclusion_reason="assigned",
        bc_app_url="https://3.basecamp.com/x/todos/9946498486",
        urgency_score=68,
        category="human_required",
    )


@pytest.fixture
def client(monkeypatch, stub_user):
    monkeypatch.setattr(my_day_router, "_require_user", lambda r: stub_user)
    monkeypatch.setattr(my_day_router.store, "get_todo", lambda email, bc_id: _todo())

    app = FastAPI()
    app.include_router(my_day_router.router)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["days_from_today"] = _days_from_today
    app.state.templates = templates
    return TestClient(app)


def test_copy_button_is_wired_to_the_workspace_handler(client):
    r = client.get("/my-day/todo/9946498486")
    assert r.status_code == 200, r.text
    html = r.text
    assert 'onclick="copyWorkspacePrompt()"' in html
    assert "function copyWorkspacePrompt(" in html


def test_copy_source_is_the_assembled_string_not_the_hidden_node(client):
    """The fix: copy from the JS-built string, not from #promptText.innerText.

    Reading `.innerText` of a node inside a collapsed <details> returns '' in
    Chromium — that exact read is what broke the copy. Guard it stays gone.
    """
    r = client.get("/my-day/todo/9946498486")
    html = r.text

    # The copy must source the assembled string returned by rebuildFullPrompt().
    assert "const text = rebuildFullPrompt();" in html
    assert "return _fullPrompt;" in html

    # The original buggy copy source must NOT come back.
    assert "const text = document.getElementById('promptText').innerText;" not in html


def test_clipboard_failure_has_a_fallback(client):
    """A rejected/absent async Clipboard API must not silently no-op."""
    r = client.get("/my-day/todo/9946498486")
    html = r.text
    # execCommand fallback + a visible failure flash exist.
    assert "document.execCommand('copy')" in html
    assert "Copy failed" in html


def test_setup_runbook_is_mcp_model_not_clone_based(client):
    """The "How to run this prompt" runbook must be the MCP model (no clone),
    and must come from the single shared _mdSetupBlock() so the card/list copy
    buttons can't drift back to the stale clone-based runbook.

    Regression (2026-06-18): the workspace page moved to the MCP runbook but the
    shared copyPrompt in _my_day_styles.html kept shipping the old "git clone /
    cd / git pull / SessionStart hook" runbook on every card and list row.
    """
    html = client.get("/my-day/todo/9946498486").text

    # MCP model present.
    assert "Colaberry MCP is connected" in html
    assert "to clone or set up any repo" in html

    # Stale clone-based runbook gone everywhere on the page. Use full step
    # phrases (not bare "git pull") so the assertion can't collide with a code
    # comment that legitimately says "no clone, no cd, no git pull".
    assert "One-time setup (skip if already cloned)" not in html
    assert "Pull latest doctrine" not in html
    assert "OPERATOR_MEMORY" not in html
    assert "fires the SessionStart" not in html

    # Single source of truth: the workspace page uses the shared block, and its
    # old local _setupBlock copy is gone.
    assert "parts.push(_mdSetupBlock())" in html
    assert "function _setupBlock(" not in html


def test_no_double_task_heading_in_copy_paths(client):
    """Neither copy path wraps the task in a `## Task` heading — the prompt
    self-titles with `# {title}` (BLUF). A wrapper produced a confusing double
    heading on every card/list copy."""
    html = client.get("/my-day/todo/9946498486").text

    # The shared copyPrompt no longer prepends the '## Task' wrapper.
    assert r"'\n## Task\n\n'" not in html
    assert r"_mdSetupBlock() + '\n' + taskPrompt" in html


def test_task_prompt_is_embedded_for_the_js_to_read(client):
    """The JS assembles its prompt from data-task-prompt — it must be present
    and carry the server-generated prompt (here: the todo title appears)."""
    r = client.get("/my-day/todo/9946498486")
    html = r.text
    assert 'id="promptBuilder"' in html
    assert "data-task-prompt=" in html
    # generate_prompt() embeds the todo title; confirm real content reached the page.
    assert "Submit TWC registration application" in html
