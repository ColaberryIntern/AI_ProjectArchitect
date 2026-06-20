# Phase 3 — Observability Audit

**Audit date:** 2026-06-20 · Scores 0-100 with evidence. Methodology: a dimension scores high only where the signal is **captured, persistent, and queryable**; partial/in-memory/scattered signals are scored down.

## Scorecard

| Dimension | Score | State |
|-----------|------:|-------|
| User observability | **80** | Strong |
| Workflow observability | **88** | Mature |
| Agent observability | **65** | Moderate |
| Tool observability | **80** | Good |
| Retrieval observability | **70** | Good |
| Decision observability | **80** | Good |
| **Cost observability** | **40** | **Partial — top gap** |
| **Weighted overall** | **~72** | Good, with one critical gap |

---

## User observability — 80

Can we tell who / when / from where? **Mostly yes.**
- Identity context schema: `config/schemas/ops/identity_context.schema.json` (user_id, email, department, roles, workspace_ids, auth_provider, session_id, issued/expires).
- Every audit row carries `actor{name,email,team,system}` — `audit_log.py:50-67`, `_normalize_actor:189-196`; filterable by actor (`:142`) and aggregated (`stats():166`).
- Auth events audited: `auth.login/logout/failed` (`auth.py:55,69,78`).
- **Gaps:** no source IP / user-agent captured on actions; anonymous bypass when SSO disabled. "From where" is weak.

## Workflow observability — 88

Can we reconstruct path / order / duration? **Yes.**
- `config/schemas/ops/pipeline_run.schema.json`: per-run id, status, initiator, **step_runs[]** (per-step status, started/finished, error), **duration_ms**.
- `pipeline_engine.py` persists runs to `output/ops_platform/pipeline_runs/{id}.json`; `list_pipeline_runs():395`, `get_pipeline_run():383`.
- Workflow runs: `workflow_runner.py` `RunRecord` (run_id, status, llm_usage, response) `:53-71`; emits to realtime bus with `correlation_id` (`:101,255`).
- **Gap:** cross-service single trace view is manual (correlation_id join), no trace UI yet.

## Agent observability — 65

Which agent ran, why, in/out? **Partial.**
- `agent_runtime.py` records every execution (outcome SUGGESTED/APPROVAL_REQUIRED/APPLIED/DENIED/PAUSED/ERROR) to `agent_executions/{id}.json` + audit (`:188`). Revoke audited (`:203`).
- Agents declared in `response_contract` (`agents_used`) and scored by `reputation_scorer` signals.
- **Gaps:** the **live My Day workers (A1/A2) do not route through `agent_runtime.execute()`** — they post directly (`cb_mention_worker._post_comment`), so their actions are in worker-local heartbeat/seen files, **not** the central agent-execution/audit log. Agent *reasoning* (inputs/why) not consistently persisted. → **OBS-1**.

## Tool observability — 80

Tools/APIs/external systems touched? **Yes, by contract.**
- `config/schemas/ops/response_contract.schema.json` requires `mcp_servers_used`, `agents_used`, `routes_added`, `database_changes`, `dependencies_added`, `known_issues` (severity-ranked).
- `telemetry.dependency_health():172-198` reports declared-vs-used MCP/agents.
- **Gap:** contract captures tools for *ops-platform capability runs*; ad-hoc external calls (BC API in workers, Mandrill send, webhook emit) are not contract-tracked. → **OBS-2**.

## Retrieval observability — 70

Docs retrieved / sources / citations? **Yes for the build pipeline.**
- `citation_injector.py` tracks `already_cited / injected / unmatched` requirement IDs (`InjectionReport:33-41`), idempotent.
- `semantic_enrichment.schema.json` records capability_similarity, preceding/following workflows, execution_dependencies (provenance graph).
- **Gaps:** no embeddings, so retrieval is keyword; **no log of which documents/snippets entered an LLM context window** for advisory/ops calls. → **OBS-3**.

## Decision observability — 80

Why / confidence / evidence? **Yes.**
- `trust_engine.py` `TrustProfile`: trust_score, risk_level, **confidence (0-1)**, **components{}** (9 explainable sub-scores) `:41-67`; deployment_recommendation.
- `verification_agent.py` two-tier (structural always + semantic) with deployment_readiness green/yellow/red.
- AI decisions surface confidence: plan_inference `confidence_pct`, advisory system-confidence + 5 maturity dims, autopickup confidence calibration.
- **Gap:** the *prompt + evidence* behind a given LLM decision isn't persisted alongside the decision (only the structured output). → **OBS-4**.

## Cost observability — 40 (TOP GAP)

Token usage / API cost / per-workflow / per-user? **Partial, not auditable.**
- **Captured:** `RunRecord.llm_usage = response.usage` (`workflow_runner.py:189`); `auto_builder.BuildMetrics` sums prompt/completion tokens and has `estimate_cost()` with hardcoded gpt-4o-mini rates (`:270-335`); `telemetry.token_usage():147-169` rolls up per-capability prompt tokens.
- **Missing:** no persistent **cost ledger** (no `cost_ledger`/`billing` schema in `config/schemas/ops/`); **no per-user / per-workspace allocation**; **no cost on the autonomous workers** (A1/A2/A3 LLM calls don't record usage centrally); hardcoded single-model pricing; Prometheus exporter exposes **no** cost metric.
- Net: you can estimate one batch build's cost locally, but you **cannot answer "what did AI cost last week, by user/workflow?"** from durable data. → **OBS-5 (P1)**.

## What already exists to build a dashboard on (no fabrication needed)

- `execution/ops_platform/telemetry.py` — health_summary, latency_stats, failure_trace, token_usage, dependency_health.
- `execution/ops_platform/prometheus_exporter.py` — queue depth, workers, runs_24h, incidents, alerts, approvals_pending, experiments, active_controls, capability_total, audit_events_24h (`/system/metrics`).
- `audit_log.stats()`, `trust_engine.trust_report()`, `reputation_scorer`, `governance_scorecards.py`, `compliance_reports.py`, worker heartbeats, TBI attestations.

## Recommendations (→ gap-analysis, dashboard-design, event-model)

1. **Cost ledger (P1):** append-only `output/ops_platform/cost_ledger/{date}.jsonl` written at every LLM call (model, prompt/completion tokens, computed $, user, workflow, correlation_id); expose `/admin/trust` Cost Explorer.
2. **Route worker actions through the audited path (P1):** wrap BC-comment/email/webhook sends with an `audit_log.record(action="bc.comment.posted"/"email.sent"/"webhook.emitted", …)` (OBS-1/OBS-2/AI-5).
3. **Persist decision context (P2):** store prompt hash + retrieved-source ids with each LLM decision (OBS-3/OBS-4).
4. Adopt the canonical event model (Phase 6) so all signals share `correlation_id`, `cost`, `confidence`, `approvalStatus`.
