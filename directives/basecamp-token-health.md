# Directive — Basecamp Token Health

**Owner:** Ali (ali@colaberry.com) · **Notified:** Ali + Kes
**Status:** living document
**Related code:**
- `execution/products/ops/bc_token_health.py` (deterministic probe + alert)
- `execution/products/library/basecamp_oauth_token.py` (OAuth refresh engine)
- `execution/products/library/mcp_tools.py` (`_bc_token` resolver, `_bc_request` self-heal)
- `execution/products/ops/scheduler.py` (daily `bc_token_health` cron)

---

## Goal

The Colaberry MCP server's Basecamp tools must **never** fail because a token
silently expired. This directive defines the process that (a) prevents the
14-day expiry incident from recurring and (b) detects + auto-heals it if it
ever slips through.

## Background — why this kept breaking

`_bc_token()` resolves a Basecamp bearer in three tiers:

| Tier | Source | Self-refreshing? |
|------|--------|------------------|
| 1. Per-operator "X AI" OAuth grant | vault (`basecamp_ai_clone`), refresh_token | **Yes** |
| 2. Legacy bare-token vault paste | vault | No — 401s at 14 days |
| 3. Shared **CB System** identity | self-refresh grant (preferred) → `BASECAMP_ACCESS_TOKEN` env (fallback) | **Yes**, once the one-time grant is stored |

The recurring outage was Tier 3 running off the **static** `BASECAMP_ACCESS_TOKEN`
env var, mirrored from the CCPP `Basecamp_AuthInfo` table. Basecamp access tokens
have a ~14-day TTL with no auto-refresh on that path, so every two weeks the env
token 401'd and every project leaning on the shared identity failed at once.

## Prevention (the process)

1. **Shared identity self-refreshes.** CB System now has its own OAuth grant
   stored in vault under the synthetic principal `cb-system`
   (`store_shared_cb_system_grant`). `_bc_token()` Tier 3 calls
   `basecamp_oauth_token.get_shared_cb_system_token()`, which exchanges the
   refresh_token on demand. The static env var is now a last-resort fallback
   only. **Result: no 14-day rotation for the shared identity.**

2. **Per-operator migration.** Each operator/project should connect via
   `/profile/connect-basecamp-ai` so it authors as its own self-refreshing AI
   persona (Tier 1) instead of borrowing the shared token.

3. **Daily health preflight.** `bc_token_health.check_all()` runs every day on
   the scheduler. It probes every active grant + the shared identity with a
   cheap `GET /my/profile.json` whoami and emails Ali + Kes when any token is
   within `BC_TOKEN_HEALTH_WARN_DAYS` (default 3) of expiry, is failing the
   whoami, or has no refresh_token on file. **The alert fires before the 401,
   not after.**

## Auto-fix (if it slips through)

- **Self-heal on 401.** `_bc_request` catches a `401` once, drops the cached
  token, forces a refresh, and retries. Tier 1 + Tier 3 both re-exchange via
  their refresh_token; only a genuinely revoked/expired *refresh* token reaches
  the human.

- **The alert is actionable.** It names the failing principal and the exact
  remediation (re-consent URL or one-time CB System grant step).

## Runbook — when an alert fires (or the MCP 401s anyway)

1. **Identify the tier** from the alert / error. A `no_basecamp_oauth_grant` or
   `basecamp_grant_invalid` code = that principal's refresh_token is gone.
2. **Per-operator (Tier 1):** the named operator re-consents at
   `https://advisor.colaberry.ai/profile/connect-basecamp-ai`. Done.
3. **Shared CB System (Tier 3):** re-run the one-time consent as CB System
   (`vishnu@colaberry.com`) and store the fresh grant — see below.
4. **Verify:** run `bc_token_health.check_all()` (or wait for the next daily
   run) and confirm all principals report `ok`.
5. **Self-anneal:** if the failure had a new root cause, add a regression test
   and update this directive.

## One-time setup — store the CB System self-refresh grant

This is the human-mediated step that retires the static env token. Do it once.

1. Complete the Basecamp OAuth consent **as CB System** against the integration
   registered at https://integrate.37signals.com/ (client id/secret already in
   `BASECAMP_OAUTH_CLIENT_ID` / `_SECRET`), capturing `access_token`,
   `refresh_token`, and `expires_in`.
2. Store it:

   ```python
   from execution.products.library import basecamp_oauth_token as bt
   bt.store_shared_cb_system_grant(
       access_token=ACCESS, refresh_token=REFRESH,
       bc_user_id=37708014, bc_user_email="vishnu@colaberry.com",
       access_token_expires_at=time.time() + EXPIRES_IN,
   )
   ```
3. Confirm with `bc_token_health.check_all()` — the `cb-system` principal should
   report `ok` with a future expiry, and the MCP no longer depends on
   `BASECAMP_ACCESS_TOKEN`.

## Config

| Env var | Default | Meaning |
|---------|---------|---------|
| `BC_TOKEN_HEALTH_ENABLED` | `1` | Master switch for the daily job |
| `BC_TOKEN_HEALTH_WARN_DAYS` | `3` | Alert this many days before expiry |
| `BC_TOKEN_HEALTH_CRON_HOUR` | `8` | Local hour for the daily run |
| `BC_TOKEN_HEALTH_TZ` | `America/New_York` | Timezone for the cron |

Recipients live in `config/report_recipients.json` (`bc_token_health` block);
Ali + Kes by default.

## Verification expectations

- Unit tests in `tests/execution/products/test_bc_token_health.py` cover the
  whoami probe (ok / 401 / network error), `check_all` aggregation, expiry-window
  warning logic, and the self-heal retry path. No live BC calls in tests.
</content>
</invoke>
