# Build Index (inlined Build Index content)

Source: BC comment 9956776017 on todo 9956775973 (https://app.basecamp.com/3945211/buckets/7463955/todos/9956775973#__recording_9956776017)
Author: CB System | Posted: 2026-06-03T00:24:14.403Z

---

### Full Build Index (read top to bottom)

## AI_ProjectArchitect — Master Build Index

**Single source of truth for the advisor.colaberry.ai buildout. Read this first.**

This Basecamp list is the entire spec. A Claude Code agent in the [ColaberryIntern/AI_ProjectArchitect](https://github.com/ColaberryIntern/AI_ProjectArchitect) repo should be able to read this list cold and build the system end to end. The list is the contract. The repo is the canvas.

### Mission

Roll out **AI_ProjectArchitect** as the company-wide **AI Operating System** that connects every employee at every customer company to their work. Public surface: `https://advisor.colaberry.ai/library/`. Repo: `github.com/ColaberryIntern/AI_ProjectArchitect`. Today: a Python/FastAPI app with an ideation→document pipeline + 4 agent personas + an anonymous library of 552 items (56 use cases, 115 skills, 17 agents, 1 prompt, 363 MCP servers, 3 capabilities) across 6 categories. Target: a multi-tenant, multi-user platform with per-company approval workflows, per-user GitHub workspaces, and per-user tool-access provisioning.

### What changes vs today

| Surface | Today | After buildout |

|---|---|---|

| Library access | anonymous, "global" workspace | Google SSO login required; identity-aware filters |

| Tenancy | implicit single tenant | many companies coexist; per-company data + approvals |

| Approval | implicit "Colaberry approved" only | every company has its own `approved` set + filter chip; cross-company sharing is opt-in |

| Admin | none | `/admin/` console: user CRUD, tools-access matrix, moderation queue, add-new-company flow |

| Per-user workspace | none | each provisioned employee gets their own GitHub repo + scaffolded `.claude/skills/` + `.mcp.json` |

| Tools access | nothing wired | admin checks boxes for Gmail / Calendar / BC / CCPP / GitHub / Mandrill → tokens land in encrypted vault → user's `.mcp.json` generated |

| Publish workflow | no submission flow | draft → submitted → under_review → approved/rejected; per-company moderation queue |

| Sync | none | when an item is `{Company}` approved, it syncs to that company's downstream GitHub repo and shows up in same-company colleagues' libraries |

### Architecture pillars

1. **Tenancy & Auth** — `companies`, `users`, `roles`, `access_scopes` tables. Per-company isolation. Google SSO for the library.

2. **Admin Console** — `/admin/` for user provisioning, tools-access matrix, add-new-company, moderation queue.

3. **Per-user GitHub Workspace** — auto-create a repo per user on provisioning. Their space for their skills.

4. **Per-user Credentials Vault** — encrypted token storage. Admin checks tools → vault stores tokens → connectors wire up.

5. **Library UX upgrade** — identity badge, workspace switcher (All / My company / Mine / Other company), per-company approval filter chip, item-level approval badge with company name.

6. **Publish + Sync workflow** — per-company moderation queue, state machine, approve → sync. Visibility tiers: same-company-only / shared-public / shared-with-allowlist.

7. **Per-DRI agents (pilot track)** — Karun-agent and Kes-agent are the first two; YAML-per-person generalization in Phase 2 covers the rest.

### Build order (dependency-aware)

Sequencing matters more than calendar dates. The due dates in each ticket reflect this sequencing. Build top-down:

WEEK 1 (foundation)
  Auth 1 (data model)  ──┬──>  Auth 2 (SSO)  ────┬──>  Library 1 (approval filter)
                         │                       │
                         │                       └──>  Library 2 (identity badge + switcher)
                         │
  Provision 2 (vault) ───┘
                         
  Infra 1 (approval classification spec) ──>  feeds into Auth 1 data model
  Infra 4 (weekly cadence) ──> operational, not blocking

WEEK 2 (admin + provisioning)
  Admin 1 (admin console) ──┬──> Admin 3 (add-company flow)
                            ├──> Provision 1 (per-user GH workspace)
                            └──> Admin 2 (tools-access matrix)
  Infra 5 (Ram comms) ──> operational

WEEK 3 (data + library UX completion)
  Data 1 (backfill 552 items into new tenant model)
  Library 1 + Library 2  ──> ship
  Karun + Kes pilots underway (use the platform as it ships)

WEEK 4 (workflows + sync)
  Workflow 1 (per-company publish + queue)  ──┬──> Workflow 2 (cross-company visibility)
                                              └──> Infra 2 (library → GH sync)

WEEK 5 (deploy + docs)
  Deploy 1 (production cut-over)
  Infra 3 (user-facing onboarding docs)

WEEK 6+ (pilot completion + phase 2 + 3 + measurement)
  Karun + Kes 30-day retros
  Phase 2 launch (YAML per-person)
  Phase 2 onboard exec (Ali, Ram, David, JJ, Sohail)
  Phase 3 onboard rest (Swati, Sai Tejesh, Jackie, Taiwo, Aleem, Dhee, Vinay, Angie, Rashi)
  Day 90 retro
  Strategic eval: Colaberry AI Box
```

### All tickets at a glance (33 total, grouped + sequenced)

#### Foundation (build first)

| # | Ticket | Due | Why |

|---|---|---|---|

| Infra 1 | Approval classification + auto-sync spec | Jun 8 | Defines the data shape that Auth 1 will model |

| Auth 1 | Multi-tenant data model (Company / User / Role / AccessScope, per-company approval first-class) | Jun 9 | Foundation. Everything else depends on this |

| Auth 2 | Google SSO on advisor.colaberry.ai/library/ | Jun 11 | Identity gate for all subsequent UX |

| Provision 2 | Per-user credentials vault | Jun 11 | Needed before any token-storing happens |

#### Admin + Provisioning

| # | Ticket | Due | Why |

|---|---|---|---|

| Admin 1 | Admin console: user provisioning UI | Jun 13 | Operator entry point |

| Admin 3 | Add-new-company (tenant onboarding) flow | Jun 14 | Required for true multi-tenancy |

| Provision 1 | Per-user GitHub workspace auto-create | Jun 16 | Each user's space inside the project |

| Admin 2 | Tools-access provisioning matrix | Jun 19 | Wires per-user connectors |

#### Data + Library UX

| # | Ticket | Due | Why |

|---|---|---|---|

| Data 1 | Backfill 552 existing items into multi-tenant model | Jun 17 | Existing content gets correct ownership |

| Library 1 | Per-company "approved" filter UI + badge | Jun 20 | The payoff users feel daily |

| Library 2 | Identity badge + workspace switcher upgrade | Jun 20 | Pairs with Library 1 |

#### Workflows + Sync

| # | Ticket | Due | Why |

|---|---|---|---|

| Workflow 1 | Per-company publish workflow + moderation queue | Jun 24 | How items become approved |

| Workflow 2 | Cross-company visibility (same-co default, opt-in cross-co) | Jun 26 | How approved items propagate |

| Infra 2 | Library → AI_ProjectArchitect GitHub sync | Jun 27 | Approval → GitHub artifact |

#### Deploy + Docs

| # | Ticket | Due | Why |

|---|---|---|---|

| Deploy 1 | Deployment pipeline for auth+admin+multi-tenant cut-over | Jun 30 | Safe rollout |

| Infra 3 | User-facing onboarding docs (post Admin 1) | Jul 2 | What users see after login |

| Infra 4 | Weekly Ali + DRI rubric cadence | Jun 9 | Pilot operational cadence |

| Infra 5 | Ram all-hands comms (Earn/Learn/Bond/Save) | Jun 16 | People-side of rollout |

#### Pilots (run in parallel with platform)

| # | Ticket | Due | Why |

|---|---|---|---|

| Karun 1 | Karun PRD (30-min session) | Jun 7 | Defines what karun-agent should do |

| Karun 2 | Build karun-agent + /karun-dash | Jun 12 | Skill ships |

| Karun 3 | Wire /karun-dash to fire 30 min before 1:1 | Jun 14 | Calendar integration |

| Karun 4 | 4-week pilot iteration | Jul 2 | Learning loop |

| Karun 5 | 30-day retro + commit Colaberry-approved | Jul 4 | First batch of approved skills |

| Kes 1 | Kes PRD | Jun 7 | Same as Karun 1 |

| Kes 2 | Build kes-agent + /kes-dash | Jun 12 | Same as Karun 2 |

| Kes 3 | Wire /kes-dash to fire 30 min before 1:1 | Jun 14 | Same as Karun 3 |

| Kes 4 | 4-week pilot iteration | Jul 2 | Same as Karun 4 |

| Kes 5 | 30-day retro | Jul 4 | Same as Karun 5 |

#### Phase 2 + 3 + Strategic

| # | Ticket | Due | Why |

|---|---|---|---|

| Phase 2 launch | Generalize agent skill to YAML-per-person | Jul 7 | One skill, N people |

| Phase 2 DRI framing | DRI model + $90/$10 budget envelope | Jul 17 | Org structure |

| Phase 2 onboard exec | Ali, Ram, David, JJ, Sohail | Aug 1 | 5 PRDs + agents + dashboards |

| Phase 3 hard rule | No 1:1 without dashboard fired 30 min prior | Aug 16 | Process discipline |

| Phase 3 onboard rest | Swati, Sai Tejesh, Jackie, Taiwo, Aleem, Dhee, Vinay, Angie, Rashi | Aug 31 | 9 more PRDs + agents |

| Day 90 | Retro: engagement, decisions/wk, time-in-meeting/wk, retention | Sep 5 | Measure the lift |

| Strategic | Evaluate "Colaberry AI Box" as Q4 2026 product | Sep 30 | Ray's question, Tier C bet |

### Repo conventions (Claude Code: read this)

The `AI_ProjectArchitect` repo top-level structure:

/agents       # 4 persona definitions (Project Architect, Ideation Coach, etc.)
/directives   # human-readable SOPs per pipeline phase
/execution    # deterministic Python scripts
/app          # FastAPI web app (the library lives here, advisor.colaberry.ai is hosted from here)
/config       # env config
/templates    # rendering templates
/tests        # automated validation
/docs         # in-repo documentation
/output       # generated artifacts
/deploy       # deployment configs
/scripts      # ops scripts
/plugins      # extension points
```

Use these conventions when picking where to add new code:

- **Schema migrations** → wherever the FastAPI app's DB layer lives. If Alembic is set up, `/alembic/versions/`. If not, set it up as part of [Auth 1].

- **Models / ORM** → likely `/app/models/` or `/app/db/`. Look for existing patterns before creating new dirs.

- **Routes** → `/app/routes/` or `/app/api/`. Group by feature: `/admin/`, `/library/`, `/auth/`.

- **Services (business logic)** → `/app/services/` or alongside routes.

- **Frontend** → if there's a `/app/static/` or `/app/templates/` directory, that's the UI layer. Confirm before adding React unless already present.

- **Tests** → `/tests/` mirroring the source structure.

- **Per-pillar docs** → `/docs/architecture/{pillar}.md` (one per Auth, Admin, Provision, Library, Workflow).

### Conventions across tickets

Every ticket description has:

- **Goal** — one paragraph: what to build, why now

- **Acceptance** — bulleted, testable criteria

- **Depends on** — explicit references to other tickets

- (Where applicable) **Implementation hints** — file paths, library choices, schema sketches, named API endpoints

Every ticket's `due_on` reflects the recommended build sequence — earlier dates = build first. Calendar slip is fine; **dependency order is not negotiable.**

When you ship a ticket:

- Mark it complete in Basecamp via the API (use the same Basecamp token CB System uses).

- Post a comment on the ticket with: PR URL, key files changed, smoke-test result, any deviations from acceptance criteria.

- If you discover a missing piece, create a new ticket in this list with the same `[Tag N]` convention so the list stays the canonical spec.

### Open questions for Ali (block specific tickets)

| Q | Blocks | Default if no answer |

|---|---|---|

| Per-user repo namespace: `ColaberryIntern/{username}-workspace`? Or a per-user GH org? | Provision 1 | use `ColaberryIntern/{username}-workspace` |

| First non-Colaberry test company for multi-tenancy: Patriot? ShipCES? a real customer? | Auth 1, Data 1 | seed with a `demo-tenant` company in dev, switch to a real customer when one is paying |

| Default visibility for newly-approved items: `same-company-only` or `shared-public`? | Workflow 2 | `same-company-only` |

| Auth provider: Google SSO only, or also email magic link / SAML? | Auth 2 | Google SSO only for v1 |

| Per-user repo template: where does it live? | Provision 1 | create `ColaberryIntern/workspace-template` as part of Provision 1 |

These all have defaults — Claude Code should proceed with the defaults and surface in a PR comment when Ali should review.

### Companion artifacts in this list

- **Plan v1 doc** (closed CB System todo) — `[Plan v1] AI Operating System buildout` — initial gap analysis. Superseded by this Build Index.

- **Project Operating Config Rubric (v2)** — separate todolist (Colaberry Ops in Ali Personal). The cross-project tracker for every Basecamp project's CB-System config. Different artifact, not this list's concern.

### TL;DR

Build in this order: **Auth 1 → Auth 2 + Provision 2 → Admin 1 → Admin 3 + Provision 1 → Data 1 + Admin 2 → Library 1 + Library 2 → Workflow 1 → Workflow 2 + Infra 2 → Deploy 1 → Infra 3 (refocused)**, while the pilot track (Karun + Kes) runs in parallel.

End state: a logged-in user at `advisor.colaberry.ai/library/` sees their company's approved skills + the global library, can submit drafts that route to their company's moderation queue, has their own GitHub workspace, has their email/BC/calendar wired up, and can see what their colleagues across the company have published.