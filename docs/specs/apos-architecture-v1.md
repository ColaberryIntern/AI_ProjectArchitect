# APOS Architecture v1

**AI Presence Operating System** — a Discord/Slack/GitHub/Notion/Claude Code/n8n hybrid where humans and AI agents are first-class peers.

**Status:** Strategy + architecture. No code in this document. Implementation begins ONLY after Ali approves the architecture and roadmap.

**Authors:** Principal Architect persona, Ali (product owner)
**Date:** 2026-06-03
**Supersedes:** None
**Continues:** Library + Multi-tenant + Vault + My Day (all already shipped — APOS is the evolution, not a rewrite)

---

## 0. Executive Summary

### What APOS is

A platform where:
- **Humans appear with presence** (online/idle/away/offline/invisible) like Discord.
- **AI agents appear as first-class participants** with their own identity, status (`executing` / `waiting` / `blocked` / `researching` / `needs_approval`), capabilities, memory, and audit trail.
- **Work products are versioned, shareable, and discoverable** like GitHub: prompts, skills, agents, workflows, MCP servers, playbooks.
- **An organizational memory graph** connects who knows what, who built what, what worked, and what didn't.
- **A local connector observes user work** (with explicit per-resource consent) and proposes automations, workflows, skills, and agents from observed patterns.
- **A marketplace** lets organizations publish and consume AI-native work product.

### What APOS is NOT

- Not a chatbot platform. AI is workforce, not assistant.
- Not a no-code automation tool. n8n exists; APOS is the social/collaboration/memory layer around tools like n8n.
- Not an IDE. Cursor/Windsurf exist; APOS is the multiplayer / organizational layer around them.

### Strategic positioning

The category APOS competes in does not exist yet. The nearest analogues each cover one slice:

| Player | Slice they own | Slice they don't |
|---|---|---|
| **Discord** | Real-time presence + voice + community | AI as peer, work product, memory |
| **Slack** | Team messaging + integrations | AI as peer, deep work product, memory |
| **GitHub** | Code versioning + community | Presence, real-time collab, AI as user |
| **Notion** | Knowledge management | Presence, executable work, real-time |
| **Claude Code** | AI-driven IDE | Multi-user, org memory, marketplace |
| **Cursor / Windsurf** | AI-augmented IDE | Multi-user, presence, marketplace |
| **LangGraph / n8n** | Workflow execution | Social, knowledge, presence |
| **Obsidian** | Personal knowledge graph | Multi-user, real-time, AI-native |

APOS's bet: **the next decade's category leaders are AI-native presence + memory + marketplace platforms.** Whoever ships the right one becomes the operating system for AI-native organizations.

### What we already have (do not throw away)

The existing FastAPI platform (`advisor.colaberry.ai`) has shipped much of APOS's foundation:

- Multi-tenant data model (`tenancy.py`: companies, users, roles, scopes, item_approvals) — APOS's **organization + user + permission substrate**
- Encrypted credential vault (`vault.py`) — APOS's **secrets layer for AI agent tokens**
- Library (`library/*`) — APOS's **asset registry v0** (skills, agents, prompts, MCP, capabilities)
- Per-tenant moderation queue (`workflow_publish.py`) — APOS's **publish + review primitive**
- Per-user GitHub workspace (`workspaces.py`) — APOS's **per-user execution surface**
- BC MCP server (`tools/bc_mcp/`) — pattern for **per-tool MCP** in the marketplace
- My Day (`my_day/*`) — APOS's **task triage + Claude Code prompt generation** for one user at a time

These maps to ~30% of APOS. The next 70% is the new feature surface: presence, connector, mirror mode, knowledge graph, marketplace, social layer, reputation, event bus.

### Recommended go/no-go decision before any code

Three "do this or kill the project" decisions Ali must make before architecture is locked:

1. **Local connector or web-only?** A useful local connector unlocks 5 of the 14 features (Claude Code, Mirror, MCP local, Workflow recording, AI agent identity continuity). Without it, APOS is a SaaS-only collaboration tool — useful but not category-defining. Building, signing, and distributing a desktop binary across Windows/Mac/Linux adds ~6 months and ongoing ops cost. **Recommendation: yes, but defer to Beta. Ship the web-only MVP first.**

2. **Build vs. integrate marketplace?** Building a marketplace from scratch is a 4-month effort with winner-take-all dynamics (a slow marketplace is worse than no marketplace). **Recommendation: piggyback on GitHub releases + a thin registry layer. Marketplace UI is a discovery surface, not a hosting system.**

3. **Open the knowledge graph standard?** The organizational memory graph is APOS's most defensible asset. Closing it creates lock-in but slows adoption. Opening it (publish schema, allow export/import) accelerates adoption but commoditizes our most strategic feature. **Recommendation: open the schema; close the inference layer (the AI Anticipation Engine).** This is the same pattern GitHub used with Git (open) vs. GitHub.com (closed).

---

## 1. Product Architecture

### First-class entities

APOS treats nine entities as first-class — each has a UUID, owner, permissions, version history, audit trail, search index entry, and presence (where applicable).

```
                 ┌──────────────┐
                 │ ORGANIZATION │ (Colaberry, Patriot, ShipCES, ...)
                 └──────┬───────┘
                        │ has many
        ┌───────────────┼───────────────┐
        │               │               │
   ┌────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
   │   TEAM   │   │  PROJECT  │   │  CHANNEL  │
   └────┬─────┘   └─────┬─────┘   └─────┬─────┘
        │               │               │
        │ members       │ contributors  │ subscribers
        ▼               ▼               ▼
   ┌─────────┐     ┌─────────┐     ┌──────────┐
   │  HUMAN  │     │AI AGENT │     │ WORKFLOW │
   └────┬────┘     └────┬────┘     └────┬─────┘
        │ author        │ author        │ uses
        │               │               │
        └───────┬───────┴───────┬───────┘
                │               │
                ▼               ▼
            ┌────────────────────────┐
            │  ASSETS                │
            │  - PROMPT              │
            │  - SKILL               │
            │  - MCP SERVER          │
            │  - PLAYBOOK            │
            │  - WORKFLOW BLUEPRINT  │
            └────────────────────────┘
```

### Entity ownership rules

- An entity has exactly one owning organization (tenant boundary).
- Users belong to one organization; can be guests in others.
- AI agents belong to one organization; can be loaned to projects in others.
- Assets can be shared `private` / `team` / `organization` / `marketplace` / `marketplace-paid`.
- A user can transfer asset ownership but cannot transfer organization ownership (compliance gate).

### Surface map (which feature lives on which page)

