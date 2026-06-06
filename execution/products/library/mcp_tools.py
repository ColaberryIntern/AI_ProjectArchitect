"""MCP tool implementations: thin wrappers over the existing Op 2/3/4 + memory
helpers so a remote Claude Code session can reach Colaberry's backend via
tool calls.

Each tool has:
  - name (string used in tools/list + tools/call)
  - description (shown to Claude in the tool catalog)
  - inputSchema (JSON Schema validated by Claude before calling)
  - run(user, args) -> dict (returns the structured result; the MCP route
    serializes it into the MCP content[] envelope)

Tools intentionally accept a `bc_project_id` argument rather than always
targeting the user's personal project, so Claude can dual-update: post
progress to BOTH the user's personal session anchor (in user.personal_bc_project_id)
AND the actual project ticket the user is working on. The doctrine resource
(see mcp_doctrine.py) tells Claude when to do which.

Auth: the route handler resolves the user from the Authorization header
before invoking run(). Tools never see raw tokens.

All BC calls go through the shared CB System token (BASECAMP_ACCESS_TOKEN
env). A future enhancement would scope writes by checking the user's actual
BC membership before letting them target a project.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from pathlib import Path
import time

from . import ticket_creation_flow


USER_AGENT = "Colaberry MCP Server (ali@colaberry.com)"

# Server-side per-user memory store. The user's local workspace repo is the
# eventual source of truth (they can also commit to it manually), but the
# MCP server owns the canonical version for the cross-session memory that
# Op 5 doctrine specifies. Stored at output/mcp_memory/<email>.md.
ROOT = Path(__file__).resolve().parents[3]
MEMORY_DIR = ROOT / "output" / "mcp_memory"


def _memory_path_for(email: str) -> Path:
    safe = email.replace("/", "_").replace("\\", "_")
    return MEMORY_DIR / f"{safe}.md"


def _read_memory(email: str) -> str:
    p = _memory_path_for(email)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _append_memory(email: str, scope: str, fact: str) -> None:
    p = _memory_path_for(email)
    p.parent.mkdir(parents=True, exist_ok=True)
    when = time.strftime("%Y-%m-%d", time.gmtime())
    entry = f"\n## {scope} ({when})\n\n{fact.strip()}\n"
    if not p.exists():
        header = f"# OPERATOR_MEMORY for {email}\n\nAppended by Claude during sessions. Read at next session start.\n"
        p.write_text(header + entry, encoding="utf-8")
    else:
        with p.open("a", encoding="utf-8") as f:
            f.write(entry)


def _bc_token(user=None) -> str:
    """Resolve the BC token to use for this call.

    Order:
      1. Per-user "<Name> AI" token from the vault (preferred -- BC writes
         appear authored by Ralph AI, not by CB System). Looked up by user
         when supplied.
      2. Shared CB System token from BASECAMP_ACCESS_TOKEN env (fallback
         until per-user AI personas are provisioned across the company).
    """
    if user is not None and getattr(user, "bc_ai_user_id", None):
        try:
            from . import vault
            plain = vault.read_secret(
                user.user_id, "basecamp_ai",
                caller_id="mcp-server", purpose="BC write as AI persona",
            )
            if plain:
                return plain
        except Exception:
            pass
    tok = os.environ.get("BASECAMP_ACCESS_TOKEN", "")
    if not tok:
        raise RuntimeError("no BC token available (no per-user AI token, no BASECAMP_ACCESS_TOKEN env)")
    return tok


def _bc_account() -> str:
    return os.environ.get("BASECAMP_ACCOUNT_ID", "3945211")


def _bc_request(method: str, url: str, payload: dict | None = None, user=None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, method=method, data=data,
        headers={
            "Authorization": f"Bearer {_bc_token(user)}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"BC {method} {url} -> HTTP {e.code} {e.reason}: {msg}") from e


# ── Tool implementations ────────────────────────────────────────────


def _tool_classify_prompt(user, args: dict) -> dict:
    prompt = (args.get("text") or "").strip()
    if not prompt:
        return {"ok": False, "error": "text is required"}
    classification = ticket_creation_flow.classify_prompt(prompt)
    return {
        "ok": True,
        "kind": classification.kind,
        "matched_signal": classification.matched_signal,
        "existing_ticket_ref": classification.existing_ticket_ref,
    }


def _tool_derive_title(user, args: dict) -> dict:
    prompt = (args.get("text") or "").strip()
    if not prompt:
        return {"ok": False, "error": "text is required"}
    return {"ok": True, "title": ticket_creation_flow.derive_proposed_title(prompt)}


def _resolve_default_anchor(user) -> dict:
    """Return the user's personal BC project + todolist for session anchoring."""
    pid = getattr(user, "personal_bc_project_id", None)
    lid = getattr(user, "personal_bc_todolist_id", None)
    return {
        "bc_project_id": int(pid) if pid else None,
        "todolist_id": int(lid) if lid else None,
        "url": (
            f"https://3.basecamp.com/{_bc_account()}/projects/{pid}"
            if pid else None
        ),
    }


