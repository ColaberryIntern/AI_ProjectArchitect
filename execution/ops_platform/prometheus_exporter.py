"""Prometheus-compatible metrics exporter — text format, no extra deps.

Renders a single string that Prometheus can scrape from
``GET /ops/metrics/prometheus``. Metrics are computed on-demand from the
platform's existing read-side modules; nothing is cached.

Exposed metric families:
  ops_queue_depth{queue=...}           — runtime_queue total/by-status
  ops_workers_total{status=...}        — worker_coordination
  ops_runs_24h_total{status=...}       — workflow_runner.list_runs
  ops_incidents_open                   — incidents.list_incidents
  ops_alerts_open                      — alerts.list_active
  ops_approvals_pending                — approvals.list_requests
  ops_experiments_running              — experiments.list_experiments
  ops_active_controls                  — controls.list_active
  ops_capability_total                 — registry size
  ops_audit_events_24h                 — audit_log.stats
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from execution.ops_platform import (
    alerts, approvals, audit_log, controls, experiments,
    incidents, runtime_queue, worker_coordination, workflow_runner,
)
from execution.ops_platform.capability_registry import default_registry


def render() -> str:
    lines: list[str] = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    # Queue depth
    qd = runtime_queue.queue_depth(queue="default")
    lines.append("# HELP ops_queue_depth Number of jobs in the default queue by status")
    lines.append("# TYPE ops_queue_depth gauge")
    for status, n in (qd.get("counts") or {}).items():
        lines.append(f'ops_queue_depth{{queue="default",status="{status}"}} {n}')

    # Workers
    workers = worker_coordination.list_workers()
    by_status: Counter = Counter(w.status for w in workers)
    lines.append("# HELP ops_workers_total Worker count by status")
    lines.append("# TYPE ops_workers_total gauge")
    for status, n in by_status.items():
        lines.append(f'ops_workers_total{{status="{status}"}} {n}')

    # Runs in last 24h
    runs = workflow_runner.list_runs(limit=5000)
    recent = []
    for r in runs:
        try:
            ts = datetime.fromisoformat(r.started_at)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            recent.append(r)
    by_run_status: Counter = Counter(r.status for r in recent)
    lines.append("# HELP ops_runs_24h_total Workflow runs in the last 24 hours by status")
    lines.append("# TYPE ops_runs_24h_total counter")
    for status, n in by_run_status.items():
        lines.append(f'ops_runs_24h_total{{status="{status}"}} {n}')

    # Open incidents
    open_incidents = incidents.list_incidents(state="open")
    lines.append("# HELP ops_incidents_open Open incidents")
    lines.append("# TYPE ops_incidents_open gauge")
    lines.append(f"ops_incidents_open {len(open_incidents)}")

    # Active alerts
    active_alerts = alerts.list_active()
    lines.append("# HELP ops_alerts_open Active alerts")
    lines.append("# TYPE ops_alerts_open gauge")
    lines.append(f"ops_alerts_open {len(active_alerts)}")

    # Pending approvals
    pending_appr = approvals.list_requests(state="pending")
    in_progress_appr = approvals.list_requests(state="in_progress")
    lines.append("# HELP ops_approvals_pending Pending or in-progress approvals")
    lines.append("# TYPE ops_approvals_pending gauge")
    lines.append(f"ops_approvals_pending {len(pending_appr) + len(in_progress_appr)}")

    # Running experiments
    running_exp = experiments.list_experiments(state="running")
    lines.append("# HELP ops_experiments_running Experiments in the running state")
    lines.append("# TYPE ops_experiments_running gauge")
    lines.append(f"ops_experiments_running {len(running_exp)}")

    # Active controls
    active_ctrls = controls.list_active()
    lines.append("# HELP ops_active_controls Active operational controls")
    lines.append("# TYPE ops_active_controls gauge")
    by_kind: Counter = Counter(c.kind for c in active_ctrls)
    for kind, n in by_kind.items():
        lines.append(f'ops_active_controls{{kind="{kind}"}} {n}')

    # Capability count
    snap = default_registry().snapshot()
    lines.append("# HELP ops_capability_total Registered capabilities")
    lines.append("# TYPE ops_capability_total gauge")
    lines.append(f"ops_capability_total {len(snap.capabilities)}")

    # Audit events 24h
    audit_stats = audit_log.stats(days=1)
    lines.append("# HELP ops_audit_events_24h Audit events in the last 24 hours")
    lines.append("# TYPE ops_audit_events_24h counter")
    lines.append(f"ops_audit_events_24h {audit_stats.get('total', 0)}")

    # ── Phase 9G: coordination metrics ──
    from execution.ops_platform import (
        coordination_diagnostics, distributed_lock, orchestration_runtime,
        redis_backends, ws_gateway,
    )
    coordination_mode = orchestration_runtime.coordination_mode()
    lines.append("# HELP ops_coordination_scope 1 when redis-backed, 0 single-host")
    lines.append("# TYPE ops_coordination_scope gauge")
    lines.append(f"ops_coordination_scope {1 if coordination_mode.get('fencing_tokens_enabled') else 0}")

    active_claims = orchestration_runtime.list_active_claims()
    lines.append("# HELP ops_active_step_claims Active distributed step claims")
    lines.append("# TYPE ops_active_step_claims gauge")
    lines.append(f"ops_active_step_claims {len(active_claims)}")

    lines.append("# HELP ops_file_locks_held File-based distributed lock count")
    lines.append("# TYPE ops_file_locks_held gauge")
    lines.append(f"ops_file_locks_held {len(distributed_lock.list_active())}")

    ws_mode = ws_gateway.mode()
    lines.append("# HELP ops_ws_redis_pubsub_active 1 when WS uses Redis pub/sub fanout")
    lines.append("# TYPE ops_ws_redis_pubsub_active gauge")
    lines.append(f"ops_ws_redis_pubsub_active {1 if ws_mode.get('redis_client_wired') else 0}")

    lines.append("# HELP ops_redis_client_wired 1 when redis_backends has a wired client")
    lines.append("# TYPE ops_redis_client_wired gauge")
    lines.append(f"ops_redis_client_wired {1 if redis_backends._CLIENT is not None else 0}")

    # Replay backlog
    backlog = coordination_diagnostics.replay_backlog()
    lines.append("# HELP ops_replay_backlog_queue Replay backlog queue depth")
    lines.append("# TYPE ops_replay_backlog_queue gauge")
    lines.append(f"ops_replay_backlog_queue {backlog.get('queue_total', 0)}")
    lines.append("# HELP ops_active_orchestrations Active orchestrations")
    lines.append("# TYPE ops_active_orchestrations gauge")
    lines.append(f"ops_active_orchestrations {backlog.get('active_orchestrations', 0)}")

    # Orphan orchestrations
    orphans = coordination_diagnostics.orphan_orchestrations(age_minutes=30)
    lines.append("# HELP ops_orphan_orchestrations Orchestrations stuck >30min")
    lines.append("# TYPE ops_orphan_orchestrations gauge")
    lines.append(f"ops_orphan_orchestrations {len(orphans)}")

    return "\n".join(lines) + "\n"
