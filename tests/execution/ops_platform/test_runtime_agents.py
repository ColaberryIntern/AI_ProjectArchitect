"""Tests for the runtime-agent bridge + agent_registry.upsert_agent idempotency.

Isolated to tmp_path so no real agent/audit state is touched.
"""

import json

import jsonschema
import pytest

from config.settings import PROJECT_ROOT, SCHEMAS_DIR
from execution.ops_platform import agent_registry, audit_log, runtime_agents


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_registry, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    yield


# ── upsert_agent ─────────────────────────────────────────────────────


def test_upsert_is_idempotent_by_id():
    a1 = agent_registry.upsert_agent(
        agent_id="agent_runtime_x", name="X", description="d",
        autonomy_policy="approval_required", confidence_threshold=0.5,
        permitted_actions=["a"])
    a2 = agent_registry.upsert_agent(
        agent_id="agent_runtime_x", name="X2", description="d2",
        autonomy_policy="recommend_only", confidence_threshold=0.6,
        permitted_actions=["a", "b"])
    same_id = [a for a in agent_registry.list_agents() if a.agent_id == "agent_runtime_x"]
    assert len(same_id) == 1
    assert a2.name == "X2"
    assert a2.autonomy_policy == "recommend_only"
    assert a1.created_at == a2.created_at  # creation provenance preserved


def test_upsert_preserves_human_pause():
    agent_registry.upsert_agent(
        agent_id="agent_runtime_p", name="P", description="d",
        autonomy_policy="approval_required", confidence_threshold=0.5,
        permitted_actions=["a"])
    agent_registry.pause("agent_runtime_p", actor="ali")
    updated = agent_registry.upsert_agent(
        agent_id="agent_runtime_p", name="P", description="d",
        autonomy_policy="approval_required", confidence_threshold=0.5,
        permitted_actions=["a"])
    assert updated.paused is True  # an update must never silently un-pause


def test_upsert_rejects_bad_policy():
    with pytest.raises(ValueError):
        agent_registry.upsert_agent(
            agent_id="agent_runtime_b", name="B", description="d",
            autonomy_policy="nope", confidence_threshold=0.5, permitted_actions=["a"])


# ── runtime_agents bridge ────────────────────────────────────────────


def test_registry_id_prefix():
    assert runtime_agents.registry_id("cb_mention_responder") == "agent_runtime_cb_mention_responder"


def test_load_declarations_reads_committed_file():
    ids = {d["id"] for d in runtime_agents.load_declarations()}
    assert {"cb_mention_responder", "autopickup_worker"} <= ids


def test_upsert_runtime_agents_registers_all_idempotently():
    decls = runtime_agents.load_declarations()
    runtime_agents.upsert_runtime_agents(declarations=decls)
    runtime_agents.upsert_runtime_agents(declarations=decls)  # run twice
    registered = {a.agent_id for a in agent_registry.list_agents()}
    for d in decls:
        assert runtime_agents.registry_id(d["id"]) in registered
    assert len(agent_registry.list_agents()) == len(decls)  # no duplicates


def test_committed_declaration_matches_schema():
    decl = json.loads((PROJECT_ROOT / "config" / "tbi_runtime_agents.json").read_text(encoding="utf-8"))
    schema = json.loads((SCHEMAS_DIR / "ops" / "tbi_runtime_agents.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(decl)
