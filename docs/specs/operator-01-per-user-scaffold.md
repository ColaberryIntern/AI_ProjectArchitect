# [Operator 1] Per-user CLAUDE.md + PROGRESS.md scaffold

**Ticket:** Basecamp [9967247766](https://app.basecamp.com/3945211/buckets/7463955/todos/9967247766)
**Status:** Designed; not yet built
**Depends on:** [Auth 1] ✅ (multi-tenant data model), [Provision 1] ✅ (workspace repo)

---

## Why this exists

When an admin provisions a new user (Karun, Kes, an intern, a client engineer), that user's Claude Code session needs to:

1. Inherit Colaberry's company-wide doctrine (the org CLAUDE.md you write today)
2. Inherit their employer/tenant's specific policies (if different from Colaberry)
3. Layer their own personal preferences on top
4. Track their work in a personal PROGRESS.md just like Ali does today

Today there is one `CLAUDE.md` at the repo root and one `PROGRESS.md` next to it. Both are global. There is no mechanism to give each provisioned user their own layered configuration that picks up Colaberry policy automatically.

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Three-layer CLAUDE.md model: org > tenant > per-user | `claude_md_resolver.resolve(user_email)` returns concatenated layers in priority order |
| 2 | Org CLAUDE.md is the **single source of truth** for shared doctrine; auto-distributed to every workspace | Source: the public `ColaberryIntern/AI_ProjectArchitect/CLAUDE.md` on GitHub (raw URL). Plus narrative content scraped from www.colaberry.com, www.colaberry.ai, www.enterprise.colaberry.com. Pulled into each workspace at session start. |
| 3 | Tenant CLAUDE.md is optional; lives in each tenant's library config | `tenants.tenant_claude_md_path(tenant_id)` returns path or `null` |
| 4 | Per-user CLAUDE.md lives in the user's workspace repo at root | `<workspace>/CLAUDE.md` — user-editable, their personal preferences only |
| 5 | Per-user PROGRESS.md lives in the user's workspace repo at root | `<workspace>/PROGRESS.md` — tracks their work; same format as Ali's |
| 6 | Both files are auto-seeded at workspace creation with starter content | Extend `workspaces.provision_user_workspace()` to call `render_starter_claude_md(user)` + `render_starter_progress_md(user)` |
| 7 | Priority order on conflict: org wins, then tenant, then per-user | Documented at the top of every per-user CLAUDE.md: "Org-level rules in `~/.claude/colaberry/CLAUDE.md` override anything here." |
| 8 | Admin can edit org CLAUDE.md and all users pick up the change on next session | Session-start hook runs `git pull` on `colaberry-policy` repo before loading CLAUDE.md |

## Architecture

### File layout (in each user's workspace repo)

```
{username}-workspace/
├── CLAUDE.md                    ← per-user (their preferences)
├── PROGRESS.md                  ← per-user (their work log)
├── .claude/
│   ├── colaberry/               ← auto-pulled from colaberry-policy repo
│   │   └── CLAUDE.md            ← ORG doctrine, read-only, wins on conflict
│   ├── tenant/                  ← only if their employer has a tenant CLAUDE.md
│   │   └── CLAUDE.md
│   ├── skills/                  ← from Provision 1
│   └── settings.local.json      ← per-user Claude Code settings
└── .mcp.json                    ← from Provision 1
```

### Session-start hook

At the top of every Claude Code session, a small Python helper:

1. Fetches the latest org CLAUDE.md from the GitHub raw URL (`https://raw.githubusercontent.com/ColaberryIntern/AI_ProjectArchitect/main/CLAUDE.md`) — caches locally at `.claude/colaberry/CLAUDE.md` with 1h TTL
2. Scrapes the 3 colaberry.com sites (see Source list below) into `.claude/colaberry/knowledge/*.md` — caches with 24h TTL
3. Reads `.claude/colaberry/CLAUDE.md`, `.claude/colaberry/knowledge/*.md`, `.claude/tenant/CLAUDE.md` (if present), `./CLAUDE.md`
4. Concatenates with section header banners (`## Layer 1: Colaberry org policy (read-only)`, `## Layer 2: Colaberry shared knowledge`, `## Layer 3: {tenant} policy`, `## Layer 4: your personal preferences`)
5. Surfaces the concatenated text to the operator at session start

This is the **shared knowledge base prioritized over per-account learning** rule from Ali's spec.

### Source URLs (Ali-controlled, 2026-06-05)

| Source | Role | TTL |
|---|---|---|
| `https://raw.githubusercontent.com/ColaberryIntern/AI_ProjectArchitect/main/CLAUDE.md` | Org doctrine (single source of truth for rules) | 1h |
| `https://www.colaberry.com` | Marketing narrative, company positioning | 24h |
| `https://www.colaberry.ai` | Consulting / services positioning | 24h |
| `https://www.enterprise.colaberry.com` | Enterprise AI platform narrative | 24h |

Lose admin access to GitHub → the raw fetch 401s → Claude Code refuses to start (the "lose access = disconnect" requirement). The 3 sites are public so they keep working, but without the org CLAUDE.md the session is gated.

### Starter content

`render_starter_claude_md(user)` produces a per-user CLAUDE.md with:

- Header: "This is your personal CLAUDE.md. Colaberry-wide rules in `.claude/colaberry/CLAUDE.md` override anything here."
- A "Your role" section (auto-populated from `tenancy.roles_for_user(email)`)
- An empty "Your preferences" section for them to fill in
- A "Your scope of access" section listing the tools they were provisioned with (from [Admin 2])

`render_starter_progress_md(user)` produces a PROGRESS.md with:

- Header naming the user, their tenant, and provisioning date
- An empty work log

## Workflows

### Provisioning a new user

1. Admin clicks "Add user" in admin console → calls `tenancy.create_user()`
2. Same flow calls `workspaces.provision_user_workspace()`
3. Workspace creation also calls `operator_scaffold.seed(user)` which writes the starter CLAUDE.md + PROGRESS.md to the new repo
4. User clones the repo, runs Claude Code, session-start hook pulls colaberry-policy automatically

### Admin updates org doctrine

1. Admin edits `ColaberryIntern/AI_ProjectArchitect/CLAUDE.md` (this repo), pushes to main
2. Next time any user opens Claude Code, the 1h-TTL fetch picks up the new version (or sooner if they force-refresh)
3. No per-user redeploy needed

### Admin updates narrative (the 3 colaberry.com sites)

1. Admin edits any of the 3 sites (or pushes a CMS change)
2. Next 24h-TTL scrape picks up the updated content
3. Every operator's shared KB now reflects the new narrative

### Admin revokes a user

1. Admin removes user's GitHub org membership (auth.colaberry-policy isn't needed anymore — the raw URL is gated by GitHub org access)
2. Session-start raw fetch returns 401/404 → Claude Code surfaces the failure and refuses to start
3. Workspace repo still exists but cannot pull org policy → effectively disconnected (matches Ali's "lose access = Claude Code can't connect" requirement)

## What's intentionally NOT in v1

- **Tenant-level CLAUDE.md generator** — for now, tenants that want their own layer must edit `.claude/tenant/CLAUDE.md` manually. UI for tenant-admin CLAUDE.md edits = [Operator 1.1] follow-up.
- **CLAUDE.md diff/merge UX** — if org and per-user disagree, the concatenation shows both. A future diff/merge tool that flags conflicts inline is deferred.
- **Versioning of org CLAUDE.md** — `git log` is the version history. No in-app surfacing of "what changed since last session" yet.

## Open questions

| # | Question | Decision |
|---|---|---|
| 1 | Where does org CLAUDE.md live? | ✅ **Resolved 2026-06-05 (Ali):** `ColaberryIntern/AI_ProjectArchitect/CLAUDE.md` (public repo, raw fetch). Narrative from www.colaberry.com + www.colaberry.ai + www.enterprise.colaberry.com (scraped). |
| 2 | Is there one org CLAUDE.md, or one per business unit? | Default: one. Revisit if Colaberry Education / Consulting diverge. |
| 3 | Should per-user CLAUDE.md be auto-committed to workspace repo? | Default: committed, so it travels with them and is admin-readable for support. |

## Hand-off

This spec assumes [Provision 1] is shipped (workspace repos exist) and [Auth 1] is shipped (we know who the user is). Both ✅.

Builds on top of this:
- [Operator 2] uses the per-user CLAUDE.md to encode the mandatory-ticket doctrine
- [Operator 5] uses the per-user PROGRESS.md as one input to the operator memory file
