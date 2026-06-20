# Phase 8 — Implementation Gap Analysis

**Audit date:** 2026-06-20. Effort: S (<1d) / M (1-3d) / L (1-2wk). Priority: P0 (blocker) · P1 (high) · P2 (medium) · P3 (low).

## Prioritized gaps

| ID | Gap | Current | Target | Effort | Complexity | Risk if unaddressed | Biz value | Priority |
|----|-----|---------|--------|:---:|:---:|---------------------|-----------|:---:|
| GOV-3 | RBAC enforcement opt-in | `OPS_ENFORCE_RBAC` default off → anonymous bypass (`rbac.py:57-61`) | Required `true` in prod + startup assertion | S | Low | Unauthorized ops actions | Access integrity | **P0** |
| SEC-1 | Vault master key dev-fallback | dev SHA fallback if `LIBRARY_VAULT_MASTER_KEY` unset (`vault.py:62-81`) | Require key in prod; refuse start without it; move vault off `output/` | M | Med | Credential exposure | Secret safety | **P0** |
| GOV-4 / OBS-1/2 / AI-5 | External-mutation audit gap | BC comment / email / webhook not in central audit | Wrap sends with `audit_log.record` / `ai_events.emit` | M | Low | Can't prove what AI sent | Auditability | **P1** |
| OBS-5 / AI-4 | Cost observability | tokens only in `RunRecord`; no ledger/allocation; workers uninstrumented | `cost_ledger` + `model_prices.json` + emit on every LLM call | M | Med | No cost governance/chargeback | Cost control | **P1** |
| ADV-1 | Advisory public endpoint hardening | unauth endpoint creates BC projects + webhooks; no kill-switch | `OPS_ADVISORY_ENABLED` kill-switch + access review (already P1.5) | S | Low | Abuse / runaway side effects | Risk reduction | **P1** |
| GOV-2 | Workers bypass runtime gate | A1/A2 post directly, not via `agent_runtime.execute()` | Route worker actions through `agent_runtime` so policy gate is authoritative | M | Med | Declared autonomy not enforced in code | Governance integrity | **P1** |
| EVT-1 | No canonical event model | many schemas, no unified `ai_event` | `ai_events.emit()` + schema (Phase 6) | M | Med | Fragmented trust signals | Dashboard foundation | **P1** |
| DASH-1 | No Trust Command Center | data exists, no unified surface | `/admin/trust` v1 read-only (Phase 7) | L | Med | No exec/ops trust visibility | Executive trust | **P1** |
| PRIV-1 | No PII retention/deletion policy | leads + tokens stored, no policy | documented retention + deletion workflow; mask email in audit | M | Low | Privacy/regulatory exposure | Compliance | **P2** |
| OBS-4 | Decision context not persisted | only structured output stored | store prompt hash + evidence ids with decisions | M | Med | Weaker explainability | Explainability | **P2** |
| SEC-2 | Weak default webhook secret | `ENTERPRISE_WEBHOOK_SECRET` default `"colaberry-advisory-sync-2026"` (`enterprise_sync.py:22-24`) | require env; no default | S | Low | Forgeable webhook | Integrity | **P2** |
| REL-1 | Process-local rate limit | `_RATE_LIMIT_HITS` in-process (`controls.py:264-279`) | wire `distributed_rate_limit` to Redis | M | Med | Limit bypass on multi-worker | Reliability | **P2** |
| AI-1 | Model-choice divergence | OpenAI only; CLAUDE.md prefers Claude | decide policy; if migrating, evaluate Claude for advisory/ops | M | Low | Strategy/cost alignment | Strategic | **P3** |
| AI-2 | No prompt versioning | prompts hardcoded; caches keyed without prompt version | add PROMPT_VERSION + cache-bust on change | S | Low | Silent prompt drift | Maintainability | **P3** |
| AI-3 | Owner metadata implicit | owner via attestations only | per-capability owner field + registry view | S | Low | Accountability clarity | Ownership | **P3** |
| OBS-6 | No distributed tracing | correlation_id manual join | optional OpenTelemetry (multi-service future) | L | High | Harder cross-service debug | Scale | **P3** |

## Sequenced plan

- **P0 (do first, both ~1 day):** GOV-3 (set `OPS_ENFORCE_RBAC=true` + startup assert), SEC-1 (require vault master key, relocate vault). Pure hardening, no redesign.
- **P1 (the trust-visibility wave):** EVT-1 → OBS-5 (cost) + GOV-4 (external audit) → DASH-1 (v1 dashboard) ; in parallel ADV-1 + GOV-2.
- **P2:** PRIV-1, OBS-4, SEC-2, REL-1.
- **P3:** AI-1/2/3, OBS-6.

## Notes

- Most P0/P1 items are **wiring/config**, not architecture — the platform already has the primitives (audit_log, agent_runtime, controls, telemetry). This is why overall maturity is LEVEL 4.
- The single highest leverage item for "can executives/regulators trust this?" is **EVT-1 + cost ledger + DASH-1** — it makes trust *visible and economic*, not just enforced.
