"""[Provision 1] workspace provisioning — fully mocked (no GitHub calls)."""

from __future__ import annotations

import pytest

from execution.products.library import tenancy, workspaces


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tenancy, "TENANT_ROOT", tmp_path / "_tenants")
    tenancy.seed_initial_companies_and_users()
    yield tmp_path


def test_username_slug_strips_domain_and_invalid_chars():
    assert workspaces.username_slug("ali@colaberry.com") == "ali"
    assert workspaces.username_slug("Alice.Smith@Colaberry.com") == "alice-smith"
    assert workspaces.username_slug("weird+plus@x.com") == "weird-plus"
    assert workspaces.username_slug("") == "unknown"


def test_workspace_repo_name():
    assert (workspaces.workspace_repo_for_user("ali@colaberry.com")
            == "ColaberryIntern/ali-workspace")


def test_dry_run_records_audit_and_no_api_call(isolated, monkeypatch):
    monkeypatch.setattr(workspaces, "_gh_api",
                              lambda *a, **kw: pytest.fail("API should not be called"))
    ali = tenancy.get_user("ali@colaberry.com")
    res = workspaces.provision_user_workspace(ali, admin_actor_id="sys", dry_run=True)
    assert res["ok"] is True
    hist = workspaces.provision_history(user_id=ali.user_id)
    assert any(r["action"] == "dry_run" for r in hist)


def test_idempotent_when_repo_already_exists(isolated, monkeypatch):
    calls = []
    def fake_api(method, path, payload=None):
        calls.append((method, path))
        if method == "GET" and "/repos/" in path:
            return {"name": "ali-workspace"}  # exists
        if method == "PUT" and "/collaborators/" in path:
            return {}  # invitation succeeded
        return {}
    monkeypatch.setattr(workspaces, "_gh_api", fake_api)
    ali = tenancy.get_user("ali@colaberry.com")
    res = workspaces.provision_user_workspace(ali, admin_actor_id="sys")
    assert res["ok"] is True
    assert res["repo_already_existed"] is True
    # We should NOT have called POST to create the repo
    create_calls = [c for c in calls if c[0] == "POST" and "/generate" in c[1]]
    assert not create_calls


def test_creates_repo_from_template_when_absent(isolated, monkeypatch):
    state = {"exists": False}
    calls = []
    def fake_api(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET" and "/repos/" in path:
            if state["exists"]:
                return {"name": "ali-workspace"}
            raise RuntimeError("github api GET failed: HTTP 404 Not Found")
        if method == "POST" and "/generate" in path:
            state["exists"] = True
            return {"id": 123, "full_name": "ColaberryIntern/ali-workspace"}
        if method == "PUT" and "/collaborators/" in path:
            return {}
        return {}
    monkeypatch.setattr(workspaces, "_gh_api", fake_api)
    ali = tenancy.get_user("ali@colaberry.com")
    res = workspaces.provision_user_workspace(ali, admin_actor_id="sys")
    assert res["ok"] is True
    assert res["repo_already_existed"] is False
    assert res["invited_user"] is True
    # We DID call generate
    assert any(c[0] == "POST" and "/generate" in c[1] for c in calls)


def test_falls_back_to_bare_repo_when_template_missing(isolated, monkeypatch):
    calls = []
    def fake_api(method, path, payload=None):
        calls.append((method, path))
        if method == "GET":
            raise RuntimeError("github api GET failed: HTTP 404 Not Found")
        if method == "POST" and "/generate" in path:
            raise RuntimeError("github api POST failed: HTTP 404 Not Found")
        if method == "POST" and "/orgs/" in path:
            return {"id": 1, "full_name": "ColaberryIntern/ali-workspace"}
        if method == "PUT":
            return {}
        return {}
    monkeypatch.setattr(workspaces, "_gh_api", fake_api)
    ali = tenancy.get_user("ali@colaberry.com")
    res = workspaces.provision_user_workspace(ali, admin_actor_id="sys")
    assert res["ok"] is True
    # Audit shows we hit the fallback
    hist = workspaces.provision_history(user_id=ali.user_id)
    assert any(r["action"] == "create_repo_bare" for r in hist)


def test_provision_failure_records_error(isolated, monkeypatch):
    def fake_api(*a, **kw):
        raise RuntimeError("github api failed: HTTP 500 something")
    monkeypatch.setattr(workspaces, "_gh_api", fake_api)
    ali = tenancy.get_user("ali@colaberry.com")
    res = workspaces.provision_user_workspace(ali, admin_actor_id="sys")
    assert res["ok"] is False
    assert "github api failed" in res["error"]
    hist = workspaces.provision_history(user_id=ali.user_id)
    assert any(r["action"] == "provision_failed" for r in hist)


# ── Rendering ───────────────────────────────────────────────────


def test_starter_user_profile_includes_email_and_links(isolated):
    ali = tenancy.get_user("ali@colaberry.com")
    md = workspaces.render_starter_user_profile_md(ali)
    assert "ali@colaberry.com" in md
    assert "/library/" in md
    assert ali.user_id in md


def test_mcp_json_only_includes_granted_tools(isolated):
    ali = tenancy.get_user("ali@colaberry.com")
    j = workspaces.render_starter_mcp_json(ali, scopes={"github", "gmail"})
    import json
    data = json.loads(j)
    assert "github" in data["mcpServers"]
    assert "gmail" in data["mcpServers"]
    assert "slack" not in data["mcpServers"]
    # Token references go through ${{ vault.X }} — NEVER a real token
    assert "${{ vault.github }}" in j
    assert "${{ vault.gmail }}" in j
