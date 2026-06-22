# Advisory Funnel — Access Review (P1.5)

**Date:** 2026-06-22 · Addresses the audit's **P1.5 (HIGH)** residual on the public advisory pipeline. Evidence `path:line`.

## Posture

The advisory funnel (`/advisory/*`) is **intentionally public/unauthenticated** — it's a lead-generation funnel (`/advisory/` is in `anonymous_paths`, `config/library_tenant_domains.json`). Anyone can start a session and get a blueprint; the **sensitive deliverable (PDF/results) is gated behind lead capture**, and downstream side effects are signed/scoped. This review documents what the funnel can do and the controls now in place.

## Side-effecting routes + their auth (the attack surface)

| Route | Side effect | Auth / secret |
|-------|-------------|---------------|
| `POST /start` | create session | none (public) |
| `POST /{id}/answer` | LLM answer validation | OPENAI_API_KEY (server) |
| `POST /{id}/design` | LLM capability map | OPENAI_API_KEY |
| `POST /{id}/capabilities` | save selection | none |
| `GET /{id}/generate` | **multiple LLM calls + creates a Basecamp project** | OPENAI_API_KEY; BC via MCP/state |
| `POST /{id}/save-lead` | lead capture, PDF, **enterprise webhook** | webhook HMAC-SHA256 (`ENTERPRISE_WEBHOOK_SECRET`, `enterprise_sync.py:28`) |
| `POST /{id}/book-strategy-call` | revenue pipeline, **enterprise webhook** | webhook HMAC |
| `POST /api/calendar/book` | **Google Calendar event** + webhook | service account (`GOOGLE_PRIVATE_KEY`, scoped) + webhook HMAC |

Read-only pages (landing, questions, results, gate, download-pdf, resume) have no side effects.

## Controls now in place

1. **`OPS_ADVISORY_ENABLED` env kill-switch** (default ON) — `app/advisory/routes.py` `require_advisory_enabled` dependency on **all 8 side-effecting routes**. Set `OPS_ADVISORY_ENABLED=false` to disable the funnel (read-only pages stay up); returns 503. This is the deploy-level "turn it all off" lever.
2. **Live pause** — `runtime_controls.is_paused("advisory_pipeline")` on `/generate` (toggle from the Trust Command Center, no redeploy). Two independent levers.
3. **Downstream is signed/scoped** — enterprise webhook is HMAC-SHA256; calendar uses a scoped service account; the deliverable is lead-gated.
4. **Observability** — advisory LLM cost is now in the cost ledger (Cost explorer); advisory attestation tracked (`recommendation_engine.py.tbi.json`, verdict compliant / risk HIGH).

## Residual recommendations (not changed here)

- **`ENTERPRISE_WEBHOOK_SECRET` has a weak default** (`"colaberry-advisory-sync-2026"`, `enterprise_sync.py:22`). Recommend requiring it from env (no default). **Not changed in this pass** — removing the default could break the prod webhook if `.env.prod` doesn't set it; verify `.env.prod` first, then harden.
- **No rate-limit / CAPTCHA** on the public funnel → abuse could burn LLM spend or spam BC. Recommend a per-IP rate limit on `/start` + `/generate` (the cost ledger now makes abuse visible as a spend spike).
- Consider moving the BC project creation behind lead capture (today `/generate` creates it pre-lead).

## Verdict

P1.5 **addressed**: the public endpoint now has a deploy kill-switch + a live pause, on top of the existing signing/gating. Remaining items (webhook secret default, rate-limiting) are tracked above as the next hardening step.
