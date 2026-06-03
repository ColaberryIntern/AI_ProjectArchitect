# [Provision 1] Per-user GitHub workspace auto-create

**Ticket:** Basecamp [9956731040](https://app.basecamp.com/3945211/buckets/7463955/todos/9956731040) · due 2026-06-17
**Status:** Shipped (scaffold); template repo + admin PAT needed for live activation
**Depends on:** [Auth 1] ✅, [Admin 1] ✅

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Naming: `ColaberryIntern/{username}-workspace` | `workspaces.workspace_repo_for_user(email)` — slug = local part of email |
| 2 | Template repo with `.claude/skills/`, `.mcp.json` placeholder, README, `USER_PROFILE.md` | `render_starter_user_profile_md()` + `render_starter_mcp_json()` generators (template repo to be seeded manually — see activation steps) |
| 3 | User added as collaborator with write access; admin retains admin | `_gh_api PUT /repos/{repo}/collaborators/{username}` with `permission=write` |
| 4 | Admin token lives in vault; rotated quarterly | Uses `GITHUB_ADMIN_TOKEN` env (not vault since this is the bootstrap credential); rotation policy: 90 days, surfaced in Provision 2 vault for any per-tool tokens |
| 5 | Idempotent: re-provisioning is a no-op | `repo_exists(repo)` check skips creation; recorded as `skip_existing` in audit |

## Files

- `execution/products/library/workspaces.py` — provisioning module
- `tests/execution/products/test_workspaces.py` — 9 tests (mocked GitHub API)
- Trigger: `admin.user_new` POST has a `create_workspace_repo` checkbox
  that calls `workspaces.provision_user_workspace()`

## Activation steps (Ali)

1. **Create the template repo** (one-time):
   - `gh repo create ColaberryIntern/workspace-template --public --template`
   - Seed with `.claude/skills/`, `.mcp.json` (empty `mcpServers: {}`),
     `README.md`, `USER_PROFILE.md` (placeholder)
   - Mark "Template repository" in settings (`gh repo edit --template`)

2. **Generate an admin token**:
   - `gh auth login` (if not already)
   - Or generate a PAT with `repo`, `admin:org` scopes
   - Set in `.env.prod`: `GITHUB_ADMIN_TOKEN=ghp_xxx`

3. **Restart container.** `gh` CLI in the container will use the token.

## Behavior matrix

| Repo state | What happens |
|---|---|
| Doesn't exist + template exists | `POST /repos/{template}/generate` → new repo |
| Doesn't exist + template missing | Fallback: `POST /orgs/{org}/repos` bare repo with auto_init=true |
| Already exists | Skip creation, just invite collaborator |
| Provision fails | Audit row with error, returns `ok: false` |

## Audit

`output/library/_tenants/workspace_provision_audit.jsonl` —
`{actor_id, target_user_id, action, repo, error, details, at}`.

Actions: `dry_run`, `skip_existing`, `create_repo`, `create_repo_bare`,
`invite_collaborator`, `invite_failed`, `provision_failed`.

## Username slug rules

`username_slug(email)`:
- Take local part of email (before `@`)
- Lowercase
- Replace anything not `[a-z0-9-]` with `-`
- Strip leading/trailing dashes
- Truncate to 39 chars (GitHub max)

Examples:
- `ali@colaberry.com` → `ali`
- `Alice.Smith@Colaberry.com` → `alice-smith`
- `weird+plus@x.com` → `weird-plus`

## Token in `.mcp.json` — secrets boundary

The generated `.mcp.json` does NOT contain plaintext credentials. It
contains references like `${{ vault.github }}` that the runtime
substitutes from the vault ([Provision 2]) at MCP-server-start time.
This means the repo can be public-readable without leaking tokens.
