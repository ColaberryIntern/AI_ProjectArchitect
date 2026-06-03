# APOS — 90-Day MVP Execution Plan

**Companion to:** `apos-architecture-v1.md`
**Status:** Draft execution plan; awaiting Ali sign-off on §1 (decisions to lock).
**Goal:** Ship APOS MVP in 90 days — 1 org (Colaberry), 50 users, presence + connector basics + extended My Day.
**Start date:** Monday after sign-off.
**Target ship date:** 90 calendar days from start.

---

## 1. Decisions to lock before Week 1

These are blocking. I will not start implementation until Ali signs off, even if it pushes the start date. Architecture without owner-signed risk acceptance is theater.

| # | Decision | My recommendation | Why it matters now |
|---|---|---|---|
| D1 | Connector at MVP or Beta? | **Beta** (web-only MVP first) | Drops MVP scope ~30% if Beta; pulls in 4 weeks of Tauri/signing work otherwise |
| D2 | Postgres+AGE or Neo4j for graph? | **AGE** (Postgres extension) | Affects MVP-9 schema; can't easily switch later |
| D3 | Tauri or Electron for connector? | **Tauri** | Only matters if D1 = MVP; defer if D1 = Beta |
| D4 | Org memory graph schema: open or closed? | **Open schema, closed inference** | Affects API surface decisions in Alpha; doesn't block MVP |
| D5 | LLM provider for Anticipation Engine: Anthropic or OpenAI primary? | **Anthropic primary** (better for our domain), OpenAI fallback | Both already wired today; just pick primary |
| D6 | First-class agent permission model: capability tokens or role-based? | **Capability tokens** | Affects MVP-10 design; capability tokens are more work upfront but right long-term |
| D7 | Default presence visibility: org or public? | **Org only** | Privacy default; can loosen later, hard to tighten |
| D8 | LLM budget per org for MVP: cap at? | **$200/month for Colaberry MVP** | Need a number for the cost guardrail. $200 covers ~50,000 gpt-4o-mini calls + ~5,000 gpt-4o focus calls |

**Action**: Ali reviews + responds with sign-off or deviations. If everything stays my recommendation, MVP starts the next Monday.

---

## 2. What ships vs. what doesn't (MVP)

### Ships in MVP (90 days)

| Capability | What it does | Replaces |
|---|---|---|
| **`/now` page** | Real-time presence: who's online, what humans are doing, what AI agents are executing, activity feed | (new) |
| **Presence schema + Redis pub/sub + WS gateway** | Backbone for everything realtime | (new) |
| **My Day extended** | Adds a presence row at top: "Your agents are working on...". Auto-refresh via WS instead of manual ↻ Sync | Existing My Day v3 |
| **Multi-project sync via per-user BC OAuth** | Drops the bucket-paste workaround; full project visibility | Current multi-bucket form |
| **Activity feed** | Org-wide stream of work events | (new) |
| **Anticipation Engine v0** | "Your top suggestion right now" — LLM-driven, based on your queue + recent activity | (new) |
| **First-class AI agent records** | Each user gets a default AI clone; visible in presence + profile | Current bc_ai_clone_name field |
| **AI agent capability tokens** | Tokens scoped to subset of inviter's permissions, 1hr TTL | (new) |

### Does NOT ship in MVP (deferred to Alpha / Beta / V1)

- Local connector (Tauri binary) — Beta
- Mirror Mode / workflow recording — Alpha
- Marketplace publish flow — Beta
- Knowledge graph browser — V1
- Cross-org federation — V3
- Mobile app — V2

### Why this scope

The MVP must answer ONE question: **"Do humans and AI agents working in shared presence + my-day-style triage make me more effective?"** If yes, we have a wedge to expand. If no, kill the project before spending money on connector + marketplace.

Everything in MVP serves answering that question. Connector and marketplace are amplifiers — they need a working core first.

---

## 3. 90-day sprint plan

Six 2-week sprints. Each sprint ends with a deploy to prod + a 1-page email to Ali summarizing what shipped, what's verified, what's deferred. Per the existing Phase email pattern.

