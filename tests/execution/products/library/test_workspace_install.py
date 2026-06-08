"""Tests for execution/products/library/workspace_install.py [Workflow 3b].

Covers the pure-Python pieces (renderers, dependency walker, normalize)
and the open_install_pr orchestration in dry_run mode. The actual gh /
GitHub REST PR-creation path (_open_pr) is monkeypatched, so tests run
without network and without burning real PRs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from execution.products.library import workspace_install as wi
from execution.products.library.store import AssetMetadata, _normalize_dependencies


# ── _normalize_dependencies ────────────────────────────────────────


class TestNormalizeDependencies:

    def test_empty_inputs(self):
        assert _normalize_dependencies([]) == []
        assert _normalize_dependencies(None) == []

    def test_legacy_strings_wrap_with_unknown_category(self):
        out = _normalize_dependencies(["react", "@modelcontextprotocol/server-filesystem"])
        assert out == [
            {"category": "?", "asset_id": "react", "optional": False},
            {"category": "?", "asset_id": "@modelcontextprotocol/server-filesystem", "optional": False},
        ]

    def test_already_typed_passes_through(self):
        deps = [{"category": "mcp", "asset_id": "fs", "optional": True}]
        assert _normalize_dependencies(deps) == [
            {"category": "mcp", "asset_id": "fs", "optional": True},
        ]

    def test_partial_dict_fills_defaults(self):
        deps = [{"category": "skills", "asset_id": "x"}]   # no 'optional'
        out = _normalize_dependencies(deps)
        assert out == [{"category": "skills", "asset_id": "x", "optional": False}]

    def test_mixed_legacy_and_typed(self):
        deps = ["legacy-str", {"category": "skills", "asset_id": "typed"}]
        out = _normalize_dependencies(deps)
        assert out == [
            {"category": "?", "asset_id": "legacy-str", "optional": False},
            {"category": "skills", "asset_id": "typed", "optional": False},
        ]

    def test_drops_malformed_entries(self):
        deps = [None, 42, "", {"category": "x", "asset_id": ""}, "valid"]
        out = _normalize_dependencies(deps)
        # None/int/empty string/dict-with-empty-asset-id all dropped
        assert out == [{"category": "?", "asset_id": "valid", "optional": False}]


# ── Per-kind renderers ─────────────────────────────────────────────


def _meta(asset_id, category, **kwargs):
    """Build an AssetMetadata with sensible defaults."""
    return AssetMetadata(
        asset_id=asset_id, category=category, workspace="global",
        **kwargs,
    )


class TestRenderers:

    def test_skill_path_and_frontmatter(self):
        meta = _meta("My Skill", "skills", name="My Skill",
                                description="Does a thing.")
        changes = wi._render_skill(meta)
        assert len(changes) == 1
        assert changes[0].path == ".claude/skills/my-skill/SKILL.md"
        body = changes[0].content
        assert body.startswith("---\n")
        assert "item_kind: skill" in body
        assert "item_id: My Skill" in body
        assert "# My Skill" in body
        assert "Does a thing." in body

    def test_agent_path_and_role_block(self):
        meta = _meta("CB Agent", "agents", name="CB Agent",
                                role="research analyst",
                                autonomy_level="recommend_only",
                                system_prompt="You are CB.",
                                allowed_tools=["search", "summarize"],
                                guardrails="No outbound emails.")
        changes = wi._render_agent(meta)
        assert changes[0].path == ".claude/agents/cb-agent.md"
        body = changes[0].content
        assert "**Role:** research analyst" in body
        assert "**Autonomy:** recommend_only" in body
        assert "You are CB." in body
        assert "- search" in body
        assert "- summarize" in body
        assert "Guardrails" in body and "No outbound emails." in body

    def test_prompt_renders_body_in_fence(self):
        meta = _meta("p1", "prompts", name="Prompt One",
                                prompt_body="Hello {{name}}.",
                                expected_output="A greeting.",
                                model_hint="claude-haiku-4-5")
        changes = wi._render_prompt(meta)
        assert changes[0].path == ".claude/prompts/p1.md"
        body = changes[0].content
        assert "```\nHello {{name}}.\n```" in body
        assert "A greeting." in body
        assert "claude-haiku-4-5" in body

    def test_capability_path(self):
        meta = _meta("Summarizer", "capabilities", name="Summarizer",
                                description="Condense long inputs.")
        changes = wi._render_capability(meta)
        assert changes[0].path == "docs/capabilities/summarizer.md"
        assert "Summarizer" in changes[0].content

    def test_use_case_renders_walkthrough_numbered(self):
        uc = SimpleNamespace(
            use_case_id="uc-001", title="Test case", summary="Sum",
            persona="Maya", problem="Slow", solution="Faster",
            walkthrough=["Step A", "Step B"],
            tools_used=[{"category": "skills", "asset_id": "x", "role": "do"}],
            outcome_metric="50% faster",
            owning_company_id="community",
        )
        changes = wi._render_use_case(uc)
        assert changes[0].path == "docs/use-cases/uc-001.md"
        body = changes[0].content
        assert "# Test case" in body
        assert "**Persona:** Maya" in body
        assert "1. Step A" in body
        assert "2. Step B" in body
        assert "50% faster" in body


# ── Dependency walker ─────────────────────────────────────────────


class TestWalkDependencies:

    def test_empty_deps_returns_empty(self):
        meta = _meta("x", "skills", dependencies=[])
        ch, b, s = wi._walk_dependencies(meta, "global", "u/r", seen=set())
        assert ch == [] and b == [] and s == []

    def test_unresolved_sentinel_category_is_skipped(self):
        meta = _meta("x", "skills", dependencies=[
            {"category": "?", "asset_id": "legacy-opaque", "optional": False},
        ])
        ch, b, s = wi._walk_dependencies(meta, "global", "u/r", seen=set())
        assert ch == []
        assert b == []
        assert s == [{"category": "?", "asset_id": "legacy-opaque",
                            "reason": "unresolved category"}]

    def test_real_edge_renders_and_bundles(self):
        meta = _meta("parent", "skills", dependencies=[
            {"category": "agents", "asset_id": "child-agent", "optional": False},
        ])
        ch, b, s = wi._walk_dependencies(meta, "global", "u/r", seen={"skills:parent"})
        assert len(ch) == 1
        assert ch[0].path.startswith(".claude/agents/")
        assert ch[0].note == "bundled-dep:agents/child-agent"
        assert b == [{"category": "agents", "asset_id": "child-agent"}]
        assert s == []

    def test_seen_set_dedups(self):
        meta = _meta("parent", "skills", dependencies=[
            {"category": "agents", "asset_id": "child", "optional": False},
            {"category": "agents", "asset_id": "child", "optional": False},
        ])
        ch, b, _ = wi._walk_dependencies(meta, "global", "u/r", seen={"skills:parent"})
        # The duplicate edge is skipped by the seen-set
        assert len(b) == 1

    def test_use_case_tools_used_treated_as_deps(self):
        uc = SimpleNamespace(
            use_case_id="uc-1",
            tools_used=[
                {"category": "skills", "asset_id": "skill-a", "role": "x"},
                {"category": "mcp", "asset_id": "mcp-b", "role": "y"},
            ],
        )
        ch, b, _ = wi._walk_dependencies(uc, "global", "u/r", seen={"use_case:uc-1"})
        assert {(d["category"], d["asset_id"]) for d in b} == {
            ("skills", "skill-a"), ("mcp", "mcp-b"),
        }


# ── open_install_pr orchestration (dry_run) ───────────────────────


@pytest.fixture
def stub_user():
    return SimpleNamespace(
        user_id="usr_test",
        email="someone@colaberry.com",
        display_name="Tester",
        workspace_repo="https://github.com/Test/someone-workspace",
    )


@pytest.fixture(autouse=True)
def tmp_audit_dir(tmp_path, monkeypatch):
    """Redirect audit writes to a tmp dir so tests don't pollute prod logs."""
    audit_dir = tmp_path / "_install_audit"
    monkeypatch.setattr(wi, "AUDIT_DIR", audit_dir)


