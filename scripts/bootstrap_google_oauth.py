"""One-time interactive bootstrap of an operator's Google OAuth refresh token
for the `colaberry_attachment_fetch` MCP tool.

Run as the operator on their own machine -- the consent flow opens their
default browser; the refresh token never touches the network between this
script and Google. After consent, the token gets written to the advisor's
per-user vault under `(user.user_id, "google_oauth_refresh")`.

Usage:
    python scripts/bootstrap_google_oauth.py [--email operator@colaberry.com]

Required env on the host running this script:
    GOOGLE_OAUTH_CLIENT_ID
    GOOGLE_OAUTH_CLIENT_SECRET
    LIBRARY_VAULT_MASTER_KEY   (the vault encryption key; matches prod)

The script:
  1. Prompts for the operator email (defaults to ali@colaberry.com)
  2. Verifies the env vars + tenancy user record
  3. Spawns a localhost callback server on a random port
  4. Opens the browser to Google OAuth consent for:
       https://www.googleapis.com/auth/gmail.readonly
       https://www.googleapis.com/auth/drive.file
  5. Receives the authorization code on the callback
  6. Exchanges code -> refresh token via Google /token
  7. Writes refresh token to vault (ttl_days=180)
  8. Prints "Bootstrap complete"

The script NEVER prints, logs, or otherwise emits the refresh token or the
client secret. The vault audit log records the store event with caller_id=
"bootstrap_script".
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

# Allow `python scripts/bootstrap_google_oauth.py` from the repo root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from execution.products.library import google_oauth_token, tenancy  # noqa: E402


AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Match the scopes registered on the OAuth consent screen exactly.
# gmail.modify lets the system read AND draft AND send AND label messages
# (but NOT permanently delete), which is what Ali wants for future
# email-sending MCP tools (not just attachment fetch).
# drive.file lets the system create + manage Drive files our app uploaded
# (the staged attachment copies), without exposing the rest of the user's
# Drive. See directives/colaberry-attachment-fetch.md.
SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive.file",
])


class _AuthCodeHandler(http.server.BaseHTTPRequestHandler):
    """Captures the `code` parameter from Google's redirect back to localhost."""

    received_code: str | None = None
    received_state: str | None = None
    expected_state: str = ""

    def do_GET(self):  # noqa: N802 - http.server interface
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        state = (qs.get("state") or [""])[0]
        error = (qs.get("error") or [""])[0]
        if error:
            _AuthCodeHandler.received_code = None
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Error from Google: {error}\n".encode("utf-8"))
            return
        if state != _AuthCodeHandler.expected_state:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"State mismatch -- possible CSRF; aborting.\n")
            return
        _AuthCodeHandler.received_code = code
        _AuthCodeHandler.received_state = state
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:sans-serif;padding:40px;'>"
            b"<h2 style='color:#137333;'>Google OAuth consent received</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, format, *args):  # noqa: A002 - http.server interface
        # Silence the default access log so we don't print anything that
        # might include the auth code from the URL line.
        pass


def _spin_callback_server(port: int) -> http.server.HTTPServer:
    srv = http.server.HTTPServer(("127.0.0.1", port), _AuthCodeHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def _exchange_code_for_refresh_token(code: str, redirect_uri: str,
                                                                  client_id: str,
                                                                  client_secret: str) -> str:
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "Google response did not include a refresh_token. "
            "This usually means you've already granted consent for this app "
            "and Google won't re-issue. Revoke the prior grant at "
            "https://myaccount.google.com/permissions and re-run."
        )
    return refresh_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="ali@colaberry.com",
                                  help="Operator email (must exist in tenancy)")
    parser.add_argument("--port", type=int, default=0,
                                  help="Localhost callback port (0 = pick random)")
    args = parser.parse_args()

    # Prefer the Desktop OAuth client (purpose-built for this flow); fall
    # back to the legacy SSO Web client only if the Desktop vars are absent.
    client_id = (
        os.environ.get("GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        os.environ.get("GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        or ""
    ).strip()
    if not client_id or not client_secret:
        print("ERROR: GOOGLE_OAUTH_ATTACHMENT_CLIENT_ID and "
                  "GOOGLE_OAUTH_ATTACHMENT_CLIENT_SECRET must be set in the environment.",
                  file=sys.stderr)
        return 2
    if not os.environ.get("LIBRARY_VAULT_MASTER_KEY"):
        print("ERROR: LIBRARY_VAULT_MASTER_KEY must be set so the vault can "
                  "encrypt the refresh token at rest.", file=sys.stderr)
        return 2

    user = tenancy.get_user(args.email)
    if not user:
        print(f"ERROR: user {args.email} not found in tenancy.", file=sys.stderr)
        return 2

    # Decide the callback port. We need it embedded in the redirect URI we
    # send to Google AND it must match an Authorized Redirect URI registered
    # on the Google Cloud OAuth client. If you registered http://127.0.0.1
    # (no path) Google accepts any port; if you registered a specific port
    # you must pass --port that matches.
    if args.port:
        port = args.port
    else:
        # Bind to a free port
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    # Server first, then open browser
    srv = _spin_callback_server(port)
    state = secrets.token_urlsafe(24)
    _AuthCodeHandler.expected_state = state
    _AuthCodeHandler.received_code = None
    _AuthCodeHandler.received_state = None

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",  # forces refresh_token issuance even on re-auth
        "state": state,
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(auth_params)
    print(f"Opening browser for {args.email}'s consent...")
    print(f"  (callback: {redirect_uri})")
    try:
        webbrowser.open(auth_url, new=2)
    except Exception:
        print("Could not auto-open a browser. Open this URL manually:")
        print(auth_url)

    # Block until the callback handler captures the code (or user gives up)
    import time
    timeout_at = time.time() + 600  # 10 minute consent window
    while _AuthCodeHandler.received_code is None and time.time() < timeout_at:
        time.sleep(0.5)
    srv.shutdown()
    srv.server_close()

    if _AuthCodeHandler.received_code is None:
        print("ERROR: no auth code received within 10 minutes. Aborting.",
                  file=sys.stderr)
        return 1

    try:
        refresh_token = _exchange_code_for_refresh_token(
            _AuthCodeHandler.received_code, redirect_uri, client_id, client_secret,
        )
    except Exception as e:
        # Don't echo the exception body -- it can contain token-shaped strings.
        print(f"ERROR: token exchange failed: {type(e).__name__}", file=sys.stderr)
        if isinstance(e, RuntimeError):
            print(str(e), file=sys.stderr)
        return 1

    google_oauth_token.store_refresh_token_for_operator(
        user, refresh_token,
        client_type="desktop",
        actor_id="bootstrap_script",
    )
    # NEVER print the token.
    print(f"Bootstrap complete for {args.email}.")
    print(f"  scopes: {SCOPES}")
    print("  refresh token stored in advisor vault under "
              f"(user_id={user.user_id}, tool='{google_oauth_token.VAULT_TOOL_NAME}')")
    print("  Re-run this script to rotate; previous entry is overwritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
