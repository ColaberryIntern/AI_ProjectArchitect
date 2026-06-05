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


def _bearer_user(authorization: str | None) -> tenancy.User | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return mcp_token.validate_token(authorization.split(" ", 1)[1].strip())


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
                                authorization: str | None = Header(default=None)):
    """JSON-RPC endpoint. Handles single requests; MCP batching not yet supported."""
    user = _bearer_user(authorization)
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
    status = mcp_token.status_for_user(user)
    # Compute the user's personal BC project URL for the quick-link card.
    import os as _os
    bc_account = _os.environ.get("BASECAMP_ACCOUNT_ID", "3945211")
    personal_bc_url = ""
    if user.personal_bc_project_id:
        personal_bc_url = f"https://3.basecamp.com/{bc_account}/projects/{user.personal_bc_project_id}"
    return request.app.state.templates.TemplateResponse(
        request, "library/mcp_setup.html",
        {
            "request": request,
            "current_session_user": user,
            "status": status,
            "current_product": "library",
            "company_id": user.company_id,
            "library_nav_active": "mcp_setup",
            "page_title": "Connect Claude Code",
            "mcp_status": status,
            "personal_bc_url": personal_bc_url,
        },
    )


@router.post("/profile/mcp-token")
async def mcp_token_generate(request: Request, label: str = Form("default")):
    """Generate a new MCP token for the signed-in user. Revokes any previous token.

    Returns the plaintext token ONCE in the JSON response so the setup page can
    show it for copying. We never store it; the user is responsible for pasting
    into their laptop's claude config.
    """
    user = _require_web_user(request)
    plain_token, updated = mcp_token.generate_for_user(user.user_id, label=label)
    return JSONResponse({
        "ok": True,
        "token": plain_token,
        "issued_at": updated.mcp_token_issued_at,
        "label": updated.mcp_token_label,
        "install_command": (
            f"claude mcp add colaberry https://advisor.colaberry.ai/mcp/v1 "
            f"--transport http --header \"Authorization: Bearer {plain_token}\""
        ),
    })


@router.post("/profile/mcp-revoke")
async def mcp_revoke(request: Request):
    user = _require_web_user(request)
    mcp_token.revoke_for_user(user.user_id)
    return JSONResponse({"ok": True, "status": "red"})


@router.get("/profile/mcp-status.json")
async def mcp_status_json(request: Request):
    """Polled by the setup page (every ~2s) to detect first ping in real time."""
    user = _require_web_user(request)
    return JSONResponse({
        "status": mcp_token.status_for_user(user),
        "issued_at": user.mcp_token_issued_at,
        "last_used_at": user.mcp_token_last_used_at,
        "revoked_at": user.mcp_token_revoked_at,
        "label": user.mcp_token_label,
    })