def _tool_get_personal_anchor(user, args: dict) -> dict:
    return {"ok": True, "anchor": _resolve_default_anchor(user)}


def _tool_create_ticket(user, args: dict) -> dict:
    """Create a BC todo. Defaults to the user's personal project + todolist when
    bc_project_id/list_id aren't supplied -- the common case for "create my
    session anchor". When the user is working on an existing project (per the
    project's .colaberry.json or an explicit reference) Claude passes the
    actual project's ids.
    """
    title = (args.get("title") or "").strip()
    description = (args.get("description") or "").strip()
    bc_project_id = args.get("bc_project_id")
    todolist_id = args.get("todolist_id") or args.get("list_id")
    if not title:
        return {"ok": False, "error": "title required"}
    if not bc_project_id or not todolist_id:
        anchor = _resolve_default_anchor(user)
        bc_project_id = bc_project_id or anchor["bc_project_id"]
        todolist_id = todolist_id or anchor["todolist_id"]
    if not bc_project_id or not todolist_id:
        return {"ok": False, "error": "bc_project_id + todolist_id required (and user has no personal project configured)"}
    try:
        body = _bc_request(
            "POST",
            f"https://3.basecampapi.com/{_bc_account()}/buckets/{bc_project_id}/todolists/{todolist_id}/todos.json",
            payload={"content": title, "description": description},
            user=user,
        )
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "ticket_id": body.get("id"),
        "url": body.get("app_url"),
        "bc_project_id": int(bc_project_id),
        "todolist_id": int(todolist_id),
    }


def _tool_post_progress(user, args: dict) -> dict:
    """Post a comment on a BC recording. Idempotent via the Op 3 marker
    convention: callers should include `<!-- step:KIND:HASH -->` at the
    top of html_body so re-posting the same card is a no-op upstream.
    """
    bc_project_id = args.get("bc_project_id")
    ticket_id = args.get("ticket_id") or args.get("todo_id")
    html_body = args.get("html_body") or args.get("content")
    if not bc_project_id or not ticket_id or not html_body:
        return {"ok": False, "error": "bc_project_id + ticket_id + html_body required"}
    try:
        body = _bc_request(
            "POST",
            f"https://3.basecampapi.com/{_bc_account()}/buckets/{bc_project_id}/recordings/{ticket_id}/comments.json",
            payload={"content": html_body},
            user=user,
        )
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "comment_id": body.get("id"), "url": body.get("app_url")}


def _tool_close_ticket(user, args: dict) -> dict:
    """Mark a BC todo complete. The route gates by `confidence`: Op 4 auto-
    close fires only at >= 0.85; below that, the tool refuses and tells
    Claude to ask the user for confirmation first.
    """
    bc_project_id = args.get("bc_project_id")
    ticket_id = args.get("ticket_id")
    confidence = float(args.get("confidence", 0.0))
    if not bc_project_id or not ticket_id:
        return {"ok": False, "error": "bc_project_id + ticket_id required"}
    if confidence < 0.85:
        return {
            "ok": False,
            "error": f"confidence {confidence} below auto-close threshold 0.85; ask the user to confirm before closing",
            "needs_user_confirmation": True,
        }
    try:
        _bc_request(
            "POST",
            f"https://3.basecampapi.com/{_bc_account()}/buckets/{bc_project_id}/todos/{ticket_id}/completion.json",
            payload={},
            user=user,
        )
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "ticket_id": int(ticket_id), "closed": True}


def _tool_find_project(user, args: dict) -> dict:
    """Fuzzy-find a BC project by name. Returns top 5 matches with their ids
    + the default todolist id of each. Lets Claude resolve "post this to
    Enterprise Accelerator" without the user knowing numeric ids.
    """
    query = (args.get("name") or args.get("query") or "").strip().lower()
    if not query:
        return {"ok": False, "error": "name (or query) required"}
    out: list[dict] = []
    page = 1
    while page <= 6 and len(out) < 5:
        try:
            projects = _bc_request("GET",
                f"https://3.basecampapi.com/{_bc_account()}/projects.json?page={page}")
        except RuntimeError as e:
            return {"ok": False, "error": str(e)}
        if not isinstance(projects, list) or not projects:
            break
        for p in projects:
            name = (p.get("name") or "").strip()
            if query in name.lower():
                out.append({
                    "bc_project_id": p.get("id"),
                    "name": name,
                    "url": p.get("app_url"),
                    "description": (p.get("description") or "")[:200],
                })
                if len(out) >= 5:
                    break
        page += 1
    return {"ok": True, "matches": out, "query": query}


