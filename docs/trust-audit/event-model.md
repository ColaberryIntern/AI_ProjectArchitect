# Phase 6 — Event Model Audit

**Audit date:** 2026-06-20 · Evidence `path:line`.

## Current state

The repo has **many strong, purpose-built event/record schemas — but no single canonical AI-action event** that unifies user, workflow, agent, decision, cost, and approval in one row.

| Signal | Where | Has correlation_id? | Has cost? | Has confidence? | Has approvalStatus? |
|--------|-------|:---:|:---:|:---:|:---:|
| Audit entry | `config/schemas/ops/audit_entry.schema.json`; `audit_log.py` | ✅ | ❌ | ❌ | partial (approval.* actions) |
| Event fabric | `event_fabric.py` → `output/.../event_fabric/{date}.jsonl` | ✅ | ❌ | ❌ | ❌ |
| Pipeline run | `pipeline_run.schema.json` | ✅ (run_id) | ❌ | ❌ | ❌ |
| Workflow RunRecord | `workflow_runner.py:53-71` | ✅ | **llm_usage tokens** ✅ | ❌ | ❌ |
| Response contract | `response_contract.schema.json` | n/a | ❌ | ❌ | ❌ |
| Reputation/Trust | `reputation_score.schema.json`, `trust_engine.py` | n/a | ❌ | ✅ (trust) | ❌ |
| Approval | `approvals.py` | ✅ | ❌ | ❌ | ✅ |
| Worker logs (A1/A2) | heartbeat/seen JSON | ❌ | ❌ | ✅ (in comment) | ❌ |
| TBI eval | `tbi_compliance.py` audit `tbi.evaluated` | ✅ | ❌ | ❌ | n/a |

**Storage:** append-only JSONL per UTC date is the de-facto event log pattern (`audit_log.py`, `event_fabric.py`). Redis Streams available but optional (`distributed_event_bus.py:78`).

## Missing state

1. **No unified `ai_event`** joining a single AI action across user → workflow → agent → tool → retrieval → decision → cost → approval → outcome.
2. **No cost field anywhere** (tokens live only inside `RunRecord.llm_usage`; not on audit/event rows; absent for the autonomous workers). → biggest gap (Phase 3 Cost = 40).
3. **External side effects** (BC comment, email, webhook) emit no event.
4. **No confidence/evidence** on the generic audit/event row.

## Recommended canonical event schema

A single append-only stream `output/ops_platform/ai_events/{date}.jsonl`, emitted by one helper `ai_events.emit(...)` that **wraps** the existing `audit_log.record` (so today's audit guarantees — immutability, schema validation, correlation replay — carry over). Backwards-compatible: existing schemas keep working; this is the unifying lens the dashboard reads.

```jsonc
{
  "eventId": "uuid",                  // unique
  "timestamp": "ISO-8601 UTC",
  "correlationId": "uuid",            // join across the whole chain (reuses run_id/correlation_id)
  "actor": {                          // who — from identity_context
    "userId": "ali@colaberry.com",
    "type": "human | agent | system",
    "source": "web | mcp | scheduler | webhook",
    "ip": null                        // NEW (User obs gap)
  },
  "workflowId": "advisory|chapter_build|my_day_cb_mention|...",
  "agentId": "agent_runtime_cb_mention_responder | null",
  "action": "blueprint.generated | bc.comment.posted | email.sent | webhook.emitted | capability.run | ...",
  "entity": { "type": "todo|capability|session|...", "id": "..." },
  "decision": {                       // why
    "confidence": 0.0,                // 0..1 (from plan_inference/trust_engine)
    "evidence": ["source/req ids"],   // retrieved sources / requirement ids
    "promptHash": "sha256"            // points to prompt without storing PII
  },
  "tools": { "mcpServers": [], "agents": [], "externalSystems": ["basecamp"] },
  "retrieval": { "sources": [], "citations": [] },
  "cost": {                           // NEW — the top gap
    "model": "gpt-4o-mini",
    "promptTokens": 0, "completionTokens": 0,
    "usd": 0.0                        // computed from a model-price table
  },
  "outcome": "succeeded | failed | denied | suggested | drafted | applied",
  "approvalStatus": "not_required | pending | approved | rejected | bypassed",
  "rollback": { "required": true, "available": true, "plan": "env kill-switch …" },
  "metadata": {}
}
```

### Field provenance (no fabrication — every field already has a source or a clear capture point)

- `actor`, `action`, `entity`, `correlationId`, `approvalStatus` → already in `audit_entry` / approvals.
- `decision.confidence` → plan_inference `confidence_pct`, advisory confidence, trust_engine.
- `tools` → `response_contract.mcp_servers_used/agents_used`.
- `cost` → `response.usage` (already captured in `RunRecord`) + a new `config/model_prices.json` table. **Requires** also emitting usage from the autonomous workers (today they don't).
- `outcome` → workflow/agent_runtime outcomes.

## Migration path (incremental, low-risk)

1. Add `execution/ops_platform/ai_events.py` `emit()` that builds the row and calls `audit_log.record(action=..., metadata=event)` (reuses immutability + replay). Schema `config/schemas/ops/ai_event.schema.json`.
2. Add `config/model_prices.json` + a `cost.compute(usage, model)` helper; emit `cost` on every LLM call (start with `workflow_runner`, then the 4 runtime agents).
3. Instrument the 3 external sends (BC comment / email / webhook) to `emit()` (closes GOV-4 / OBS-1/2 / AI-5).
4. Point the Phase 7 dashboard's Cost/Decision/Audit explorers at this stream.

No existing schema is removed; `ai_event` is the read-optimized union the Trust Command Center consumes.
