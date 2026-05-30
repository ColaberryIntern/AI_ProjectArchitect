"""Build a comprehensive system tour HTML — covers Phase 0 (the original
AI Project Architect) through Phase 10 (Reliability layer), with critique
forms, operator instructions, and a 'generate response' button.

Run:  python scripts/build_system_tour.py
Output: output/system_tour/index.html (auto-opens)
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output" / "system_tour"
SHOTS = OUT_DIR / "screenshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHOTS.mkdir(parents=True, exist_ok=True)


# ─── Phase metadata ────────────────────────────────────────────────────────

@dataclass
class Phase:
    code: str               # e.g. "0", "1", "10"
    title: str
    emoji: str
    one_liner: str          # 1-sentence mission
    plain_english: str      # 2-3 sentence "what it does without jargon"
    modules: list[tuple[str, str]] = field(default_factory=list)  # (filename, plain-english role)
    extras: list[str] = field(default_factory=list)               # other notable files/dirs
    key_concepts: list[tuple[str, str]] = field(default_factory=list)  # (concept, explanation)


PHASES: list[Phase] = [
    Phase(
        "0",
        "Before Phase 1 — The AI Project Architect & Build Companion",
        "📐",
        "Turn a raw business idea into a build-ready spec doc that an intern can execute.",
        "A chat-driven 5-step pipeline that asks questions until the idea is concrete, "
        "then generates a structured project document — checked against 5 quality gates "
        "before delivery. No automation theater — every gate requires explicit approval.",
        modules=[
            ("execution/ambiguity_detector.py", "Catches vague language ('handle edge cases', 'optimize later')"),
            ("execution/feature_classifier.py", "Sorts ideas into core vs optional features"),
            ("execution/feature_advisor.py", "Suggests catalog features that fit the idea"),
            ("execution/profile_generator.py", "Builds the project's character profile"),
            ("execution/outline_generator.py", "Creates the 7-section locked outline"),
            ("execution/chapter_writer.py", "Writes each chapter with Purpose, Design Intent, Implementation"),
            ("execution/quality_gate_runner.py", "Enforces the 5 quality gates"),
            ("execution/document_assembler.py", "Compiles approved chapters into final Markdown"),
            ("execution/state_manager.py", "Tracks pipeline state in project_state.json"),
            ("app/routers/idea_intake.py", "HTTP routes for step 1 (idea entry)"),
            ("app/routers/feature_discovery.py", "HTTP routes for step 2 (feature selection)"),
            ("app/routers/outline_approval.py", "HTTP routes for step 3 (outline gate)"),
            ("app/routers/chapter_build.py", "HTTP routes for step 4 (chapter loop)"),
            ("app/routers/final_assembly.py", "HTTP routes for step 5 (final doc)"),
        ],
        extras=[
            "directives/01-idea-intake.md", "directives/03-feature-discovery.md",
            "directives/04-outline-generation.md", "directives/06-chapter-build.md",
            "directives/07-quality-gates.md", "directives/08-final-assembly.md",
        ],
        key_concepts=[
            ("5-step pipeline", "Idea Intake → Feature Discovery → Outline Locking → Chapter Build → Final Assembly"),
            ("5 quality gates", "Completeness · Clarity · Build Readiness · Anti-Vagueness · Intern Success Test"),
            ("4 agent personas", "Project Architect · Ideation Coach · Quality Gatekeeper · Document Assembler"),
            ("Outline locking", "Once approved, the outline is SHA256-hashed — no silent drift"),
            ("Intern Test", "Could a junior dev execute this project using ONLY the produced doc?"),
        ],
    ),

    Phase(
        "1",
        "Phase 1 — Operations Platform Foundation",
        "🏗️",
        "Add the operational chassis: workspaces, identities, capability catalog, HTTP shell.",
        "The Project Architect generates docs but had no operational layer underneath it. "
        "Phase 1 added the foundation for treating this as a real platform — multiple "
        "workspaces, user identities, a registry of what the system can do, and a stable "
        "HTTP surface to control it.",
        modules=[
            ("execution/ops_platform/workspaces.py", "Multi-tenant workspace boundaries"),
            ("execution/ops_platform/identity.py", "User and service identity primitives"),
            ("execution/ops_platform/auth.py", "Authentication wiring"),
            ("execution/ops_platform/capability_registry.py", "Registry of what the platform can do"),
            ("execution/ops_platform/plugin_loader.py", "Loads plugin capabilities at startup"),
            ("execution/ops_platform/secrets.py", "Secret access without baking creds in code"),
            ("app/routers/ops_platform.py", "All HTTP endpoints for the ops platform (~109 KB)"),
        ],
        key_concepts=[
            ("Workspaces", "Isolated tenants — one company's data can't bleed into another's"),
            ("Capability versioning", "Every operation has a version so old clients keep working"),
            ("Identity vs Service identity", "Humans (with sessions) vs background workers (with service tokens)"),
        ],
    ),

    Phase(
        "2",
        "Phase 2 — Semantic Intelligence + Discovery",
        "🧠",
        "Make the platform understand what it sees — features, requirements, knowledge graph.",
        "The system needed to reason about content, not just store it. Phase 2 added "
        "semantic analysis (matching ideas to known patterns), a knowledge graph "
        "(relationships between concepts), and discovery queues that surface things "
        "that look related or duplicate.",
        modules=[
            ("execution/ops_platform/semantic_analyzer.py", "Pattern matching across content (~672 LOC)"),
            ("execution/ops_platform/knowledge_graph.py", "Concept-relationship graph"),
            ("execution/ops_platform/requirements_intelligence.py", "Extracts and reasons about requirements"),
            ("execution/ops_platform/discovery_queue.py", "Surfaces related/duplicate items for review"),
            ("execution/ops_platform/search_index.py", "Full-text + semantic search"),
            ("execution/ops_platform/operational_graph.py", "Graph of operational entities"),
            ("execution/ops_platform/intelligence_goals.py", "Ties content to measurable goals (~693 LOC)"),
        ],
        key_concepts=[
            ("Semantic ≠ keyword", "The system recognizes 'customer churn' and 'user retention' point at the same thing"),
            ("Knowledge graph", "Stored as nodes + edges — questions become graph traversals"),
            ("Discovery queue", "Like an inbox of 'this looks related, decide what to do' items"),
        ],
    ),

    Phase(
        "3",
        "Phase 3 — Orchestration + Workflow Runtime",
        "🎼",
        "Run multi-step workflows reliably with queues, schedulers, and routing.",
        "Single-step operations weren't enough — Phase 3 added the ability to define "
        "multi-step workflows, queue them, schedule them, and route work to the right "
        "handler. This is the 'do a sequence of things, in order, retrying on failure' "
        "layer.",
        modules=[
            ("execution/ops_platform/orchestration_engine.py", "Defines and runs workflows (~417 LOC)"),
            ("execution/ops_platform/orchestration_runtime.py", "Workflow execution + step claims"),
            ("execution/ops_platform/runtime_queue.py", "Background work queue"),
            ("execution/ops_platform/runtime_router.py", "Routes queued items to handlers"),
            ("execution/ops_platform/scheduler.py", "Time-based scheduling (cron-like)"),
            ("execution/ops_platform/worker_main.py", "Long-running worker entry point"),
            ("execution/ops_platform/scheduler_main.py", "Long-running scheduler entry point"),
            ("execution/ops_platform/workflow_runner.py", "Glue between queue + runtime"),
            ("execution/ops_platform/workflow_discovery.py", "Finds and registers workflows"),
            ("execution/ops_platform/workflow_optimizer.py", "Suggests workflow improvements"),
        ],
        key_concepts=[
            ("Orchestration", "A multi-step recipe with branches, retries, and observable state"),
            ("Step claim", "A worker says 'I'm working on this' so another worker doesn't double-process"),
            ("Scheduler vs queue", "Scheduler decides WHEN; queue decides WHO. Both wired together."),
        ],
    ),

    Phase(
        "4",
        "Phase 4 — Lifecycle + Builder + Pipeline Engine",
        "🛠️",
        "End-to-end build pipelines with depth control, builders, and pipeline engine.",
        "Phase 4 wired together the workflow primitives into a real lifecycle — a "
        "builder that knows how to take an idea through stages, a pipeline engine "
        "that runs each stage, and depth controls so you can choose how thorough "
        "to be.",
        modules=[
            ("execution/ops_platform/builder.py", "Lifecycle builder (~445 LOC)"),
            ("execution/ops_platform/pipeline_engine.py", "Pipeline execution (~718 LOC)"),
            ("execution/build_depth.py", "Controls how deep/thorough builds go"),
            ("execution/auto_builder.py", "Automated build driver (~813 LOC)"),
            ("execution/ops_platform/recommendation_engine.py", "Recommends next actions (~546 LOC)"),
            ("execution/ops_platform/training_pipeline.py", "Trains models from operational data"),
        ],
        key_concepts=[
            ("Lifecycle", "Idea → Refinement → Build → Validation → Delivery — each stage has gates"),
            ("Build depth", "Quick prototype vs enterprise-grade — same pipeline, different rigor"),
            ("Pipeline engine", "Generic runner that any stage can plug into"),
        ],
    ),

    Phase(
        "5",
        "Phase 5 — RBAC + Policy + Enforcement",
        "🛡️",
        "Add role-based access control, policy engine, and enforcement at the boundary.",
        "Once multiple people use it, you need to control who can do what. Phase 5 "
        "added roles (admin, builder, viewer…), a policy engine (configurable rules), "
        "and enforcement points so the rules actually get applied — not just defined.",
        modules=[
            ("execution/ops_platform/rbac.py", "Role definitions and assignments"),
            ("execution/ops_platform/policy_engine.py", "Evaluates policies against requests"),
            ("execution/ops_platform/enforcement.py", "Blocks/allows operations at the boundary"),
            ("execution/ops_platform/access_reviews.py", "Periodic 'who has what access' audits"),
            ("execution/ops_platform/controls.py", "Operator controls (maintenance mode, rate limits)"),
            ("execution/ops_platform/jwt_verifier.py", "Validates signed tokens"),
            ("execution/ops_platform/service_identities.py", "Background-job identity primitives"),
            ("execution/ops_platform/idp.py", "External identity provider wiring"),
        ],
        key_concepts=[
            ("Role vs permission", "Roles are bundles of permissions — 'builder' might be 6 permissions"),
            ("Policy engine", "Rules written in config, not code — change without redeploy"),
            ("Enforcement point", "Where the rule actually gets checked — at API boundary, not deep in code"),
        ],
    ),

    Phase(
        "6",
        "Phase 6 — Single-host Hardening + Optimistic Concurrency",
        "🔒",
        "Make a single-server install rock-solid — locks, caching, concurrency control.",
        "Phase 6 hardened the single-host story before going multi-host. Added file-based "
        "locks (only one worker touches a thing at a time), a cache bus (workers on the "
        "same host share cache state), and optimistic concurrency (two people can't "
        "silently overwrite each other's edits).",
        modules=[
            ("execution/ops_platform/distributed_lock.py", "File-locked mutual exclusion (single host)"),
            ("execution/ops_platform/cache_bus.py", "Cross-process cache invalidation"),
            ("execution/ops_platform/shared_cache_backend.py", "File-backed shared cache"),
            ("execution/ops_platform/optimistic_concurrency.py", "Revision-ID conflict detection"),
            ("execution/ops_platform/session_store.py", "Persistent session storage"),
            ("execution/ops_platform/migrations.py", "Schema migrations"),
            ("execution/ops_platform/retention_policy.py", "How long data is kept"),
        ],
        key_concepts=[
            ("Optimistic concurrency", "Every record has a revision_id; updates must match it or get rejected"),
            ("File lock", "Works perfectly on one machine, useless across machines — explicit scope"),
            ("Single-host honest scope", "Phase 6 explicitly said: 'this is single-host only' — no fake distributed claims"),
        ],
    ),

    Phase(
        "7",
        "Phase 7 — Realtime + WebSocket + Presence",
        "📡",
        "Live updates pushed to browsers — presence, realtime bus, websocket gateway.",
        "Phase 7 added the realtime layer. Browsers now get pushed updates over "
        "WebSocket instead of polling. Presence tracking (who's online, where), "
        "a realtime bus (one place to publish 'something happened'), and a websocket "
        "gateway (the actual socket server).",
        modules=[
            ("execution/ops_platform/realtime_bus.py", "Pub/sub bus for live events (~320 LOC)"),
            ("execution/ops_platform/ws_gateway.py", "WebSocket connection handler"),
            ("execution/ops_platform/presence.py", "Who is currently online (single-host)"),
            ("execution/ops_platform/distributed_presence.py", "Who is online (multi-host)"),
            ("execution/ops_platform/notifications.py", "Push notifications to users"),
            ("execution/ops_platform/alerts.py", "Operational alerts (paging) (~327 LOC)"),
            ("execution/ops_platform/incidents.py", "Incident lifecycle tracking"),
            ("execution/ops_platform/telemetry.py", "Metrics + traces emission"),
        ],
        key_concepts=[
            ("Pub/sub bus", "Publishers don't know subscribers exist — loose coupling"),
            ("Presence", "Each user emits a heartbeat; absence = offline (TTL-based)"),
            ("Realtime ≠ instant", "There's latency; we measure it (telemetry)"),
        ],
    ),

    Phase(
        "8",
        "Phase 8 — Autonomous Agents + Collaboration + Signed Audit",
        "🤖",
        "Agent runtime with autonomy policies, collab sessions, tamper-evident audit log.",
        "Phase 8 was huge. Added autonomous agents (with explicit autonomy tiers: "
        "recommend only, require approval, low-risk auto, full auto), collaborative "
        "editing sessions (multiple users on the same doc), and a signed audit log "
        "(every action chained with HMAC — tampering is provable).",
        modules=[
            ("execution/ops_platform/agent_runtime.py", "Runs agents under autonomy policy (~327 LOC)"),
            ("execution/ops_platform/agent_registry.py", "Registry of available agents"),
            ("execution/ops_platform/collab_sessions.py", "Multi-user editing sessions"),
            ("execution/ops_platform/signed_audit.py", "HMAC-chained tamper-evident log"),
            ("execution/ops_platform/audit_log.py", "Append-only audit log"),
            ("execution/ops_platform/approvals.py", "Human-in-the-loop approval flow"),
            ("execution/ops_platform/change_requests.py", "Proposed changes pending approval"),
            ("execution/ops_platform/chaos_engine.py", "Inject failures to test resilience"),
            ("execution/ops_platform/governance_scorecards.py", "Track governance metrics"),
            ("execution/ops_platform/forecasting.py", "Forecasts operational load"),
            ("execution/ops_platform/training_agent.py", "Agent that trains on operational data"),
            ("execution/ops_platform/verification_agent.py", "Agent that verifies claims"),
            ("execution/ops_platform/compliance_reports.py", "Compliance reporting"),
            ("execution/ops_platform/copilot.py", "User-facing AI assistant (~406 LOC)"),
        ],
        key_concepts=[
            ("Autonomy policy", "recommend_only · approval_required · autonomous_low_risk_only · autonomous_full"),
            ("Signed audit", "Each row hashes the previous row — break the chain, tampering is provable"),
            ("Collab session", "Like Google Docs — multiple cursors, conflict resolution via revision_id"),
        ],
    ),

    Phase(
        "9",
        "Phase 9 — Distributed Coordination + Event Fabric + Multi-Host",
        "🌐",
        "Multi-host coordination via Redis Streams, distributed locks v2, event sourcing.",
        "Phase 9 made the platform work across multiple machines. Added a unified "
        "event fabric (every event flows through one bus), Redis Streams for "
        "distributed coordination, Redis-backed distributed locks (with fencing "
        "tokens), and event-sourced projections (rebuildable from history).",
        modules=[
            ("execution/ops_platform/event_fabric.py", "Unified event bus (~389 LOC)"),
            ("execution/ops_platform/distributed_event_bus.py", "Multi-host event distribution"),
            ("execution/ops_platform/distributed_lock_v2.py", "Redis SETNX + Lua + fencing"),
            ("execution/ops_platform/distributed_rate_limit.py", "Multi-host rate limiting"),
            ("execution/ops_platform/projection_engine.py", "Rebuildable read models from event history"),
            ("execution/ops_platform/redis_backends.py", "Redis client wiring + adapters"),
            ("execution/ops_platform/worker_coordination.py", "Multi-host worker registry"),
            ("execution/ops_platform/coordination_diagnostics.py", "Inspect coordination state"),
            ("execution/ops_platform/reliability_monitor.py", "Watches platform health"),
            ("execution/ops_platform/prometheus_exporter.py", "Metrics export for Prometheus"),
            ("execution/ops_platform/tracing.py", "Distributed tracing instrumentation"),
            ("execution/ops_platform/self_healing.py", "Auto-detect + suggest fixes"),
        ],
        key_concepts=[
            ("Event fabric", "One bus for everything — no per-feature pub/sub silos"),
            ("Fencing token", "Each lock acquire gets a monotonic number — late writers get rejected"),
            ("Event sourcing", "State is a function of events; projections rebuild from history"),
            ("Redis Streams", "Consumer groups, replay-from-zero, durable history"),
        ],
    ),

    Phase(
        "10",
        "Phase 10 — Reliability + Recovery + Consensus Boundary Hardening",
        "🛡️",
        "Outbox pattern, Sentinel failover, poison quarantine, recovery coordinator, snapshot integrity, K8s HA.",
        "Phase 10 made the system handle failures on purpose. Transactional outbox "
        "(no silent message loss), Redis Sentinel watching (auto-detect failover), "
        "poison quarantine (bad messages get caught, not retried forever), recovery "
        "coordinator (proposes fixes, never silent), snapshot integrity (cryptographic "
        "fingerprints), orchestration crash recovery, load benchmarks with honest "
        "topology labels, and a full K8s HA deployment + runbook.",
        modules=[
            ("execution/ops_platform/transactional_outbox.py", "Bounded-retry outbox + DLQ"),
            ("execution/ops_platform/redis_sentinel.py", "Failover detection + reconnect"),
            ("execution/ops_platform/poison_handler.py", "Bad-event quarantine"),
            ("execution/ops_platform/recovery_coordinator.py", "Autonomy-gated recovery proposals"),
            ("execution/ops_platform/backup_integrity.py", "SHA-256 manifests + lineage DAG"),
            ("execution/ops_platform/orchestration_recovery.py", "Checkpoints + crash recovery"),
            ("execution/ops_platform/load_test.py", "Benchmarks with hardware/topology labels"),
        ],
        extras=[
            "deploy/kubernetes/ha/sentinel.yaml",
            "deploy/kubernetes/ha/rolling-upgrade.md",
        ],
        key_concepts=[
            ("Outbox pattern", "Publish-via-DB so a crash mid-publish doesn't lose the message"),
            ("Poison quarantine", "After N retries, the bad message stops blocking everything else"),
            ("Recovery coordinator", "Suggests fixes, applies only with explicit autonomy authorization"),
            ("Snapshot manifest", "Per-file SHA-256 — corruption is detectable, not silent"),
            ("Honest consensus boundary", "We say what we do NOT guarantee: no Raft, no exactly-once, no auto split-brain merge"),
        ],
    ),
]


OPERATOR_GUIDE = [
    ("🟢 Start the server", "uvicorn app.main:app --reload",
     "Runs the HTTP API + UI on http://localhost:8000."),
    ("🧪 Run all tests", "python -m pytest tests/ -q",
     "Runs the full test suite (~22 min, 2138 tests). Use -k 'phase10' for a subset."),
    ("🩺 Cluster health", "curl http://localhost:8000/ops/system/cluster-health",
     "Reports ready/not-ready across every subsystem."),
    ("📥 Outbox metrics", "curl http://localhost:8000/ops/outbox/metrics",
     "Pending / published / failed / dead-letter counts. Watch this during rollouts."),
    ("📤 Drain the outbox", "curl -X POST http://localhost:8000/ops/outbox/drain",
     "Force one drain cycle. Useful after reconnect or test."),
    ("💾 Take a backup", "curl -X POST http://localhost:8000/ops/system/backup",
     "Captures a SHA-256-manifested snapshot."),
    ("🔍 Verify a backup", "curl -X POST http://localhost:8000/ops/backup/verify/<manifest_id>",
     "Recomputes SHA-256 for every file in the manifest."),
    ("🩹 Recover after crash",
     "curl -X POST http://localhost:8000/ops/orchestrations/recover-after-crash",
     "Releases stale step claims so other workers can pick them up."),
    ("🚦 Maintenance mode",
     "curl -X POST http://localhost:8000/ops/controls/maintenance-mode -d '{\"reason\":\"...\"}'",
     "Blocks workflow execution platform-wide. Disable with the matching /disable endpoint."),
    ("📊 Run a load test",
     "python -c \"from execution.ops_platform import load_test; print(load_test.run_suite())\"",
     "Runs the 4-benchmark suite + records hardware/topology snapshot."),
    ("🐳 Container build", "docker compose build",
     "Reads docker-compose.yml; builds api + worker + scheduler images."),
    ("☸️ Kubernetes HA deploy",
     "kubectl apply -f deploy/kubernetes/ha/sentinel.yaml",
     "Brings up Sentinel + StatefulSet + PodDisruptionBudget. See rolling-upgrade.md for upgrades."),
    ("📚 Generate a project doc (Phase 0)",
     "Open http://localhost:8000/idea-intake in a browser",
     "Step through the original 5-step pipeline: Idea → Features → Outline → Chapters → Final doc."),
]


BEFORE_AFTER = [
    ("📝 Document generation",
     "5-step pipeline (chat-driven, single-user, file-based state)",
     "Still there + governed by workspaces, audit, RBAC, and approval gates"),
    ("👥 Users",
     "Single-user; no auth beyond the URL",
     "Workspaces · roles · service identities · approval flows · access reviews"),
    ("🔁 Reliability",
     "If a step crashed mid-flight: restart from the top",
     "Checkpoints · stale-claim release · bounded retry · DLQ · poison quarantine"),
    ("📡 Real-time",
     "Page refresh to see updates",
     "WebSocket gateway · presence · realtime bus · pushed events"),
    ("📊 Observability",
     "Print statements + tail the log file",
     "Audit log (HMAC-chained) · telemetry · tracing · Prometheus exporter · alerts · incidents"),
    ("🤖 Automation",
     "Manual click-through",
     "Agent runtime with 4 autonomy tiers · recovery coordinator · self-healing · forecasting"),
    ("🌐 Multi-host",
     "Single process on one machine",
     "Redis-backed distributed locks · event fabric · projections · Sentinel failover · K8s HA"),
    ("💾 Backups",
     "Manual file copies",
     "Snapshot manifests with SHA-256 · lineage DAG · partial restore by profile · orphan detection"),
    ("🛑 Failure handling",
     "Errors crash the request; you find out from the user",
     "Chaos engine · reliability monitor · poison quarantine · proposed recovery actions"),
    ("🏛️ Compliance",
     "Trust me bro",
     "Signed audit log · access reviews · governance scorecards · compliance reports"),
]


# ─── Helpers ───────────────────────────────────────────────────────────────

def loc(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except Exception:
        return 0


def render_screenshot(browser, page_html: str, out_path: Path,
                          width: int = 1280, height: int = 820) -> None:
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.set_content(page_html, wait_until="load")
    page.wait_for_timeout(700)
    page.screenshot(path=str(out_path), full_page=False)
    ctx.close()


def hero_html() -> str:
    return dedent("""<!doctype html>
    <html><head><meta charset="utf-8">
    <script src="https://cdn.tailwindcss.com"></script>
    <style> body { background: radial-gradient(circle at 30% 20%, #1e3a8a 0%, #0f172a 60%); }
    .glow { box-shadow: 0 0 60px rgba(99, 102, 241, 0.5); } </style>
    </head><body class="text-white p-10">
      <div class="max-w-5xl mx-auto">
        <div class="text-center mb-8">
          <div class="text-7xl mb-4">📐 ➜ 🛡️</div>
          <h1 class="text-5xl font-extrabold leading-tight">From Spec Generator<br>to Operational Control Plane</h1>
          <p class="text-2xl text-slate-300 mt-3">A tour of the system today — Phase 0 through Phase 10</p>
        </div>
        <div class="grid grid-cols-5 gap-3 mt-10">
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">📐</div>
            <div class="font-bold text-sm mt-1">Phase 0</div>
            <div class="text-xs text-slate-400">Project Architect</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🏗️</div>
            <div class="font-bold text-sm mt-1">Phase 1</div>
            <div class="text-xs text-slate-400">Foundation</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🧠</div>
            <div class="font-bold text-sm mt-1">Phase 2</div>
            <div class="text-xs text-slate-400">Semantic Intelligence</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🎼</div>
            <div class="font-bold text-sm mt-1">Phase 3</div>
            <div class="text-xs text-slate-400">Orchestration</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🛠️</div>
            <div class="font-bold text-sm mt-1">Phase 4</div>
            <div class="text-xs text-slate-400">Lifecycle</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🛡️</div>
            <div class="font-bold text-sm mt-1">Phase 5</div>
            <div class="text-xs text-slate-400">RBAC + Policy</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🔒</div>
            <div class="font-bold text-sm mt-1">Phase 6</div>
            <div class="text-xs text-slate-400">Single-host hardening</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">📡</div>
            <div class="font-bold text-sm mt-1">Phase 7</div>
            <div class="text-xs text-slate-400">Realtime</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🤖</div>
            <div class="font-bold text-sm mt-1">Phase 8</div>
            <div class="text-xs text-slate-400">Autonomous + Collab</div>
          </div>
          <div class="bg-slate-800/60 rounded-xl p-4 text-center">
            <div class="text-3xl">🌐</div>
            <div class="font-bold text-sm mt-1">Phase 9</div>
            <div class="text-xs text-slate-400">Distributed</div>
          </div>
        </div>
        <div class="mt-3 bg-indigo-600/30 border border-indigo-500/50 rounded-xl p-5 text-center glow">
          <div class="text-3xl">🛡️</div>
          <div class="font-bold text-lg mt-1">Phase 10 — Reliability + Recovery (current)</div>
          <div class="text-sm text-slate-300 mt-1">Transactional outbox · Sentinel failover · Poison quarantine · Recovery coordinator · Snapshot integrity · K8s HA</div>
        </div>
        <div class="text-center text-slate-400 mt-8 text-sm">
          🧪 <span class="text-emerald-400 font-bold">2138 tests</span> passing &nbsp;·&nbsp;
          📦 <span class="text-amber-400 font-bold">95 modules</span> in ops_platform &nbsp;·&nbsp;
          🚫 <span class="text-rose-400 font-bold">0 regressions</span>
        </div>
      </div>
    </body></html>""")


def architecture_html() -> str:
    return dedent("""<!doctype html>
    <html><head><meta charset="utf-8">
    <script src="https://cdn.tailwindcss.com"></script>
    </head><body class="bg-slate-50 p-10">
      <div class="max-w-5xl mx-auto">
        <h1 class="text-3xl font-bold mb-6 text-slate-800">🏛️ The Four-Layer Architecture</h1>
        <div class="space-y-3">

          <div class="bg-rose-100 border-2 border-rose-300 rounded-2xl p-5">
            <div class="flex items-start gap-4">
              <div class="text-4xl">📜</div>
              <div>
                <div class="font-bold text-rose-900 text-lg">Layer 1 — Directives (the WHAT)</div>
                <div class="text-sm text-rose-800 mt-1">Plain-language SOPs in <code>/directives</code>. What success looks like, what to verify, what's forbidden.</div>
                <div class="text-xs text-rose-700 mt-2">Living docs · updated as the system learns · readable by interns</div>
              </div>
            </div>
          </div>

          <div class="text-center text-2xl text-slate-400">↓</div>

          <div class="bg-amber-100 border-2 border-amber-300 rounded-2xl p-5">
            <div class="flex items-start gap-4">
              <div class="text-4xl">🧠</div>
              <div>
                <div class="font-bold text-amber-900 text-lg">Layer 2 — Orchestration (the DECIDING)</div>
                <div class="text-sm text-amber-800 mt-1">Claude (during dev) + the Copilot agent (in prod). Reads directives, plans, asks clarifying questions, designs tests.</div>
                <div class="text-xs text-amber-700 mt-2">Never executes business logic — only reasons + orchestrates</div>
              </div>
            </div>
          </div>

          <div class="text-center text-2xl text-slate-400">↓</div>

          <div class="bg-emerald-100 border-2 border-emerald-300 rounded-2xl p-5">
            <div class="flex items-start gap-4">
              <div class="text-4xl">⚙️</div>
              <div>
                <div class="font-bold text-emerald-900 text-lg">Layer 3 — Execution (the DOING)</div>
                <div class="text-sm text-emerald-800 mt-1">Deterministic Python in <code>/execution</code>. One script = one responsibility. Testable, repeatable, audit-loggable.</div>
                <div class="text-xs text-emerald-700 mt-2">95 modules in <code>execution/ops_platform/</code> alone</div>
              </div>
            </div>
          </div>

          <div class="text-center text-2xl text-slate-400">↓</div>

          <div class="bg-indigo-100 border-2 border-indigo-300 rounded-2xl p-5">
            <div class="flex items-start gap-4">
              <div class="text-4xl">✅</div>
              <div>
                <div class="font-bold text-indigo-900 text-lg">Layer 4 — Verification (the PROVING)</div>
                <div class="text-sm text-indigo-800 mt-1">Tests in <code>/tests</code> — unit, integration, end-to-end. Claude designs them; CI runs them.</div>
                <div class="text-xs text-indigo-700 mt-2">2138 tests · 0 regressions · ~22 min full run</div>
              </div>
            </div>
          </div>
        </div>

        <div class="mt-8 bg-slate-200 rounded-xl p-5 text-sm text-slate-700">
          <div class="font-bold mb-1">💡 Core principle</div>
          <p>LLMs are probabilistic. Production systems must be deterministic. Claude reasons, plans, validates — it never runs business logic. The system's behavior comes from <strong>code + tests</strong>, not from prompts at runtime.</p>
        </div>
      </div>
    </body></html>""")


def pipeline_diagram_html() -> str:
    return dedent("""<!doctype html>
    <html><head><meta charset="utf-8">
    <script src="https://cdn.tailwindcss.com"></script>
    </head><body class="bg-white p-10">
      <div class="max-w-6xl mx-auto">
        <h1 class="text-3xl font-bold mb-2 text-slate-800">📐 The Project Architect Pipeline (Phase 0)</h1>
        <p class="text-slate-600 mb-6">5 steps · 5 quality gates · 1 build-ready Markdown doc at the end</p>

        <div class="grid grid-cols-5 gap-3 mb-6">
          <div class="bg-rose-50 border-2 border-rose-300 rounded-xl p-4 text-center">
            <div class="text-3xl">💡</div>
            <div class="font-bold mt-1 text-sm">1. Idea Intake</div>
            <div class="text-xs text-slate-600 mt-1">Raw idea → guided refinement</div>
          </div>
          <div class="bg-amber-50 border-2 border-amber-300 rounded-xl p-4 text-center">
            <div class="text-3xl">🧭</div>
            <div class="font-bold mt-1 text-sm">2. Feature Discovery</div>
            <div class="text-xs text-slate-600 mt-1">Core vs Optional, anti-overengineering</div>
          </div>
          <div class="bg-emerald-50 border-2 border-emerald-300 rounded-xl p-4 text-center">
            <div class="text-3xl">🔒</div>
            <div class="font-bold mt-1 text-sm">3. Outline Locking</div>
            <div class="text-xs text-slate-600 mt-1">7 sections, SHA256-locked</div>
          </div>
          <div class="bg-cyan-50 border-2 border-cyan-300 rounded-xl p-4 text-center">
            <div class="text-3xl">📚</div>
            <div class="font-bold mt-1 text-sm">4. Chapter Build</div>
            <div class="text-xs text-slate-600 mt-1">One chapter at a time, max 2 revisions</div>
          </div>
          <div class="bg-indigo-50 border-2 border-indigo-300 rounded-xl p-4 text-center">
            <div class="text-3xl">📄</div>
            <div class="font-bold mt-1 text-sm">5. Final Assembly</div>
            <div class="text-xs text-slate-600 mt-1">Compiled, versioned Markdown</div>
          </div>
        </div>

        <h2 class="text-xl font-bold mt-8 mb-3 text-slate-800">🎯 The Five Quality Gates</h2>
        <div class="grid grid-cols-5 gap-3">
          <div class="bg-slate-100 rounded-xl p-3 text-center">
            <div class="text-2xl">✅</div>
            <div class="font-bold text-xs mt-1">Completeness</div>
            <div class="text-[10px] text-slate-600">No TBD, no 'we'll decide later'</div>
          </div>
          <div class="bg-slate-100 rounded-xl p-3 text-center">
            <div class="text-2xl">🔎</div>
            <div class="font-bold text-xs mt-1">Clarity</div>
            <div class="text-[10px] text-slate-600">One-sentence chapter summaries</div>
          </div>
          <div class="bg-slate-100 rounded-xl p-3 text-center">
            <div class="text-2xl">🔧</div>
            <div class="font-bold text-xs mt-1">Build Readiness</div>
            <div class="text-[10px] text-slate-600">Execution order + inputs/outputs</div>
          </div>
          <div class="bg-slate-100 rounded-xl p-3 text-center">
            <div class="text-2xl">🚫</div>
            <div class="font-bold text-xs mt-1">Anti-Vagueness</div>
            <div class="text-[10px] text-slate-600">Forbidden phrases flagged</div>
          </div>
          <div class="bg-slate-100 rounded-xl p-3 text-center">
            <div class="text-2xl">🎓</div>
            <div class="font-bold text-xs mt-1">Intern Test</div>
            <div class="text-[10px] text-slate-600">Junior dev could execute this</div>
          </div>
        </div>
      </div>
    </body></html>""")


def stack_diagram_html() -> str:
    return dedent("""<!doctype html>
    <html><head><meta charset="utf-8">
    <script src="https://cdn.tailwindcss.com"></script>
    </head><body class="bg-slate-50 p-10">
      <div class="max-w-5xl mx-auto">
        <h1 class="text-3xl font-bold mb-2 text-slate-800">🥞 The Phase-1-to-10 Stack</h1>
        <p class="text-slate-600 mb-6">Each layer depends on the ones below. Phase 10 sits on top of everything that came before.</p>

        <div class="space-y-2">
          <div class="bg-indigo-500 text-white rounded-xl p-4">
            <div class="font-bold">🛡️ Phase 10 — Reliability + Recovery</div>
            <div class="text-xs opacity-90">Outbox · Sentinel · Poison · Recovery Coordinator · Snapshot Integrity · K8s HA</div>
          </div>
          <div class="bg-purple-500 text-white rounded-xl p-4">
            <div class="font-bold">🌐 Phase 9 — Distributed Coordination + Event Fabric</div>
            <div class="text-xs opacity-90">Event fabric · Redis Streams · distributed locks v2 · projections</div>
          </div>
          <div class="bg-fuchsia-500 text-white rounded-xl p-4">
            <div class="font-bold">🤖 Phase 8 — Autonomous Agents + Collaboration + Signed Audit</div>
            <div class="text-xs opacity-90">Agent runtime · collab sessions · HMAC-chained audit · chaos engine</div>
          </div>
          <div class="bg-pink-500 text-white rounded-xl p-4">
            <div class="font-bold">📡 Phase 7 — Realtime + WebSocket + Presence</div>
            <div class="text-xs opacity-90">Realtime bus · ws gateway · presence · notifications · alerts</div>
          </div>
          <div class="bg-rose-500 text-white rounded-xl p-4">
            <div class="font-bold">🔒 Phase 6 — Single-host Hardening + Optimistic Concurrency</div>
            <div class="text-xs opacity-90">File locks · cache bus · revision_id concurrency · migrations</div>
          </div>
          <div class="bg-orange-500 text-white rounded-xl p-4">
            <div class="font-bold">🛡️ Phase 5 — RBAC + Policy + Enforcement</div>
            <div class="text-xs opacity-90">Roles · policy engine · enforcement points · access reviews</div>
          </div>
          <div class="bg-amber-500 text-white rounded-xl p-4">
            <div class="font-bold">🛠️ Phase 4 — Lifecycle + Builder + Pipeline Engine</div>
            <div class="text-xs opacity-90">Builder · pipeline engine · build depth · recommendation engine</div>
          </div>
          <div class="bg-lime-500 text-white rounded-xl p-4">
            <div class="font-bold">🎼 Phase 3 — Orchestration + Workflow Runtime</div>
            <div class="text-xs opacity-90">Orchestration · queues · scheduler · worker · workflow runner</div>
          </div>
          <div class="bg-emerald-500 text-white rounded-xl p-4">
            <div class="font-bold">🧠 Phase 2 — Semantic Intelligence + Discovery</div>
            <div class="text-xs opacity-90">Semantic analyzer · knowledge graph · requirements intel · search</div>
          </div>
          <div class="bg-teal-500 text-white rounded-xl p-4">
            <div class="font-bold">🏗️ Phase 1 — Operations Platform Foundation</div>
            <div class="text-xs opacity-90">Workspaces · identity · auth · capability registry · plugin loader</div>
          </div>
          <div class="bg-slate-700 text-white rounded-xl p-4">
            <div class="font-bold">📐 Phase 0 — AI Project Architect (the original product)</div>
            <div class="text-xs opacity-90">5-step pipeline · 5 quality gates · 4 agent personas · spec docs for interns</div>
          </div>
        </div>
      </div>
    </body></html>""")


def operator_diagram_html() -> str:
    rows = "".join(
        f'<tr class="border-b border-slate-200"><td class="py-3 pr-4 align-top text-2xl">{html.escape(emoji)}</td>'
        f'<td class="py-3 pr-4 align-top font-semibold text-slate-800">{html.escape(label)}</td>'
        f'<td class="py-3 pr-4 align-top"><code class="bg-slate-100 px-2 py-1 rounded text-sm">{html.escape(cmd)}</code></td>'
        f'<td class="py-3 align-top text-sm text-slate-600">{html.escape(why)}</td></tr>'
        for emoji_label, cmd, why in [(x[0], x[1], x[2]) for x in OPERATOR_GUIDE]
        for emoji, label in [tuple(emoji_label.split(" ", 1))]
    )
    return dedent(f"""<!doctype html>
    <html><head><meta charset="utf-8">
    <script src="https://cdn.tailwindcss.com"></script>
    </head><body class="bg-white p-10">
      <div class="max-w-6xl mx-auto">
        <h1 class="text-3xl font-bold mb-2 text-slate-800">🛠️ Operator Quick-Start</h1>
        <p class="text-slate-600 mb-6">The 13 commands you'll actually run.</p>
        <table class="w-full"><thead>
          <tr class="border-b-2 border-slate-300">
            <th class="text-left pb-2 pr-4 text-xs uppercase text-slate-500"></th>
            <th class="text-left pb-2 pr-4 text-xs uppercase text-slate-500">What</th>
            <th class="text-left pb-2 pr-4 text-xs uppercase text-slate-500">Command</th>
            <th class="text-left pb-2 text-xs uppercase text-slate-500">Why</th>
          </tr></thead>
        <tbody>{rows}</tbody></table>
      </div>
    </body></html>""")


def before_after_html() -> str:
    rows = "".join(
        f'<tr class="border-b border-slate-200">'
        f'<td class="py-3 pr-4 align-top font-bold text-slate-800">{html.escape(area)}</td>'
        f'<td class="py-3 pr-4 align-top text-sm text-slate-600 bg-rose-50 rounded">{html.escape(before)}</td>'
        f'<td class="py-3 align-top text-sm text-slate-800 bg-emerald-50 rounded">{html.escape(after)}</td>'
        f'</tr>'
        for area, before, after in BEFORE_AFTER
    )
    return dedent(f"""<!doctype html>
    <html><head><meta charset="utf-8">
    <script src="https://cdn.tailwindcss.com"></script>
    </head><body class="bg-white p-10">
      <div class="max-w-6xl mx-auto">
        <h1 class="text-3xl font-bold mb-2 text-slate-800">🔄 Before Phase 1 vs After Phase 10</h1>
        <p class="text-slate-600 mb-6">Same product surface; far stronger floor.</p>
        <table class="w-full">
          <thead>
            <tr class="border-b-2 border-slate-300">
              <th class="text-left pb-2 pr-4 text-xs uppercase text-slate-500 w-1/5">Area</th>
              <th class="text-left pb-2 pr-4 text-xs uppercase text-rose-600">😬 Before Phase 1</th>
              <th class="text-left pb-2 text-xs uppercase text-emerald-600">🛡️ After Phase 10</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </body></html>""")


# ─── HTML assembly ─────────────────────────────────────────────────────────

def critique_form(key: str, label: str) -> str:
    safe = html.escape(label)
    return dedent(f"""
    <div class="bg-indigo-50 p-5 border-t border-slate-200" style="border-left:4px solid #6366f1;">
      <div class="flex items-center gap-2 mb-3">
        <span class="text-xl">💬</span>
        <strong class="text-slate-800">Your verdict on {safe}</strong>
      </div>
      <div class="flex flex-wrap gap-4 items-center mb-3">
        <label class="flex items-center gap-2 text-sm">
          <input type="radio" name="verdict-{key}" value="approved" class="text-emerald-600">
          <span>✅ Approved</span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input type="radio" name="verdict-{key}" value="approved-with-notes" class="text-amber-600">
          <span>🟡 Approved with notes</span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input type="radio" name="verdict-{key}" value="needs-work" class="text-rose-600">
          <span>🔴 Needs work / unclear</span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input type="radio" name="verdict-{key}" value="cut-it" class="text-slate-600">
          <span>🗑️ Cut it</span>
        </label>
        <label class="flex items-center gap-2 text-sm ml-auto">
          <span class="text-slate-600">Rating:</span>
          <select name="rating-{key}" class="border border-slate-300 rounded px-2 py-1 text-sm">
            <option value="">—</option>
            <option value="5">⭐⭐⭐⭐⭐</option>
            <option value="4">⭐⭐⭐⭐</option>
            <option value="3">⭐⭐⭐</option>
            <option value="2">⭐⭐</option>
            <option value="1">⭐</option>
          </select>
        </label>
      </div>
      <textarea name="notes-{key}"
                rows="2"
                placeholder="Notes — questions, confusions, things to verify, things to change..."
                class="w-full border border-slate-300 rounded-md p-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
    </div>""")


def phase_card(phase: Phase, hero_shot: str) -> str:
    mods = "".join(
        f'<li class="text-sm text-slate-700 py-1"><code class="text-indigo-700 text-xs">{html.escape(m)}</code><br>'
        f'<span class="text-slate-600 text-xs ml-1">— {html.escape(d)}</span></li>'
        for m, d in phase.modules
    )
    extras = ""
    if phase.extras:
        extras = "<div class='mt-3'><div class='text-xs font-bold text-slate-500 uppercase mb-1'>📂 Other files</div>"
        extras += "".join(f"<div class='text-xs text-slate-600'>📄 <code>{html.escape(e)}</code></div>" for e in phase.extras)
        extras += "</div>"
    concepts = "".join(
        f'<div class="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-2">'
        f'<div class="font-bold text-amber-900 text-sm">{html.escape(c)}</div>'
        f'<div class="text-xs text-amber-800 mt-1">{html.escape(e)}</div></div>'
        for c, e in phase.key_concepts
    )

    return dedent(f"""
    <section id="p{phase.code}" class="bg-white rounded-2xl shadow-sm border border-slate-200 mb-6 overflow-hidden">
      <div class="p-6 border-b border-slate-100">
        <div class="flex items-start gap-4">
          <div class="text-6xl">{phase.emoji}</div>
          <div class="flex-1">
            <div class="flex items-center gap-2 mb-1">
              <span class="bg-indigo-600 text-white text-xs font-bold px-2 py-1 rounded">P{phase.code}</span>
              <h2 class="text-2xl font-bold text-slate-900">{html.escape(phase.title)}</h2>
            </div>
            <p class="text-lg text-slate-700 italic mb-2">{html.escape(phase.one_liner)}</p>
            <p class="text-slate-700">{html.escape(phase.plain_english)}</p>
          </div>
        </div>
      </div>
      <div class="grid md:grid-cols-2 gap-6 p-6">
        <div>
          <div class="rounded-lg overflow-hidden shadow-md border border-slate-200">
            <img src="screenshots/{hero_shot}" class="w-full block">
          </div>
        </div>
        <div>
          <div class="text-xs font-bold text-slate-500 uppercase mb-2">🧩 Key concepts in plain English</div>
          {concepts}
        </div>
      </div>
      <div class="p-6 pt-0">
        <details class="bg-slate-50 rounded-lg border border-slate-200">
          <summary class="cursor-pointer p-3 font-semibold text-slate-700">📦 Files added in this phase ({len(phase.modules)})</summary>
          <ul class="px-5 py-2 list-disc">{mods}</ul>
          <div class="px-5 pb-3">{extras}</div>
        </details>
      </div>
      {critique_form('p' + phase.code, phase.title)}
    </section>""")


def build_html(screenshots: dict[str, str]) -> str:
    phase_cards = "\n".join(phase_card(p, screenshots[p.code]) for p in PHASES)

    toc = "".join(
        f'<a href="#p{p.code}" class="block px-3 py-1.5 rounded hover:bg-indigo-50 hover:text-indigo-700 text-sm">'
        f'{p.emoji} <span class="font-semibold">P{p.code}</span> — {html.escape(p.title.split(" — ", 1)[-1])}</a>'
        for p in PHASES
    )

    return dedent(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>System Tour · Phase 0 → Phase 10 📐 ➜ 🛡️</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .glow {{ box-shadow: 0 0 40px rgba(99,102,241,0.4); }}
    pre {{ font-family: 'Cascadia Code',Consolas,monospace; }}
  </style>
</head>
<body class="bg-slate-50 text-slate-900">

  <!-- HERO -->
  <div class="bg-slate-900 py-8">
    <div class="max-w-7xl mx-auto px-6">
      <img src="screenshots/hero.png" alt="hero" class="rounded-2xl shadow-2xl glow w-full">
    </div>
  </div>

  <!-- LAYOUT -->
  <div class="max-w-7xl mx-auto px-6 py-10 flex gap-8">
    <aside class="w-64 flex-none sticky top-6 self-start hidden lg:block">
      <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
        <div class="font-bold text-slate-800 mb-2 px-2">📚 Phases</div>
        {toc}
        <div class="font-bold text-slate-800 mt-4 mb-2 px-2">📋 Sections</div>
        <a href="#architecture" class="block px-3 py-1.5 rounded hover:bg-indigo-50 hover:text-indigo-700 text-sm">🏛️ Architecture</a>
        <a href="#stack" class="block px-3 py-1.5 rounded hover:bg-indigo-50 hover:text-indigo-700 text-sm">🥞 The full stack</a>
        <a href="#operator" class="block px-3 py-1.5 rounded hover:bg-indigo-50 hover:text-indigo-700 text-sm">🛠️ How to operate it</a>
        <a href="#beforeafter" class="block px-3 py-1.5 rounded hover:bg-indigo-50 hover:text-indigo-700 text-sm">🔄 Before vs After</a>
        <a href="#next" class="block px-3 py-1.5 rounded hover:bg-indigo-50 hover:text-indigo-700 text-sm">🚀 Where next</a>
        <div class="mt-4 pt-3 border-t border-slate-200">
          <a href="#critique" class="block bg-indigo-600 text-white text-center font-semibold py-2 rounded hover:bg-indigo-700">🎯 Generate Response</a>
        </div>
      </div>
    </aside>

    <main class="flex-1 min-w-0">

      <h1 class="text-3xl font-bold mb-2 flex items-center gap-3">
        <span>📚</span>What's in the system today
      </h1>
      <p class="text-slate-600 mb-6">
        Two products under one roof: the original
        <strong>AI Project Architect</strong> (your chat-driven spec generator) and the
        <strong>AI Operations Platform</strong> (Phases 1–10, 95 modules of operational control plane underneath).
        Each phase below is a separate "what was added and why," with a critique box so you can flag anything.
      </p>

      {phase_cards}

      <!-- ARCHITECTURE -->
      <h2 id="architecture" class="text-3xl font-bold mt-12 mb-4 flex items-center gap-3">
        <span>🏛️</span>The Four-Layer Architecture
      </h2>
      <p class="text-slate-600 mb-4">From the project's CLAUDE.md — this is the contract every change has to fit.</p>
      <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-6">
        <img src="screenshots/architecture.png" class="w-full block">
      </div>
      {critique_form('architecture', 'Four-Layer Architecture')}

      <!-- FULL STACK -->
      <h2 id="stack" class="text-3xl font-bold mt-12 mb-4 flex items-center gap-3">
        <span>🥞</span>The Full Stack — Phase 0 through Phase 10
      </h2>
      <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-6">
        <img src="screenshots/stack.png" class="w-full block">
      </div>
      {critique_form('stack', 'Phase 0-10 stack diagram')}

      <!-- OPERATOR -->
      <h2 id="operator" class="text-3xl font-bold mt-12 mb-4 flex items-center gap-3">
        <span>🛠️</span>How to Operate It
      </h2>
      <p class="text-slate-600 mb-4">The 13 commands you'll actually run. Server, tests, health, backups, recovery, deploy.</p>
      <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-6">
        <img src="screenshots/operator.png" class="w-full block">
      </div>
      {critique_form('operator', 'Operator quick-start')}

      <!-- BEFORE / AFTER -->
      <h2 id="beforeafter" class="text-3xl font-bold mt-12 mb-4 flex items-center gap-3">
        <span>🔄</span>What Changed Since Before Phase 1
      </h2>
      <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-6">
        <img src="screenshots/beforeafter.png" class="w-full block">
      </div>
      {critique_form('beforeafter', 'Before vs After comparison')}

      <!-- WHERE NEXT -->
      <h2 id="next" class="text-3xl font-bold mt-12 mb-4 flex items-center gap-3">
        <span>🚀</span>Where to Go Next (Phase 11 Candidates)
      </h2>
      <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
        <ol class="space-y-3 list-decimal ml-6 text-slate-800">
          <li><strong>🏠 Multi-tenancy isolation at the storage layer.</strong> RBAC keeps workspaces apart logically; storage-level isolation would stop a noisy tenant from starving another's outbox drain.</li>
          <li><strong>🗳️ External consensus adapter.</strong> Pluggable etcd/Consul backend so distributed locks can be upgraded from "advisory" to "real consensus" without rewriting callers.</li>
          <li><strong>🌍 Cross-region replication.</strong> Log-shipping for the audit chain and outbox, with conflict-resolution policy.</li>
          <li><strong>🌪️ Real-Redis chaos drills.</strong> Today's Sentinel validation is against an in-memory test double — Phase 11 should add a docker-compose Redis+Sentinel cluster to CI.</li>
          <li><strong>👤 DLQ release UX.</strong> Today an operator can release-via-API; UI flow for guided release with justification capture.</li>
          <li><strong>📈 Real-hardware load test.</strong> Today's numbers are FakeRedis on the dev box. A capacity declaration requires production-equivalent runs.</li>
        </ol>
      </div>
      {critique_form('next', 'Phase 11 candidate list')}

      <!-- FINAL CRITIQUE -->
      <h2 id="critique" class="text-3xl font-bold mt-12 mb-4 flex items-center gap-3">
        <span>🎯</span>Overall Verdict — Generate Response
      </h2>
      <div class="bg-white rounded-2xl shadow-sm border border-slate-200 mb-6 p-6">
        <h3 class="font-semibold text-slate-800 mb-2">Anything else? 💭</h3>
        <textarea id="overall-notes"
                  rows="6"
                  placeholder="Overall — what you'd cut, what's unclear, what should be next, what you want explained more..."
                  class="w-full border border-slate-300 rounded-md p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"></textarea>
        <div class="mt-6 flex items-center gap-4">
          <button onclick="generateResponse()"
                  class="bg-indigo-600 hover:bg-indigo-700 text-white font-semibold px-6 py-3 rounded-lg shadow-md transition flex items-center gap-2 text-lg">
            <span>🎯</span> Generate Response for Claude
          </button>
          <span class="text-sm text-slate-500">Click → review compiled response → copy → paste back in chat.</span>
        </div>
      </div>

    </main>
  </div>

  <!-- MODAL -->
  <div id="response-modal" class="fixed inset-0 bg-black/60 z-50 hidden items-center justify-center p-6" style="display:none;">
    <div class="bg-white rounded-2xl shadow-2xl max-w-4xl w-full max-h-[85vh] flex flex-col">
      <div class="p-5 border-b border-slate-200 flex items-center justify-between">
        <h3 class="text-xl font-bold flex items-center gap-2"><span>📨</span>Paste this back to Claude</h3>
        <button onclick="closeModal()" class="text-slate-400 hover:text-slate-700 text-2xl leading-none">×</button>
      </div>
      <div class="p-5 flex-1 overflow-hidden flex flex-col">
        <textarea id="response-output" readonly
                  class="flex-1 border border-slate-300 rounded-lg p-4 font-mono text-sm bg-slate-50 resize-none"
                  style="min-height:300px;"></textarea>
        <div class="mt-4 flex gap-3">
          <button id="copy-btn" onclick="copyResponse()"
                  class="bg-emerald-600 hover:bg-emerald-700 text-white font-semibold px-5 py-2 rounded-lg flex items-center gap-2">
            <span>📋</span><span>Copy to Clipboard</span>
          </button>
          <button onclick="closeModal()"
                  class="bg-slate-200 hover:bg-slate-300 text-slate-800 px-5 py-2 rounded-lg">Close</button>
        </div>
      </div>
    </div>
  </div>

  <script>
    const SECTIONS = {json.dumps([("p" + p.code, p.title) for p in PHASES] + [
        ("architecture", "Four-Layer Architecture"),
        ("stack", "Phase 0-10 stack diagram"),
        ("operator", "Operator quick-start"),
        ("beforeafter", "Before vs After"),
        ("next", "Phase 11 candidates"),
    ])};

    function collectSection(key, label) {{
      const verdictEl = document.querySelector('input[name="verdict-' + key + '"]:checked');
      const ratingEl = document.querySelector('select[name="rating-' + key + '"]');
      const notesEl = document.querySelector('textarea[name="notes-' + key + '"]');
      return {{
        key, label,
        verdict: verdictEl ? verdictEl.value : null,
        rating: ratingEl ? ratingEl.value : '',
        notes: notesEl ? notesEl.value.trim() : ''
      }};
    }}

    function generateResponse() {{
      const lines = [];
      lines.push('# System Tour — Review Response');
      lines.push('_Compiled from in-browser critique form covering Phase 0 through Phase 10._\\n');

      lines.push('## Per-section verdicts');
      let anyAnswered = false;
      SECTIONS.forEach(([key, label]) => {{
        const s = collectSection(key, label);
        if (s.verdict || s.rating || s.notes) {{
          anyAnswered = true;
          lines.push('### ' + label);
          if (s.verdict) lines.push('- Verdict: **' + s.verdict + '**');
          if (s.rating) lines.push('- Rating: ' + s.rating + '/5');
          if (s.notes) lines.push('- Notes: ' + s.notes);
        }}
      }});
      if (!anyAnswered) lines.push('_(No per-section input — see Overall below.)_');

      const overall = document.getElementById('overall-notes').value.trim();
      if (overall) {{
        lines.push('\\n## Overall feedback');
        lines.push(overall);
      }}

      lines.push('\\n---');
      lines.push('Please address the items marked **needs-work** and **cut-it** first; confirm scope/intent on **approved-with-notes**.');

      document.getElementById('response-output').value = lines.join('\\n');
      const modal = document.getElementById('response-modal');
      modal.style.display = 'flex';
      modal.classList.remove('hidden');
    }}

    function closeModal() {{
      const modal = document.getElementById('response-modal');
      modal.style.display = 'none';
      modal.classList.add('hidden');
    }}

    function copyResponse() {{
      const ta = document.getElementById('response-output');
      ta.select();
      ta.setSelectionRange(0, 999999);
      try {{
        navigator.clipboard.writeText(ta.value).then(() => {{
          const btn = document.getElementById('copy-btn');
          btn.innerHTML = '<span>✅</span><span>Copied!</span>';
          setTimeout(() => btn.innerHTML = '<span>📋</span><span>Copy to Clipboard</span>', 2000);
        }});
      }} catch (e) {{
        document.execCommand('copy');
      }}
    }}
  </script>
</body>
</html>""")


# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("🔧 Building system tour HTML report...")

    screenshots: dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()

        print("  📸 Hero...")
        render_screenshot(browser, hero_html(), SHOTS / "hero.png",
                              width=1280, height=820)

        print("  📸 Architecture diagram...")
        render_screenshot(browser, architecture_html(), SHOTS / "architecture.png",
                              width=1280, height=900)

        print("  📸 Stack diagram...")
        render_screenshot(browser, stack_diagram_html(), SHOTS / "stack.png",
                              width=1280, height=900)

        print("  📸 Operator guide...")
        render_screenshot(browser, operator_diagram_html(), SHOTS / "operator.png",
                              width=1280, height=1100)

        print("  📸 Before/After table...")
        render_screenshot(browser, before_after_html(), SHOTS / "beforeafter.png",
                              width=1280, height=900)

        # Per-phase hero shot — reuse purposeful diagrams.
        phase_diagram_map = {
            "0": pipeline_diagram_html(),
            "1": stack_diagram_html(),  # foundation -- show full stack
            "10": hero_html(),
        }

        for ph in PHASES:
            if ph.code in phase_diagram_map:
                src = phase_diagram_map[ph.code]
                fname = f"phase_{ph.code}.png"
                print(f"  📸 P{ph.code}...")
                render_screenshot(browser, src, SHOTS / fname,
                                      width=1280, height=900)
                screenshots[ph.code] = fname
            else:
                # Render a stylized phase card as the hero
                phase_card_html = dedent(f"""<!doctype html>
                <html><head><meta charset="utf-8">
                <script src="https://cdn.tailwindcss.com"></script>
                </head><body class="bg-gradient-to-br from-slate-900 to-indigo-900 p-12 text-white">
                  <div class="max-w-3xl mx-auto">
                    <div class="text-center mb-6">
                      <div class="text-8xl mb-3">{ph.emoji}</div>
                      <div class="text-sm uppercase tracking-widest text-indigo-300">Phase {ph.code}</div>
                      <h1 class="text-4xl font-extrabold mt-2">{html.escape(ph.title.split(" — ", 1)[-1])}</h1>
                      <p class="text-xl text-slate-300 mt-3 italic">{html.escape(ph.one_liner)}</p>
                    </div>
                    <div class="bg-white/5 backdrop-blur rounded-xl p-5 mt-6">
                      <div class="text-xs uppercase tracking-widest text-indigo-300 mb-2">📦 Added in this phase</div>
                      <div class="grid grid-cols-2 gap-2">
                        {"".join(f'<div class="text-sm text-slate-100 truncate">📄 <code class="text-indigo-300">{html.escape(m.split("/")[-1])}</code></div>' for m, _ in ph.modules[:12])}
                      </div>
                      {f'<div class="text-xs text-slate-400 mt-2">…and {len(ph.modules) - 12} more</div>' if len(ph.modules) > 12 else ""}
                    </div>
                    <div class="text-center mt-6 text-xs text-slate-400">
                      🧩 {len(ph.modules)} modules · 🎯 {len(ph.key_concepts)} key concepts
                    </div>
                  </div>
                </body></html>""")
                fname = f"phase_{ph.code}.png"
                print(f"  📸 P{ph.code} card...")
                render_screenshot(browser, phase_card_html, SHOTS / fname,
                                      width=1280, height=820)
                screenshots[ph.code] = fname

        browser.close()

    print("  🧱 Assembling index.html...")
    html_doc = build_html(screenshots)
    out = OUT_DIR / "index.html"
    out.write_text(html_doc, encoding="utf-8")

    print(f"\n✅ Report: {out}")
    print(f"📂 Screenshots: {SHOTS}")

    if sys.platform == "win32":
        os.startfile(str(out))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(out)])
    else:
        subprocess.run(["xdg-open", str(out)])
    print("🚀 Opened in default browser.")


if __name__ == "__main__":
    main()