class TestOpenInstallPRDryRun:

    def test_dry_run_returns_status_dry_run_with_files(self, stub_user, monkeypatch):
        # Stub workspaces.workspace_repo_for_user so we don't compute the
        # real URL from the test email
        monkeypatch.setattr(wi.workspaces, "workspace_repo_for_user",
                                       lambda email: "Test/someone-workspace")
        # Stub get_metadata to return a deterministic skill
        monkeypatch.setattr(wi.store, "get_metadata",
                                       lambda ws, cat, aid: _meta(aid, cat, name=aid))
        r = wi.open_install_pr(stub_user, "skills", "fixture-skill",
                                              dry_run=True)
        assert r.status == "dry_run"
        assert r.pr_url == "(dry_run)"
        assert r.files_written == [".claude/skills/fixture-skill/SKILL.md"]
        assert r.deps_bundled == []
        assert r.deps_skipped == []
        assert r.target_repo == "Test/someone-workspace"
        assert r.branch.startswith("install/skill/fixture-skill-")
        assert r.user_email == stub_user.email
        assert r.subscribed is False  # dry_run never subscribes

    def test_no_workspace_repo_fails_fast(self, monkeypatch):
        u = SimpleNamespace(
            user_id="u", email="x@y.com", workspace_repo=None,
            display_name="x",
        )
        monkeypatch.setattr(wi.workspaces, "workspace_repo_for_user",
                                       lambda email: "")
        r = wi.open_install_pr(u, "skills", "anything", dry_run=False)
        assert r.status == "failed"
        assert "workspace_repo" in r.error.lower()

    def test_render_failure_returns_failed_status(self, stub_user, monkeypatch):
        monkeypatch.setattr(wi.workspaces, "workspace_repo_for_user",
                                       lambda email: "Test/someone-workspace")
        # Make use_cases.get raise so the use-case render path fails
        monkeypatch.setattr(wi.use_cases, "get",
                                       lambda ws, aid: (_ for _ in ()).throw(RuntimeError("boom")))
        r = wi.open_install_pr(stub_user, "use_case", "uc-x", dry_run=True)
        # The use_cases.get returns None on RuntimeError because of the
        # try/except in get_metadata? Actually use_cases.get just calls
        # _path(...).exists(); since uc-x doesn't exist it returns None.
        # The renderer raises "use case ... not found".
        assert r.status == "failed"
        assert "not found" in r.error.lower() or "boom" in r.error.lower()


