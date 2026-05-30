"""Tests for execution/ops_platform/plugin_loader.py"""

import json

import pytest

from execution.ops_platform.plugin_loader import load_plugins


def test_loads_valid_plugins(fake_plugin_root):
    result = load_plugins(root=fake_plugin_root)
    assert result.ok
    assert len(result.capabilities) == 3
    ids = sorted(c["id"] for c in result.capabilities)
    assert ids == ["test_agent", "test_compose", "test_summary"]


def test_enriches_with_meta(fake_plugin_root):
    result = load_plugins(root=fake_plugin_root)
    cap = next(c for c in result.capabilities if c["id"] == "test_summary")
    assert cap["_meta"]["has_prompt"] is True
    assert cap["_meta"]["has_readme"] is True
    assert cap["_meta"]["plugin_type_folder"] == "workflows"


def test_skips_missing_manifest(fake_plugin_root):
    # Create a folder with no manifest.
    (fake_plugin_root / "workflows" / "broken_no_manifest").mkdir()
    result = load_plugins(root=fake_plugin_root)
    assert any("broken_no_manifest" in s["path"] for s in result.skipped)
    assert result.ok  # missing manifest = skipped, not error


def test_rejects_invalid_manifest(fake_plugin_root):
    bad = fake_plugin_root / "workflows" / "bad_id" / "manifest.json"
    bad.parent.mkdir()
    bad.write_text(json.dumps({
        "id": "BadID with spaces!",  # violates pattern
        "name": "Bad",
        "type": "workflow",
        "category": "Test",
        "description": "Too short.",
        "owner": {"name": "x"},
        "version": "1.0.0",
    }))
    result = load_plugins(root=fake_plugin_root)
    assert not result.ok
    assert any("bad_id" in e["path"] for e in result.errors)


def test_rejects_type_folder_mismatch(fake_plugin_root):
    # Put a workflow-typed manifest inside the agents folder.
    bad = fake_plugin_root / "agents" / "mismatch" / "manifest.json"
    bad.parent.mkdir(parents=True)
    bad.write_text(json.dumps({
        "id": "mismatch",
        "name": "Mismatch",
        "type": "workflow",
        "category": "Test",
        "description": "This has the wrong type for its folder placement.",
        "owner": {"name": "x"},
        "version": "1.0.0",
    }))
    result = load_plugins(root=fake_plugin_root)
    assert any("mismatch" in e["path"] and "does not match folder" in e["error"] for e in result.errors)


def test_detects_duplicate_ids(fake_plugin_root):
    # Add a second plugin with the same id as test_summary.
    dup = fake_plugin_root / "workflows" / "dup" / "manifest.json"
    dup.parent.mkdir()
    dup.write_text(json.dumps({
        "id": "test_summary",
        "name": "Duplicate",
        "type": "workflow",
        "category": "Test",
        "description": "Has a duplicate id, should be rejected.",
        "owner": {"name": "x"},
        "version": "1.0.0",
    }))
    result = load_plugins(root=fake_plugin_root)
    assert any("duplicate id" in e["error"] for e in result.errors)


def test_empty_plugin_root(tmp_path):
    result = load_plugins(root=tmp_path / "does_not_exist")
    assert result.ok
    assert result.capabilities == []
