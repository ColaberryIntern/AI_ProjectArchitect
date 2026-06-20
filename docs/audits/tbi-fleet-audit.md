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

1. **Instant / Availability are unmeasured (🔴).** Legitimately `n_a` for design-time and
   static artifacts, but for Tier 0 runtime systems they must become real signals.
   *Remediation:* add a health/latency signal to `ops_platform` and feed it into
   `trust_engine` so runtime attestations can set `trust_score_ref` and be gated on it.
2. **Lexicon is partial (🟡).** Terminology is consistent per-artifact but there is no
   enforced canonical glossary. *Remediation:* add a glossary the semantic layer checks.
3. **Adaptive is not measured per-artifact (🟡).** `reputation_scorer` exists but is not
   wired to attestations. *Remediation:* for runtime capabilities, link `trust_score_ref`
   to the reputation/trust profile so "Adaptive" is evidenced by real signal, not prose.

## Remediation status & backlog

- [x] Vendor the pinned framework; bind the mandate in CLAUDE.md; ship schema + scorer + CI gate + tests.
- [x] Attest all 9 declarative artifacts → gate green.
- [x] **P0:** runtime declaration + attestations for the My Day auto-responder + auto-pickup (`config/tbi_runtime_agents.json`, gate-covered, `agent_registry` policy wired via `runtime_agents.upsert_runtime_agents()`). Deploy step: run the upsert so the registry reflects the declaration.
- [x] **P1:** attest the advisory pipeline and productivity report generator (same runtime-declaration pattern).
- [ ] **P1.5 (HIGH, advisory hardening):** add an `OPS_ADVISORY_ENABLED` kill-switch + access review for the public advisory endpoint (creates BC projects + fires webhooks unauthenticated).
- [ ] Close systemic gaps 1–3 (availability signal, glossary, reputation wiring).
- [ ] Re-confirm `persona:karun` / `persona:kes` attestations when their PRDs are ratified (Colaberry-approved).

> Each backlog item is its own approval-gated PR (CLAUDE.md). **Coverage now:** all gated
> declarative artifacts + all four runtime AI agents (P0 + P1) are attested and pass the
> gate. Remaining: the advisory HIGH-risk hardening (P1.5) and the systemic gaps. Do not
> read this as "100% trusted" until P1.5 + the systemic gaps close.