| Surface | Primary entity | Replaces today's | Status |
|---|---|---|---|
| `/now` | Presence + activity feed | (new) | Spec |
| `/my-day` | Task triage | Existing My Day | **Shipped** |
| `/library` | Asset registry | Existing Library | **Shipped** (extend with presence + reputation) |
| `/workspaces/{org}/{project}` | Project + team + channel | (partial — admin queue exists) | Spec |
| `/people/{user}` | User profile + reputation | (new) | Spec |
| `/agents/{agent}` | AI agent profile + memory + capabilities | (new) | Spec |
| `/knowledge` | Org memory graph browser | (new) | Spec |
| `/marketplace` | Public asset discovery | Library `?scope=all` | Extension |
| `/connector` | Local connector management | (new) | Spec |
| `/admin` | Org admin console | Existing Admin | **Shipped** (extend) |

### Information architecture vs. existing platform

The hard rule: **no URL we've shipped breaks.** Every existing route stays valid. APOS adds new routes and progressively enhances old ones.

---

## 2. Technical Architecture

### Component overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                          CLIENT TIER                                 │
├──────────────────────────────────────────────────────────────────────┤
│  Web App        Local Connector       Mobile (Phase 4)              │
│  (FastAPI       (Tauri or Electron;   (Phase 4 only)                │
│   serves        Windows/Mac/Linux;                                   │
│   server-side   stdio MCP for                                        │
│   templates +   Claude Code +                                        │
│   small JS for  file watcher +                                       │
│   reactivity)   workflow recorder)                                   │
└────────────┬────────────────────┬────────────────────────────────────┘
             │                    │
             │ HTTP + WSS         │ HTTPS + WSS + stdio
             ▼                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          EDGE TIER                                   │
├──────────────────────────────────────────────────────────────────────┤
│  Reverse proxy (Caddy)  Rate limiting   TLS termination             │
│  Sticky sessions for WS gateway                                      │
└────────────┬────────────────────┬────────────────────────────────────┘
             ▼                    ▼
┌──────────────────────────────┐  ┌──────────────────────────────────┐
│      WEB API TIER            │  │   REALTIME GATEWAY               │
│  FastAPI (existing app)      │  │  WebSocket fanout from Redis     │
│  Server-rendered HTML        │  │  Presence ticks                  │
│  REST + form POSTs           │  │  Event delivery to clients       │
└────────────┬─────────────────┘  └────────┬─────────────────────────┘
             │                              │
             └──────────────┬───────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       SERVICE TIER                                   │
