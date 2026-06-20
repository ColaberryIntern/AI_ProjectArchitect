"""The MCP install flow must install at USER scope, not project-local.

Real bug (jackie@, 2026-06-19): the install agent registered the colaberry
server under `projects["C:/.../Python Project"].mcpServers` -- Claude Code's
DEFAULT `local` scope -- so no colaberry_* tools appeared in any other folder.
The fix forces `-s user` on every `claude mcp add` and tells the direct-edit
fallback to write the TOP-LEVEL `mcpServers` key, never `projects[...]`.

These tests guard the install payload so the scope can't silently regress.
`_build_install_payload` is the single source of truth that /profile/mcp-token,
/profile/mcp-token-reissue, and the setup page all consume.
"""
from __future__ import annotations

import pytest

from app.routers.mcp_server import _build_install_payload

TOKEN = "cmcp_deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


@pytest.fixture()
def payload():
    return _build_install_payload(TOKEN, "jackie@colaberry.com", "Windows laptop")


# ── Every CLI command installs at user scope ───────────────────────────

CLI_COMMAND_KEYS = [
    "install_command",
    "install_command_mac_linux",
    "install_command_win_cmd",
    "install_command_win_ps",
]


@pytest.mark.parametrize("key", CLI_COMMAND_KEYS)
def test_cli_commands_force_user_scope(payload, key):
    cmd = payload[key]
    assert "claude mcp add colaberry" in cmd
    assert "-s user" in cmd, f"{key} must install at user scope, not local"
    assert "https://advisor.colaberry.ai/mcp/v1" in cmd
    assert "--transport http" in cmd
    assert TOKEN in cmd


def test_hostname_variants_embed_native_substitution(payload):
    assert "$(hostname)" in payload["install_command_mac_linux"]
    assert "%COMPUTERNAME%" in payload["install_command_win_cmd"]
    assert "$env:COMPUTERNAME" in payload["install_command_win_ps"]


# ── The self-orienting Claude prompt ───────────────────────────────────


def test_claude_prompt_cli_path_uses_user_scope(payload):
    prompt = payload["claude_install_prompt"]
    # The embedded `claude mcp add` (Step 2) must carry -s user.
    assert "-s user" in prompt
    # And it must explain WHY, so an agent doesn't strip it.
    assert "USER scope" in prompt or "user scope" in prompt


def test_claude_prompt_directs_top_level_not_projects(payload):
    prompt = payload["claude_install_prompt"]
    # Step 3 (direct edit) must point at the top-level key and warn off the
    # projects[...] nesting that caused the original bug.
    assert "TOP-LEVEL `mcpServers`" in prompt
    assert "projects[" in prompt  # it names the wrong location to forbid it
    assert "DO NOT nest" in prompt


def test_claude_prompt_does_not_echo_local_scope_default(payload):
    """A stray `claude mcp add ... --transport http` WITHOUT -s user would
    reintroduce the bug. Assert no such bare form survives in the prompt."""
    prompt = payload["claude_install_prompt"]
    # Wherever the add command appears, -s user must be on the same logical
    # command. Cheap proxy: the substring just before --transport is -s user.
    assert "-s user --transport http" in prompt


# ── Payload shape stays stable for the JS that consumes it ─────────────


def test_payload_has_all_consumed_keys(payload):
    for key in CLI_COMMAND_KEYS + ["token", "label", "claude_install_prompt"]:
        assert key in payload
    assert payload["token"] == TOKEN
    assert payload["label"] == "Windows laptop"
