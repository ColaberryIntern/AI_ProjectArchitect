# TRUST COMPLIANCE REPORT — Trust Before Intelligence Audit

**Repository:** AI Project Architect & Build Companion
**Audit date:** 2026-06-20 · **Framework:** TBI-2025.12.0 (pinned `a296fe9`)
**Method:** read-only inspection, 4 parallel evidence sweeps, claims verified against source. Full evidence in the sibling `docs/trust-audit/*.md`.

---

## Executive summary

This is a **mature, governance-first AI platform** — unusually so. It already implements the controls most AI systems lack: an immutable audit log, an enforced approval state machine, agent autonomy policies with a 6-gate runtime, operational controls (freeze/quarantine/rollback), a 9-component trust engine, and a **mandatory, CI-enforced Trust-Before-Intelligence compliance gate** (the strongest single control). All four production runtime AI agents are attested.

The gaps that remain are **not architectural** — they are **enforcement-by-default, coverage, and visibility** gaps that are fixable with wiring and config:
1. **Cost is the weakest signal (40/100)** — tokens are captured but there's no durable cost ledger, so "what did AI cost, by whom?" is unanswerable.
2. **RBAC enforcement is opt-in** (`OPS_ENFORCE_RBAC` default off).
3. **External side effects** (Basecamp comments, emails, webhooks) aren't in the central audit log.
4. **The advisory funnel is a public, unauthenticated endpoint** with downstream side effects and no kill-switch.

**Bottom line:** the system *can* be trusted, governed, and audited today for internal/controlled use; it is **not yet "trust-visible"** to executives/regulators because there is no Trust Command Center and no cost economics. Both are buildable on data that already exists.

> **Correction to a sub-agent claim:** an evidence sweep reported a committed secret in `.env`. **Verified false** — `.env` is git-ignored (`.gitignore`) and untracked (`git ls-files .env` → empty). Secrets are env-based and not in the repo. (A real, lesser finding stands: a weak *default* `ENTERPRISE_WEBHOOK_SECRET` fallback in code.)

---

## Scores

| Dimension | Score | Band |
|-----------|------:|------|
| **Overall Trust** | **74 / 100** | 🟡 Trusted-with-conditions |
| Governance | 86 | 🟢 |
| Auditability | 80 | 🟢 |
| Observability | 72 | 🟡 |
| Explainability | 78 | 🟡 |
| Reliability | 78 | 🟡 |
| Security | 70 | 🟡 |
| Business Impact | 68 | 🟡 |
| Privacy | 62 | 🟡 |

**Governance maturity: LEVEL 4 — TRUSTED (with conditions).** **TBI compliance sub-control: LEVEL 5.**

Observability sub-signals: Workflow 88 · User 80 · Tool 80 · Decision 80 · Retrieval 70 · Agent 65 · **Cost 40**.

---

## Critical findings

| # | Finding | Severity | Evidence |
|---|---------|:--------:|----------|
| C1 | RBAC enforcement opt-in (anonymous bypass when `OPS_ENFORCE_RBAC` unset) | **P0** | `rbac.py:57-61` |
| C2 | Vault master key dev-fallback; vault stored under `output/` | **P0** | `vault.py:62-81` |
| C3 | External AI side effects (BC comment/email/webhook) not centrally audited | **P1** | `cb_mention_worker._post_comment`, `productivity/delivery.py`, `enterprise_sync.py` |
| C4 | No durable cost ledger / per-user cost allocation | **P1** | Phase 3 Cost=40; tokens only in `RunRecord.llm_usage` |
| C5 | Advisory: public unauthenticated endpoint with downstream side effects, no kill-switch | **P1** | `recommendation_engine.py.tbi.json` (risk HIGH) |
| C6 | Live workers bypass `agent_runtime` policy gate | **P1** | declared in `tbi_runtime_agents.json`, enforced operationally not in-code |

No CRITICAL-severity AI (no fully-autonomous irreversible-action capability); all autonomous writes are low-risk comments/emails, gated by circuit breakers, allowlists, and off-by-default flags.

---

## Top 10 risks

1. 🔴 Cost blindness — no cost governance/chargeback (C4).
2. 🟠 RBAC bypass in any env missing `OPS_ENFORCE_RBAC=true` (C1).
3. 🟠 Advisory public endpoint abuse / runaway side effects (C5).
4. 🟠 Unprovable external AI actions — audit gap (C3).
5. 🟠 Vault key fallback / vault on disk (C2).
6. 🟡 Declared agent autonomy not enforced by code path (C6).
7. 🟡 Weak default webhook secret (`enterprise_sync.py:22-24`).
8. 🟡 No PII retention/deletion policy; unmasked email in audit.
9. 🟡 Process-local rate limit bypass on multi-worker (`controls.py:264-279`).
10. 🟡 Decision prompt/evidence not persisted → weaker explainability/repro.

