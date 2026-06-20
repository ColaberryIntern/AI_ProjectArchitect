# Phase 1 — Repository Map

**Audit date:** 2026-06-20 · **Method:** read-only inspection (4 parallel evidence sweeps), claims verified against source. Evidence is cited as `path:line`.

> Scope note: this maps what exists, with evidence. Where a sub-agent claim could not be confirmed it is marked **(unverified)**. One sub-agent claim — "secret committed in `.env`" — was **verified FALSE**: `.env` is git-ignored (`.gitignore:1` lists `.env`, `.env.prod`) and `git ls-files .env` returns nothing. Secrets are env-based and untracked.

## 1. System inventory

| System | Tech | Entry | Evidence |
|--------|------|-------|----------|
| Web app | FastAPI + Jinja2 | `app/main.py` | `app/main.py:43-106` (lifespan), `:195-217` (routers) |
| Execution layer | Python modules | `execution/` | layered manifest `execution/ops_platform/__layers__.py:1-121` |
| Ops platform (governance/runtime) | Python, file-backed | `execution/ops_platform/` | 60+ modules per `__layers__.py:34-66` |
| Advisory product (public funnel) | FastAPI routes + engines | `app/advisory/routes.py`, `execution/advisory/` | routes `app/routers/advisory.py` |
| My Day / Ops product | scheduler + workers | `execution/products/ops/` | `scheduler.py:15-100` |
| Library product (governed catalog) | FastAPI + vault + tenancy | `execution/products/library/` | `app/routers/library.py` |
| MCP server | JSON-RPC over HTTP | `/mcp/v1` | `app/routers/mcp_server.py` |

**Architecture model:** Agent-First, Deterministic-Execution, Test-First (per `CLAUDE.md`). Layer boundaries enforced declaratively in `execution/ops_platform/__layers__.py` (PLATFORM_CORE → OPS/ARCHITECT/LIBRARY products).

## 2. Service inventory (FastAPI routers — `app/main.py:195-217`)

projects, idea_intake, feature_discovery, outline_generation, outline_approval, auto_build, chapter_build, quality_gates, final_assembly, chat, demo, generate, advisory, **ops_platform** (100+ endpoints, `app/routers/ops_platform.py`), library, my_day, **auth** (Google SSO), **admin** (`app/routers/admin.py`, ~40 endpoints incl. moderation queue, scopes, bc-ai rollout, `/admin/cb-mentions.json:896`), basecamp_webhook, mcp_server, google_connect, basecamp_connect, welcome.

Notable existing admin/observability endpoints (relevant to the dashboard): `/admin/cb-mentions.json` (`admin.py:896`), `/admin/cb-webhooks.json` (`:962`), `/admin/autopickup.json` (`:1020`), `/system/health` (`ops_platform.py:1043`), `/system/metrics` Prometheus (`ops_platform.py:1048`), `/trust/{capability_id}` (`ops_platform.py:1260`), controls freeze/quarantine/emergency-rollback (`:1272-1311`).

## 3. Agent inventory

**Build-time personas (declarative, `/agents`):** project_architect, ideation_coach, quality_gatekeeper, document_assembler — all attested (`agents/*.md.tbi.json`).

**Runtime AI agents (declared in `config/tbi_runtime_agents.json`):**

| Agent | Entry | Autonomy | Status |
|-------|-------|----------|--------|
| cb_mention_responder | `execution/products/ops/cb_mention_worker.py` | `autonomous_low_risk_only` | live prod |
| autopickup_worker | `execution/products/ops/autopickup_worker.py` | `approval_required` | off-by-default |
| advisory_pipeline | `execution/advisory/recommendation_engine.py` | `recommend_only` | live prod |
| productivity_report | `execution/products/ops/productivity/runner.py` | `autonomous_low_risk_only` | live prod |

**Governed-operator registry:** `execution/ops_platform/agent_registry.py` (policies `:32-37`), runtime enforcement `execution/ops_platform/agent_runtime.py` (6-gate pipeline `:80-190`).

## 4. Data inventory

**No SQL database.** State is **file-backed JSON/JSONL under `output/`** (verified: no `sqlite3`/`psycopg`/`pyodbc` imports). Config paths: `config/settings.py:10-15`.

| Store | Path | Notes |
|-------|------|-------|
| Audit log | `output/ops_platform/audit/{date}.jsonl` | append-only, immutable (`audit_log.py:13,222-230`) |
| Event fabric | `output/ops_platform/event_fabric/{date}.jsonl` | local pub/sub + SSE (`event_fabric.py:323`) |
| Pipeline runs | `output/ops_platform/pipeline_runs/{id}.json` | `pipeline_engine.py:43` |
| Capabilities + versions | `output/ops_platform/capabilities/`, `capability_versions/` | versioned |
| Vault (encrypted creds) | `output/library/_vault/credentials.json` + `audit.jsonl` | AES-GCM (`vault.py:95-124`) |
| Tenancy | `output/library/_tenants/*.json(l)` | companies, users, approvals, scopes |
| Advisory sessions | `output/advisory/{session_id}/advisory_state.json` | + `_events_log.json` |
| Worker state | `output/ops/_cb_mentions/`, `_autopickup/`, `_productivity/` | seen/heartbeat/cursor + reports |
| Redis (optional) | `redis_backends`, `distributed_event_bus.py` | activated only when wired (`:56`) |

