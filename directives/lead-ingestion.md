# Directive: Universal Lead Ingestion & Attribution

## Purpose

Accept lead submissions from any website (trustbeforeintelligence.ai, colaberry.ai, advisor.colaberry.ai, and future sites) via a single endpoint, normalize them into a canonical schema, deduplicate by email or phone, attribute them to a source and entry point with full UTM/session/referrer context, and route them through deterministic rules to downstream actions.

This system lives in a new `app/enterprise/` module and uses Postgres (not JSON). It runs **in parallel** with the existing advisory CRM (`execution/advisory/lead_manager.py`), which stays untouched during the transition.

## Inputs

- HTTP `POST /api/leads/ingest` with JSON or form-data
- Optional `X-API-Key` or `X-Webhook-Signature` (per-source auth)
- Optional `X-Session-Id` for visitor-session attribution
- Query params `?source=<slug>&entry=<slug>` (alternative to body fields)

## Outputs

- A row in `leads` (new or updated via email/phone dedup)
- A row in `raw_payloads` (every request, success or failure — for debug/replay)
- One or more rows in `lead_events` (`form_submit`, `lead_identified`, `routing_action`)
- A row in `lead_sessions` (created or linked)
- Zero or more downstream routing actions dispatched (send_pdf, notify_sales, enroll_campaign, create_deal, trigger_booking_flow, tag_lead)
- JSON response with `lead_id`, `is_new_lead`, `normalized` payload preview, `routing_actions` status

## Steps

1. Persist the raw payload + headers in `raw_payloads` **before** any processing — ensures full replay on failure
2. Resolve `source` + `entry_point` via `source_registry.py` (slug lookup)
3. Verify auth (`X-API-Key` hash match or HMAC signature) if the source requires it; reject 401 otherwise
4. Load the active `form_definitions.field_map` for this entry point
5. Apply `payload_normalizer.py` — map incoming fields into the canonical `LeadPayload` shape. Apply fallbacks: `full_name` → `name` split; phone → E.164 via `phonenumbers`; unknown fields accumulate in `metadata`
6. Validate: `email` OR `phone` required. On failure, mark `raw_payloads.status='rejected'` and return 422
7. Upsert the lead via `lead_repository.py`:
   - look up by `email_normalized`, then by `phone_normalized`
   - on hit, merge non-empty fields (never overwrite existing non-empty unless `metadata.force_update=true`)
   - on miss, `INSERT` inside the same transaction (`ON CONFLICT` guarded)
8. Resolve or create `lead_sessions` by `session_key` (`X-Session-Id` or body `session_id`); link to lead
9. Append `form_submit` + `lead_identified` events (with UTM, referrer, page_url) via `event_repository.py`
10. Evaluate `routing_rules` via `routing_engine.py` (priority-ordered; first match wins unless `continue_on_match`)
11. Dispatch matched actions async; each records a `routing_action` (or `routing_action_failed`) event — **never block or fail ingest on action failure**
12. If the source is `advisor` and `MIRROR_TO_ADVISORY_JSON=true`, write a mirror into `output/advisory/_leads_db.json` via `advisory_bridge.py` (one-direction only)
13. Return 200 with `success: true`, `lead_id`, `is_new_lead`, `normalized`, and `routing_actions[]`

## Edge Cases

- **Email AND phone both missing** → 422 with `error: "email_or_phone_required"`. Raw payload still stored.
- **Same email submitted twice in parallel** → dedup enforced by `UNIQUE INDEX ux_leads_email`. Second transaction retries on conflict and updates instead.
- **Phone-only lead** → accepted; `routing_engine` skips email-only actions and emits `skipped_action` event.
- **Unknown `source` or `entry` slug** → 400 with `error: "unknown_source"`. Raw payload stored for diagnosis. Admin can add the source and replay.
- **Malformed JSON / invalid UTF-8** → 400. Raw body (base64) stored in `raw_payloads.body`.
- **Source requires HMAC and header missing/invalid** → 401. Raw payload stored with `status='rejected'`.
- **Postgres down** → 503 with `Retry-After`. Raw body dumped to `tmp/ingest_fallback/<uuid>.json` for later replay.
- **Routing action fails (SMTP, CRM 500, etc.)** → ingest response still 200. Action event is `routing_action_failed`. APScheduler retries.
- **HMAC secret rotation** → accept both `hmac_secret` and `hmac_secret_prev` for a 24h grace window.
- **Advisory `enterprise_sync.py` hits the compat shim `/api/webhooks/advisory`** → legacy payload translated to `IngestRequest` for the `advisor` source. Must honor the existing `X-Webhook-Signature: sha256=...` scheme from `execution/advisory/enterprise_sync.py`.
- **Legacy advisory admin UI (`/advisory/admin/leads`)** must keep working throughout the transition — `advisory_bridge.py` mirror-sync keeps `_leads_db.json` populated until cutover.
- **PII in `raw_payloads`** → 30-day retention; redaction job strips email/phone once the real `leads` row is confirmed.

