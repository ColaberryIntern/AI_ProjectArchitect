"""Route tests for the My-Day 'Create a new project' build flow."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def advisory_output_dir(monkeypatch, tmp_path):
    import config.settings as settings
    import execution.advisory.advisory_state_manager as asm
    advisory_dir = tmp_path / "advisory"
    advisory_dir.mkdir()
    monkeypatch.setattr(settings, "ADVISORY_OUTPUT_DIR", advisory_dir)
    monkeypatch.setattr(asm, "ADVISORY_OUTPUT_DIR", advisory_dir)
    return advisory_dir


@pytest.fixture
def client(tmp_output_dir):
    from app.main import app
    return TestClient(app)


def _new_session(idea="Test idea", **fields):
    from execution.advisory.advisory_state_manager import initialize_session, save_session
    s = initialize_session(idea)
    s.update(fields)
    save_session(s)
    return s


def _fake_operator(monkeypatch):
    import app.advisory.routes as routes
    monkeypatch.setattr(
        routes, "_session_user",
        lambda req: SimpleNamespace(email="ali@colaberry.com", user_id="ali@colaberry.com", bc_user_id=42),
    )


class TestEntryAndFlag:
    def test_new_project_page_is_focused_start(self, client, advisory_output_dir):
        # "🚀 New project" lands here — a focused idea box, not the marketing page.
        r = client.get("/advisory/new")
        assert r.status_code == 200
        assert 'name="business_idea"' in r.text
        assert 'name="myday_build"' in r.text            # flag carried into the flow
        assert 'action="/advisory/start"' in r.text
        assert "AI Operating System" not in r.text       # not the marketing landing
        # app-consistent chrome, not the public marketing nav/footer
        assert "Back to My Day" in r.text
        assert "Participant Login" not in r.text
        assert "Design AI Org" not in r.text

    def test_landing_forwards_myday_build_flag(self, client, advisory_output_dir):
        r = client.get("/advisory/?myday_build=1")
        assert r.status_code == 200
        assert 'name="myday_build"' in r.text

    def test_start_sets_flag_on_session(self, client, advisory_output_dir):
        r = client.post("/advisory/start",
                        data={"business_idea": "X", "myday_build": "1"},
                        follow_redirects=False)
        assert r.status_code == 303
        sid = r.headers["location"].split("/advisory/")[1].split("/")[0]
        from execution.advisory.advisory_state_manager import load_session
        assert load_session(sid).get("myday_build") is True

    def test_start_without_flag_is_unaffected(self, client, advisory_output_dir):
        r = client.post("/advisory/start", data={"business_idea": "X"}, follow_redirects=False)
        sid = r.headers["location"].split("/advisory/")[1].split("/")[0]
        from execution.advisory.advisory_state_manager import load_session
        assert load_session(sid).get("myday_build") is None


class TestCapabilitiesBranch:
    def test_redirects_to_build_setup_when_flagged(self, client, advisory_output_dir):
        s = _new_session(myday_build=True, selected_outcomes=["x"])
        r = client.post(f"/advisory/{s['session_id']}/capabilities",
                        data={"capabilities": ["cap1"]}, follow_redirects=False)
        assert r.status_code == 303
        assert "/build-setup" in r.headers["location"]

    def test_defaults_to_generate_when_not_flagged(self, client, advisory_output_dir):
        s = _new_session(selected_outcomes=["x"])
        r = client.post(f"/advisory/{s['session_id']}/capabilities",
                        data={"capabilities": ["cap1"]}, follow_redirects=False)
        assert r.status_code == 303
        assert "/generate" in r.headers["location"]


class TestBuildSetupAndStatus:
    def test_build_setup_renders(self, client, advisory_output_dir, monkeypatch):
        _fake_operator(monkeypatch)
        s = _new_session(myday_build=True)
        r = client.get(f"/advisory/{s['session_id']}/build-setup")
        assert r.status_code == 200
        assert "how fast" in r.text.lower()

    def test_build_status_json_is_no_store(self, client, advisory_output_dir):
        s = _new_session()
        r = client.get(f"/advisory/{s['session_id']}/build-status.json")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        assert "phase" in r.json()


class TestStartBuild:
    def test_start_build_runs_phase_a_kicks_bg_and_redirects(self, client, advisory_output_dir, monkeypatch, tmp_path):
        s = _new_session(myday_build=True)
        _fake_operator(monkeypatch)
        # phase a (advisory generation) → stub to return a slug
        import execution.advisory.advisory_generation as ag
        monkeypatch.setattr(ag, "generate_advisory_outputs",
                            lambda sid: {"slug": "demo-slug", "email": "ali@colaberry.com", "already_complete": False})
        # background kick → record, don't actually run
        import execution.advisory.myday_build_orchestrator as orch
        calls = {}
        monkeypatch.setattr(orch, "kick_build", lambda *a, **k: calls.setdefault("args", a))
        # keep build_status writes inside tmp
        import execution.advisory.build_status as bs
        monkeypatch.setattr(bs, "OUTPUT_DIR", tmp_path)

        r = client.post(f"/advisory/{s['session_id']}/start-build",
                        data={"bc_project_id": "123", "pace": "sprint"}, follow_redirects=False)
        assert r.status_code == 303
        assert "building=1" in r.headers["location"]
        assert calls.get("args") and calls["args"][1] == 123  # bc_project_id passed through
        # build status seeded
        assert bs.read_status("demo-slug")["phase"] == "advisory"

    def test_start_build_invalid_project_returns_to_setup(self, client, advisory_output_dir, monkeypatch):
        s = _new_session(myday_build=True)
        _fake_operator(monkeypatch)
        r = client.post(f"/advisory/{s['session_id']}/start-build",
                        data={"bc_project_id": "notanumber", "pace": "sprint"}, follow_redirects=False)
        assert r.status_code == 303
        assert "/build-setup" in r.headers["location"]
