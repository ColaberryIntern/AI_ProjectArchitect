# Phase 7 — Trust Command Center: Dashboard Design

**Audit date:** 2026-06-20. Design constraint (per directive): **derive every metric from real repository data; placeholders only where a source does not yet exist, and label them.**

## Route & access

- Mount at **`/admin/trust`** (super-admin gated, reusing `app/routers/admin.py:44,57` `_require_admin`/`_require_super_admin`). New router `app/routers/trust_center.py`.
- Read-only. No mutation endpoints in v1 (controls already exist under `/controls/*`).
- JSON twins for every view (`/admin/trust/*.json`) so it's scriptable and testable (mirrors `/admin/cb-mentions.json`).

## Five views

### 1. Executive View
| Tile | Source (real) |
|------|---------------|
| Overall Trust Score (74) + 8-pillar heat | `trust-scorecard.md` values; later computed live |
| AI Activity (runs 24h, by status) | `telemetry.health_summary()`, `prometheus_exporter` `ops_runs_24h_total` |
| Cost (USD 7d) | ⚠️ **placeholder until cost ledger (Phase 6/8 P1)** — shows "not yet instrumented" |
| Revenue / Business Impact | advisory ROI model + productivity report; partial → labeled |
| Compliance Status | TBI attestations: # compliant/conditional/non_compliant from `*.tbi.json` + `tbi_compliance.evaluate_attestation` |

### 2. Operations View
Active workflows, running agents, errors, throughput → `telemetry.latency_stats/failure_trace`, `prometheus_exporter` (`ops_queue_depth`, `ops_workers_total`, `ops_runs_24h_total`), worker heartbeats (`/admin/cb-mentions.json`, autopickup heartbeat, productivity JSON).

### 3. Governance View
Approval queue, violations, overrides, policy exceptions → `approvals.list_*`, `enforcement.denied` audit rows, `controls.list_active()`, agent pause states (`agent_registry.list_agents`).

### 4. Observability View
Workflow traces, agent traces, tool usage, decision history → `pipeline_engine.list_pipeline_runs`, `agent_runtime` executions, `response_contract` tool fields, `audit_log` filtered by correlation_id (replay).

### 5. Business Impact View
Time saved, opportunities, revenue influenced, customer impact → productivity report JSON (`output/ops/_productivity/{date}.json`: hours/dollars saved), advisory impact_model, reputation `business_impact_score`. Partial fields labeled.

## Wireframe (ASCII)

```
┌ /admin/trust ─────────────────────────────────────────────── ali (super-admin) ┐
│ [Executive] [Operations] [Governance] [Observability] [Business]   ⟳ 60s        │
├─────────────────────────────────────────────────────────────────────────────────┤
│  TRUST SCORE            AI ACTIVITY (24h)        COMPLIANCE                        │
│   ┌───────────┐         runs   142  ▇▇▇▇▇▁       attested artifacts  13            │
│   │    74     │         ok     96%             compliant   5   conditional  8     │
│   │  /100 🟡  │         agents  4 live          non-compliant 0  ✅               │
│   └───────────┘         errors  3                                                  │
│  Gov 86 Aud 80 Obs 72 Sec 70 Priv 62 Expl 78 Rel 78 BizImp 68                     │
│                                                                                   │
│  COST (7d)   ⚠ not yet instrumented (cost ledger P1)        REVENUE  (partial)    │
├─────────────────────────────────────────────────────────────────────────────────┤
│  TOP RISKS                              QUICK WINS                                 │
│  1 Cost observability (40) 🔴           1 Enable OPS_ENFORCE_RBAC                  │
│  2 RBAC enforcement opt-in 🟠           2 Audit external sends                     │
│  3 Advisory public endpoint 🟠          3 Advisory kill-switch                     │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Explorer drill-down (Observability tab) — workflow trace by correlation_id:

```
correlation_id 9f3a…   advisory session  · started 14:02:11Z · 1,840ms
 ├ answer.validated   gpt-4o-mini  conf 0.92  120ms
 ├ blueprint.generated gpt-4o-mini  conf 0.78  900ms   tokens 1,240  ($ —)
 ├ lead.captured       —                        ─
 └ webhook.emitted     enterprise   signed      ⚠ not audited (GOV-4)
```

## Seven explorers (v1 scope)

1. **Global Trust Overview** — scorecard + trend (read `trust-scorecard.md` → later live compute).
2. **Workflow Explorer** — `pipeline_engine` + `ai_events` by correlation_id.
3. **Agent Explorer** — runtime agents (`tbi_runtime_agents.json`), `agent_runtime` executions, pause state.
4. **Governance Explorer** — approvals + controls + enforcement denials.
5. **Decision Explorer** — confidence + evidence from `ai_events.decision` (needs Phase 6 emit).
6. **Cost Explorer** — `cost_ledger` (needs Phase 6/8 P1; until then, placeholder + token rollup from `telemetry.token_usage`).
7. **Audit Explorer** — `audit_log.list_entries/replay/stats`.

## Component architecture

```
app/routers/trust_center.py        # 5 HTML views + *.json twins (admin-gated)
templates/trust/*.html             # Jinja2 (reuse admin base template)
execution/ops_platform/trust_center.py   # READ-ONLY aggregator:
  - overview()  -> scorecard + counts        (audit_log.stats, tbi attestations)
  - operations()-> telemetry + heartbeats
  - governance()-> approvals + controls + denials
  - observability()-> pipeline runs + ai_events
  - business()  -> productivity JSON + impact models
  (no new writes; pure reads of existing stores)
tests/.../test_trust_center.py     # asserts aggregator returns real shapes, handles empty
```

The aggregator imports only existing read APIs (`telemetry`, `audit_log`, `trust_engine.trust_report`, `reputation_scorer`, `approvals`, `controls`, `tbi_compliance`) — **no fabricated metrics**; any not-yet-available metric returns `{"status":"not_instrumented","reason":...}` so the UI labels it honestly.

## "Database" design

No SQL DB in this system (Phase 1). The dashboard reads the existing append-only JSONL/JSON stores. The **only new store** is the canonical event stream from Phase 6: `output/ops_platform/ai_events/{date}.jsonl` (schema `config/schemas/ops/ai_event.schema.json`) + `cost_ledger/{date}.jsonl`. Both are append-only files consistent with the current persistence model; if/when scale demands, swap the read layer for Redis/SQL behind the same aggregator API (same pattern `audit_log.py:24-28` already anticipates).

## Build phasing (see gap-analysis P-levels)

- **v1 (read-only, ships on existing data):** Executive + Operations + Governance + Audit explorers. No new instrumentation required.
- **v2 (after Phase 6 emit + cost ledger):** Decision + Cost explorers light up with real data.
- **v3:** live trend computation of the 8 pillars (replace static scorecard values).

Phase 10 implements **v1** (read-only, admin-gated) — pending go-ahead, since it adds a production route.