## Safety Constraints

- **Never modify `execution/advisory/lead_manager.py`, `campaign_manager.py`, `event_tracker.py`, or `enterprise_sync.py`.** These remain the authoritative advisory CRM during transition.
- **Never write back to advisory JSON from the new enterprise code except via `advisory_bridge.py`**, and only when `MIRROR_TO_ADVISORY_JSON=true`. Never the reverse direction.
- Never commit secrets. `DATABASE_URL`, `ENTERPRISE_ADMIN_TOKEN`, and HMAC secrets live in `.env`.
- Never touch production Postgres from tests. Integration tests require `INGEST_INTEGRATION=1` and a local `DATABASE_URL` pointing at a disposable dev DB.
- Admin endpoints (`/api/sources`, `/api/routing-rules`, `/enterprise/*`) require `Authorization: Bearer <ENTERPRISE_ADMIN_TOKEN>` until real auth lands.
- Autonomous mode is **off by default** (`AUTONOMOUS_AUTOAPPLY=false`). Suggestions are reviewed by a human before any routing rule is applied.
- Routing action failures must never cause the ingest request to fail — they are logged as events and retried asynchronously.
- Rate limiting (slowapi) per source + IP is mandatory before the endpoint is exposed publicly.

## Verification

Each rollout gate must pass its own verification before the next gate begins.

### Gate 0 — Directive (this file)
- File exists at `/directives/lead-ingestion.md`
- Referenced files listed in §Critical Files of the plan exist or are marked as "to be created"

### Gate 1 — Skeleton
- `alembic upgrade head` succeeds on local Postgres
- `GET /enterprise/` returns 200
- Full existing test suite (`pytest tests/`) passes — zero advisory regressions

### Gate 2 — Ingest MVP
- `pytest tests/enterprise/test_payload_normalizer.py tests/enterprise/test_lead_repository_dedup.py tests/enterprise/test_ingest_api.py` all green
- Manual: `curl -X POST /api/leads/ingest -d @tests/fixtures/trust_book.json` returns `success=true`; resubmitting the same email returns `is_new_lead=false` with the same `lead_id`
- `raw_payloads` has one row per request, regardless of outcome

### Gate 3 — Registry + attribution
- `/api/sources` and `/api/sources/{id}/entry-points` CRUD works end-to-end
- `scripts/seed_enterprise_sources.py` creates 3 sources (trust, colaberry, advisor)
- `lead_events` rows carry UTM + referrer; `lead_sessions.last_seen_at` updates on repeat submissions

### Gate 4 — Routing
- `pytest tests/enterprise/test_routing_engine.py` covers rule ordering, `continue_on_match`, action failure isolation
- Integration: a `get_book_modal` submission dispatches `send_pdf` + `enroll_campaign` actions; a `request_demo_form` with `company_size>=100` triggers the enterprise tag

### Gate 5 — Advisory compat shim
- `pytest tests/enterprise/test_advisory_bridge.py` green
- Integration: calling `execution.advisory.enterprise_sync.send_enterprise_event("report.completed", payload)` (pointed at `http://localhost:8000/api/webhooks/advisory`) lands a row in Postgres AND (when flag on) in `_leads_db.json`
- Existing advisory flows (`/advisory/{sid}/save-lead`, `/advisory/{sid}/book-strategy-call`) still work with zero code change

### Gate 6 — Generator UI
- `/enterprise/generator` renders; dropdowns populate from `lead_sources` + `entry_points`
- Copy-and-run: the curl emitted for the trust source creates a lead end-to-end
- JS embed snippet emitted is valid and matches §4.1 of the plan

### Gate 7 — Dashboard
- `/enterprise/` shows non-zero leads/conversions after ≥1 test ingest
- SSE tail updates in real time as new `raw_payloads` arrive
- Stats math verified against seeded fixtures

### Gate 8 — Autonomous
- APScheduler job runs on schedule; writes `output/enterprise/insights.json`
- `/enterprise/autonomous` renders human-reviewable cards
- `AUTONOMOUS_AUTOAPPLY=false` confirmed — no rule is ever applied without a human click

### Gate 9 — Cutover
- External sites flipped to new webhook URLs
- 7 consecutive days of zero `status='error'` rows in `raw_payloads` (excluding explicit 4xx validation rejections)
- `MIRROR_TO_ADVISORY_JSON` disabled; advisory admin UI becomes read-only for historical reads

## Self-Annealing Loop

When an ingest fails in production:
1. Inspect `raw_payloads` for the failing `id`
2. Identify root cause (missing field map? new field from a site? malformed auth?)
3. Fix the normalizer, field map, or auth handling
4. Add a fixture + test covering the failure case
5. Update this directive if the failure mode is new
6. Replay via `POST /api/admin/replay/{raw_payload_id}` to confirm the fix

Failures are inputs, not mistakes.