class TestLiveInMCPRefusal:

    def test_refuses_live_in_mcp_asset(self, stub_user, monkeypatch):
        # Patch the import inside open_install_pr: monkeypatch
        # is_live_in_colaberry_mcp via the imported app.routers.library
        # module. We patch on that module attribute so the import inside
        # open_install_pr picks up the stub.
        import app.routers.library as lib_router_mod
        monkeypatch.setattr(lib_router_mod, "is_live_in_colaberry_mcp",
                                       lambda **kwargs: True)
        monkeypatch.setattr(wi.workspaces, "workspace_repo_for_user",
                                       lambda email: "Test/someone-workspace")
        monkeypatch.setattr(wi.store, "get_metadata",
                                       lambda ws, cat, aid: _meta(aid, cat, name=aid))
        r = wi.open_install_pr(stub_user, "mcp", "colaberry_find_project",
                                              dry_run=False)
        assert r.status == "refused"
        assert "live in your colaberry mcp" in r.error.lower()
        # No PR URL, no files written
        assert r.pr_url == ""
        assert r.files_written == []

    def test_does_not_refuse_external_mcp(self, stub_user, monkeypatch):
        import app.routers.library as lib_router_mod
        monkeypatch.setattr(lib_router_mod, "is_live_in_colaberry_mcp",
                                       lambda **kwargs: False)
        monkeypatch.setattr(wi.workspaces, "workspace_repo_for_user",
                                       lambda email: "Test/someone-workspace")
        monkeypatch.setattr(wi.store, "get_metadata",
                                       lambda ws, cat, aid: _meta(aid, cat, name=aid,
                                                                              install_command="npm i fs-mcp"))
        r = wi.open_install_pr(stub_user, "mcp", "external-fs", dry_run=True)
        assert r.status == "dry_run"
        assert r.files_written == [".mcp.json"]