### Sprint 1 (Weeks 1-2): Presence backbone

**Goal**: Presence schema + Redis pub/sub + WS gateway running. Hello-world WS connection from a logged-in user updates a `presence` row.

**Deliverables**:
- `presence` and `agent_presence` Postgres tables (per architecture doc §3 data model)
- Redis pub/sub channel scheme: `presence:org:{org_id}`
- WS gateway as a Go process at `:8001`; Caddy proxies `/ws` → `:8001` with sticky session
- Web app emits `presence.online` / `presence.offline` on session lifecycle
- `/now/status` debug page shows current presence state for the org

**Acceptance**: Ali logs into Colaberry → his row in `presence` flips to `online` within 1 second → debug page shows him + the timestamp.

**Risks**: WS sticky session via Caddy needs verification under load. Mitigation: load test with 100 simulated connections in week 2.

### Sprint 2 (Weeks 3-4): `/now` page (read side)

**Goal**: A working `/now` page that shows presence + activity feed for the user's org. No interactive features yet.

**Deliverables**:
- `/now` route + server-rendered template (3-column layout per architecture doc §9)
- Activity feed reads from `events_log` table (cap last 100 events)
- WS client (small vanilla JS) subscribes to org presence updates
- "Who's online" panel updates in realtime
- Org tree on the left navigates between projects (links only; project pages don't exist yet)

**Acceptance**: Ali opens `/now`, sees himself online, sees an activity row for his most recent My Day task completion. Karun opens `/now` in another browser; Ali sees Karun appear online within 1 second.

### Sprint 3 (Weeks 5-6): First-class AI agents + capability tokens

**Goal**: Move AI agents from "string field on User" to first-class records. Capability token issuance + verification working.

**Deliverables**:
- `ai_agents` table per architecture doc §7
- Migration: existing `User.bc_ai_clone_name` → `ai_agents` row with kind=`basecamp_clone`
- Capability token issuer: derives a short-lived token (1hr TTL) from user session + scope subset
- Verifier in all server routes that need to check agent permissions
- `/agents/{agent_id}` profile page (read-only)
- AI agent presence: `executing` / `idle` / `offline` (faked for MVP — real status comes when connector ships)

**Acceptance**: Ali has a default AI clone visible on his profile + on `/now` + on `/agents/{id}`. Capability token is issued when an agent acts on his behalf and verified on every call.

### Sprint 4 (Weeks 7-8): My Day refresh + AI agent integration

**Goal**: My Day is upgraded to APOS-native. Real-time updates via WS. AI agent activity is visible. Cost dashboard.

**Deliverables**:
- My Day page subscribes to user's task events via WS; new tasks appear without refresh
- "Your agents are working on..." section at top of My Day shows real-time agent status
- AI Orchestrator service (extracted from the LLM enhance code we shipped); records cost per call to Postgres
- `/admin/org/llm-cost` page for org admins: cost trend, top spenders, anomaly highlights
- Capability token enforcement: agent can only see/act on tasks within its capability scope

**Acceptance**: Ali opens My Day; the page shows his agent's current state. Cost dashboard shows last 30 days of LLM spend. A test agent attempt to read a task outside its scope is rejected with a clear error.

### Sprint 5 (Weeks 9-10): Multi-project BC sync via per-user OAuth

**Goal**: Drop the bucket-paste workaround. Each user runs a BC OAuth flow to authorize APOS with their own BC token. Sync uses the user's own token; full project visibility.

**Deliverables**:
- BC OAuth client registered for APOS
- `/connect/basecamp` flow: redirect to BC consent, callback captures token, stores in vault
- Sync service uses the user's own BC token (not CB System); `projects.json` now returns all of Ali's projects
- Migration: existing `User.bc_extra_buckets` field deprecated; data migration to per-user OAuth
- 14-day refresh handling: connector token expiry triggers re-auth flow

**Acceptance**: Ali revokes the manual buckets, clicks "Connect Basecamp," authorizes APOS in BC consent screen, returns to APOS, clicks Sync — all his projects appear, ~200+ todos pull in.

**Risk**: BC OAuth client registration requires admin access in the BC account. May need to coordinate with whoever has BC admin (likely Ali himself).

### Sprint 6 (Weeks 11-12): Anticipation Engine v0 + Activity Feed depth

**Goal**: The platform proactively surfaces "what to do next" using LLM analysis of the user's queue + activity. Activity feed enriched with structured event types.

**Deliverables**:
- Anticipation Engine: LLM that looks at user's My Day queue + recent activity + active agents and surfaces 1 top recommendation
- "Suggested for you" banner on `/now` and My Day
- Activity feed: structured event types (task completed, agent started, asset published, decision made) with kind-specific rendering
- Suggestion accept/dismiss telemetry (informs reputation later)
- 1-page Ali briefing email cron — Mon/Wed/Fri at 7am CT, summary of weekly trajectory

**Acceptance**: Ali sees a "Suggested for you" recommendation on `/now`; it's specific (not "you should be productive"); accept/dismiss buttons work; dismiss feedback influences future suggestions.

### Sprint 7 (Week 13): Hardening + soft launch

**Goal**: Stress test, security pen-test, fix top 10 paper cuts, write user docs, soft-launch to 5 internal Colaberry team members.

**Deliverables**:
- Load test: 100 concurrent users + 100 concurrent WS connections sustained for 1 hour
- Security pen-test (capability tokens, tenant isolation, cross-org leak)
- Top 10 UX paper cuts (collected through Sprint 1-6 user feedback)
- User documentation (5 pages: getting started, presence, agents, library, cost)
- Soft launch: invite 5 Colaberry users (Karun, Kes, Ram, David, JJ)
- Launch retro: what to expedite for Alpha

---

## 4. Team + roles

### Recommended composition for MVP

| Role | FTE | Responsibilities |
|---|---|---|
| **Backend engineer (you/me/Claude Code)** | 1.0 | All Python/FastAPI, Postgres, integration with LLM providers |
| **Frontend engineer** | 0.5 | Templates, WS client JS, command palette, CSS polish |
| **Infrastructure / SRE** | 0.3 | Deploy, monitoring, security pen-test prep |
| **Designer / UX writer** | 0.2 | Visual design for `/now`, agent profiles, presence indicators |
| **Product owner (Ali)** | 0.2 | Sign-off on decisions, weekly review |
| **Customer / pilot user** | (5 users at Colaberry) | Real usage in Sprint 7 |

### If team is smaller (just Ali + Claude Code)

| Adjustment | Trade-off |
|---|---|
| Drop UX polish to deferred items | Slightly worse first impression at launch |
| WS gateway in Python (FastAPI WS support) instead of Go | 5-10% lower perf; manageable for 50 users |
| Skip BC OAuth in Sprint 5; reuse current vault flow | No multi-project visibility at MVP; defer to Alpha |
| Skip cost dashboard in Sprint 4 | No per-org cost visibility until Alpha |

Realistic timeline with Ali + Claude Code only: **~120 days instead of 90.** Mark this clearly on the calendar.

---

## 5. Concrete first-week tasks

Assuming D1=Beta (connector deferred) and Ali signs off on all D-recommendations.

**Day 1 (Mon)**:
- Create branch `feature/apos-mvp-presence`
- Sketch `presence` and `agent_presence` schemas in a docs/specs file
- Decide table location: same Postgres or new schema? (Recommend: same DB, `apos` schema)
- Wire Redis pub/sub publisher in existing FastAPI app

**Day 2 (Tue)**:
- Implement `presence.online` event emission on session login
- Implement `presence.offline` on session logout / idle 5 min
- Verify Redis pub/sub round-trip in dev

**Day 3 (Wed)**:
- WS gateway choice (if Go: spin up basic Go process; if Python: extend FastAPI WS routes)
- Caddy config update to proxy `/ws` with sticky session

**Day 4 (Thu)**:
- Web client JS for WS subscribe
- Initial `/now/debug` page rendering current presence state from Postgres

**Day 5 (Fri)**:
- End-to-end test: 2 users in 2 browsers, both flips visible on debug page
- Sprint 1 retro + Sprint 2 ticket grooming

**Sprint 1 success criterion**: Friday demo to Ali — open 2 browsers, log in as 2 users, debug page shows both online; close 1, watch it go offline within 5 minutes.

---

## 6. Budget envelope

### Implementation (90 days)

- Engineering (Ali + Claude Code only): Ali's time; opportunity cost only
- Engineering (with full team): ~$120-180K in 90 days
- Hetzner infra: existing VM probably handles MVP; add 1 small VM for WS gateway = +$30/month
- LLM: ~$200/month for Colaberry (per D8 above)
- OAuth (BC) registration: free

### After MVP — Alpha (months 4-6)

- Add Mirror Mode, asset library extensions, Linux connector
- LLM cost scales linearly with users; budget $1K/month at 200 users
- Hetzner: probably need to add a second VM = +$60/month

### After Beta (months 7-9)

- Marketplace + reputation + social
- LLM: $5K/month at 1,000 users
- Infra: +$300/month (CDN, replica DB)
- Security: SOC 2 prep starts here (~$30K for prep + audit if going for it)

---

## 7. What I (Claude Code, in this session) will do next

Pending Ali's sign-off on §1, here's what I'll do once approved:

1. **This week**: Write the Sprint 1 detailed ticket spec (presence schema, WS gateway choice, Redis pub/sub design) as `docs/specs/apos-mvp-sprint-1-presence.md`.

2. **Next week**: Start Sprint 1 implementation. First commit will be the presence schema migration + the `apos` Postgres schema namespace + the WS gateway scaffold.

3. **Weekly cadence**: Friday email to Ali (per existing Phase pattern) with: what shipped, what's verified, what's deferred, what I need from him for the next sprint.

---

## 8. Open questions for Ali (need answers to proceed)

These are not blockers for sign-off, but I'll ask before I make assumptions:

1. **Org admin vs. super-admin distinction**: Today Ali is super_admin for everything. APOS needs cross-org admin (for Colaberry-managing-Patriot scenarios). Same role or split?

2. **AI clone naming convention**: We have "CB System" today as the shared clone. Going forward: each user gets their own clone (Ali-clone, Karun-clone, Kes-clone)? Or keep CB System as the org-shared clone?

3. **Presence privacy for executives**: Will Ram / David / JJ want their `/now` activity visible to all of Colaberry, or just to their direct reports + executive peers?

4. **Anticipation Engine: aggressive or conservative?** First-draft Anticipation can either surface bold "do this NOW" recommendations or polite "you might consider..." Bold is more useful but riskier (wrong recommendations erode trust faster). Recommendation: start conservative, expand as accuracy proves out.

5. **Marketplace timing**: Beta or V1? Earlier marketplace = more publishers, but also more security risk per asset. My recommendation: Beta with strict scanner; V1 expands to community publishers.

---

## 9. Definition of done for MVP

The MVP is "done" — meaning ready to commit to Alpha — when ALL of these are true:

1. ✅ Ali + 4 Colaberry users have been using APOS for at least 2 weeks
2. ✅ Daily active usage: ≥3 of the 5 users open `/now` or `/my-day` every workday
3. ✅ At least 50 task completions tracked through the My Day → BC writeback flow
4. ✅ At least 10 "Anticipation Engine suggested" tasks acted on
5. ✅ Zero P0 bugs open (P0 = data loss, security, cross-tenant leak)
6. ✅ Average WS round-trip latency < 200ms p95
7. ✅ Average page load < 1500ms p95
8. ✅ Total LLM spend for Colaberry ≤ $200/month over 30 days
9. ✅ Pen-test signed off on capability tokens + tenant isolation
10. ✅ 1-page user docs published; 5 users say it was useful

If we hit all 10 by end of Week 13, we move into Alpha. If we miss 3+, we extend MVP by a month and address.

---

## Summary in one sentence

**APOS MVP ships in 90 days as: presence + AI agents as first-class users + extended My Day with realtime + anticipation — to validate that "humans and AI in shared presence" is the right wedge before investing in connector and marketplace.**