## Top 10 quick wins (≤ ~1 day each)

1. Set `OPS_ENFORCE_RBAC=true` in prod + startup assertion (C1).
2. Require `LIBRARY_VAULT_MASTER_KEY`; refuse to boot without it (C2).
3. Remove the `ENTERPRISE_WEBHOOK_SECRET` default; require env.
4. Add `OPS_ADVISORY_ENABLED` kill-switch (C5 / P1.5).
5. `audit_log.record("bc.comment.posted"/"email.sent"/"webhook.emitted")` at the 3 send sites (C3).
6. Add `config/model_prices.json` + `cost.compute(usage, model)` helper.
7. Emit `llm_usage` from the 4 runtime agents (today they don't).
8. Mask email in audit actor (privacy).
9. Add `PROMPT_VERSION` constant + cache-bust on change (AI-2).
10. Ship `/admin/trust` v1 read-only on existing data (DASH-1, see Phase 7/10).

---

## Recommended roadmap

### 30-day plan — "Harden + make trust visible"
- **P0:** RBAC enforcement default-on in prod; vault master key required + relocate off `output/` (C1, C2).
- **P1 start:** canonical `ai_events.emit()` + `ai_event` schema (EVT-1); cost ledger + model price table (C4); audit the 3 external sends (C3).
- Advisory kill-switch + access review (C5).
- Ship **Trust Command Center v1** (read-only: Executive/Operations/Governance/Audit) on existing data.

### 60-day plan — "Enforce + economize"
- Route runtime workers through `agent_runtime.execute()` so autonomy policies are code-enforced (C6).
- Cost + Decision explorers live (depend on EVT-1 + cost ledger).
- PII retention/deletion policy + email masking (PRIV-1); distributed rate limit on Redis (REL-1).

### 90-day plan — "Trust at scale"
- Live computation of the 8 trust pillars (replace static scorecard) + trend lines.
- Persist decision prompt-hash + evidence (OBS-4); prompt versioning (AI-2).
- Decide model strategy (OpenAI vs Claude per CLAUDE.md guidance, AI-1); optional OpenTelemetry (OBS-6).
- Quarterly re-attestation + re-audit against this control matrix.

---

## Executive recommendation: **GO WITH CONDITIONS**

The platform is safe to operate for internal and controlled-external use today, on the strength of its governance and audit controls and the TBI gate. Proceed to broader/production use **conditioned on the two P0 items and the advisory kill-switch**:

**Conditions (must, before broad prod):**
1. `OPS_ENFORCE_RBAC=true` enforced in production (C1).
2. Vault master key provisioned from secrets manager; vault relocated off `output/` (C2).
3. Advisory `OPS_ADVISORY_ENABLED` kill-switch + access review (C5).

**Strongly recommended (P1, within 30-60 days):** external-send audit (C3), cost ledger (C4), Trust Command Center v1, route workers through `agent_runtime` (C6).

Not a NO-GO: there are no architectural defects and no unmitigated critical autonomous-action risks. Not an unconditional GO: the P0 enforcement/secret items and the public advisory endpoint must be closed first.

---

## FINAL OUTPUT (directive deliverables)

1. **Repository Trust Score:** 74/100 (🟡 Trusted-with-conditions).
2. **Governance Score:** 86 (maturity LEVEL 4; TBI sub-control LEVEL 5).
3. **Observability Score:** 72 (Cost dimension 40 = top gap).
4. **Auditability Score:** 80 (immutable log; external-mutation coverage gap).
5. **Compliance Score (TBI):** all AI artifacts attested & passing the gate — 0 non-compliant; advisory carries a tracked HIGH residual.
6. **Executive Summary:** above.
7. **Trust Dashboard Design:** `dashboard-design.md` (5 views, 7 explorers, components, data sources, build phasing).
8. **Missing Capabilities:** `gap-analysis.md` (16 gaps, P0-P3).
9. **Prioritized Roadmap:** 30/60/90 above.
10. **TBI Maturity Level:** **LEVEL 4 — TRUSTED (with conditions)**.

**Evidence:** every finding cites `path:line` in the phase docs; the one unverifiable sub-agent claim (committed secret) was checked and refuted. No metric in the dashboard design is fabricated — each maps to an existing read API or is explicitly labeled "not yet instrumented."