def _tool_get_memory(user, args: dict) -> dict:
    """Return the user's OPERATOR_MEMORY content."""
    return {"ok": True, "memory_markdown": _read_memory(user.email)}


def _tool_remember(user, args: dict) -> dict:
    """Append a new entry to the user's OPERATOR_MEMORY."""
    fact = (args.get("fact") or args.get("value") or "").strip()
    scope = (args.get("scope") or "general").strip()
    if not fact:
        return {"ok": False, "error": "fact required"}
    try:
        _append_memory(user.email, scope, fact)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "scope": scope, "saved": True}


def _tool_attachment_fetch(user, args: dict) -> dict:
    """Stage an attachment from Gmail / Basecamp / Drive into the operator's
    Google Drive and return a Drive ref. See
    directives/colaberry-attachment-fetch.md for the contract.
    """
    from . import attachment_index, drive_staging, google_oauth_token
    from .attachment_sources import (
        basecamp as bc_source,
        drive as drive_source,
        gmail as gmail_source,
    )

    source = (args.get("source") or "").strip().lower()
    if source not in ("gmail", "basecamp", "drive"):
        return {"ok": False, "error": "missing_required: source must be gmail|basecamp|drive"}

    # 1. Per-source arg validation. Echo IDs back so the caller's result
    # is self-describing without re-emitting the raw args dict.
    id_echo: dict = {}
    if source == "gmail":
        message_id = (args.get("message_id") or "").strip()
        attachment_id = (args.get("attachment_id") or "").strip()
        if not message_id or not attachment_id:
            return {"ok": False, "error": "missing_required: gmail needs message_id + attachment_id"}
        id_echo = {"message_id": message_id, "attachment_id": attachment_id}
    elif source == "basecamp":
        project_id = args.get("project_id")
        recording_id = args.get("recording_id")
        attachment_sgid = (args.get("attachment_sgid") or "").strip()
        if not project_id or not recording_id or not attachment_sgid:
            return {"ok": False, "error": "missing_required: basecamp needs project_id + recording_id + attachment_sgid"}
        id_echo = {"project_id": project_id, "recording_id": recording_id, "attachment_sgid": attachment_sgid}
    else:  # drive
        drive_file_id = (args.get("drive_file_id") or "").strip()
        if not drive_file_id:
            return {"ok": False, "error": "missing_required: drive needs drive_file_id"}
        id_echo = {"drive_file_id": drive_file_id}

    # 2. Compute idempotency key + check the per-operator index.
    idempotency_key = attachment_index.compute_key(
        source=source,
        message_id=id_echo.get("message_id", "") or "",
        attachment_id=id_echo.get("attachment_id", "") or "",
        project_id=id_echo.get("project_id", "") or "",
        recording_id=id_echo.get("recording_id", "") or "",
        sgid=id_echo.get("attachment_sgid", "") or "",
        drive_file_id=id_echo.get("drive_file_id", "") or "",
    )
    existing = attachment_index.lookup(user.email, idempotency_key)
    if existing:
        return {
            "ok": True,
            "drive_file_id": existing.drive_file_id,
            "drive_url": existing.drive_url,
            "mime_type": existing.mime_type,
            "size_bytes": existing.size_bytes,
            "source": existing.source,
            "source_message_id": existing.source_message_id,
            "sender": existing.sender,
            "filename": existing.filename,
            "saved_at": existing.saved_at,
            "reused_existing": True,
        }

    # 3. Concurrency guard. Two MCP calls with the same key shouldn't both
    # upload; second caller gets a clean retry signal.
    if not attachment_index.begin_inflight(user.email, idempotency_key):
        return {
            "ok": False,
            "error": "fetch_in_progress: another call is already staging this attachment; retry shortly",
            "source": source,
            "source_id_echo": id_echo,
        }

    try:
        # 4. Resolve credentials.
        try:
            access_token = google_oauth_token.get_access_token_for_operator(user)
        except google_oauth_token.OAuthError as e:
            return {
                "ok": False,
                "error": f"{e.code}",
                "source": source,
                "source_id_echo": id_echo,
            }

        # 5. Source-specific fetch.
        try:
            if source == "gmail":
                fetched = gmail_source.fetch(
                    id_echo["message_id"], id_echo["attachment_id"], access_token,
                )
            elif source == "basecamp":
                # Reuse the existing per-user BC token resolution.
                bc_token = _bc_token(user)
                fetched = bc_source.fetch(
                    int(id_echo["project_id"]),
                    int(id_echo["recording_id"]),
                    id_echo["attachment_sgid"],
                    bc_token,
                )
            else:  # drive passthrough
                fetched = drive_source.fetch(id_echo["drive_file_id"], access_token)
        except (gmail_source.GmailError, bc_source.BasecampError,
                  drive_source.DriveError) as e:
            return {"ok": False, "error": e.code, "source": source, "source_id_echo": id_echo}

        # 6. Stage to Drive (skip for drive passthrough -- it already IS Drive).
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        year_month = time.strftime("%Y-%m", time.gmtime())
        destination_subpath = (args.get("destination_subpath") or "").strip() or None

        if source == "drive":
            drive_file_id = fetched.drive_file_id or id_echo["drive_file_id"]
            drive_url = fetched.drive_url or f"https://drive.google.com/file/d/{drive_file_id}/view"
            source_message_id = id_echo["drive_file_id"]
        else:
            try:
                meta = drive_staging.upload(
                    data=fetched.data,
                    filename=fetched.filename,
                    mime_type=fetched.mime_type,
                    source=source,
                    sender_slug=fetched.sender,
                    year_month=year_month,
                    access_token=access_token,
                    destination_subpath=destination_subpath,
                )
            except drive_staging.DriveStagingError as e:
                return {"ok": False, "error": e.code, "source": source, "source_id_echo": id_echo}
            drive_file_id = meta.get("id") or ""
            drive_url = meta.get("webViewLink") or (
                f"https://drive.google.com/file/d/{drive_file_id}/view" if drive_file_id else ""
            )
            if source == "gmail":
                source_message_id = id_echo["message_id"]
            else:  # basecamp
                source_message_id = str(id_echo["recording_id"])

        if not drive_file_id:
            return {"ok": False, "error": "drive_upload_returned_no_id",
                            "source": source, "source_id_echo": id_echo}

        # 7. Record in idempotency index.
        ref = attachment_index.AttachmentRef(
            idempotency_key=idempotency_key,
            source=source,
            drive_file_id=drive_file_id,
            drive_url=drive_url,
            mime_type=fetched.mime_type,
            size_bytes=fetched.size_bytes,
            filename=fetched.filename,
            sender=fetched.sender,
            saved_at=now_iso,
            source_message_id=source_message_id,
            source_attachment_id=(
                id_echo.get("attachment_id")
                or id_echo.get("attachment_sgid")
                or id_echo.get("drive_file_id")
                or ""
            ),
        )
        attachment_index.record(user.email, ref)

        return {
            "ok": True,
            "drive_file_id": drive_file_id,
            "drive_url": drive_url,
            "mime_type": fetched.mime_type,
            "size_bytes": fetched.size_bytes,
            "source": source,
            "source_message_id": source_message_id,
            "sender": fetched.sender,
            "filename": fetched.filename,
            "saved_at": now_iso,
            "reused_existing": False,
        }
    finally:
        attachment_index.end_inflight(user.email, idempotency_key)


