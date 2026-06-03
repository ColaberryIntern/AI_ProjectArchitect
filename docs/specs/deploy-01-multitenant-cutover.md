# [Deploy 1] Multi-tenant cut-over runbook

**Status:** Shipped (runbook + preflight script + deploy.sh safety gate)
**Depends on:** Auth 1+2, Provision 1+2, Admin 1+2+3, Library 1+2, Workflow 1+2, Infra 1+2

**Note:** No BC ticket exists yet for Deploy 1. Per BUILD_INDEX Week 5 plan, Ali to create the BC todo and pin this runbook to it.

---

## Goal

A safe, reversible cut-over of advisor.colaberry.ai/library from today's anonymous-single-tenant mode to the new multi-tenant, SSO-gated mode.

## Safety principles

1. **Preflight before restart.** `scripts/deploy_preflight.py` runs before the container restart and fails the deploy if hard-required env is missing.
2. **Soft-degrade by default.** When optional env (SSO, vault key, GH tokens) is missing, the affected features auto-degrade to a safe mode (open library / vault refuses / sync becomes noop). Nothing crashes.
3. **Backup tenant data before the first deploy with [Auth 1].** Once `output/library/_tenants/companies.json` exists, that file IS the multi-tenant state. Snapshot to a dated backup before any deploy.
4. **One-tenant cut-over first.** Colaberry tenant is the smoke-test population for week 1. Demo-tenant exists for parallel verification. Customer tenants (Patriot, etc.) added in week 2 only after week-1 metrics look healthy.

## Pre-cut-over checklist (Ali)

Run these in order, **before** running `./deploy.sh` for the first time after PR #1 merges.

### 1. Register Google OAuth app
- console.cloud.google.com → APIs & Services → OAuth consent screen
- App type: Web application; user type: Internal (Colaberry workspace)
- Authorized redirect URI: `https://advisor.colaberry.ai/auth/callback`
- Capture `CLIENT_ID` + `CLIENT_SECRET`

### 2. Mint a vault master key
```bash
python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```
Capture the output (44-char base64 string). This is `LIBRARY_VAULT_MASTER_KEY`. **Once chosen, NEVER ROTATE without re-encrypting every stored secret** — write that down somewhere.

### 3. Mint a session secret
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```
Capture for `LIBRARY_SESSION_SECRET`.

### 4. Create GitHub PAT
- github.com/settings/tokens → Generate new (classic) → scope: `repo`, `admin:org` (for Provision 1 workspace creation)
- Capture for `GITHUB_ADMIN_TOKEN`

### 5. Create the canonical sync repo (if not already)
- github.com/ColaberryIntern/AI_ProjectArchitect — already exists for the app code; sync target uses the SAME repo at `library/` path
- Optional second repo `ColaberryIntern/library-published` for [Infra 1] direct-commit mirror — create as bare empty repo if you want a separate published-only namespace

### 6. SSH to prod box + write .env.prod
```bash
ssh root@95.216.199.47
cd /opt/ai-project-architect
nano .env.prod
```
Append (keep existing OPENAI_API_KEY):
```
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=https://advisor.colaberry.ai/auth/callback
LIBRARY_SESSION_SECRET=...
LIBRARY_VAULT_MASTER_KEY=...
GITHUB_ADMIN_TOKEN=...
GITHUB_LIBRARY_REPO=ColaberryIntern/AI_ProjectArchitect
```

### 7. Backup existing data
```bash
ssh root@95.216.199.47 "cd /opt/ai-project-architect && tar -czf output_backup_$(date +%Y%m%d_%H%M%S).tar.gz output/"
```

## Cut-over

From your dev machine:

```bash
./deploy.sh
```

The script will:
1. SSH to prod, `git pull origin main`
2. Run `python scripts/deploy_preflight.py`
   - **Exit 0** → green light → proceed with build + restart
   - **Exit 1** → hard failure → STOP, fix env, retry
   - **Exit 2** → warnings only → proceed but features will be degraded (you'll see exactly which)
3. `docker compose build`
4. `docker compose down && docker compose up -d`
5. Verify with `docker compose ps`

## First-deploy post-flight

1. Open `https://advisor.colaberry.ai/library/` in a fresh incognito window.
2. Click "Sign in with Google" → log in as `ali@colaberry.com`.
3. Verify the header shows your name + "colaberry" chip + "🏢 My company" scope selected.
4. Open `/admin/` — you should land on the admin home (Ali = super_admin).
5. Open `/admin/companies` — should show Colaberry + demo-tenant.
6. Submit one test item via Library Add → check it appears in `/admin/colaberry/queue`.
7. Approve it → check the bell + a sync PR opens at github.com/ColaberryIntern/AI_ProjectArchitect/pulls (assuming Infra 2 hook is wired and `GITHUB_ADMIN_TOKEN` is set).

## Rollback procedure

If post-flight reveals a problem:

```bash
ssh root@95.216.199.47
cd /opt/ai-project-architect
git log --oneline -5                # find the last known good commit
git checkout <last_good_sha>
docker compose down && docker compose up -d
# If tenant data corrupted:
rm -rf output/library/_tenants
tar -xzf output_backup_<latest>.tar.gz
docker compose restart
```

The application boots cleanly with no tenant data — it'll just be in pre-Auth-1 mode until reseeded.

## Cadence after first cut-over

| Week | Action |
|---|---|
| 1 | Colaberry-only. Daily check of `/admin/colaberry/queue` + sync PRs + audit logs |
| 2 | Add demo-tenant verification. Provision Karun + Kes workspaces via [Provision 1] |
| 3 | Onboard first customer tenant (Patriot or similar) per [Admin 3] |
| 4 | Roll Phase 2 exec onboarding (Ali, Ram, David, JJ, Sohail) |
| 5+ | Phase 3 + Pilot retros per BUILD_INDEX |

## Audit log locations on prod

| What | Where |
|---|---|
| Admin actions | `/opt/ai-project-architect/output/library/_tenants/admin_audit.jsonl` |
| Vault decrypts | `/opt/ai-project-architect/output/library/_vault/decrypt_audit.jsonl` |
| Sync PRs (Infra 2) | `/opt/ai-project-architect/output/library/_github_pr_sync/{date}.jsonl` |
| Sync commits (Infra 1) | `/opt/ai-project-architect/output/library/_github_sync/{date}.jsonl` |
| Workflow transitions | `/opt/ai-project-architect/output/library/_tenants/approval_transitions.jsonl` |
| Notifications fan-out | `/opt/ai-project-architect/output/library/_tenants/notifications/{company}.jsonl` |

Snapshot these weekly into the backup tarball.

## Trade-offs / deferred

- **No blue-green deploy.** This is a single-host `docker compose down/up` cut-over. Brief downtime (~10 sec) is acceptable for the user volume today. When traffic crosses ~10 RPS, switch to a reverse-proxy + two-container blue-green.
- **No DB migrations.** The whole tenancy model is JSON-file backed (see CLAUDE.md). When we outgrow that, migrate to Postgres + Alembic. That's a separate ticket.
- **No CDN cache invalidation.** Library pages are served fresh each request today. When we add edge caching, the deploy must purge.
- **No staging environment.** First-deploy testing happens on the prod box itself; safe because the new features auto-degrade if anything is misconfigured. Future ticket: stand up `staging.advisor.colaberry.ai` on a second Hetzner box.
