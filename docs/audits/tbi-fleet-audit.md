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

## Tier 0 — runtime AI NOT covered by the path-based gate (remediation backlog)

These live in `/execution` as code, not as declarative artifacts under the gated paths,
so the CI gate does not yet attest them. They are the **highest-risk** AI in the system
(prod-facing, autonomous, live). Each needs a runtime-level attestation wired to the
`ops_platform` trust controls in a follow-up batch.

| System | Where | Status | Priority |
|--------|-------|--------|----------|
| My Day `@CB` auto-responder | `execution/` My Day scheduler | Live since 2026-06-14, prod | **P0** |
| My Day auto-pickup / sync / purge | My Day scheduler | Live, prod | **P0** |
| Advisory session pipeline | `execution/advisory` | Live | P1 |
| Productivity report generator | `execution/products/ops/productivity` | Live (07:30 ET weekdays) | P1 |

**Why P0:** these take autonomous actions on live data (posting to Basecamp, picking up
work). Per the TBI thesis, autonomous action is exactly where trust must be proven.

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
- [ ] **P0:** runtime attestations for the My Day auto-responder + auto-pickup (wrap with `agent_registry` autonomy policy + `trust_engine`, add `*.tbi.json` or a runtime registry).
- [ ] **P1:** attest the advisory pipeline and productivity report generator.
- [ ] Close systemic gaps 1–3 (availability signal, glossary, reputation wiring).
- [ ] Re-confirm `persona:karun` / `persona:kes` attestations when their PRDs are ratified (Colaberry-approved).

> Each backlog item is its own approval-gated PR (CLAUDE.md). Until Tier 0 is closed, this
> audit must not be read as "100% of AI is compliant" — it is "100% of *gated declarative*
> AI is compliant; runtime AI attestation is in progress."
