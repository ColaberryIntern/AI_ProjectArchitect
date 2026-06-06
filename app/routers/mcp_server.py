"""MCP server + token management routes.

Two surfaces in one router:

  1. JSON-RPC over HTTP at `/mcp/v1` (and back-compat `/mcp`). Speaks the
     MCP protocol's `initialize`, `tools/list`, `tools/call`, `resources/list`,
     `resources/read`. Auth via `Authorization: Bearer <cmcp_...>` header.
     This is what `claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1`
     points at.

  2. Profile pages at `/profile/mcp-*` for the Google-SSO'd web user:
     - GET  /profile/mcp-setup        - install instructions + status
     - POST /profile/mcp-token        - generate a fresh token (revokes prior)
     - POST /profile/mcp-revoke       - revoke the token
     - GET  /profile/mcp-status.json  - polled by the setup page to detect
                                       the first MCP ping in real time

Status semantics live in mcp_token.status_for_user(): red / yellow / green.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from execution.products.library import (
    auth_google, mcp_doctrine, mcp_token, mcp_tools, tenancy,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Auth helpers ────────────────────────────────────────────────────


def _session_user(request: Request):
    cookie = request.cookies.get(auth_google.SESSION_COOKIE_NAME)
    user = auth_google.current_user_from_cookie(cookie)
    if user:
        return user
    if not auth_google.is_enabled():
        return tenancy.get_user("ali@colaberry.com")
    return None


def _require_web_user(request: Request) -> tenancy.User:
    user = _session_user(request)
    if not user:
        from urllib.parse import quote
        qs = request.url.query
        full = request.url.path + ("?" + qs if qs else "")
        raise HTTPException(303, headers={"Location": f"/auth/login?next={quote(full, safe='')}"})
    return user


def _bearer_user(authorization: str | None,
                                user_agent: str | None = None,
                                hostname: str | None = None,
                                client_ip: str | None = None) -> tenancy.User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return mcp_token.validate_token(
        authorization.split(" ", 1)[1].strip(),
        user_agent=user_agent,
        hostname=hostname,
        client_ip=client_ip,
    )


# ── MCP JSON-RPC handler ────────────────────────────────────────────


JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _rpc_ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _handle_rpc(user: tenancy.User, msg: dict) -> dict | None:
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications (no id) get no response per JSON-RPC 2.0 spec.
    is_notification = "id" not in msg

    if method == "initialize":
        return _rpc_ok(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": "colaberry-mcp", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        return None  # ack; no reply

    if method == "tools/list":
        return _rpc_ok(req_id, {
            "tools": [t.to_listing() for t in mcp_tools.TOOLS],
        })

    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments") or {}
        result = mcp_tools.call_tool(tool_name, user, args)
        # MCP envelope: tools return {content: [{type: text, text: <json>}]}
        return _rpc_ok(req_id, {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
            "isError": not result.get("ok", True),
        })

    if method == "resources/list":
        return _rpc_ok(req_id, {
            "resources": [r.to_listing() for r in mcp_doctrine.RESOURCES],
        })

    if method == "resources/read":
        uri = params.get("uri", "")
        try:
            text = mcp_doctrine.read_resource(uri, user)
        except ValueError as e:
            return _rpc_error(req_id, JSONRPC_INVALID_PARAMS, str(e))
        except Exception as e:
            logger.exception("resource read failed")
            return _rpc_error(req_id, JSONRPC_INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        return _rpc_ok(req_id, {
            "contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}],
        })

    if is_notification:
        return None
    return _rpc_error(req_id, JSONRPC_METHOD_NOT_FOUND, f"unknown method {method!r}")


@router.post("/mcp/v1")
@router.post("/mcp")
async def mcp_rpc(request: Request,
                                authorization: str | None = Header(default=None),
                                user_agent: str | None = Header(default=None),
                                x_mcp_hostname: str | None = Header(default=None)):
    """JSON-RPC endpoint. Handles single requests; MCP batching not yet supported.

    Captures X-MCP-Hostname header (embedded by the install command via shell
    substitution -- $(hostname) on Mac/Linux, %COMPUTERNAME% on Windows cmd,
    $env:COMPUTERNAME on PowerShell) so the setup page can identify WHICH
    physical computer each device row represents.
    """
    # Best-effort client IP -- behind nginx + Cloudflare, walk the forwarded
    # chain so we get the real public IP, not Cloudflare's edge address.
    client_ip = (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    )
    user = _bearer_user(authorization, user_agent=user_agent,
                                          hostname=x_mcp_hostname, client_ip=client_ip)
    if not user:
        return JSONResponse(
            _rpc_error(None, -32001, "invalid or missing MCP token; generate one at /profile/mcp-setup"),
            status_code=401,
        )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(_rpc_error(None, JSONRPC_PARSE_ERROR, "invalid JSON"), status_code=400)

    if not isinstance(body, dict):
        return JSONResponse(_rpc_error(None, JSONRPC_INVALID_REQUEST, "expected JSON object"), status_code=400)

    response = _handle_rpc(user, body)
    if response is None:
        # Notification ack -> 204 No Content
        return JSONResponse(content=None, status_code=204)
    return JSONResponse(response)


# ── Profile / token management routes ───────────────────────────────


@router.get("/profile/mcp-setup")
async def mcp_setup_page(request: Request):
    user = _require_web_user(request)
    # Persist any legacy single-token -> mcp_tokens migration once so
    # subsequent loads don't redo the in-memory upgrade.
    if mcp_token._migrate_legacy(user):
        tenancy.upsert_user(user)
    # Hard-purge revoked entries -- user explicitly doesn't want them.
    user, _purged = mcp_token.purge_revoked_for_user(user.user_id)
    status = mcp_token.status_for_user(user)
    devices = mcp_token.list_devices(user)

    # Best-effort "this computer" auto-detect: compare the browser's public
    # IP against each device's last_client_ip. Same IP -> almost certainly
    # the same physical machine (one laptop running both a browser and
    # Claude Code from the same network). Won't match across VPNs or when
    # browser/CLI run on different machines, but covers the common case.
    browser_ip = (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else None)
    )
    for d in devices:
        d["is_likely_current"] = bool(
            browser_ip and d.get("last_client_ip") and
            d["last_client_ip"] == browser_ip
        )
    # Compute the user's personal BC project URL for the quick-link card.
    import os as _os
    bc_account = _os.environ.get("BASECAMP_ACCOUNT_ID", "3945211")
    personal_bc_url = ""
    if user.personal_bc_project_id:
        personal_bc_url = f"https://3.basecamp.com/{bc_account}/projects/{user.personal_bc_project_id}"

    # _library_base.html references a dozen context vars that My Day's _ctx()
    # populates. Without them, Jinja's StrictUndefined-ish access raises.
    # Build the same safe-defaults dict here so the setup page renders
    # standalone without depending on the my_day router.
    company_display = user.company_id
    try:
        c = tenancy.get_company(user.company_id)
        if c:
            company_display = c.display_name
    except Exception:
        pass
    return request.app.state.templates.TemplateResponse(
        request, "library/mcp_setup.html",
        {
            "request": request,
            "current_session_user": user,
            "status": status,
            "current_product": "library",
            "company_id": user.company_id,
            "company_display": company_display,
            "library_nav_active": "mcp_setup",
            "page_title": "Connect Claude Code",
            "mcp_status": status,
            "personal_bc_url": personal_bc_url,
            "devices": devices,
            "browser_ip": browser_ip or "",
            # Safe defaults for library/_library_base.html
            "actor": user.display_name or user.email,
            "workspace": "global",
            "workspaces": [],
            "scope": "my-company",
            "viewer_company_id": user.company_id,
            "counts": {},
            "use_case_count": 0,
            "pending_count": 0,
            "bell_count": 0,
            "queue_count": 0,
            "is_reviewer": False,
            "is_my_day_admin": "admin" in (user.roles or []),
            "my_day_total_open": None,
            "q": "",
        },
    )


@router.post("/profile/mcp-token")
async def mcp_token_generate(request: Request, label: str = Form("device")):
    """Mint a new per-device MCP token. Each call ADDS to the user's token
    list -- previous device tokens stay valid until explicitly revoked.

    Returns the plaintext token ONCE so the setup page can show it for
    copying; only sha256 hash is persisted server-side.
    """
    user = _require_web_user(request)
    plain_token, updated = mcp_token.generate_for_user(user.user_id, label=label)
    # Base install command (no hostname). Used as a fallback for the
    # "live Claude Code session" pane where shell substitution wouldn't apply
    # cleanly (Claude will run via Bash tool which handles the variant).
    shell_command = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http --header "Authorization: Bearer {plain_token}"'
    )
    # Platform-specific variants that EMBED the user's hostname via shell
    # substitution. After `claude mcp add` runs, the resolved literal
    # hostname is baked into ~/.claude/settings.json, so every MCP request
    # sends X-MCP-Hostname automatically -- giving the portal a stable
    # identifier per physical computer in the device table.
    shell_command_mac_linux = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http '
        f'--header "Authorization: Bearer {plain_token}" '
        f'--header "X-MCP-Hostname: $(hostname)"'
    )
    shell_command_win_cmd = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http '
        f'--header "Authorization: Bearer {plain_token}" '
        f'--header "X-MCP-Hostname: %COMPUTERNAME%"'
    )
    shell_command_win_ps = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http '
        f'--header "Authorization: Bearer {plain_token}" '
        f'--header "X-MCP-Hostname: $env:COMPUTERNAME"'
    )
    # Self-orienting instruction block. Designed to work whether the user has
    # the `claude` CLI on PATH (uses `claude mcp add`) or only has the VSCode
    # extension (must direct-edit ~/.claude.json). The prompt detects which
    # works and falls through gracefully -- no questions asked.
    claude_prompt = (
        f"INSTALL TASK: Register the Colaberry MCP server for the operator "
        f"{user.email}. This is a one-time install on this machine.\n\n"
        f"DO ALL STEPS BELOW. Don't ask the user what the goal is or which "
        f"approach to use -- pick automatically. Don't echo the token in chat. "
        f"Report a short result line for each step.\n\n"
        f"--- Step 1: detect hostname ---\n"
        f"Run via Bash: `hostname` (Mac/Linux) or `echo %COMPUTERNAME%` "
        f"(Windows cmd) or `$env:COMPUTERNAME` (PowerShell). Whichever your "
        f"OS supports. Capture the result as <HOSTNAME> -- you'll need it.\n\n"
        f"--- Step 2: try the CLI path FIRST ---\n"
        f"Run: `claude --version`\n"
        f"   - If exit 0: run the install via CLI -- `claude mcp add colaberry "
        f"https://advisor.colaberry.ai/mcp/v1 --transport http "
        f"--header \"Authorization: Bearer {plain_token}\" "
        f"--header \"X-MCP-Hostname: <HOSTNAME>\"` -- then jump to Step 4.\n"
        f"   - If exit non-zero or \"command not found\": fall through to Step 3 "
        f"(direct config edit). This is the case for users with only the "
        f"VSCode extension, not the standalone CLI.\n\n"
        f"--- Step 3: direct config edit (universal fallback) ---\n"
        f"Locate the Claude Code config file:\n"
        f"  Mac/Linux: ~/.claude.json\n"
        f"  Windows:   %USERPROFILE%\\.claude.json (e.g. C:\\Users\\<name>\\.claude.json)\n\n"
        f"Use your Read + Write tools (NOT Bash, so JSON is parsed cleanly):\n"
        f"  1. Read the file. If missing, treat as empty object `{{}}`.\n"
        f"  2. Backup: copy to .claude.json.bak.\n"
        f"  3. Ensure the JSON has an `mcpServers` object key; set/overwrite "
        f"its `colaberry` entry to EXACTLY:\n"
        f"     {{\n"
        f"       \"type\": \"http\",\n"
        f"       \"url\": \"https://advisor.colaberry.ai/mcp/v1\",\n"
        f"       \"headers\": {{\n"
        f"         \"Authorization\": \"Bearer {plain_token}\",\n"
        f"         \"X-MCP-Hostname\": \"<HOSTNAME>\"\n"
        f"       }}\n"
        f"     }}\n"
        f"  4. PRESERVE all other mcpServers entries -- only set the "
        f"`colaberry` key. Don't drop sibling servers.\n"
        f"  5. Write the file back as pretty-printed JSON.\n\n"
        f"--- Step 4: verify ---\n"
        f"Read the config file back. Confirm the `colaberry` entry is present "
        f"with the right URL. Report success or failure.\n\n"
        f"--- Step 5: tell the user and STOP ---\n"
        f"Print exactly:\n"
        f"  ✅ MCP installed for {user.email} as host <HOSTNAME>.\n"
        f"  Close this Claude Code window and open a new one so the new\n"
        f"  Colaberry MCP server is loaded. After restart, ask Claude to\n"
        f"  list its tools -- you should see the colaberry_* tool family.\n\n"
        f"Then STOP. Don't run further tools. Don't ask questions. Don't echo "
        f"the token. The token grants this machine permission to call "
        f"Colaberry MCP tools (BC tickets, doctrine, memory) on the user's "
        f"behalf -- treat it like a credential.\n"
    )
    return JSONResponse({
        "ok": True,
        "token": plain_token,
        "issued_at": updated.mcp_token_issued_at,
        "label": label,
        # Legacy single-shell command (kept for back-compat; UI now picks
        # the platform variant below)
        "install_command": shell_command,
        # Platform variants that embed hostname via shell substitution
        "install_command_mac_linux": shell_command_mac_linux,
        "install_command_win_cmd": shell_command_win_cmd,
        "install_command_win_ps": shell_command_win_ps,
        "claude_install_prompt": claude_prompt,
    })


@router.post("/profile/mcp-revoke")
async def mcp_revoke(request: Request, label: str = Form("")):
    """Revoke a specific device by label, or ALL devices if no label given."""
    user = _require_web_user(request)
    if label.strip():
        mcp_token.revoke_device(user.user_id, label.strip())
    else:
        mcp_token.revoke_all_for_user(user.user_id)
    refreshed = tenancy.get_user(user.user_id)
    return JSONResponse({
        "ok": True,
        "status": mcp_token.status_for_user(refreshed),
        "devices": mcp_token.list_devices(refreshed),
    })


def _build_install_payload(plain_token: str, user_email: str, label: str) -> dict:
    """Shared between /profile/mcp-token and /profile/mcp-token-reissue.
    Returns the same shape so the UI flow that consumes it is identical.
    """
    shell_command = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http --header "Authorization: Bearer {plain_token}"'
    )
    install_mac_linux = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http '
        f'--header "Authorization: Bearer {plain_token}" '
        f'--header "X-MCP-Hostname: $(hostname)"'
    )
    install_win_cmd = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http '
        f'--header "Authorization: Bearer {plain_token}" '
        f'--header "X-MCP-Hostname: %COMPUTERNAME%"'
    )
    install_win_ps = (
        f'claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 '
        f'--transport http '
        f'--header "Authorization: Bearer {plain_token}" '
        f'--header "X-MCP-Hostname: $env:COMPUTERNAME"'
    )
    claude_prompt = (
        f"INSTALL TASK: Register the Colaberry MCP server for the operator "
        f"{user_email}. This is a one-time install on this machine.\n\n"
        f"DO ALL STEPS BELOW. Don't ask the user what the goal is or which "
        f"approach to use -- pick automatically. Don't echo the token in chat. "
        f"Report a short result line for each step.\n\n"
        f"--- Step 1: detect hostname ---\n"
        f"Run via Bash: `hostname` (Mac/Linux) or `echo %COMPUTERNAME%` "
        f"(Windows cmd) or `$env:COMPUTERNAME` (PowerShell). Whichever your "
        f"OS supports. Capture the result as <HOSTNAME> -- you'll need it.\n\n"
        f"--- Step 2: try the CLI path FIRST ---\n"
        f"Run: `claude --version`\n"
        f"   - If exit 0: run the install via CLI -- `claude mcp add colaberry "
        f"https://advisor.colaberry.ai/mcp/v1 --transport http "
        f"--header \"Authorization: Bearer {plain_token}\" "
        f"--header \"X-MCP-Hostname: <HOSTNAME>\"` -- then jump to Step 4.\n"
        f"   - If exit non-zero or \"command not found\": fall through to Step 3 "
        f"(direct config edit). This is the case for users with only the "
        f"VSCode extension, not the standalone CLI.\n\n"
        f"--- Step 3: direct config edit (universal fallback) ---\n"
        f"Locate the Claude Code config file:\n"
        f"  Mac/Linux: ~/.claude.json\n"
        f"  Windows:   %USERPROFILE%\\.claude.json (e.g. C:\\Users\\<name>\\.claude.json)\n\n"
        f"Use your Read + Write tools (NOT Bash, so JSON is parsed cleanly):\n"
        f"  1. Read the file. If missing, treat as empty object `{{}}`.\n"
        f"  2. Backup: copy to .claude.json.bak.\n"
        f"  3. Ensure the JSON has an `mcpServers` object key; set/overwrite "
        f"its `colaberry` entry to EXACTLY:\n"
        f"     {{\n"
        f"       \"type\": \"http\",\n"
        f"       \"url\": \"https://advisor.colaberry.ai/mcp/v1\",\n"
        f"       \"headers\": {{\n"
        f"         \"Authorization\": \"Bearer {plain_token}\",\n"
        f"         \"X-MCP-Hostname\": \"<HOSTNAME>\"\n"
        f"       }}\n"
        f"     }}\n"
        f"  4. PRESERVE all other mcpServers entries -- only set the "
        f"`colaberry` key. Don't drop sibling servers.\n"
        f"  5. Write the file back as pretty-printed JSON.\n\n"
        f"--- Step 4: verify ---\n"
        f"Read the config file back. Confirm the `colaberry` entry is present "
        f"with the right URL. Report success or failure.\n\n"
        f"--- Step 5: tell the user and STOP ---\n"
        f"Print exactly:\n"
        f"  ✅ MCP installed for {user_email} as host <HOSTNAME>.\n"
        f"  Close this Claude Code window and open a new one so the new\n"
        f"  Colaberry MCP server is loaded. After restart, ask Claude to\n"
        f"  list its tools -- you should see the colaberry_* tool family.\n\n"
        f"Then STOP. Don't run further tools. Don't ask questions. Don't echo "
        f"the token. The token grants this machine permission to call "
        f"Colaberry MCP tools (BC tickets, doctrine, memory) on the user's "
        f"behalf -- treat it like a credential.\n"
    )
    return {
        "token": plain_token,
        "label": label,
        "install_command": shell_command,
        "install_command_mac_linux": install_mac_linux,
        "install_command_win_cmd": install_win_cmd,
        "install_command_win_ps": install_win_ps,
        "claude_install_prompt": claude_prompt,
    }


@router.post("/profile/mcp-token-reissue")
async def mcp_token_reissue(request: Request, label: str = Form(...)):
    """Atomically revoke the existing device-token for `label` and mint a
    fresh one with the same label. Used by the "Reshow install" button on
    awaiting-ping rows: the user lost the original token (shown once at
    mint time), so we give them a new one without piling up duplicate
    rows or label suffixes.
    """
    user = _require_web_user(request)
    label = label.strip()
    if not label:
        return JSONResponse({"ok": False, "error": "label required"}, status_code=400)
    mcp_token.revoke_device(user.user_id, label)
    plain_token, updated = mcp_token.generate_for_user(user.user_id, label=label)
    payload = _build_install_payload(plain_token, user.email, updated.mcp_token_label or label)
    payload["ok"] = True
    payload["issued_at"] = updated.mcp_token_issued_at
    return JSONResponse(payload)


@router.post("/profile/mcp-revoke-unidentified")
async def mcp_revoke_unidentified(request: Request):
    """Revoke tokens for devices that never reported a hostname.

    Cleans up half-finished installs + older pre-hostname-capture entries
    without nuking devices that ARE clearly identified.
    """
    user = _require_web_user(request)
    _, count = mcp_token.revoke_unidentified_for_user(user.user_id)
    refreshed = tenancy.get_user(user.user_id)
    return JSONResponse({
        "ok": True,
        "revoked_count": count,
        "status": mcp_token.status_for_user(refreshed),
        "devices": mcp_token.list_devices(refreshed),
    })


@router.get("/profile/mcp-status.json")
async def mcp_status_json(request: Request):
    """Polled by the setup page (every ~2s) to detect first ping in real time.
    Returns aggregate status + per-device list so the UI can update each row.
    """
    user = _require_web_user(request)
    return JSONResponse({
        "status": mcp_token.status_for_user(user),
        "devices": mcp_token.list_devices(user),
    })
