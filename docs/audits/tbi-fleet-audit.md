# TBI Fleet Audit — Trust Before Intelligence Compliance

**Date:** 2026-06-20 · **Auditor:** Claude (orchestration) · **Owner:** Ali Muwwakkil
**Framework snapshot:** `TBI-2025.12.0` (pinned commit `a296fe9`) —
see [trust-before-intelligence.md](../../directives/compliance/trust-before-intelligence.md)
**Gate:** `scripts/tbi_compliance_check.py` · **Scorer:** `execution/ops_platform/tbi_compliance.py`

This is the one-time retroactive audit mandated when TBI compliance became a hard
requirement (see [CLAUDE.md](../../CLAUDE.md)). Every declarative AI artifact has been
inventoried, scored, and given a passing attestation. Runtime AI systems not covered by
the path-based gate are listed in **Tier 0** with a remediation backlog — they are *not*
silently assumed compliant.

## How to read verdicts

- **compliant** — every INPACT/GOALS dimension satisfied.
- **conditional** — passes the gate, but carries justified `n_a`s or a trust caution.
  All current artifacts are `conditional` because design-time/static artifacts legitimately
  mark live-runtime dimensions (Instant, Availability) as not-applicable.
- **non_compliant** — blocks the PR. None remain.

## Scorecard — declarative fleet (gated, attested)

| # | Artifact | Kind | Risk | Verdict | Justified `n_a` | Attestation |
|---|----------|------|------|---------|-----------------|-------------|
| 1 | `agent:project_architect` | agent | LOW | conditional | Instant, Availability | ✅ |
| 2 | `agent:ideation_coach` | agent | LOW | conditional | Instant, Availability | ✅ |
| 3 | `agent:quality_gatekeeper` | agent | LOW | conditional | Instant, Adaptive, Availability | ✅ |
| 4 | `agent:document_assembler` | agent | LOW | conditional | Instant, Natural, Adaptive, Availability | ✅ |
| 5 | `persona:karun` | persona | MODERATE | conditional | Availability | ✅ (PRD DRAFT) |
| 6 | `persona:kes` | persona | MODERATE | conditional | Availability | ✅ (PRD DRAFT) |
| 7 | `blueprint:standard` | blueprint | LOW | conditional | 6× INPACT + 2× GOALS (inert default) | ✅ |
| 8 | `blueprint:autonomous` | blueprint | MODERATE | conditional | Instant | ✅ |
| 9 | `skill_registry` | skill_registry | LOW | conditional | Instant, Natural, Contextual, Availability | ✅ |

**Result:** `python scripts/tbi_compliance_check.py <all 9>` → exit 0, 0 `non_compliant`.

## Tier 0 — runtime AI (code under `/execution`)

These take autonomous action on live data — per the TBI thesis, exactly where trust
must be proven. They are now gated via a committed declaration
([config/tbi_runtime_agents.json](../../config/tbi_runtime_agents.json)): each declared
entrypoint requires a `<entrypoint>.tbi.json` attestation (the CI gate enforces it), and
`execution/ops_platform/runtime_agents.upsert_runtime_agents()` registers each with an
`agent_registry` autonomy policy at deploy (idempotent via `agent_registry.upsert_agent`).

| System | Entrypoint | Policy | Verdict | Status |
|--------|-----------|--------|---------|--------|
| My Day `@CB` auto-responder | `execution/products/ops/cb_mention_worker.py` | `autonomous_low_risk_only` | **compliant** | ✅ P0 done |
| My Day auto-pickup (draft-only) | `execution/products/ops/autopickup_worker.py` | `approval_required` | **conditional** | ✅ P0 done |
| Advisory session pipeline | `execution/advisory/recommendation_engine.py` | `recommend_only` | **compliant** (HIGH risk) | ✅ P1 done* |
| Productivity report generator | `execution/products/ops/productivity/runner.py` | `autonomous_low_risk_only` | **conditional** | ✅ P1 done |

> \*Advisory is compliant on the framework dimensions but carries **HIGH residual risk**: a
> public unauthenticated endpoint that creates Basecamp projects and fires enterprise
> webhooks, with no single kill-switch (only per-subsystem env degradation). Tracked as a
> hardening follow-up below — not a clean bill of health.

