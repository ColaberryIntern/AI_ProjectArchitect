# [Auth 2] Google SSO on advisor.colaberry.ai/library/

**Ticket:** Basecamp [9956730973](https://app.basecamp.com/3945211/buckets/7463955/todos/9956730973) · due 2026-06-13
**Status:** Shipped (gate enforced via middleware); requires Ali to register OAuth app + populate `.env.prod` before identity gate activates in prod
**Depends on:** [Auth 1] ✅
**Unblocks:** [Library 1], [Library 2], [Workflow 1]

---

## Acceptance criteria

| # | Criterion | Implementation |
|---|---|---|
| 1 | Google OAuth via @colaberry.com workspace | `execution/products/library/auth_google.py::build_login_url()` + `exchange_code_for_userinfo()` |
| 2 | First-login provisions a User row if their domain matches a configured tenant | `provision_or_lookup_user()` — uses `config/library_tenant_domains.json` for domain→company mapping |
| 3 | Top-right identity badge | Library `_library_base.html` reads `/auth/whoami` (Library 2 wires the visual badge) |
| 4 | Anonymous browsing allowed for marketing/landing; library requires login | `app/middleware/auth_gate.py` — checks `path_requires_login()`, redirects unauthenticated requests to `/auth/login?next=<original>`. No-op when `is_enabled()` returns False (preserves dev + unregistered prod). |
| 5 | Session cookie + JWT (same pattern as enterprise.colaberry.ai/portal/) | `issue_session_token()` HS256, set on `library_session` httpOnly secure samesite=lax cookie, 24h TTL |

## What ships in this ticket

- **`execution/products/library/auth_google.py`** — OAuth URL building, code-exchange, userinfo fetch, user provisioning, session JWT issue/verify, cookie-resolution helper
- **`app/routers/auth.py`** — HTTP routes `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/whoami`, `/auth/status`
- **`app/middleware/auth_gate.py`** — HTTP middleware that gates `login_required_paths`; redirects unauthenticated requests to `/auth/login?next=<original-path+query>`. Wired in `app/main.py`. No-op when `is_enabled()` returns False.
- **`config/library_tenant_domains.json`** — domain → company mapping + `anonymous_paths` + `login_required_paths`
- **`tests/execution/products/test_auth_google.py`** — 18 tests, fully offline
- **`tests/app/test_auth_gate_middleware.py`** — 10 tests covering the four decision branches (SSO disabled, anonymous, login-required + no session, login-required + valid session)

## Activation steps (Ali, after merge)

1. **Register the OAuth app** at https://console.cloud.google.com/apis/credentials → "OAuth client ID" → Web application
   - Authorized redirect URI: `https://advisor.colaberry.ai/auth/callback`
   - Scopes: `openid email profile`
2. **Set env vars** in `.env.prod` on the Hetzner box:
   ```
   GOOGLE_OAUTH_CLIENT_ID=<from-console>
   GOOGLE_OAUTH_CLIENT_SECRET=<from-console>
   GOOGLE_OAUTH_REDIRECT_URI=https://advisor.colaberry.ai/auth/callback
   LIBRARY_SESSION_SECRET=<32+ random bytes, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`>
   ```
3. **Restart the container** (`./deploy.sh` or `docker compose restart app`)
4. Verify: `GET https://advisor.colaberry.ai/auth/status` should return `enabled: true`
5. Verify: visit `https://advisor.colaberry.ai/auth/login` → Google consent → redirected back logged in
6. Verify: `GET /auth/whoami` returns `authenticated: true`

**Until activation, `/library/*` continues to work anonymously** — the
SSO check is gated on `is_enabled()`; missing env = SSO disabled = old
behavior preserved.

## Unmatched-domain handling

Per BUILD_INDEX default: an unmatched email domain does NOT auto-create
a tenant. The user is shown a 403 with `status="queued_for_review"`.
Admin 1 + Admin 3 will provide the UI to handle these requests
(approve → create company + user, or reject).

## Session JWT details

- Algorithm: HS256 (HMAC-SHA256)
- Header: `{"alg": "HS256", "typ": "JWT"}`
- Payload: `{sub: user_id, email, company, roles, iat, exp}`
- Signed with `LIBRARY_SESSION_SECRET`
- Stored in cookie `library_session` (httpOnly, secure, samesite=lax, max-age=86400)
- Verification: signature + `exp` check via `verify_session_token()`

No external JWT lib used — handwritten with stdlib `hmac`/`hashlib`/`base64`
to keep the dependency surface minimal. Switch to `python-jose` or
`pyjwt` in a future ticket if we need richer claims / algorithms.

## Security notes

- HS256 means the secret on the server can both sign and verify; rotate
  via `LIBRARY_SESSION_SECRET` rotation (forces all sessions to re-login)
- CSRF: `oauth_state` cookie is set on login, verified on callback
- HTTPS required (the cookies are marked `secure=True`)
- The handwritten JWT is fine for v1; if we ever issue tokens for
  third-party consumption, move to RS256 + a real library
