"""Phase 9 end-to-end smoke — event fabric + projections + distributed
coordination + diagnostics together."""

import pytest

from execution.ops_platform import (
    audit_log, cache_bus, chaos_engine, controls, coordination_diagnostics,
    distributed_event_bus, distributed_lock, distributed_lock_v2,
    distributed_presence, event_fabric, orchestration_engine,
    orchestration_runtime, projection_engine, redis_backends, runtime_queue,
    worker_coordination, ws_gateway,
)
from execution.ops_platform.capability_registry import CapabilityRegistry
from execution.ops_platform.plugin_loader import load_plugins

from ._fakeredis import FakeRedis


@pytest.fixture
def registry(fake_plugin_root, tmp_path, monkeypatch):
    import execution.ops_platform.capability_registry as crm
    monkeypatch.setattr(crm, "_STATS_PATH", tmp_path / "registry_stats.json")
    monkeypatch.setattr(event_fabric, "_EVENTS_DIR", tmp_path / "fabric")
    monkeypatch.setattr(event_fabric, "_SEQUENCE_PATH", tmp_path / "seq.json")
    monkeypatch.setattr(projection_engine, "_PROJECTIONS_DIR", tmp_path / "projections")
    monkeypatch.setattr(orchestration_engine, "_ORCH_DIR", tmp_path / "orchestrations")
    monkeypatch.setattr(orchestration_runtime, "_CLAIMS_DIR", tmp_path / "claims")
    monkeypatch.setattr(distributed_lock, "_LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(chaos_engine, "_CHAOS_DIR", tmp_path / "chaos")
    monkeypatch.setattr(controls, "_CONTROLS_DIR", tmp_path / "controls")
    monkeypatch.setattr(runtime_queue, "_QUEUE_DIR", tmp_path / "queue")
    monkeypatch.setattr(worker_coordination, "_WORKERS_DIR", tmp_path / "workers")
    monkeypatch.setattr(audit_log, "_AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(cache_bus, "_VERSION_DIR", tmp_path / "versions")
    cache_bus.reset_for_tests()
    event_fabric.reset_for_tests()
    controls._RATE_LIMIT_HITS.clear()
    monkeypatch.setattr(redis_backends, "_REDIS_AVAILABLE", True)
    redis_backends._CLIENT = FakeRedis()
    redis_backends._KEY_PREFIX = "ops:"
    projection_engine._REGISTRY.clear()
    reg = CapabilityRegistry(load_fn=lambda: load_plugins(root=fake_plugin_root))
    import execution.ops_platform.capability_registry as crm2
    monkeypatch.setattr(crm2, "_DEFAULT_REGISTRY", reg)
    yield reg
    redis_backends._CLIENT = None
    projection_engine._REGISTRY.clear()


def test_phase9_substrate_holds_together(registry):
    # 1. Event fabric — emit then replay
    ev = event_fabric.emit("smoke.start", actor_id="alice",
                              durability_scope="redis-distributed",
                              consistency_scope="at-least-once")
    assert ev.sequence > 0

    # 2. Distributed event bus — publish + consume + ack
    publish = distributed_event_bus.publish(ev)
    assert publish.published
    msgs = distributed_event_bus.consume_group(group="smoke", consumer="c1",
                                                    event_types=["smoke.start"])
    assert msgs
    first = msgs[0]
    assert distributed_event_bus.ack(group="smoke",
                                         stream_key=first["stream_key"],
                                         message_id=first["stream_message_id"])

    # 3. Distributed lock v2 — fencing token round-trip
    lease = distributed_lock_v2.acquire("smoke.lock", lease_seconds=30)
    assert distributed_lock_v2.verify_fencing_token("smoke.lock",
                                                          lease.fencing_token)
    assert distributed_lock_v2.release("smoke.lock", owner_token=lease.owner_token)

    # 4. Distributed presence — heartbeat + topology
    distributed_presence.heartbeat(workspace_id="sales", user_id="alice",
                                       display_name="Alice")
    assert any(p.user_id == "alice"
                  for p in distributed_presence.list_active("sales"))
    distributed_presence.register_ws_subscriber(subscriber_id="sub1",
                                                    host_id="hostA",
                                                    workspace_id="sales")
    topo = distributed_presence.ws_topology()
    assert topo["subscriber_count"] >= 1

    # 5. Orchestration runtime — claim uses Redis path when wired
    o = orchestration_engine.create_orchestration(
        name="smoke",
        steps=[{"step_id": "s1", "kind": "wait", "wait_seconds": 0}],
    )
    claim = orchestration_runtime.claim_step(o.orchestration_id, step_id="s1",
                                                 worker_id="w1")
    assert claim is not None
    assert claim.coordination_scope == "redis-distributed"
    assert claim.fencing_token >= 1
    orchestration_runtime.release_claim(claim, worker_id="w1")

    # 6. Projection engine — deterministic rebuild
    projection_engine.register_default_projections()
    event_fabric.emit("incident.opened", correlation_id="smoke-inc",
                          actor_id="alice")
    result = projection_engine.rebuild("incident_timeline")
    assert "smoke-inc" in result["state"]

    # 7. Coordination diagnostics — topology reports Redis-wired
    topo = coordination_diagnostics.coordination_topology()
    assert topo["redis_client_wired"] is True

    # 8. Chaos: duplicate delivery emits twice
    before = len(event_fabric.replay(event_types=["chaos.duplicate_delivery"]))
    chaos_engine.inject(kind="duplicate_delivery", target_id="t",
                            duration_seconds=10, actor="chaos")
    after = len(event_fabric.replay(event_types=["chaos.duplicate_delivery"]))
    assert after >= before + 2