## 5. Workflow inventory

- **Project build pipeline** (idea → features → outline → chapters → quality gates → assembly): `directives/01..08`, engines in `execution/*.py`.
- **Advisory** (10-Q intake → blueprint → lead/PDF/webhook): `execution/advisory/*`.
- **My Day** (BC sync → urgency scoring → @CB auto-response / auto-pickup draft → productivity report): `execution/products/ops/*`.
- **Ops platform** (capability run → response-contract validation → verification → reputation/trust → discovery/marketplace): `execution/ops_platform/*`.
- **TBI compliance gate** (attestation → scorer → CI block): `scripts/tbi_compliance_check.py`, `execution/ops_platform/tbi_compliance.py`.

## 6. Dependency & integration inventory

| Integration | Module | Auth | Evidence |
|-------------|--------|------|----------|
| OpenAI (only LLM) | `execution/llm_client.py` | `OPENAI_API_KEY` env | `:79`; config `settings.py:42-46` |
| Basecamp 3 | `execution/products/ops/sync.py`, `library/basecamp_oauth_token.py` | per-user OAuth (vault) | `sync.py:34`, token `:52-54` |
| Google OAuth/SSO | `execution/products/library/auth_google.py` | client id/secret env | `:54-61` |
| Google Calendar | `config/settings.py:48-62` | service account | `:50-54` |
| Mandrill/SMTP | `execution/products/ops/productivity/delivery.py` | `MANDRILL_API_KEY` env, off-by-default | `:59-134` |
| Enterprise webhook | `execution/advisory/enterprise_sync.py` | HMAC-SHA256 | `:28,66`; **weak default secret `:22-24`** |
| GitHub | `execution/products/library/github_sync.py` | `GITHUB_TOKEN` env | trusted sources `config/library_trusted_sources.json` |
| Basecamp inbound webhooks | `app/routers/basecamp_webhook.py` | shared secret + per-user token hash | `:34-55` |
| MCP (Basecamp) | `.mcp.json`, `tools.bc_mcp.server` | bearer `cmcp_…` | `app/routers/mcp_server.py` |
| HubSpot / Apollo | pilot `dash_runner.py` | not configured (PRD pending) | (unverified — marked pending) |

**Runtime libs (`pyproject.toml:10-19`):** fastapi, uvicorn, jinja2, openai, apscheduler, jsonschema, python-dotenv. **No** vector-DB / embeddings / OpenTelemetry libs.

## 7. Security boundaries (detail in `governance-audit.md`)

- **SSO** (Google OAuth) middleware `app/middleware/auth_gate_middleware` (`app/main.py:151-152`); login-required `/library/`, `/admin/`, `/my-day/` (`config/library_tenant_domains.json`).
- **RBAC** `execution/ops_platform/rbac.py:19-54` (5 roles × 16 perms); **enforcement opt-in** via `OPS_ENFORCE_RBAC` (`rbac.py:57-61`) — **default off (finding)**.
- **Admin gate** `app/routers/admin.py:44,57` (`_require_admin`, `_require_super_admin`).
- **Vault** AES-GCM, master key `LIBRARY_VAULT_MASTER_KEY` with **dev fallback** (`vault.py:62-81` — finding).
- **Secrets** env-based, **`.env` git-ignored (verified)**; CI secret scan `scripts/library_sync_smoke.py:23-29`.

## Architecture diagram

```
                         ┌──────────────── Clients ────────────────┐
                         │ Web UI · MCP (cmcp_) · BC webhooks · cron │
                         └───────────────────┬──────────────────────┘
                                             │
                    ┌────────────────── app/ (FastAPI) ──────────────────┐
                    │ auth_gate SSO · admin · advisory · my_day · ops · …  │
                    └───────────────────────┬─────────────────────────────┘
                                            │ calls
   ┌──────────────────────────── execution/ ───────────────────────────────────┐
   │  PRODUCTS                                  PLATFORM-CORE (ops_platform)     │
   │  advisory/  products/ops/  products/library│ agent_registry · agent_runtime │
   │  (LLM funnel) (My Day workers) (vault/RBAC) │ approvals · controls · rbac    │
   │        │            │              │        │ audit_log · event_fabric       │
   │        └── llm_client (OpenAI) ────┘        │ trust_engine · reputation      │
   │                                             │ telemetry · prometheus         │
   └───────────────────────────┬─────────────────────────────────────────────────┘
                               │ persist
                    ┌──────────▼───────────┐        (optional) Redis backends
                    │ output/ JSON · JSONL  │        for event bus / locks / RL
                    │ append-only audit log │
                    └───────────────────────┘
```

See `ai-inventory.md` (Phase 2) for every AI capability and `event-model.md` (Phase 6) for the data/event flow.