├──────────────────────────────────────────────────────────────────────┤
│  - AI Orchestrator (LLM routing, prompt cache, fallback)            │
│  - Recording Engine (workflow capture from connector events)        │
│  - Anticipation Engine (LLM-driven suggestions)                     │
│  - Graph Inference (query org memory)                               │
│  - Marketplace (publish + install + signing)                        │
│  - Notification Service                                             │
│  - Sync Engine (BC, Linear, GH, Gmail, Calendar)                    │
└────────────┬─────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        DATA TIER                                     │
├──────────────────────────────────────────────────────────────────────┤
│  Postgres (primary tx state — users, orgs, projects, tasks, audit)  │
│  Postgres + pgvector (embeddings for semantic search)               │
│  Postgres + AGE extension (organizational memory graph)             │
│  Redis Streams (event bus, durable, replayable)                     │
│  Redis pub/sub (presence ticks, ephemeral)                          │
│  S3-compatible (assets: prompt bodies, skill MD, MCP configs,       │
│                 recorded workflow tarballs, signed marketplace pkgs)│
│  Meilisearch (full-text search across messages, assets, profiles)   │
└──────────────────────────────────────────────────────────────────────┘
```

### Why this stack

| Choice | Why | Alternative considered | Why not |
|---|---|---|---|
| **FastAPI + server-side templates** | Already in use; SSR pages load fast; lowest team complexity | React SPA + JSON API | 2× the code, 3× the bugs, no SEO advantage for our use case |
| **Postgres for everything possible** (relational + graph + vector) | Ops complexity is the #1 startup killer; Postgres + extensions covers 90% of needs | Neo4j (graph) + Pinecone (vector) | 3 databases to operate, 3 backup chains, 3 query languages |
| **Redis Streams over Kafka/NATS** | Already running for cache; durable enough for our scale; simpler ops | Kafka | Need Zookeeper or KRaft; 10× the ops burden |
| **Caddy over Nginx** | Auto-HTTPS via Let's Encrypt; simpler config; same perf | Nginx + certbot | More moving parts |
| **Meilisearch over Elasticsearch** | 10× smaller footprint; faster for our scale; one binary | Elasticsearch / OpenSearch | JVM, 4GB+ RAM, cluster management |
| **Tauri over Electron for connector** | 10× smaller binaries; native perf; Rust core | Electron | 200MB binary; Chromium update treadmill |
| **WebSocket for realtime** | Industry standard; works through corporate proxies; Caddy handles termination | SSE | One-way only; needs separate channel for client→server |

### What stays from today's stack

- The single FastAPI process running on Hetzner (no rush to microservices until ≥1k orgs)
- Docker Compose deploy (no rush to Kubernetes until multi-region)
- File-backed JSON for low-write entities (tenants, scopes) — migrate to Postgres at Phase B per the deferred list, not Day 1

---

## 3. Data Architecture

### Recommendation: 4 stores, all simple

| Store | Purpose | Why this and not something else |
|---|---|---|
| **Postgres** (primary) | Transactional state: users, orgs, projects, tasks, audit, sessions, approvals, marketplace metadata, asset registry | Mature, ACID, extensions cover graph + vector — operational simplicity |
| **Postgres + pgvector** | Semantic embeddings for search ("find prompts that increased proposal close rate"), suggestion ranking | Same DB, same backup, same auth — avoid a separate Pinecone/Qdrant ops surface |
| **Postgres + AGE** (Apache AGE graph extension) | Organizational memory graph queries ("show users who built workflows used by high performers") | Cypher-compatible queries; same DB; mature enough at our scale (≤100M nodes) |
| **Redis Streams + pub/sub** | Event bus (durable streams) + presence ticks (ephemeral pub/sub) | Redis already running; Streams give Kafka-shaped semantics without Kafka ops |
| **S3-compatible blob store** (Hetzner Object Storage or Wasabi) | Asset bodies, recorded workflow archives, marketplace signed packages, conversation transcripts | Cheap, durable, scales horizontally; file-backed JSON cannot serve marketplace |

### Why NOT a dedicated graph DB

Neo4j and Memgraph offer slightly better graph perf, but the cost is:
- Separate operational surface (backup, monitoring, scaling, auth)
- Separate query language (Cypher vs. SQL)
- Cross-store join pain (when you want "find people who built workflows containing prompt X" — half in Postgres, half in Neo4j)

AGE inside Postgres gives us Cypher when we need it, SQL when we don't, one backup, one access control surface. The trade-off (10% slower on very large graphs) is acceptable to ≤10k orgs. We revisit at scale Phase 4.

### Core data model sketch

```
-- Tenancy (already exists; extend, don't rewrite)
organizations(id, slug, display_name, plan, created_at, ...)
users(id, email, org_id, ..., bc_user_id, ai_clone_name, ...) ← exists, extend
ai_agents(id, name, org_id, owner_user_id, capabilities[], status, current_task_id, last_heartbeat_at)

-- Workspaces (NEW: extends the existing project concept)
projects(id, org_id, slug, name, description, visibility, owner_user_id)
teams(id, org_id, name, description)
team_members(team_id, user_id, role)
channels(id, kind, parent_type, parent_id, name)  -- channel for org/team/project

-- Assets (extend Library)
assets(id, org_id, kind, slug, current_version_id, visibility, owner_user_id, owner_agent_id)
asset_versions(id, asset_id, version_number, body_blob_url, manifest_json, signed_by, signed_at)
asset_installs(id, asset_version_id, installer_user_id, installer_org_id, installed_at)
asset_ratings(id, asset_id, rater_user_id, score, review_text, created_at)

-- Presence (NEW)
presence(user_id PK, status, current_workspace_id, current_project_id, current_activity, updated_at)
agent_presence(agent_id PK, status, current_task_id, progress_pct, eta_seconds, updated_at)

-- Activities (NEW)
activities(id, actor_kind, actor_id, kind, payload_json, ts)  -- the activity-feed source

-- Recordings (NEW: Mirror Mode capture)
recordings(id, org_id, owner_user_id, started_at, ended_at, status, derived_workflow_id)
recording_events(recording_id, ts, kind, target, payload_json)

-- Workflows (NEW)
workflows(id, org_id, name, blueprint_json, derived_from_recording_id, status)
workflow_runs(id, workflow_id, started_at, ended_at, status, actor_id, output_blob_url)

-- Reputation (NEW)
reputation_scores(subject_kind, subject_id, dimension, score, computed_at, ...)
-- dimensions: helpfulness, accuracy, completion_rate, adoption

-- Event bus (NEW — log-tailable from Redis Streams; mirrored to Postgres for audit)
events_log(event_id PK, ts, kind, actor_kind, actor_id, org_id, payload_json)

-- Graph layer (AGE) — vertices match the above tables; edges are computed:
-- (user)-[CREATED]->(asset)
-- (user)-[FOLLOWS]->(user)
-- (asset)-[FORKED_FROM]->(asset)
-- (workflow)-[USES]->(prompt)
-- (workflow)-[ACHIEVED]->(outcome)
```

### Sizing estimates (Year 1)

| Table | Rows by Y1 | Indexes | Storage |
|---|---|---|---|
| `users` | ≤5,000 | email, org_id | <50 MB |
| `ai_agents` | ≤2,000 | org_id, owner_user_id | <20 MB |
| `assets` | ≤50,000 | org_id+kind, visibility | <500 MB |
| `asset_versions` | ≤500,000 | asset_id | <2 GB (manifest), ~50 GB blob (S3) |
| `presence` | =users (in-memory + 1 row each) | — | <5 MB (Redis) |
| `activities` | ≤50M | actor, ts, org_id | ~10 GB |
| `events_log` | ≤500M | ts, kind | ~100 GB (compressed; 90-day retention) |
| `recordings` | ≤10k | owner_user_id | <100 MB metadata, ~1 TB blob (S3) |

Conclusion: Postgres footprint stays modest (≤200 GB). Blob store + events_log are where the volume lives. Both are cheap.

---

## 4. Service Architecture

### Phase-by-phase decomposition

APOS does NOT need microservices on Day 1. The right answer is: **monolith first, extract when measurement shows a real bottleneck.**

#### MVP (months 1-3): Single FastAPI process

All existing My Day / Library / Admin code plus new routers for `/now`, `/connector`, `/people`, `/agents`. Same Docker Compose deploy.

#### Alpha (months 4-6): Extract the Realtime Gateway

The websocket fanout for presence + events benefits from a dedicated process with sticky sessions. Extract to a Node.js or Go process (Go is recommended — single binary, low GC). Stays on the same Hetzner box, separate port behind Caddy.

#### Beta (months 7-9): Extract the AI Orchestrator + Recording Engine

LLM calls have very different latency and cost profiles than CRUD. Extract to a dedicated Python service. Recording Engine processes connector events asynchronously — Celery worker pulling from Redis Streams.

#### V1 (months 10-12): Extract the Marketplace + Graph Inference

Marketplace publishes are infrequent but security-critical (signing, scanning). Graph Inference is read-heavy and cacheable. Both deserve isolation by V1.

### Service responsibilities

| Service | Owns | Talks to | Avoids |
|---|---|---|---|
| **Web API** | HTTP routes, session auth, form POSTs, server-side rendering | Postgres, Redis, S3, AI Orchestrator | Direct LLM calls, long-running work |
| **Realtime Gateway** | WebSocket connection state, presence ticks, event fanout | Redis pub/sub + Streams | Persistent storage (stateless) |
| **AI Orchestrator** | LLM routing, prompt cache, fallback chain, cost tracking | Anthropic, OpenAI, Claude Code MCP | Direct user requests (always via Web API) |
| **Recording Engine** | Process connector event streams into workflow blueprints | Redis Streams, S3, Postgres | Real-time response (async) |
| **Anticipation Engine** | Run LLM-driven suggestion pipelines (next task, blocker prediction) | AI Orchestrator, Postgres, Graph | Direct user requests |
| **Graph Inference** | Cypher queries against AGE; cached read paths | Postgres (AGE) | Writes (only Web API writes) |
| **Marketplace** | Publish, sign, scan, install | Postgres, S3, GitHub API | Day-to-day asset reads (Library does that) |
| **Sync Engine** | Pull BC/Linear/GH/Gmail/Calendar; push back-writes | External APIs, Postgres | LLM calls (AI Orch does that) |
| **Notification Service** | Email, SMS (Twilio), Slack, BC comment | External APIs, Postgres | Generating content (other services do that) |
| **Connector (client)** | Run on Ali's laptop; stdio MCP for Claude Code; activity reporting; file watching | Web API + Realtime Gateway over HTTPS+WSS | Storing platform secrets (vault holds those) |

### Inter-service contracts

- **Web API ↔ everything else**: HTTP + JSON. Versioned (`/api/v1/`). OpenAPI spec generated from FastAPI.
- **All services ↔ Event bus**: CloudEvents v1.1 spec on Redis Streams. Producers fire-and-forget; consumers commit offsets.
- **Connector ↔ Realtime Gateway**: WebSocket with `Authorization: Bearer <token>` and a heartbeat every 15s.
- **AI Orchestrator ↔ LLM providers**: Anthropic SDK (Claude), OpenAI SDK (GPT). Both with prompt caching enabled. Cost telemetry to Postgres per-call.

---

## 5. Event Architecture

### Why event-driven (and not request/response everywhere)

Three concrete benefits:

1. **Audit by default.** Every state change emits an event. The `events_log` table IS the audit trail — no separate logging code.
2. **Loose coupling.** Reputation Engine can listen for `task_completed` events without Web API knowing it exists. Add new services later without touching old ones.
3. **Replay.** When a service has a bug or schema changes, replay the events_log from a timestamp to rebuild derived state.

### Event taxonomy

```yaml
# Presence
user.online
user.idle
user.offline
user.invisible
user.switched_workspace
user.switched_project

agent.connected
agent.disconnected
agent.status_changed   # executing / waiting / blocked / needs_approval / etc.
agent.heartbeat        # frequent, ephemeral — pub/sub not stream
agent.task_started
agent.task_completed
agent.task_failed

# Workspaces
workspace.created
workspace.member_added
workspace.member_removed
channel.created
channel.message_posted
channel.notification_fired

# Assets (extends current Library + adds marketplace)
asset.created
asset.version_published
asset.installed         # by user X into workspace Y
asset.rated
asset.forked
asset.transferred       # ownership transfer
asset.marketplace_published
asset.marketplace_signed
asset.marketplace_blocked   # security scanner blocked

# Workflows
workflow.created
workflow.executed
workflow.failed
workflow.derived_from_recording   # Mirror Mode produced one

# Mirror Mode recording
recording.started
recording.action_captured
recording.stopped
recording.pattern_detected   # Anticipation Engine fires this

# Approvals (existing Workflow 1 publish queue, generalized)
approval.requested
approval.granted
approval.denied
approval.timed_out

# Reputation
reputation.score_computed
reputation.tier_changed

# Anticipation
suggestion.surfaced       # "I think you should next..."
suggestion.accepted
suggestion.dismissed

# Communication (Feature 4 — structured messages)
message.sent             # 1:1, channel, or thread
notification.delivered
request.opened           # structured "I need X by Y"
request.fulfilled
```

### Event schema (CloudEvents v1.1)

```json
{
  "specversion": "1.0",
  "id": "evt_01HF7YS4M0CXVQ2N3XQH8YZ7TR",
  "source": "/api/v1/workspaces/colaberry/projects/shipces",
  "type": "workflow.executed",
  "datacontenttype": "application/json",
  "time": "2026-06-03T18:42:11.327Z",
  "subject": "workflow:wf_01HF7Y...",
  "data": {
    "org_id": "colaberry",
    "workflow_id": "wf_01HF7Y...",
    "actor_kind": "agent",
    "actor_id": "agent_karun-clone",
    "run_id": "run_01HF7Y...",
    "duration_ms": 4231,
    "status": "ok",
    "output_blob_url": "s3://apos-prod/runs/run_01HF7Y.../output.json"
  }
}
```

### Routing semantics

- **Durable subscribers** (Postgres mirror, Reputation Engine, Anticipation Engine): Redis Streams consumer groups.
- **Ephemeral subscribers** (web clients via Realtime Gateway): Redis pub/sub, no replay.
- **Backpressure**: Redis Streams have natural backpressure (consumer commits offsets); pub/sub drops if consumer can't keep up — fine for presence ticks.

### Reliability

- All events double-written: Redis Streams (fast, ephemeral after 90 days) + Postgres `events_log` (durable, indexed).
- Consumers idempotent on `event.id`.
- Replay tool: `python scripts/replay_events.py --from "2026-06-01" --consumer reputation` re-emits to a single consumer.

---

## 6. Security Architecture

### Threat model — what could go wrong

| Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Connector reads files user didn't intend to share | High (without strong UX) | Severe (privacy + IP leak) | **Per-resource consent** with explicit, persistent grant; recorded in `connector_grants` table; visible in user settings; revokable; expires after 90 days inactivity |
| Malicious marketplace asset executes arbitrary code on install | Medium | Severe (org compromise) | **Signed manifests** (sigstore-style); content scan for credential patterns; sandboxed install dry-run; community reporting |
| AI agent escalates privilege beyond inviter's scope | Medium | Severe (privilege escalation) | **Capability tokens** — agent gets a derived token with subset scope of its inviter; tokens TTL 1 hour; renewal requires fresh inviter session |
| Cross-tenant data leak via graph queries | Medium | Severe (multi-tenant breach) | **Tenant filter applied at AGE query layer** (mandatory `WHERE org_id = $1` injection); pen-tested before V1 |
| Compromised connector binary phones home with secrets | Low (with signed updates) | Catastrophic | **Code signing per OS** (Apple notarization, Authenticode, GPG); auto-update verifies signature; transparency log |
| Phishing into approval grants | High (without good UX) | Severe (broad consent given to attacker) | **Approval UX shows actor + resource + reason + diff**; high-risk approvals require typed confirmation, not just click |
| Real-time presence info leaks org structure | Medium | Moderate (reconnaissance) | **Presence visibility scoped to org by default**; invite-only for cross-org follows; per-user toggle for invisible |
| Event log mining reveals competitive info | Medium | Moderate | **Events scoped to org for retention**; cross-org aggregates anonymized; org admin can disable optional analytics |
| LLM prompt injection from BC ticket content writes back malicious BC comments | High (without guardrails) | Moderate (reputation, embarrassment) | **Prompt-injection-resistant prompts** for write-back actions; preview-before-send for all outbound messages; no execution from user-supplied content |
| Vault master key loss | Low (with backup) | Catastrophic (all stored secrets unrecoverable) | **Already mitigated** — Ali stored in password manager during Phase 0 |

### Zero-trust posture

Every request authenticates and authorizes. Every action is logged. Every cross-service call carries a token derived from the user's session, scoped to the minimum needed:

```
User session (broad)
    └─ Capability token (this service, this resource, this action, 1hr TTL)
         └─ Downstream call carries the capability, not the session
```

### Local-first principles (for the connector)

- Connector stores all user data locally; only sends events + metadata to platform.
- Files NEVER uploaded without explicit per-file consent.
- Connector telemetry is opt-in, not opt-out.
- "Send a copy to me" feature: connector logs every event it ships to platform, available in a local view, user can wipe at will.

### Audit trail

Every state change emits an event into `events_log` with:
- `actor_kind` (`user` / `agent` / `system`)
- `actor_id`
- `org_id`
- `target_kind` + `target_id`
- `before_hash` + `after_hash` (for diff queries)
- `correlation_id` (groups events from one user action across services)

`/admin/audit` page (org admins) queries this table with filters.

### Encryption

- TLS 1.3 everywhere (Caddy auto-renews)
- Postgres at-rest encryption (Hetzner provides; verify enabled)
- S3 server-side encryption
- Existing AES-GCM 256 vault for secrets (already shipped)
- Connector local SQLite encrypted with OS keychain key

### Data classification

Every asset has a classification level (assignable by org admin):

| Level | Default visibility | Outbound LLM allowed? | Stored where |
|---|---|---|---|
| `public` | Everyone | Yes | S3 |
| `organization` | Org members | Yes (with org consent) | S3 |
| `team` | Team members | Yes (with team consent) | S3 |
| `private` | Owner only | Owner choice (default off) | S3 |
| `restricted` | Explicit allowlist | NEVER to external LLM | Postgres encrypted column |

---

## 7. AI Architecture

### AI as first-class users

Every AI agent has the same kind of record as a human:

```
ai_agents:
  id: agent_01HF7YS...
  name: "Karun Clone"
  display_emoji: 🤖
  org_id: colaberry
  owner_user_id: usr-karun
  description: "Karun's AI clone — operates BC, reviews PRs, drafts replies"
  capabilities: ["bc.write", "github.read", "gmail.draft"]  -- subset of owner's scope
  status: "executing"
  current_task_id: task_01HF7Z...
  current_workspace_id: workspace_shipces
  memory_pointer: vault://agent_01HF7YS.../memory_v3
  last_heartbeat_at: 2026-06-03T18:42:11Z
  trust_score: 0.87
```

### AI agent lifecycle

1. **Created** by a human (or by Mirror Mode from observed work). Auto-approved if within owner's scope.
2. **Configured** with capabilities (subset of owner's scope), memory, prompt template.
3. **Connected** to compute (local Claude Code via connector, or platform-hosted runner).
4. **Executing** tasks routed via the AI Orchestrator. Status broadcast via presence events.
5. **Memory-evolved**: every completed task can update the agent's memory bank. Owner can review.
6. **Reputation-scored**: completion rate, accuracy (where measurable), human override rate.

### LLM routing

The AI Orchestrator is the chokepoint for all LLM calls:

```
caller → AI Orchestrator
              │
              ├─ Classify request: cheap / medium / expensive
              ├─ Check prompt cache (Anthropic prompt caching)
              ├─ Choose model:
              │     cheap     → gpt-4o-mini / haiku
              │     medium    → gpt-4o / sonnet
              │     expensive → claude-opus-4
              ├─ Fall back deterministically on:
              │     - rate limit → next model down
              │     - error → next model down
              │     - cost cap exceeded → deterministic fallback (no LLM)
              ├─ Record cost + latency to Postgres per (org, agent, task)
              └─ Return response
```

### Prompt caching strategy

Cache the SYSTEM prompt + tool definitions (the long static bits) at the Anthropic API layer. Vary only the per-request user content. Expected cache hit ratio at scale: >70%, which means 70% of LLM cost saved on those calls.

### Memory architecture

Each agent has a `memory_pointer` to a vault-encrypted blob. Memory is structured:

- **Profile** (one-time): role description, behavioral guardrails, voice/tone.
- **Recent observations** (sliding window 30 days): events the agent witnessed.
- **Skill index**: which skills/prompts/MCP servers the agent has access to.
- **Knowledge graph view**: read-only window into org memory graph, scoped to agent's permissions.

When the agent makes an LLM call, the Orchestrator assembles context: profile + relevant recent observations + the specific task. This is Anthropic's "Claude Memory" + RAG, but org-aware.

### Cost controls

- Per-org monthly LLM budget (set by admin, defaults to org plan limit).
- Per-agent daily budget (set by owner; defaults to 10% of org budget).
- Per-task estimated cost shown to user before execution if >$0.50.
- Automatic deterministic fallback when budget exhausted (with banner to user).

### Why this architecture vs. just "let users hit LLMs directly"

If users could fire LLM calls directly from connector → provider, we'd lose:
- Cost visibility (per-org, per-agent)
- Cache opportunities (each user's calls in isolation)
- Audit (no central log of AI actions)
- Capability enforcement (no scope check)

The orchestrator is the value-add. It's also the easiest place to swap models when a better one comes out (Anthropic → OpenAI → Anthropic without touching client code).

---

## 8. Claude Code Integration Architecture

### Strategic role of the connector

Claude Code is the IDE; the APOS connector is what makes Claude Code a **multiplayer, observable, memory-connected** experience. Without the connector, APOS is "yet another web SaaS." With it, APOS becomes the layer that turns individual Claude Code sessions into org-level intelligence.

### Connector deliverables

#### Layer 1: MCP server (stdio)

The connector exposes an MCP server to Claude Code with these tools (built on the same pattern as our shipped `tools/bc_mcp/`):

- `apos.tasks.list_my_day(filter?)` — fetch the user's current My Day queue
- `apos.tasks.get_workspace(task_id)` — fetch one task's structured workspace + prompt + recent comments
- `apos.tasks.complete(task_id, evidence?)` — mark task complete with optional artifact
- `apos.tasks.create(workspace_id, content, ...)` — create a new task
- `apos.assets.search(query, kind?)` — search Library
- `apos.assets.install(asset_id, install_path?)` — install a skill/MCP locally
- `apos.assets.publish(local_path, kind, visibility)` — publish from local to Library
- `apos.workflows.start_recording(name)` — begin Mirror Mode
- `apos.workflows.stop_recording()` — end Mirror Mode, derive a workflow blueprint
- `apos.presence.set(status, current_activity?)` — manual status override
- `apos.knowledge.query(question)` — ask the org memory graph
- `apos.agents.list_online()` — see who else (human + AI) is available right now

#### Layer 2: File watcher + activity reporter

A small Rust daemon (Tauri-side process) that:

- Watches a list of user-approved folders.
- Reports current Git branch + file count + last commit to presence.
- Streams "I'm editing `X` in project `Y`" activity events (filename only, never contents).
- Detects when the user opens Claude Code and adjusts presence to `coding`.

#### Layer 3: Approval gateway

For high-trust actions (vault read, marketplace install of unsigned asset, cross-org share):

- Connector pops a system notification with `[Approve] [Deny] [Always allow]`.
- Approval records to platform with `correlation_id` linking back to the originating request.
- Denials are not silent — emit `approval.denied` event for audit.

#### Layer 4: Workflow recorder (Mirror Mode)

When `apos.workflows.start_recording` is called:

- Connector starts capturing: keystrokes (count only, never content), file edits (which files, which ranges), shell commands (full command), Claude Code prompts (the user-written part), Claude Code tool calls (which tools).
- Stops on explicit `stop_recording` or 4-hour timeout.
- Result uploaded as a recording archive.
- Recording Engine processes async: extract pattern, propose workflow blueprint, notify owner.

### Distribution

| OS | Format | Signing | Auto-update |
|---|---|---|---|
| Windows | MSIX | Authenticode | Built-in MSIX update |
| Mac | DMG + .app | Apple notarized | Sparkle framework |
| Linux | AppImage + DEB + RPM | GPG signature | AppImage UpdateInformation |

Build pipeline: GitHub Actions cross-compile via Tauri's CI; sign in each platform's matrix slot; publish releases to GitHub; connector checks for updates daily.

### Why Tauri over Electron

Already covered in §2 stack table — but the kicker is binary size. A 200MB Electron download has the highest install-abandonment rate of any feature. A 12MB Tauri binary is invisible.

---

## 9. UX Architecture

### Top-level navigation

```
┌─────────────────────────────────────────────────────────────────────┐
│ Colaberry · APOS    [Now] [My Day] [Workspaces] [Library] [Graph]  │
│                     [Marketplace] [Connector] [Profile]            │
│                                          👤 Ali · 🟢 · Cmd+K       │
└─────────────────────────────────────────────────────────────────────┘
```

### Page-by-page sketches

#### `/now` — Presence + Activity Feed (Discord/Slack hybrid)

Three columns:

```
┌──────────────┬────────────────────────────────┬──────────────┐
│ ORG TREE     │ ACTIVITY FEED                  │ WHO'S ONLINE │
│              │                                │              │
│ Colaberry    │ 🟢 Karun completed task        │ Humans (3)   │
│ ├ Pilots     │   "Negotiation engine — tier" │ 🟢 Karun     │
│ │ ├ Karun ✓  │   2m ago · Phase B             │ 🟢 Kes       │
│ │ └ Kes ✓    │                                │ 🟡 Ali (idle)│
│ ├ Projects   │ 🤖 Karun Clone is executing   │              │
│ │ ├ ShipCES  │   "Build proposal matrix"      │ AI (5)       │
│ │ ├ Patriot  │   67% · ETA 8m                 │ 🟢 Karun-cl. │
│ │ └ AI Prod  │                                │ 🟡 Kes-cl.   │
│ └ Library    │ 📥 New skill published         │ ⚪ Ali-clone │
│              │   "RFP-Analyzer v2" by Ram     │              │
│              │   3 installs in last hour      │              │
└──────────────┴────────────────────────────────┴──────────────┘
```

#### `/my-day` (already shipped — extend with presence and AI peek)

Add to existing layout:
- Presence badge in top bar showing what AI agents are working on your behalf.
- "My agents currently working" row above KPIs.

#### `/workspaces/{org}/{project}` — Project view (NEW)

Slack-channel-style with structured messages:

```
┌──────────────┬────────────────────────────────────────────────┐
│ # general    │ #shipces · Project channel                    │
│ # planning   │ ────────────────────────────────────────────  │
│ # build      │ Ali · 2m ago                                  │
│ # research   │ Asking: who has context on the stager export?│
│              │   [Karun is typing...]                       │
│ MEMBERS      │                                              │
│ 👤 Ali       │ Karun · just now                              │
│ 👤 Karun     │ Pulled it together — see PR #42              │
│ 🤖 Karun-cl. │                                              │
│ ⊕ Invite     │ 🤖 Karun-clone · 5m ago                       │
│              │ Workflow "Triage incoming RFP" completed.    │
│              │ 4 new items in /my-day for Ali.              │
│              │                                              │
│ TASKS        │ ────────────────────────────────────────────  │
│ 14 open      │ [type a message...] [+attach] [@mention]     │
└──────────────┴────────────────────────────────────────────────┘
```

#### `/people/{user}` — User profile (NEW)

```
┌───────────────────────────────────────────────────────────────┐
│  👤 Ali Muwwakkil                       Status: 🟢 Online    │
│  Colaberry · Managing Director          Follow • Message     │
│                                                              │
│  Skills built (7)  Agents owned (1)  Workflows (4)          │
│  Reputation: 0.92  ·  Adoption: 142 installs                │
│                                                              │
│  ──── Recent ───────────────────────────────────────────    │
│  Published "RFP triage skill v3"  · 2d ago                   │
│  Completed 31 tasks across 5 projects  · this week           │
│  Followed by Karun, Kes, Ram                                 │
│                                                              │
│  ──── Agents ────────────────────────────────────────────    │
│  🤖 Ali-clone   ⚪ Offline      [Wake up] [View memory]      │
└───────────────────────────────────────────────────────────────┘
```

#### `/knowledge` — Org memory graph browser (NEW)

Two modes:
- **Question mode**: natural language ("show workflows that increased proposal success"). LLM converts to Cypher, runs, renders nodes.
- **Browse mode**: pick a node, see its incoming/outgoing edges, click to navigate.

Visualization: react-force-graph or D3. Server-rendered for the question; client-side for interactive browse.

#### `/marketplace` — Public asset discovery (NEW)

Looks like a curated app store with category filters. Asset cards show: name, description, author, install count, reputation score, last updated, install button. One-click install pushes to user's Library + (if technical) syncs to their workspace.

### Universal command palette (Cmd+K)

Inspired by Linear / Raycast. From anywhere:

```
> ___________________________
   Search across:
   - Tasks (yours + assigned to you)
   - People (online first)
   - Agents (online first)
   - Assets (skills, prompts, workflows)
   - Workspaces + channels
   - Knowledge graph nodes
   - Recent activity
```

### Design system

Use the existing `static/css/colaberry-tokens.css` palette. The visual language already established (greens for action, blues for identity, ambers for warning, reds for blockers) is fine.

### Accessibility

- All interactive elements keyboard-reachable.
- ARIA labels on icon-only buttons (we have ❌ ⚠ 🟢 emoji buttons — they need names).
- Color is never the only signal; pair with text or icon.
- Reduced motion preference respected.

---

## 10. Scaling Architecture

### Capacity targets

| Phase | Orgs | Users | Concurrent WS connections | LLM calls/day | Notes |
|---|---|---|---|---|---|
| MVP | 1 | 50 | 50 | ~500 | Single Hetzner VM |
| Alpha | 3 | 200 | 200 | ~3,000 | Same VM, headroom checked |
| Beta | 10 | 1,000 | 1,000 | ~30,000 | Add 2nd VM for Realtime Gateway |
| V1 | 50 | 5,000 | 5,000 | ~150,000 | Load balanced, Postgres replica |
| V2 | 500 | 50,000 | 50,000 | ~2,000,000 | Multi-region, Postgres clustered |
| V3 | 5,000 | 500,000 | 500,000 | ~20,000,000 | Full distributed |

### Bottlenecks and remediation

| Bottleneck | When it bites | Fix |
|---|---|---|
| FastAPI single process | ~500 concurrent users on one box | uvicorn workers + horizontal scale via Caddy |
| Postgres write contention | ~50,000 writes/min | Connection pooling (PgBouncer); partition `events_log` by org+month |
| Realtime Gateway memory | ~10,000 WS connections per process | Multiple gateway processes; sticky session via Caddy |
| LLM rate limits | Per-provider | Multi-provider routing; queue + retry; cost-based degradation |
| S3 bandwidth | Marketplace popular asset bursts | CDN (Cloudflare R2 with caching) |
| Graph queries on large org | >10M nodes per org | Materialized view of common queries; precomputed reputation aggregates |
| Anticipation Engine LLM cost | Linear with # active users | Sampling — only run for top-K users by recent activity |

### Geographic distribution

Stay single-region (Hetzner Finland) until enough non-EU customer demand justifies it. At that point: dual-region with active-active Postgres logical replication, eventual-consistency events_log, region-local S3.

---

## 11. Development Roadmap

### Estimation assumptions

- Team of 4: 2 backend, 1 frontend, 1 infra/security
- 2-week sprints
- 70% of capacity goes to feature work, 30% to incident response + tech debt
- LLM costs amortized at $500/month per active org

### MVP (months 1-3) — "Now + connector basics"

**Goal**: 1 org (Colaberry), 50 users, basic presence + connector + My Day extended.

| Ticket | Effort (weeks) | Risk | Depends on |
|---|---|---|---|
| MVP-1 Presence schema + Redis pub/sub | 1 | low | — |
| MVP-2 Realtime Gateway (Go process) | 2 | medium | MVP-1 |
| MVP-3 `/now` page (server-rendered + WS) | 2 | low | MVP-2 |
| MVP-4 Connector skeleton (Tauri, Mac+Win) | 4 | high | — |
| MVP-5 Connector MCP server for Claude Code | 2 | medium | MVP-4 |
| MVP-6 Connector ↔ Realtime Gateway WSS | 1 | low | MVP-2, MVP-4 |
| MVP-7 `apos.tasks.*` tools for Claude Code | 2 | low | MVP-5, existing My Day |
| MVP-8 Per-resource consent UX | 2 | medium (UX-heavy) | MVP-4 |
| MVP-9 Events schema + `events_log` mirror | 1 | low | — |
| MVP-10 Anticipation v0 (LLM suggestion: "next task from your queue") | 2 | medium | existing My Day |

**Total**: ~19 person-weeks. With 4 engineers at 70% utilization: 9-week wall-clock. MVP ships month 3.

**Risks**:
- Connector signing/distribution on Day 1 is hardest. Mitigation: start with Mac+Win only; Linux at Alpha.
- Per-resource consent UX is the biggest privacy lever. Mitigation: prototype with 5 internal users week 1; iterate before wider release.

### Alpha (months 4-6) — "Workflows + recording"

**Goal**: 3 orgs, 200 users, Mirror Mode + workflow recording + basic asset library extensions.

| Ticket | Effort | Risk |
|---|---|---|
| ALPHA-1 Recording Engine (async) | 3 | high |
| ALPHA-2 Connector file watcher + recording capture | 3 | high |
| ALPHA-3 Workflow blueprint derivation (LLM) | 3 | high |
| ALPHA-4 Workflow review + edit UI | 2 | medium |
| ALPHA-5 Asset registry extensions (versions, manifests) | 2 | low |
| ALPHA-6 Asset publish + install flow | 3 | medium |
| ALPHA-7 Activity feed | 2 | low |
| ALPHA-8 First reputation signals | 2 | medium |
| ALPHA-9 Linux connector | 2 | medium |
| ALPHA-10 Universal command palette | 2 | low |

**Total**: ~24 weeks. Alpha ships month 6.

**Risks**:
- Mirror Mode pattern derivation is the make-or-break feature. If the workflows it generates are bad, users won't trust the platform. Mitigation: design pattern engine to be conservative — better to under-detect than over-detect.

### Beta (months 7-9) — "Marketplace + social + reputation"

**Goal**: 10 orgs, 1,000 users, public marketplace, social features, reputation feeds discovery.

| Ticket | Effort | Risk |
|---|---|---|
| BETA-1 Marketplace publish flow + signing | 3 | high (security) |
| BETA-2 Marketplace discovery UI | 2 | low |
| BETA-3 Sigstore signature verification | 2 | medium |
| BETA-4 Asset scanner (regex + LLM for credential leaks) | 3 | medium |
| BETA-5 Social profiles + follow | 2 | low |
| BETA-6 Activity feed (org-wide + following) | 2 | low |
| BETA-7 Reputation scoring v2 (multi-dimensional) | 3 | medium |
| BETA-8 Cross-org guest collaboration | 3 | high (security) |
| BETA-9 Notification service (email, BC, Slack) | 2 | low |
| BETA-10 Onboarding flow for new orgs | 2 | medium |

**Total**: ~24 weeks. Beta ships month 9.

**Risks**:
- Marketplace security. A single malicious asset breaks user trust forever. Mitigation: every asset signed; every install requires explicit user confirmation; security scanner runs synchronously; 24h delay for first publish of any author.

### V1 (months 10-12) — "Knowledge graph + AI agents at scale"

**Goal**: 50 orgs, 5,000 users, full org memory graph + AI agent platform.

| Ticket | Effort | Risk |
|---|---|---|
| V1-1 AGE schema + Cypher API | 3 | high |
| V1-2 Knowledge graph browse UI | 3 | medium |
| V1-3 Knowledge question→Cypher LLM | 3 | medium |
| V1-4 AI agent platform: containerized agents | 4 | high |
| V1-5 Agent permission model + capability tokens | 3 | high (security) |
| V1-6 Agent memory bank + RAG | 4 | high |
| V1-7 Anticipation Engine v1 (proactive suggestions) | 3 | medium |
| V1-8 Multi-region prep (read replicas) | 3 | medium |
| V1-9 Performance + cost dashboards for org admins | 2 | low |
| V1-10 SOC 2 prep | 4 | high (process) |

**Total**: ~32 weeks. V1 ships month 12.

### V2 (Y2) — "Mobile + enterprise"

Mobile app for presence + messaging + approvals. Enterprise features: SSO via SAML/OIDC, audit log export, data residency choices, custom branding.

### V3 (Y3) — "Federation"

Cross-org federation. APOS orgs can choose to federate (selectively) — share certain assets, reputation scores carry across, knowledge graph queries can span federated orgs with consent.

---

## 12. Competitive Analysis

### Positioning matrix

```
                    AI-native ───────────────────────────► AI-augmented
                       (AI as peer)              (AI as helper)
                          │
        High social │  APOS                       Slack + Slack AI
        (multi-user)│                              Discord + bots
                    │
                    │  GitHub                      Notion AI
                    │  (review = social)
                    │
        ────────────┼─────────────────────────────────────────────►
                    │
        Low social  │  Cursor                      Claude Code (local)
        (single-user│  Windsurf                    Copilot
        IDE / tool) │  LangGraph (workflows)        n8n
                    │  Obsidian                     ChatGPT
                    │
                    ▼
```

### Per-competitor analysis

#### vs. Slack / Discord

- **They have**: enormous user base, real-time messaging, voice/video, integrations marketplace.
- **They don't have**: AI as first-class user (Slack AI summarizes messages; doesn't execute work). Asset versioning. Memory beyond search. Workflow recording.
- **APOS advantage**: AI agents appearing alongside humans in presence + channels, with structured task semantics not freeform chat.

#### vs. GitHub

- **They have**: code versioning, PR workflows, Actions for automation, Copilot for AI assist.
- **They don't have**: presence (PR threads are async), AI workflows (Actions are deterministic), org-wide knowledge graph.
- **APOS advantage**: GitHub for AI artifacts (skills, prompts, agents) + the social/presence layer GitHub never built (because they didn't need to for code).

#### vs. Notion

- **They have**: rich docs, databases, AI summarization, embeds.
- **They don't have**: presence, executable workflows, AI agents that act, marketplace.
- **APOS advantage**: Notion is where you write down what you'll do; APOS is where AI actually does it.

#### vs. Cursor / Windsurf

- **They have**: AI-augmented IDE with deep file awareness.
- **They don't have**: multi-user, org memory, marketplace, presence, workflow capture.
- **APOS advantage**: Cursor is the IDE; APOS is the layer that turns Cursor sessions into org-level intelligence.

#### vs. Claude Code

- **They have**: best-in-class AI coding agent, MCP support.
- **They don't have**: multi-user, presence, marketplace, knowledge graph, workflow capture.
- **APOS advantage**: APOS extends Claude Code with the multiplayer/social/memory layer Anthropic explicitly hasn't built (they're focused on the agent core).

#### vs. LangGraph / n8n

- **They have**: programmable workflows + agent orchestration.
- **They don't have**: social, presence, knowledge graph, marketplace as the front door.
- **APOS advantage**: LangGraph/n8n are execution engines; APOS is the org-context wrapper. APOS could even use LangGraph as its workflow runtime.

### Strategic differentiators (what APOS uniquely offers)

1. **Humans and AI in shared presence**. Nobody else has "Karun is online; Karun-clone is executing task X" in one feed.
2. **Mirror Mode → workflow blueprint**. Watch any user work, generate a reusable workflow + AI agent. No competitor does this; LangGraph requires hand-coding workflows.
3. **Organizational memory graph**. Persistent, queryable, multi-modal (humans, agents, workflows, outcomes). Notion has docs; APOS has connected meaning.
4. **Marketplace specifically for AI work product**. Not code (GitHub), not docs (Notion), not workflows (n8n) — but the full mix.
5. **Local connector + cloud platform**. Cursor is local; Slack is cloud; APOS bridges both.

### Defensibility

- **Network effects**: more users → more assets → better marketplace → more users.
- **Switching cost**: org memory graph is hard to export and recreate elsewhere.
- **Data**: aggregated (privacy-respecting) usage data improves Anticipation Engine for everyone.
- **Integration depth**: as APOS grows, integration cost for competitors grows superlinearly.

### Risks to defensibility

- Anthropic could ship a multi-user Claude Code with their own MCP marketplace. Mitigation: be faster + serve customer needs Anthropic won't (Slack-style, knowledge graph).
- Microsoft (Copilot Workspace) could ship a similar play. Mitigation: open the standards (Mirror Mode spec, knowledge graph schema) before they do.
- Slack could add native AI agent identity. Mitigation: ship first; APOS becomes "the AI-first one" the same way Slack became "the better one" vs. HipChat.

---

## 13. Risk Inventory

### Technical risks

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Connector crashes / hangs in production for a percentage of users | High | Medium | Crash telemetry; auto-restart; staged rollout (10% → 50% → 100%) |
| WS Gateway memory leak with many idle connections | Medium | Medium | Memory budget per process; auto-recycle every 24h |
| AGE Cypher queries get slow on large graphs | Medium | Medium | Materialized views; per-org graph isolation |
| Postgres + AGE has fewer eyeballs than Neo4j; we hit unmaintained bugs | Medium | Low | Test thoroughly in Alpha; have a Neo4j escape hatch designed |
| Tauri ecosystem matures slower than Electron | Low | Medium | Acceptable; can switch to Electron if needed |

### Privacy risks

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Connector reads files user didn't intend to share | High | High (without UX work) | Per-resource consent; recordable + revokable grants |
| LLM provider trained on our users' assets | Medium | Medium | Use zero-retention providers (Anthropic does this with API); contractual provisions |
| Org memory graph reveals competitive intelligence | Medium | Low (with tenant isolation) | Tenant filter at AGE layer; audit cross-tenant queries; pen-test before V1 |
| Mirror Mode captures things the user doesn't realize | High | Medium | Recording indicator always visible; "discard" button per event; opt-in not opt-out |

### Scaling risks

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| LLM cost grows superlinearly with users | High | Medium | Prompt caching; per-user budgets; cheap-first routing |
| Realtime Gateway becomes single point of failure | High | Medium | Multiple gateway processes; sticky session via Caddy; auto-failover |
| Marketplace popular asset overwhelms S3 | Low | Low | CDN cache at edge |
| Events log unbounded growth | Medium | Medium | 90-day retention for streams; partition + archive to S3 for cold |

### Adoption risks

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Users won't install local connector | High | High | Web-first MVP; connector for power-user features only at first |
| Users find presence "creepy" (am I being watched?) | High | Medium | Invisible mode; opt-out toggles per feature; transparency dashboard |
| Org admins don't trust marketplace (won't install assets) | Medium | Medium | Signed manifests; sandbox dry-run; community reporting; trust badges |
| First mover advantage lost if Anthropic/Microsoft ships similar | High | Medium | Move fast; open standards to commoditize their plays |

### Business model risks

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Marketplace doesn't develop (chicken-and-egg) | High | High | Colaberry seeds with high-value assets at launch; revenue share to early publishers |
| Pricing too low: can't fund LLM costs | High | Medium | Per-seat + LLM passthrough above plan; cost dashboards for transparency |
| Pricing too high: kills adoption | Medium | Medium | Free tier for individuals; paid for orgs |
| Enterprise sales cycle too long to fund growth | Medium | Medium | Self-serve mid-market; enterprise opportunistic |

---

## 14. Decisions Required from Ali (before any code)

### Strategic

1. **Local connector at MVP or Beta?** My recommendation: Beta. Build conviction with web-only MVP first.
2. **Open the knowledge graph schema?** My recommendation: yes (open schema, closed inference layer).
3. **Marketplace business model**: free + paid mix, revenue share, exclusive publishing? My recommendation: free + paid items, 70/30 split favoring publishers, no exclusivity.
4. **Cross-org federation in V3?** My recommendation: yes; it's a strategic moat.

### Architectural

5. **Postgres + AGE OR Neo4j for graph?** My recommendation: AGE, with documented escape hatch.
6. **Tauri OR Electron for connector?** My recommendation: Tauri.
7. **Single binary monolith OR microservices from Day 1?** My recommendation: monolith; extract when measurement shows pain.

### Product

8. **AI agent permission model — capability-based OR role-based?** My recommendation: capability-based (more flexible, easier to audit).
9. **Mirror Mode default: opt-in OR opt-out per session?** My recommendation: opt-in; user must explicitly start recording.
10. **Default presence visibility: org OR public?** My recommendation: org-only; cross-org follows are invite-only.

### Business

11. **Pricing structure: per-seat, per-org, usage-based, hybrid?** My recommendation: per-seat with usage overage for LLM and AI agent runtime.
12. **Public launch timing: target date for Beta open access?** My recommendation: month 7 of build, soft launch with 5-10 friendly orgs.

---

## 15. Recommended Next Step

This document needs to be reviewed by Ali in one sitting. If approved:

1. **Week 1**: We pin the decisions above (or document explicit deviations).
2. **Week 2**: I produce a sequenced JIRA-style ticket list for the MVP from §11.
3. **Week 3**: Start MVP-1 (presence schema + Redis pub/sub).

If not approved: feedback loop on what's wrong, revise this doc, re-review.

**Do not start MVP code before Ali signs off on §13 (risk inventory) and §14 (decisions required).** Architecture without owner-signed risk acceptance is theater.