# ── Tool registry ────────────────────────────────────────────────────


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable

    def to_listing(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


TOOLS: list[Tool] = [
    Tool(
        name="colaberry_classify_prompt",
        description=(
            "Classify a user prompt as 'substantive' (will mutate something; needs a BC ticket), "
            "'readonly' (just answers a question; no ticket needed), or 'override_*' "
            "(user explicitly set --no-ticket or --ticket flag). Returns the kind + the "
            "matched signal. Call this FIRST on every user prompt per Op 2 doctrine."
        ),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The user's prompt text"}},
            "required": ["text"],
        },
        handler=_tool_classify_prompt,
    ),
    Tool(
        name="colaberry_derive_ticket_title",
        description="Derive a short BC ticket title (~90 chars) from a prompt's first sentence.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=_tool_derive_title,
    ),
    Tool(
        name="colaberry_get_personal_anchor",
        description=(
            "Return the user's personal BC project id + default todolist id + project URL. "
            "Use this when you need to create a session-anchor ticket in the user's personal project."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_tool_get_personal_anchor,
    ),
    Tool(
        name="colaberry_create_ticket",
        description=(
            "Create a BC todo. When bc_project_id + todolist_id are omitted, defaults to "
            "the user's personal project + default todolist (the common case for creating "
            "a session anchor). Pass explicit ids to create in any other project the user "
            "has access to (e.g. when working on an Enterprise Accelerator task, target that "
            "project's BC; per Op 2 doctrine, ALSO create a session anchor in personal)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string", "description": "HTML allowed"},
                "bc_project_id": {"type": "integer"},
                "todolist_id": {"type": "integer"},
            },
            "required": ["title"],
        },
        handler=_tool_create_ticket,
    ),
    Tool(
        name="colaberry_post_progress",
        description=(
            "Post a progress comment on a BC ticket. Caller should include the Op 3 "
            "idempotency marker `<!-- step:KIND:HASH -->` at the top of html_body so "
            "repeat-posting the same card is a safe no-op upstream."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "bc_project_id": {"type": "integer"},
                "ticket_id": {"type": "integer"},
                "html_body": {"type": "string"},
            },
            "required": ["bc_project_id", "ticket_id", "html_body"],
        },
        handler=_tool_post_progress,
    ),
    Tool(
        name="colaberry_close_ticket",
        description=(
            "Mark a BC todo complete. Requires confidence >= 0.85 (Op 4 auto-close gate); "
            "below that, refuses and asks the caller to confirm with the user first. "
            "Only close the personal session anchor automatically; for project tickets, "
            "always ask the user."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "bc_project_id": {"type": "integer"},
                "ticket_id": {"type": "integer"},
                "confidence": {"type": "number", "description": "0.0-1.0"},
            },
            "required": ["bc_project_id", "ticket_id", "confidence"],
        },
        handler=_tool_close_ticket,
    ),
    Tool(
        name="colaberry_find_project",
        description=(
            "Fuzzy-find a BC project by name. Returns up to 5 matches. Use this when the "
            "user references a project verbally (e.g. 'post this to Enterprise Accelerator') "
            "so you can resolve the numeric bc_project_id before calling colaberry_post_progress."
        ),
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_tool_find_project,
    ),
    Tool(
        name="colaberry_get_memory",
        description=(
            "Return the user's OPERATOR_MEMORY markdown. Contains corrections + preferences "
            "the user has accumulated across sessions. Read it at session start to avoid "
            "repeating mistakes the user has already corrected."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_tool_get_memory,
    ),
    Tool(
        name="colaberry_remember",
        description=(
            "Append a fact to the user's OPERATOR_MEMORY. Use when the user corrects you "
            "or expresses a strong preference -- save it so future sessions don't repeat the mistake."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "fact": {"type": "string"},
                "scope": {"type": "string", "description": "Category, e.g. 'style', 'tooling', 'bc-flow'"},
            },
            "required": ["fact"],
        },
        handler=_tool_remember,
    ),
    Tool(
        name="colaberry_attachment_fetch",
        description=(
            "Download a file attachment from Gmail, Basecamp, or Drive and stage it in the operator's "
            "Google Drive under Colaberry Inbound/<source>/<sender>/<YYYY-MM>/<filename>. Returns "
            "a Drive ref (file id + URL + metadata) -- never the raw bytes -- so downstream Claude "
            "sessions can read the file with their existing Google Drive connector. Idempotent: "
            "repeat calls with the same source identifiers return the previously-staged file. "
            "Operator must have run scripts/bootstrap_google_oauth.py first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["gmail", "basecamp", "drive"],
                    "description": "Where to download from",
                },
                "message_id": {"type": "string", "description": "Gmail message/thread id"},
                "attachment_id": {"type": "string", "description": "Gmail attachment id from the message's payload.parts"},
                "project_id": {"type": "integer", "description": "Basecamp bucket id"},
                "recording_id": {"type": "integer", "description": "Basecamp recording id (todo / comment) hosting the attachment"},
                "attachment_sgid": {"type": "string", "description": "Basecamp blob sgid"},
                "drive_file_id": {"type": "string", "description": "Drive file id for passthrough mode"},
                "destination_subpath": {"type": "string", "description": "Optional override for the YYYY-MM folder segment"},
            },
            "required": ["source"],
        },
        handler=_tool_attachment_fetch,
    ),
]


TOOL_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def call_tool(tool_name: str, user, args: dict) -> dict:
    """Dispatch a tools/call request to the right handler. Returns the
    structured dict; the route wraps it into the MCP content[] envelope.
    """
    tool = TOOL_BY_NAME.get(tool_name)
    if not tool:
        return {"ok": False, "error": f"unknown tool {tool_name!r}; "
                                                f"available: {list(TOOL_BY_NAME.keys())}"}
    try:
        return tool.handler(user, args or {})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
