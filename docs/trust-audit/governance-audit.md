# Phase 4 — Governance Audit

**Audit date:** 2026-06-20 · Evidence `path:line`.

## Where AI can act, and how it's governed

| Action class | Where | Autonomous? | Human review enforceable? | Auditable? | Rollback? |
|--------------|-------|-------------|---------------------------|------------|-----------|
| Create content (blueprints, chapters, drafts) | advisory, chapter_writer, autopickup | recommend/draft only | yes (quality gates, approve-to-execute) | partial | n/a (drafts) |
| Send communication (BC comment) | `cb_mention_worker._post_comment`, `bc_comments.post` | **yes** (A1) | circuit breaker + polling kill-switch, not per-post approval | **gap** (worker-local, not central audit) | env kill-switch; can't un-post |
| Send communication (email) | `productivity/delivery.send_report` | yes, **off by default** | env flag gate `PRODUCTIVITY_REPORT_DELIVERY` | **gap** (no `email.sent` audit) | flag off |
| Modify records (capabilities/versions) | `capability_versions.py`, `controls.py` | no (approval-gated) | **yes** (approvals state machine) | **yes** | **yes** (`emergency_rollback:122`) |
| Make recommendations | advisory, ops recommendation_engine | yes (advice) | n/a | partial | n/a |
| Trigger workflows | `workflow_runner`, `pipeline_engine`, scheduler | scheduled/agentic | controls freeze/quarantine | yes | freeze/abort |
| Call external systems | BC, Mandrill, webhook, Google | yes | per-integration env gates | **gap** for BC/email/webhook | env kill-switches |

## Approvals (LEVEL 4)

`execution/ops_platform/approvals.py` — state machine `pending→in_progress→approved→executed | rejected | expired | cancelled` (`:9-14`). Kinds: single / quorum (N-of-M) / multi_stage (`:3-7`). Evidence:
- `request_approval():97-142`, `submit_decision():145-197` (role-checked `:164`, quorum auto-advance `:188`).
- `mark_executed():209-226` prevents reuse; decision hashing for tamper-evidence (`:274-280`); every transition audited (`:291-305`).
- TTL expiry via `expire_stale():258-268`.
- **Gap GOV-1:** requester can `cancel` before execution (`:200-206`) with no second sign-off (intentional abort, but a bypass surface); no escalation on TTL.

## Agent autonomy (LEVEL 4)

`agent_registry.py` policies (`:32-37`): `recommend_only`, `approval_required`, `autonomous_low_risk_only`, `autonomous_full`. Runtime enforcement `agent_runtime.py:80-190` (6 gates: paused → permitted_actions → confidence≥threshold → rollback-plan-present → policy → maintenance/freeze). `pause/resume` human override (`agent_registry.py:146-175`), idempotent `upsert_agent` preserves pause (`:97-143`).
- **Gap GOV-2:** the **live My Day workers don't execute through `agent_runtime`** — the declared policies in `config/tbi_runtime_agents.json` are documented intent, enforced operationally (circuit breaker, allowlist, env flags) rather than by the runtime gate. Wiring workers through `agent_runtime.execute()` would make the policy gate authoritative.

## Controls (LEVEL 4)

`controls.py` — freeze, quarantine, workspace_suspend, maintenance_mode, rate_limit (`:42`); `is_blocked()` checked pre-run; `emergency_rollback()` freeze→rollback→unfreeze (`:122-146`); endpoints `/controls/freeze|unfreeze|quarantine|emergency-rollback` (`ops_platform.py:1272-1311`). All audited.

## RBAC (LEVEL 3 — enforcement opt-in)

`rbac.py:19-54` — 5 roles (admin/operator/reviewer/builder/viewer) × 16 perms; `enforcement.enforce()` single check point (`enforcement.py:21-50`) audits `enforcement.denied` (`:77`).
- **Gap GOV-3 (HIGH):** `OPS_ENFORCE_RBAC` defaults off → anonymous bypass (`rbac.py:57-61`). Must be `true` in prod.

## Auditability (LEVEL 3)

Append-only immutable JSONL (`audit_log.py:13,222-230`), schema-validated (`audit_entry.schema.json`), correlation-id replay (`:161`). 70+ `record()` call sites across approvals, agent_registry, agent_runtime, controls, enforcement, auth, capability_versions, tbi_compliance.
- **Gap GOV-4 (P1):** external-system mutations (BC comment post, email send, webhook emit) are **not** in the central audit log — only worker heartbeats/seen files. The audit shows *intent inside the platform* but not *the external side effect*.

## Rollback (LEVEL 3)

Version promote/rollback audited; `emergency_rollback` atomic; `agent_runtime.revoke()` audited (`:193-215`).
- **Gap GOV-5:** mechanics for some external actions (un-post a BC comment) are operator-driven, not automated; rollback records *that* it happened.

## TBI compliance gate (LEVEL 5)

Mandatory CI gate (`scripts/tbi_compliance_check.py` + `.github/workflows/tbi-compliance-check.yml`), deterministic scorer (`tbi_compliance.py`, no LLM), vendored pinned framework, per-artifact attestation, fork-proof re-attestation on version bump. CLAUDE.md makes it non-negotiable. **This is the strongest control in the system.**

## Governance maturity by component

| Component | Level (0-5) | Basis |
|-----------|:-----------:|-------|
| TBI compliance gate | **5** | mandatory, deterministic, CI-enforced |
| Approvals | 4 | enforced state machine, tamper-evident |
| Agent autonomy | 4 | policies + 6-gate runtime (workers not yet wired → GOV-2) |
| Controls | 4 | freeze/quarantine/rollback, audited |
| Trust engine | 4 | 9-component, deployment recs |
| Auditability | 3 | immutable but external mutations uncovered (GOV-4) |
| RBAC | 3 | complete but enforcement opt-in (GOV-3) |
| Rollback | 3 | audited; semi-manual mechanics |
| Secrets/Vault | 3 | AES-GCM + gitignored env; dev-fallback master key |

## Overall governance maturity: **LEVEL 4 — TRUSTED (with conditions)**

Rationale: the *design* is enterprise-grade (LEVEL 4-5 controls exist and are mostly enforced). It is held below a clean LEVEL 4-across-the-board by three enforcement/coverage gaps that are environment- or wiring-dependent, not architectural: **GOV-3** (RBAC opt-in), **GOV-2** (workers bypass the runtime gate), **GOV-4** (external-mutation audit). All three are addressable without redesign (see `gap-analysis.md`).