> Note: My Day `sync`/`purge` are deterministic data operations (not LLM agents), so they
> are out of scope for per-artifact TBI attestation; their governance is the pipeline's.
> Promotion of auto-pickup to `local-execute` (Phase 2) requires re-attestation at a
> higher risk tier.

## Systemic gaps (apply across the fleet)

1. **Instant / Availability are unmeasured (🔴) — RESOLVED.** Was legitimately `n_a` for
   design-time/static artifacts, but unmeasured for Tier 0 runtime systems. *Done:*
   `trust_center.availability()` derives per-agent health/latency from heartbeats + the app
   health brief (overall %, healthy/stale/down/on_demand); surfaced on `/admin/trust` and
   folded into the runtime trust score's `availability` component.
2. **Lexicon is partial (🟡) — RESOLVED.** Terminology was consistent per-artifact but
   there was no enforced canonical glossary. *Done:* `config/lexicon.json` is the single
   source of truth; the deterministic checker `execution/ops_platform/lexicon.py` (+ CI
   gate `scripts/lexicon_check.py`) scans the AI fleet for forbidden terms (block) and
   drift (warn); a live Lexicon panel is on `/admin/trust`. See
   [lexicon.md](../../directives/compliance/lexicon.md).
3. **Adaptive is not measured per-artifact (🟡) — RESOLVED.** `reputation_scorer` existed but
   was not wired to attestations. *Done:* `trust_center.runtime_trust(agent_id)` derives a
   live 0-100 trust score (availability·reliability·governance·compliance) shown in the agent
   drill-down, so "Adaptive" is evidenced by real signal, not prose.

## Remediation status & backlog

- [x] Vendor the pinned framework; bind the mandate in CLAUDE.md; ship schema + scorer + CI gate + tests.
- [x] Attest all 9 declarative artifacts → gate green.
- [x] **P0:** runtime declaration + attestations for the My Day auto-responder + auto-pickup (`config/tbi_runtime_agents.json`, gate-covered, `agent_registry` policy wired via `runtime_agents.upsert_runtime_agents()`). Deploy step: run the upsert so the registry reflects the declaration.
- [x] **P1:** attest the advisory pipeline and productivity report generator (same runtime-declaration pattern).
- [x] **P1.5 (HIGH, advisory hardening) — CLOSED:** `OPS_ADVISORY_ENABLED` kill-switch on all 8 side-effecting routes + live pause + **per-IP rate limit** (`/start`,`/generate`) + **fail-safe webhook signing** (no default secret). See [advisory-access-review.md](../trust-audit/advisory-access-review.md).
- [x] **Cost observability (was the top gap, 40/100):** real per-call cost ledger (`execution/ops_platform/cost_ledger.py` + `config/model_prices.json`) instrumented at `llm_client.chat` + the 3 ops direct-client sites; Cost explorer live on `/admin/trust`. Forward-only (no backfill).
- [x] **Availability/SLO signal:** `trust_center.availability()` (per-agent + app health, overall %), live on `/admin/trust`. Closes systemic gap #1.
- [x] **Reputation→attestation wiring:** `trust_center.runtime_trust(agent_id)` derived live trust score in the agent drill-down (the Adaptive signal). Closes systemic gap #3.
- [x] **Enforced glossary (Lexicon):** `config/lexicon.json` + `execution/ops_platform/lexicon.py` + `scripts/lexicon_check.py` CI gate + live `/admin/trust` Lexicon panel; fleet scans 0 violations. Closes systemic gap #2. See [lexicon.md](../../directives/compliance/lexicon.md).
- [ ] Re-confirm `persona:karun` / `persona:kes` attestations when their PRDs are ratified (Colaberry-approved).

> Each backlog item is its own approval-gated PR (CLAUDE.md). **Coverage now:** all gated
> declarative artifacts + all four runtime AI agents (P0 + P1) are attested and pass the
> gate; P1.5 advisory hardening CLOSED; cost, availability, reputation→attestation, and
> enforced-lexicon signals all live. **All three systemic gaps are now closed.** The only
> open follow-up is re-confirming the two persona attestations once their PRDs are ratified
> — read this as a hardened, fully-instrumented fleet, not as a frozen "done".
