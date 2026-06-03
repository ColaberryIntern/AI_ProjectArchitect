"""Tests for [Infra 1] github_sync — fully mocked, no network, no gh CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.products.library import github_sync, store


# ── Approver authorization ────────────────────────────────────────


def test_ali_can_approve_any_category():
    ok, reason = github_sync.can_approve("ali@colaberry.com", "skills")
    assert ok
    assert "all" in reason.lower() or "ali" in reason.lower()


def test_ram_can_approve_use_cases_but_not_skills():
    ok, _ = github_sync.can_approve("ram@colaberry.com", "use_cases")
    assert ok
    ok2, _ = github_sync.can_approve("ram@colaberry.com", "skills")
    assert not ok2


def test_kes_can_approve_skills_and_mcp_but_not_use_cases():
    assert github_sync.can_approve("kes@colaberry.com", "skills")[0]
    assert github_sync.can_approve("kes@colaberry.com", "mcp")[0]
    assert not github_sync.can_approve("kes@colaberry.com", "use_cases")[0]


def test_unknown_user_cannot_approve():
    ok, reason = github_sync.can_approve("stranger@example.com", "skills")
    assert not ok
    assert "not in the approvers list" in reason


# ── Slug + path ───────────────────────────────────────────────────


def test_slugify_normalizes_spaces_and_specials():
    assert github_sync.slugify("MCP Filesystem Server") == "MCP-Filesystem-Server"
    assert github_sync.slugify("A / weird? * name") == "A-weird-name"
    assert github_sync.slugify("") == "asset"


def test_target_path_renders_template():
    assert github_sync.target_path_for("skills", "MCP X") == "library/skills/MCP-X.md"
    assert github_sync.target_path_for("mcp", "Test", "lib/{type}/{slug}.md") == "lib/mcp/Test.md"


# ── Markdown rendering ────────────────────────────────────────────


def test_render_includes_frontmatter_and_sections():
    class FakeMeta:
        vetted = True
        vetted_by = "ali@colaberry.com"
        vetted_at = "2026-06-02T20:00:00Z"
        what_its_for = "Build forecasting models"
        description = "Long description here"
        how_to_use = "Run `forecast.py --input x.csv`"
        example = "forecast(...)"
        install_command = "pip install foo"
        readme_markdown = "# Foo\nSome readme content."

    raw = {
        "name": "Forecast Tool", "version": "1.2", "owner": "Sales Engineering",
        "source": "https://github.com/colaberry/foo",
        "tags": ["forecast", "sales"],
        "description": "Helpful forecast tool",
    }
    md = github_sync.render_asset_markdown("skills", "Forecast Tool", raw, FakeMeta())
    assert md.startswith("---\n")
    assert 'asset_id: "Forecast Tool"' in md
    assert "category: skills" in md
    assert "vetted: true" in md
    assert "## What it's used for" in md
    assert "## How to use" in md
    assert "## Example" in md
    assert "## Install" in md
    assert "## README (snapshot)" in md
    assert "[↗ Source]" in md


# ── Audit log roundtrip ───────────────────────────────────────────


def test_audit_event_persisted_and_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(github_sync, "AUDIT_DIR", tmp_path / "audit")
    ev = github_sync.SyncEvent(
        event_id="abc123", operation="upsert", asset_kind="library_asset",
        category="skills", asset_id="Test Skill",
        repo="x/y", branch="main", target_path="library/skills/Test-Skill.md",
        author_email="ali@colaberry.com", author_display_name="Ali",
        triggered_by="manual", commit_sha="deadbeef", status="success",
        bytes_written=120, started_at="2026-06-02T20:00:00Z",
        finished_at="2026-06-02T20:00:01Z",
    )
    github_sync._append_audit(ev)
    rows = github_sync.history(category="skills", asset_id="Test Skill")
    assert len(rows) == 1
    assert rows[0].commit_sha == "deadbeef"
    assert rows[0].status == "success"


# ── sync_asset orchestration (gh mocked) ──────────────────────────


@pytest.fixture
def fake_inventory(monkeypatch):
    """Patch inventory.load_category + store.get_metadata to known values."""
    from execution.products.library import inventory, store
    fake_rows = [{"name": "Test Skill", "version": "1.0",
                       "description": "A test skill", "source": "https://example.com",
                       "tags": ["test"]}]
    monkeypatch.setattr(inventory, "load_category", lambda c: fake_rows)

    class FakeMeta:
        vetted = True
        vetted_by = "ali@colaberry.com"
        vetted_at = "2026-06-02T20:00:00Z"
        what_its_for = "Testing"
        description = "Test"
        how_to_use = ""
        example = ""
        install_command = ""
        readme_markdown = ""
    monkeypatch.setattr(store, "get_metadata", lambda w, c, a: FakeMeta())
    return None


def test_sync_asset_dry_run_records_noop_with_full_path(tmp_path, monkeypatch, fake_inventory):
    monkeypatch.setattr(github_sync, "AUDIT_DIR", tmp_path / "audit")
    ev = github_sync.sync_asset("skills", "Test Skill",
                                            approver_email="ali@colaberry.com",
                                            operation="upsert", dry_run=True)
    assert ev.status == "noop"
    assert ev.commit_sha == "dry-run"
    assert ev.target_path == "library/skills/Test-Skill.md"
    assert ev.author_display_name == "Ali Muwwakkil"
    assert ev.repo == "ColaberryIntern/AI_ProjectArchitect"
    assert ev.bytes_written > 100   # markdown body got rendered
    # Audit row persisted
    rows = github_sync.history(category="skills", asset_id="Test Skill")
    assert len(rows) == 1


def test_sync_asset_records_failure_when_gh_missing(tmp_path, monkeypatch, fake_inventory):
    monkeypatch.setattr(github_sync, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(github_sync, "_gh_available", lambda: False)
    ev = github_sync.sync_asset("skills", "Test Skill", approver_email="ali@colaberry.com")
    assert ev.status == "failed"
    assert "gh CLI not available" in ev.error


def test_sync_asset_via_mocked_gh_records_commit_sha(tmp_path, monkeypatch, fake_inventory):
    monkeypatch.setattr(github_sync, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(github_sync, "_gh_available", lambda: True)
    monkeypatch.setattr(github_sync, "_sync_via_gh",
                              lambda *a, **kw: "abc12345")
    ev = github_sync.sync_asset("skills", "Test Skill", approver_email="ali@colaberry.com")
    assert ev.status == "success"
    assert ev.commit_sha == "abc12345"


def test_sync_asset_delete_operation(tmp_path, monkeypatch, fake_inventory):
    monkeypatch.setattr(github_sync, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(github_sync, "_gh_available", lambda: True)
    monkeypatch.setattr(github_sync, "_sync_via_gh",
                              lambda *a, **kw: "del-sha-001")
    ev = github_sync.sync_asset("skills", "Test Skill",
                                            approver_email="ali@colaberry.com",
                                            operation="delete")
    assert ev.status == "success"
    assert ev.operation == "delete"
    assert ev.commit_sha == "del-sha-001"


# ── sync_all_approved reconciliation ───────────────────────────────


def test_sync_all_approved_buckets_vetted_vs_unvetted(tmp_path, monkeypatch):
    """Vetted items upsert; unvetted items that were previously synced get deleted."""
    monkeypatch.setattr(github_sync, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(github_sync, "_gh_available", lambda: True)
    monkeypatch.setattr(github_sync, "_sync_via_gh",
                              lambda *a, **kw: "sha-test")

    from execution.products.library import inventory, store
    # Use one real category with two items: one vetted, one not
    monkeypatch.setattr(inventory, "CATEGORIES",
                              [inventory.CATEGORY_BY_KEY["skills"]])
    fake_rows = [
        {"name": "Approved Skill", "description": "ok", "source": "x"},
        {"name": "Pending Skill", "description": "draft", "source": "y"},
    ]
    monkeypatch.setattr(inventory, "load_category", lambda c: fake_rows)

    def fake_meta(w, c, a):
        m = type("M", (), {})()
        m.vetted = (a == "Approved Skill")
        m.vetted_by = "ali@colaberry.com" if m.vetted else None
        m.vetted_at = "2026-06-02T20:00:00Z"
        m.what_its_for = ""
        m.description = ""
        m.how_to_use = ""
        m.example = ""
        m.install_command = ""
        m.readme_markdown = ""
        return m
    monkeypatch.setattr(store, "get_metadata", fake_meta)

    res = github_sync.sync_all_approved()
    # Approved Skill → upsert; Pending Skill → skipped (never synced before)
    assert res["upserted"] == 1
    assert res["skipped"] == 1
    assert res["failed"] == 0
