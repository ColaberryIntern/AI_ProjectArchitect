# [Admin 2] Tools-access provisioning matrix

**Ticket:** Basecamp [9956731016](https://app.basecamp.com/3945211/buckets/7463955/todos/9956731016) · due 2026-06-20
**Status:** Shipped (UI + vault wiring; live connector smoke tests deferred to per-tool follow-ups)
**Depends on:** [Admin 1] ✅, [Provision 2] ✅

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Tool inventory: Gmail, Calendar, Basecamp, CCPP, GitHub, Mandrill | `admin.py::user_scopes` → `all_tools = ["gmail", "calendar", "basecamp", "ccpp", "github", "mandrill", "slack"]` |
| 2 | Per-tool capability level: none / read / draft / send | v1 simplification: per-tool granted/not. Capability granularity deferred — `AccessScope.grant_type` enum is the slot to extend |
| 3 | Maps to AccessScope rows | `tenancy.grant_scope()` / `revoke_scope()` on every action |
| 4 | OAuth or token-paste flow per tool | v1 = token-paste only (simpler). OAuth-per-tool deferred (need per-tool OAuth app registration) |
| 5 | Tokens land in vault — never plaintext in DB | `POST /admin/users/{id}/scopes/credential` calls `vault.store_secret()` |
| 6 | Smoke test on save: ping connector, surface failure inline | **Deferred** — per-tool connector "ping" implementations are individual follow-ups, surfaced as `[Admin 2.1]` in a future ticket |
| 7 | User's `.mcp.json` generated from selections | `workspaces.render_starter_mcp_json(user, scopes)` |

## What's shipped

- `/admin/users/{user_id}/scopes` matrix page — toggle grant/revoke per tool
- Credential-store form on the same page — paste token, set TTL, save (encrypted into vault)
- Status column shows: granted? · credential status · last rotated · days until expiry
- Expiry surfacing: green if >7 days, amber if ≤7, red if expired
- Every action audit-logged (scope.grant / scope.revoke / credential.set)

## What's intentionally NOT shipped

- **Live connector ping** — would require implementing per-tool clients
  (Gmail API hello, Basecamp /authorization.json, GitHub /user, etc.)
  Each is its own ticket. Listed as gap → create `[Admin 2.1]` follow-ups.
- **OAuth flow per tool** — paste-flow only for v1. OAuth needs per-tool
  OAuth app + redirect URL config. Deferred.
- **Capability granularity (none/read/draft/send)** — schema supports
  it (`AccessScope.grant_type` is an enum) but UI is binary for v1.

## Hand-off

When the user logs in to their workspace repo and runs Claude Code, the
runtime should:
1. Read `.mcp.json` (committed in their workspace repo by Provision 1)
2. For each `${{ vault.X }}` placeholder, call `vault.read_secret()`
   with caller_id="mcp-runtime" + purpose="MCP server X bootstrap"
3. Inject the decrypted value into the MCP server's env vars
4. Each decrypt event = one audit row in the vault audit log

This integration is the **next ticket** — create as `[Workflow 3]`-style
or `[Provision 3]`. Out of scope for Admin 2 acceptance.
