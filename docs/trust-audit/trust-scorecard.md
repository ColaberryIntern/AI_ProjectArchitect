# Phase 5 — Trust Scorecard

**Audit date:** 2026-06-20 · Scores 0-100, evidence-based (see Phases 1-4). Overall = simple mean of the 8 pillars.

## Scores

| # | Pillar | Score | Band |
|---|--------|------:|------|
| 1 | Security | 70 | 🟡 Amber |
| 2 | Privacy | 62 | 🟡 Amber |
| 3 | Observability | 72 | 🟡 Amber |
| 4 | Governance | 86 | 🟢 Green |
| 5 | Auditability | 80 | 🟢 Green |
| 6 | Explainability | 78 | 🟡 Amber |
| 7 | Reliability | 78 | 🟡 Amber |
| 8 | Business Impact | 68 | 🟡 Amber |
| — | **Overall Trust Score** | **74** | 🟡 **Amber (Trusted-with-conditions)** |

## Heat map

```
PILLAR            0────25────50────75───100
Governance        ███████████████████░  86 🟢
Auditability      ████████████████░░░░  80 🟢
Explainability    ███████████████░░░░░  78 🟡
Reliability       ███████████████░░░░░  78 🟡
Observability     ██████████████░░░░░░  72 🟡
Security          ██████████████░░░░░░  70 🟡
Business Impact   █████████████░░░░░░░  68 🟡
Privacy           ████████████░░░░░░░░  62 🟡   ← lowest
OVERALL           ██████████████░░░░░░  74 🟡
```

Sub-signal heat (observability dimensions, from Phase 3):

```
Workflow   ████████████████░░  88 🟢
User       ████████████████░░  80 🟢
Tool       ████████████████░░  80 🟢
Decision   ████████████████░░  80 🟢
Retrieval  ██████████████░░░░  70 🟡
Agent      █████████████░░░░░  65 🟡
Cost       ████████░░░░░░░░░░  40 🔴   ← highest-risk single signal
```

## Per-pillar rationale + top deduction

| Pillar | Why this score | Biggest deduction |
|--------|----------------|-------------------|
| **Security 70** | env secrets, `.env` gitignored (verified), CI secret scan, AES-GCM vault | RBAC enforcement opt-in (GOV-3); vault in `output/` + dev-fallback master key; weak default webhook secret |
| **Privacy 62** | PII user-scoped; BC data account-isolated | unmasked email in audit; **no retention/deletion policy**; public advisory lead capture |
| **Observability 72** | mature audit/workflow/telemetry/Prometheus | **Cost = 40** (no ledger); agent/worker actions partially uncovered |
| **Governance 86** | approvals + autonomy + controls + TBI gate (LEVEL 5) | RBAC opt-in; workers bypass runtime gate (GOV-2) |
| **Auditability 80** | append-only immutable, correlation replay | external mutations not centrally audited (GOV-4) |
| **Explainability 78** | trust_engine components, confidence, verification two-tier, citations | prompt/evidence behind a decision not persisted (OBS-4) |
| **Reliability 78** | trust scoring, universal fallbacks, idempotency, circuit breakers | process-local rate limit; semi-manual rollback |
| **Business Impact 68** | productivity report quantifies AI leverage; advisory ROI model; reputation business_impact | no cost-per-value; no unified business KPI surface |

## Highest-risk areas (ranked)

1. 🔴 **Cost observability (40)** — cannot answer "what did AI cost, by whom?" → blocks chargeback/governance economics.
2. 🟠 **RBAC enforcement opt-in (GOV-3)** — prod must set `OPS_ENFORCE_RBAC=true`; otherwise anonymous bypass.
3. 🟠 **Advisory public endpoint (HIGH)** — unauthenticated, creates BC projects + fires webhooks, no kill-switch (P1.5).
4. 🟠 **External-mutation audit gap (GOV-4)** — BC comment / email / webhook sends absent from central audit.
5. 🟡 **Vault hardening** — move off `output/`, require master key, remove dev fallback in prod.
6. 🟡 **Privacy** — define PII retention + deletion; mask email in audit.

## Trend note

The TBI compliance program (PRs #64-#66) already moved Governance/Auditability into Green and put all four runtime AI agents under attestation. Closing Cost + RBAC-enforcement + external-audit would lift Overall from 74 → ~85.
