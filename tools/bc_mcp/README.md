# Basecamp MCP

Local stdio MCP server that lets Claude Code (and any MCP client) read and
write the AI_ProjectArchitect Basecamp project without the SSH-token dance
each call.

## What it does

- On startup, fetches an active Basecamp API token by SSHing to the prod
  backend container (`root@95.216.199.47`), running a one-liner inside the
  `accelerator-backend` container that SELECTs from `CCPP.Basecamp_AuthInfo`.
- Caches the token at `~/.cache/bc_mcp/token.json` for 12 hours.
- Auto-refreshes on 401 (one retry) and on cache age > 12h.
- Exposes 9 tools: `health`, `list_todolists`, `list_todos`, `get_todo`,
  `complete_todo`, `uncomplete_todo`, `comment_todo`, `create_todo`, `search`.

## Install

```powershell
pip install -r tools/bc_mcp/requirements.txt
```

## Register with Claude Code

`.mcp.json` at repo root registers it project-scoped:

```json
{
  "mcpServers": {
    "basecamp": {
      "command": "python",
      "args": ["-m", "tools.bc_mcp.server"]
    }
  }
}
```

## Smoke test

```powershell
python -m tools.bc_mcp.smoke
```

Hits `auth.get_token()` then a `/my/profile.json` GET. Prints token-cache
metadata + the identity BC returned (or the error).

## Auth chain

Single source of truth: `Basecamp_AuthInfo` table in CCPP SQL Server. Highest
`BasecampAuthInfoID` with `IsActive=1` holds the active token. 14-day
rotation is human-mediated via Basecamp's OAuth UI. **There is no auto-refresh
anywhere upstream** — when CCPP's token row expires, a human re-auths.

If you change SSH host or container name, set:
- `BC_MCP_SSH_HOST` (default `root@95.216.199.47`)
- `BC_MCP_CONTAINER` (default `accelerator-backend`)

## Limits

- Account is hard-coded to `3945211` (Colaberry). Bucket defaults to `7463955`
  (AI_ProjectArchitect) but every write tool takes a `bucket_id` arg.
- Coverage today: todos + todolists + comments + account search. Messages /
  schedules / vaults / campfires not implemented — add them on the same
  pattern as `complete_todo` if you need them.

## Token expiry escalation

If CCPP itself returns a stale row (ExpiryDate past), the server keeps using
the cached value until BC 401s. On 401, refresh is attempted; if that 401s
again, the tool returns the error. At that point a human needs to rotate the
BC OAuth token in CCPP — see [memory/reference_basecamp_auth.md](../../../.claude/projects/c--Users-ali-m-OneDrive-Business-Colaberry-Novedea-AI-Projects-AI-Project-Architect---Build-Companion/memory/reference_basecamp_auth.md) for the playbook.
