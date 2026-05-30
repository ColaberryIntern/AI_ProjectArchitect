"""Logical layer manifest for the Colaberry Platform.

This file declares which modules under `execution/ops_platform/` belong
to which architectural layer. It exists so we can enforce the
Platform-Core / Product-shell boundary *without* moving 95 modules
(which would break thousands of imports and tests).

A linter or boundary test can read this manifest and fail builds that
import a Product-layer module from Platform Core (illegal direction)
or that route a request through a Product-only module from inside
Platform Core.

Layers:
    platform_core — shared runtime (no UI, no product-specific logic).
                    Event fabric, distributed coordination, orchestration
                    runtime, governance, audit/replay, projections, RBAC,
                    observability, autonomy controls.

    ops_product   — Operations product surfaces: incidents, alerts,
                    operational dashboards, executive reporting, controls UI.

    architect_product — Project Architect surfaces: planning, builder,
                       recommendation, requirements intelligence.

    library_product — Library surfaces (governed assets catalog).
                      Today this is in execution/products/library/.

    shared_util   — small helpers used by multiple layers. No dependencies
                    on layer-specific modules.
"""

from __future__ import annotations

PLATFORM_CORE: set[str] = {
    # ── eventing + coordination ───────────────────────────────────
    "event_fabric", "distributed_event_bus", "realtime_bus", "ws_gateway",
    "distributed_lock", "distributed_lock_v2", "distributed_presence",
    "distributed_rate_limit", "worker_coordination", "coordination_diagnostics",
    "redis_backends", "redis_sentinel", "shared_cache_backend", "cache_bus",
    # ── persistence + sequencing ──────────────────────────────────
    "session_store", "migrations", "retention_policy",
    "optimistic_concurrency",
    # ── orchestration runtime ─────────────────────────────────────
    "orchestration_engine", "orchestration_runtime", "orchestration_recovery",
    "runtime_queue", "runtime_router", "scheduler", "scheduler_main",
    "worker_main", "workflow_runner",
    # ── governance + identity ─────────────────────────────────────
    "rbac", "policy_engine", "enforcement", "controls",
    "auth", "identity", "service_identities", "jwt_verifier", "idp",
    "secrets", "workspaces",
    # ── audit + observability ─────────────────────────────────────
    "audit_log", "signed_audit", "telemetry", "tracing",
    "prometheus_exporter", "reliability_monitor", "self_healing",
    "security_telemetry", "response_contract",
    # ── reliability + recovery (Phase 10) ─────────────────────────
    "transactional_outbox", "poison_handler", "recovery_coordinator",
    "backup_integrity", "backup_restore", "load_test",
    "chaos_engine",
    # ── event sourcing + projections ──────────────────────────────
    "projection_engine",
    # ── core registries used by all products ──────────────────────
    "capability_registry", "capability_versions",
    "agent_registry", "agent_runtime", "plugin_loader",
    # ── shared abstractions ───────────────────────────────────────
    "errors", "presence",
}

OPS_PRODUCT: set[str] = {
    "incidents", "alerts", "notifications",
    "governance_scorecards", "executive_reporting", "compliance_reports",
    "access_reviews",
    "approvals", "change_requests",
    "forecasting", "adoption", "analytics",
    "operational_graph", "evaluation",
}

ARCHITECT_PRODUCT: set[str] = {
    "builder", "recommendation_engine", "requirements_intelligence",
    "workflow_optimizer", "workflow_discovery", "pipeline_engine",
    "discovery_queue", "search_index", "semantic_analyzer",
    "knowledge_graph",  # intelligence_goals lives at execution/ top-level, not ops_platform
    "execution_assistant", "verification_agent", "training_agent",
    "training_pipeline", "experiments",
    "trust_engine", "reputation_scorer", "prompt_diff",
    "scoped_memory", "organizational_memory", "feedback_store",
    "copilot",  # the Architect-facing AI assistant
    "collab_sessions",   # Architect's collab editor
}

LIBRARY_PRODUCT: set[str] = {
    "marketplace",
    # The library *product* itself lives at execution/products/library/.
    # marketplace remains here today; it's part of Library.
}

SHARED_UTIL: set[str] = set()  # add as we identify pure helpers


# ── Inverse lookup for boundary-checking tools ────────────────────

LAYER_OF: dict[str, str] = {}
for name, group, _label in (
    (PLATFORM_CORE,    "platform_core",    "Platform Core"),
    (OPS_PRODUCT,      "ops_product",      "Ops product"),
    (ARCHITECT_PRODUCT,"architect_product","Architect product"),
    (LIBRARY_PRODUCT,  "library_product",  "Library product"),
    (SHARED_UTIL,      "shared_util",      "Shared util"),
):
    for mod in name:
        LAYER_OF[mod] = group


def layer_of(module_name: str) -> str:
    """Return the layer for an ops_platform module name (no path prefix)."""
    return LAYER_OF.get(module_name, "uncategorized")


def all_known_modules() -> set[str]:
    return (PLATFORM_CORE | OPS_PRODUCT | ARCHITECT_PRODUCT
                | LIBRARY_PRODUCT | SHARED_UTIL)
