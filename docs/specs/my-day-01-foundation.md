# [My Day 01] AI Ops Command Center — Foundation

**Status:** Shipped (Phase A through D-light) on 2026-06-03.
**Live at:** https://advisor.colaberry.ai/my-day/

## What shipped

Per-user task triage surface that mirrors a user's Basecamp todos, scores them deterministically, and emits a Claude Code-ready prompt for each task. Engine in `execution/products/ops/`, surface at `/my-day/`, admin AI-clone setup at `/admin/users/{id}/ai-clone`.

### Engine (`execution/products/ops/`)
- **store.py** — file-backed mirror under `output/ops/{user_id}/`; atomic JSON writes; per-user `todos.json`, `projects.json`, `state.json`
- **tokens.py** — resolves a user's BC token (vault entry `basecamp_ai_clone` first; CCPP CB System fallback for Ali during transition); strips whitespace; resolves email -> user_id automatically
- **sync.py** — idempotent BC pull via project dock (`/projects/{id}.json` -> dock -> todoset -> todolists -> todos), filtered to items assigned to the user, with 90-day freshness cutoff and per-project resilience
- **scorer.py** — deterministic 5-input urgency 0-100 (due-date, staleness, keyword tier, assignee presence, project signal) capped + categorized
- **suggestions.py** — regex-keyed action recipes (8 kinds) + `generate_prompt()` that wraps the recipe in a ready-to-paste Claude Code prompt

### Surface (`/my-day/`)
- `GET /my-day/` — queue, urgency-sorted, KPI tiles, project filter, dismissed toggle
- `GET /my-day/todo/{bc_id}` — workspace with structured suggestion, Claude Code prompt, copy button, BC deep-link
- `POST /my-day/sync` — manual re-sync trigger
- `POST /my-day/todo/{bc_id}/dismiss` and `/undismiss` — local-only soft state

### Admin (`/admin/users/{id}/ai-clone`)
- GET — paste form to register user's BC user id + AI clone display name + BC OAuth token
- POST — saves to User record + vault as `basecamp_ai_clone` with TTL
- Link added on user detail page

### Tests
- 20/20 unit tests for scorer + suggestions (`tests/execution/products/test_ops_scorer.py`, `test_ops_suggestions.py`)

## Deferred — explicit follow-ups for next session

### 1. Real BC OAuth flow (replaces paste-form)
The current admin form makes the admin paste a Basecamp OAuth token. That's fine for bootstrap but won't scale. Need:
- BC OAuth client registered (separate from Google OAuth)
- `/admin/users/{id}/ai-clone/connect` initiates BC OAuth, redirects user (or admin) to BC consent screen
- `/admin/users/{id}/ai-clone/callback` captures the token, stores in vault, sets `bc_user_id` from `/people.json` lookup
- Refresh handling: BC tokens rotate every 14 days — see `directives/refresh-bc-token-flow.md` (TODO)

### 2. Admin-grade platform token + auto-create Personal/Work projects
On user creation in admin, auto-provision a Basecamp "[Display Name] Personal" project (and optionally "[Display Name] Work"). Adds the user + their AI clone as members. Needs:
- `bc_platform_admin` token in vault (or env), captured via OAuth from Ali's BC account
- `execution/products/ops/provision.py` with `create_user_workspace_project(user)`
- Wire into `/admin/users/new` POST handler

### 3. Gmail integration (read + send)
The user wants users to SEND emails from My Day workflows. Approach: incremental opt-in OAuth.
- Add "Connect Gmail" button on `/admin/users/{id}/ai-clone` or `/auth/profile`
- Triggers separate Google OAuth scoped only to `gmail.send` + `gmail.readonly`
- Token lands in vault as `gmail`
- New module `execution/products/ops/email.py` with `send_email(user, to, subject, body, attachments)`
- Suggestion recipes for `reply` and `decision` action_kinds wire in to `send_email` so workspace can send straight from the page

### 4. Decide-and-write-back (Phase 1.2 from the skill)
Currently `/my-day/todo/{bc_id}` shows the suggestion + prompt but doesn't capture decisions. Next:
- Approval workspace UI block: decision buttons (Approve / Approve+next / Approve+skill / Revise / Reject / Escalate) + reasoning textarea
- `record_decision()` writes `ops_approval_queue` row (currently no such store — add)
- Optional comment write-back via the user's AI clone token POST'd to BC `/buckets/{p}/recordings/{t}/comments.json`
- Brand-compliance preflight before write-back (regex-based: blocks ghp_, eyJ tokens, AKIA keys, etc.)

### 5. Auto-sync cron + live-update polling
Phase E from the original plan:
- Cron job (every 2 min) runs `sync.pull_todos_for_user` for every user with an active vault token
- Score after sync
- Frontend long-poll or SSE to push updates without manual refresh

### 6. Multi-project sync (drop the `ali_legacy_bucket` escape hatch)
Currently Ali's sync uses bucket 7463955 directly because the CB System token sees 0 active projects via `/projects.json`. When per-user OAuth tokens land (item 1 above), each user's token will see all their projects naturally — drop the legacy path.

### 7. Cross-tenant — apply same model to non-Colaberry tenants
Per-user My Day works the same way for any tenant once their AI clone token is in the vault. No tenancy-specific code needed; the user's `company_id` already isolates data.

## Open architectural decisions for next session

- Where does the `ops_approval_queue` audit trail live? File-backed (matches existing pattern) or SQLite (more queryable)?
- Brand-compliance preflight: copy from the Colaberry build, or re-derive against current Colaberry voice docs?
- Live-update mechanism: long-poll (simplest), SSE (better UX), or websocket (overkill for v1)?
- BC OAuth client: one for the platform that all users authenticate through, or one per user company?

## Reference

- Source skill: `build-ai-ops-command-center` (uploaded 2026-06-03)
- Original Colaberry build: `ColaberryIntern/ColaberryEnterprise_AI_LeadershipAccelerator` enterprise.colaberry.ai/admin/ops
- This session's commits: `ae754b0` (engine) → `e635aa8` (surface) → `5cc9b35` (admin) → `4258bd1` (vault lookup) → `fe706a5` (BC dock path)
