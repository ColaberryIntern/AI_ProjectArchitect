"""Tests for [Infra 2] PR-based GitHub sync + smoke-test CI gate.

Strategy:
  - Monkeypatch `_run` in github_pr_sync to avoid any real subprocess.
  - Capture command sequences to verify branch creation / file PUT /
    PR creation / auto-merge invocation.
  - Smoke-test runs on real fixture files in tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.products.library import github_pr_sync, github_sync


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def approvers_only_ali(tmp_path, monkeypatch):
    """Minimal approver config — Ali only, no auto-merge."""
    cfg = {
        "approvers": [
            {"email": "ali@colaberry.com", "display_name": "Ali",
              "role": "lead", "can_approve": ["all"]},
        ],
        "approval_target_repo": "ColaberryIntern/AI_ProjectArchitect",
        "sync_path_template": "library/{type}/{slug}.md",
        "default_branch": "main",
        "sync_commit_author_name": "Test Sync",
        "sync_commit_author_email": "test-sync@colaberry.com",
        "pr_auto_merge": False,
    }
    path = tmp_path / "library_approvers.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(github_sync, "APPROVERS_PATH", path)
    return path


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(github_pr_sync, "AUDIT_DIR", tmp_path / "_audit")
    return tmp_path / "_audit"


@pytest.fixture
def fake_gh(monkeypatch):
    """Capture every _run() call so we can assert on the sequence."""
    calls = []

    def _fake_run(cmd, cwd=None, env=None, timeout=90):
        calls.append({"cmd": cmd, "cwd": cwd})
        if cmd[0] == "gh" and cmd[1] == "--version":
            return (0, "gh version 2.0", "")
        if cmd[:3] == ["gh", "api", "/repos/ColaberryIntern/AI_ProjectArchitect/git/ref/heads/main"]:
            return (0, "abc123def456\n", "")
        if cmd[:3] == ["gh", "api", "--method"] and "POST" in cmd:
            return (0, "{}", "")
        if cmd[:3] == ["gh", "api", "--method"] and "PUT" in cmd:
            return (0, '{"commit":{"sha":"newsha"}}', "")
        if "contents/" in " ".join(cmd) and "--jq" in cmd:
            # First lookup for existing file sha — pretend it doesn't exist yet
            return (1, "", "Not Found")
        if cmd[:3] == ["gh", "pr", "create"]:
            return (0, "https://github.com/ColaberryIntern/AI_ProjectArchitect/pull/42\n", "")
        if cmd[:3] == ["gh", "pr", "merge"]:
            return (0, "Merge queued", "")
        # Fallback success
        return (0, "", "")

    monkeypatch.setattr(github_pr_sync, "_run", _fake_run)
    return calls


@pytest.fixture
def fake_render(monkeypatch):
    """Stub render_asset_markdown to return a known body."""
    monkeypatch.setattr(github_sync, "render_asset_markdown",
                                  lambda category, asset_id, **kw: (
                                      f"---\ntitle: {asset_id}\nkind: {category}\n"
                                      f"slug: {asset_id}\nversion: 1.0\nowner: test\n"
                                      f"---\n\n# {asset_id}\n\nFixture body.\n"
                                  ))


# ── open_sync_pr ─────────────────────────────────────────────────


def test_open_sync_pr_creates_branch_file_and_pr(
        approvers_only_ali, audit_dir, fake_gh, fake_render):
    result = github_pr_sync.open_sync_pr(
        category="skills", asset_id="my-skill",
        approver_email="ali@colaberry.com",
    )
    assert result.status == "opened"
    assert result.pr_number == 42
    assert "pull/42" in result.pr_url
    assert result.branch.startswith("sync/skills/my-skill-")
    # Verify gh calls included: get-main-sha, create-ref, PUT contents, pr create
    cmd_blobs = [" ".join(c["cmd"]) for c in fake_gh]
    assert any("git/ref/heads/main" in c for c in cmd_blobs)
    assert any("POST" in c and "refs" in c for c in cmd_blobs)
    assert any("PUT" in c and "contents/library/skills/my-skill.md" in c for c in cmd_blobs)
    assert any("pr create" in c for c in cmd_blobs)


def test_open_sync_pr_rejects_unauthorised_approver(
        approvers_only_ali, audit_dir, fake_gh, fake_render):
    result = github_pr_sync.open_sync_pr(
        category="skills", asset_id="my-skill",
        approver_email="someone-else@external.com",
    )
    assert result.status == "failed"
    assert "not authorised" in result.error
    # No gh commands should have run beyond the auth gate
    cmd_blobs = [" ".join(c["cmd"]) for c in fake_gh]
    assert not any("pr create" in c for c in cmd_blobs)


def test_open_sync_pr_dry_run_does_not_call_gh(
        approvers_only_ali, audit_dir, fake_gh, fake_render):
    result = github_pr_sync.open_sync_pr(
        category="skills", asset_id="my-skill",
        approver_email="ali@colaberry.com", dry_run=True,
    )
    assert result.status == "noop"
    assert result.pr_url == "(dry_run)"
    assert fake_gh == []


def test_open_sync_pr_audits_failures(
        approvers_only_ali, audit_dir, monkeypatch, fake_render):
    def _failing_run(cmd, cwd=None, env=None, timeout=90):
        if cmd[0] == "gh" and cmd[1] == "--version":
            return (0, "gh", "")
        if "/git/ref/heads/main" in " ".join(cmd):
            return (0, "abc123\n", "")
        if "POST" in cmd and "refs" in " ".join(cmd):
            return (1, "", "branch already exists")
        return (0, "", "")

    monkeypatch.setattr(github_pr_sync, "_run", _failing_run)
    result = github_pr_sync.open_sync_pr(
        category="skills", asset_id="my-skill",
        approver_email="ali@colaberry.com",
    )
    assert result.status == "failed"
    assert "create branch failed" in result.error
    # Audit row written
    audit_files = list((audit_dir).glob("*.jsonl"))
    assert len(audit_files) == 1
    contents = audit_files[0].read_text(encoding="utf-8")
    assert "failed" in contents


def test_auto_merge_triggers_when_enabled(
        approvers_only_ali, audit_dir, fake_gh, fake_render, monkeypatch):
    # Flip the cached config to enable auto-merge
    cfg = json.loads(approvers_only_ali.read_text(encoding="utf-8"))
    cfg["pr_auto_merge"] = True
    approvers_only_ali.write_text(json.dumps(cfg), encoding="utf-8")

    result = github_pr_sync.open_sync_pr(
        category="skills", asset_id="my-skill",
        approver_email="ali@colaberry.com",
    )
    assert result.status == "auto_merged"
    assert result.auto_merged is True
    cmd_blobs = [" ".join(c["cmd"]) for c in fake_gh]
    assert any("pr merge" in c and "--auto" in c for c in cmd_blobs)


def test_gh_not_available_fails_with_clear_message(
        approvers_only_ali, audit_dir, monkeypatch, fake_render):
    monkeypatch.setattr(github_pr_sync, "_run",
                                  lambda *a, **k: (127, "", "gh: command not found"))
    result = github_pr_sync.open_sync_pr(
        category="skills", asset_id="my-skill",
        approver_email="ali@colaberry.com",
    )
    assert result.status == "failed"
    assert "gh CLI not available" in result.error


# ── maybe_trigger_pr_for_approval ────────────────────────────────


def test_maybe_trigger_returns_none_for_non_colaberry_tenant(approvers_only_ali, audit_dir):
    result = github_pr_sync.maybe_trigger_pr_for_approval(
        item_kind="library_asset", item_id="x", category="skills",
        company_id="patriot", approver_email="pat@patriot.com",
    )
    assert result is None


def test_maybe_trigger_respects_disable_env(
        approvers_only_ali, audit_dir, monkeypatch):
    monkeypatch.setenv("LIBRARY_PR_SYNC_DISABLED", "1")
    result = github_pr_sync.maybe_trigger_pr_for_approval(
        item_kind="library_asset", item_id="x", category="skills",
        company_id="colaberry", approver_email="ali@colaberry.com",
    )
    assert result is None


# ── Smoke test CLI ───────────────────────────────────────────────


def _write(p: Path, body: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_smoke_test_passes_valid_artifact(tmp_path):
    from scripts import library_sync_smoke

    good = _write(tmp_path / "library/skills/good.md",
                          "---\ntitle: Foo\nkind: skill\nslug: foo\n"
                          "version: 1.0\nowner: ali\n---\n\n# Foo\n\nBody here.\n")
    rc = library_sync_smoke.main([str(good)])
    assert rc == 0


def test_smoke_test_blocks_missing_frontmatter(tmp_path):
    from scripts import library_sync_smoke
    bad = _write(tmp_path / "library/skills/bad.md", "# Just markdown\n\nNo frontmatter.\n")
    rc = library_sync_smoke.main([str(bad)])
    assert rc == 1


def test_smoke_test_blocks_missing_required_keys(tmp_path):
    from scripts import library_sync_smoke
    bad = _write(tmp_path / "library/skills/bad.md",
                         "---\ntitle: Foo\n---\n\nbody\n")
    rc = library_sync_smoke.main([str(bad)])
    assert rc == 1


def test_smoke_test_detects_github_pat(tmp_path):
    from scripts import library_sync_smoke
    bad = _write(tmp_path / "library/skills/leaky.md",
                         "---\ntitle: Foo\nkind: skill\nslug: foo\n"
                         "version: 1.0\nowner: ali\n---\n\n"
                         "Token is ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
    rc = library_sync_smoke.main([str(bad)])
    assert rc == 1


def test_smoke_test_detects_aws_key(tmp_path):
    from scripts import library_sync_smoke
    bad = _write(tmp_path / "library/skills/leaky.md",
                         "---\ntitle: Foo\nkind: skill\nslug: foo\n"
                         "version: 1.0\nowner: ali\n---\n\n"
                         "Key: AKIAIOSFODNN7EXAMPLE\n")
    rc = library_sync_smoke.main([str(bad)])
    assert rc == 1


def test_smoke_test_blocks_empty_body(tmp_path):
    from scripts import library_sync_smoke
    bad = _write(tmp_path / "library/skills/bad.md",
                         "---\ntitle: Foo\nkind: skill\nslug: foo\n"
                         "version: 1.0\nowner: ali\n---\n\n")
    rc = library_sync_smoke.main([str(bad)])
    assert rc == 1


def test_smoke_test_skips_non_markdown_files(tmp_path):
    from scripts import library_sync_smoke
    txt = _write(tmp_path / "README.txt", "not markdown")
    rc = library_sync_smoke.main([str(txt)])
    assert rc == 0  # nothing to check
