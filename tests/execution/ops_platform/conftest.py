"""Shared fixtures for ops_platform tests.

Builds a tiny plugin tree in a tmp directory so each test runs in isolation
without depending on the repo's real /plugins folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_manifest(plugin_id: str, type_str: str, category: str = "Sales") -> dict:
    return {
        "id": plugin_id,
        "name": plugin_id.replace("_", " ").title(),
        "type": type_str,
        "category": category,
        "subcategory": "Test",
        "description": f"Test capability {plugin_id} for unit tests in the ops platform.",
        "business_value": f"Saves time for users of {plugin_id}.",
        "version": "1.0.0",
        "owner": {"name": "Test Owner", "team": "QA"},
        "inputs": [
            {"name": "text", "type": "text", "description": "Some input text.", "required": True}
        ],
        "outputs": [
            {"name": "summary", "type": "markdown", "description": "A summary."}
        ],
        "tags": ["test", plugin_id],
        "launch_mode": "guided",
        "difficulty": "beginner",
        "estimated_time_savings": {"minutes_per_run": 10, "runs_per_week_estimate": 5},
        "training_video": {"source": "generated", "url": None, "duration_seconds": None, "generated_walkthrough_path": None},
        "feedback_enabled": True,
        "prompt_path": "prompts/execute.txt",
        "readme_path": "README.md",
        "response_contract_required": True,
        "mcp_servers_used": [],
        "agents_used": [],
        "changelog": [{"version": "1.0.0", "date": "2026-05-26", "summary": "init"}]
    }


@pytest.fixture
def fake_plugin_root(tmp_path: Path) -> Path:
    """Construct a minimal /plugins-shaped tree with two workflows + one agent."""
    root = tmp_path / "plugins"
    root.mkdir()

    def write_plugin(type_folder: str, plugin_id: str, type_str: str):
        plugin_dir = root / type_folder / plugin_id
        (plugin_dir / "prompts").mkdir(parents=True)
        manifest = _make_manifest(plugin_id, type_str)
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        (plugin_dir / "prompts" / "execute.txt").write_text(
            "Execute task with {text}. Return the response contract."
        )
        (plugin_dir / "README.md").write_text(f"# {plugin_id}\n")

    write_plugin("workflows", "test_summary", "workflow")
    write_plugin("workflows", "test_compose", "workflow")
    write_plugin("agents", "test_agent", "agent")
    return root


@pytest.fixture
def make_response():
    """Factory for a valid response-contract payload."""
    def _build(**overrides):
        base = {
            "summary": "Did the thing.",
            "files_created": [],
            "files_modified": [],
            "components_added": [],
            "database_changes": [],
            "routes_added": [],
            "dependencies_added": [],
            "mcp_servers_used": [],
            "agents_used": [],
            "tests_written": [],
            "known_issues": [],
            "verification_steps": [],
            "next_recommended_tasks": [],
        }
        base.update(overrides)
        return base
    return _build
