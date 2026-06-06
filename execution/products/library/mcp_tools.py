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
      1. Per-user "<Name> AI" token, auto-refreshed via basecamp_oauth_token
         module if a refresh_token is on file. BC writes appear authored
         by the user's AI persona, not by CB System.
      2. Legacy bare-token vault entry (admin paste-form era, no refresh).
         Returned as-is; will 401 once the 14-day TTL elapses.
      3. Shared CB System token from BASECAMP_ACCESS_TOKEN env (fallback
         until every operator has their own per-user AI persona).
    """
    if user is not None and getattr(user, "user_id", None):
        try:
            from . import basecamp_oauth_token
            tok = basecamp_oauth_token.get_access_token_for_operator(user)
            if tok:
                return tok
        except basecamp_oauth_token.OAuthError:
            pass
        except ImportError:
            pass
        except Exception:
            pass
        try:
            from . import vault
            for key in ("basecamp_ai_clone", "basecamp_ai"):
                try:
                    plain = vault.read_secret(
                        user.user_id, key,
                        caller_id="mcp-server",
                        purpose="BC write as AI persona",
                    )
                    if plain:
                        return plain
                except KeyError:
                    continue
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


def _with_attribution(user, body: str) -> str:
    """Prepend a one-line attribution to BC write-tool bodies so readers
    can distinguish automated MCP posts from manual ones. Authorship in
    BC still shows the human's real name (we use their personal OAuth
    grant); this prefix says "the typing came through Claude."

    If the body starts with HTML comments (Op 3 idempotency markers
    `<!-- step:KIND:HASH -->`), the prefix is inserted AFTER them so the
    markers remain at the head where upstream idempotency scanners look.
    """
    if not body:
        return body
    import re
    name = (getattr(user, "display_name", "") or "").strip()
    if not name:
        name = (getattr(user, "email", "") or "").split("@")[0] or "Unknown"
    prefix = f"<p><em>via {name}'s Claude Code</em></p>\n"
    m = re.match(r"^(\s*(?:<!--.*?-->\s*)+)", body, flags=re.DOTALL)
    if m:
        return body[:m.end()] + prefix + body[m.end():]
    return prefix + body


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
            payload={"content": title, "description": _with_attribution(user, description)},
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
            payload={"content": _with_attribution(user, html_body)},
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
        filename = (args.get("filename") or "").strip()
        if not message_id:
            return {"ok": False, "error": "missing_required: gmail needs message_id"}
        if not attachment_id and not filename:
            return {
                "ok": False,
                "error": "missing_required: gmail needs `filename` (preferred) or `attachment_id`",
            }
        id_echo = {
            "message_id": message_id,
            "attachment_id": attachment_id,
            "filename": filename,
        }
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
    # For Gmail, prefer filename for the idempotency key when given (since
    # attachment_ids from different Gmail wrappers don't always match the
    # canonical Gmail v1 form -- a second call with the same wrapper id
    # would correctly hit cache, but if the caller switches to filename on
    # the second call the underlying file is the same yet the cache misses.
    # Filename is the stable user-visible identity).
    gmail_id_for_key = id_echo.get("filename") or id_echo.get("attachment_id") or ""
    idempotency_key = attachment_index.compute_key(
        source=source,
        message_id=id_echo.get("message_id", "") or "",
        attachment_id=gmail_id_for_key,
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
                    id_echo["message_id"],
                    access_token,
                    attachment_id=id_echo.get("attachment_id") or "",
                    filename=id_echo.get("filename") or "",
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
                "message_id": {"type": "string", "description": "Gmail message id (the stable rfc822 id, NOT the connector-side wrapper id)"},
                "filename": {"type": "string", "description": "Gmail attachment filename (preferred over attachment_id -- robust against id-format drift across Gmail-API wrappers). Case-insensitive basename match."},
                "attachment_id": {"type": "string", "description": "Canonical Gmail v1 attachment id from `users.messages.get(format=full).payload.parts[*].body.attachmentId`. Use `filename` instead if your Gmail client returns a wrapper-internal id format."},
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


def _tool_list_assets(user, args: dict) -> dict:
    """List library assets visible to the calling operator's company.

    Returns asset summaries (asset_id, name, short description) for the
    category requested, optionally narrowed by a free-text query. Scoping:
    the operator's own-company assets + any community-owned assets that
    have an approval row for their company.
    """
    from . import inventory
    category = (args.get("category") or "").strip()
    if not category:
        return {"ok": False, "error": "category is required"}
    if not inventory.get_category(category):
        return {"ok": False,
                "error": f"unknown category {category!r}; valid: "
                                + ", ".join(c.key for c in inventory.CATEGORIES)}
    q = (args.get("query") or "").strip().lower()
    try:
        limit = max(1, min(int(args.get("limit", 20)), 100))
    except (TypeError, ValueError):
        limit = 20
    try:
        rows = inventory.load_category(category)
    except Exception as e:
        return {"ok": False, "error": f"could not load category: {e}"}
    visible = inventory.filter_for_company(
        rows, category, getattr(user, "company_id", None),
    )
    if q:
        visible = [
            r for r in visible
            if q in (r.get("name") or "").lower()
            or q in (r.get("description") or "").lower()
            or q in " ".join((r.get("tags") or [])).lower()
        ]
    out = []
    for r in visible[:limit]:
        out.append({
            "asset_id": r.get("id") or r.get("name") or "",
            "name": r.get("name") or "",
            "description": (r.get("description") or "")[:240],
            "tags": list(r.get("tags") or []),
            "vetted": bool(r.get("vetted")),
        })
    return {"ok": True, "category": category, "count": len(out),
            "total_visible": len(visible), "assets": out}


def _tool_get_asset(user, args: dict) -> dict:
    """Return full content of a library asset so Claude can read + apply it.

    Visibility: same-company assets and community assets are returned
    directly; assets owned by another company require an approval row for
    the caller's company (companies_with_access). On miss, returns a
    clean error code so Claude can render a useful message to the user.
    """
    from dataclasses import asdict
    from . import store, inventory
    category = (args.get("category") or "").strip()
    asset_id = (args.get("asset_id") or "").strip()
    if not category or not asset_id:
        return {"ok": False, "error": "category and asset_id are required"}
    if not inventory.get_category(category):
        return {"ok": False, "error": f"unknown category {category!r}"}
    # store.get_metadata returns an empty default record when no file
    # exists, so probe the file directly to distinguish "no asset" from
    # "asset exists but has no metadata yet".
    if not store.meta_path("global", category, asset_id).exists():
        return {"ok": False, "error": "asset_not_found",
                "asset_id": asset_id, "category": category}
    meta = store.get_metadata("global", category, asset_id)
    owning = (getattr(meta, "owning_company_id", "") or "community").strip() or "community"
    viewer_co = getattr(user, "company_id", None)
    visible = (owning == viewer_co) or (owning == "community")
    if not visible:
        try:
            from . import tenancy
            visible = bool(tenancy.companies_with_access(
                "library_asset", asset_id, category, viewer_co,
            ))
        except Exception:
            visible = False
    if not visible:
        return {"ok": False, "error": "asset_not_visible_to_your_company",
                "asset_id": asset_id, "category": category,
                "owning_company_id": owning}
    return {"ok": True, "asset": asdict(meta)}


def _tool_propose_asset(user, args: dict) -> dict:
    """Light-weight asset proposal from inside a live Claude Code session.

    Triggered when Claude notices the user authoring or invoking a
    reusable thing (skill / agent / prompt / MCP / template / workflow /
    etc.) that isn't yet in the operator's Colaberry library. Creates a
    Submission tagged to the operator's company; when
    LIBRARY_AUTO_APPROVE_ON_SUBMIT=1 (the default rollout posture) the
    submission accepts immediately and the asset shows up at
    /library/<category>/<id> for everyone at the company.

    Deliberately tiny argument surface so Claude can fire it without
    belaboring the proposal: category, name, description plus a brief
    why-useful + source_url. Anything richer (full readme, install
    steps, code samples) can be edited in later via the asset detail
    page; the point of this tool is fast capture, not exhaustive
    documentation.
    """
    from . import inventory, store
    import os

    category = (args.get("category") or "").strip()
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip()
    if not category or not name or not description:
        return {"ok": False,
                "error": "category, name, and description are required"}
    if not inventory.get_category(category):
        return {"ok": False,
                "error": f"unknown category {category!r}; valid: "
                                + ", ".join(c.key for c in inventory.CATEGORIES)}

    why_useful = (args.get("why_useful") or "").strip()
    source_url = (args.get("source_url") or "").strip()
    tags = args.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []

    # Owner: the operator's own company. This is the locked-in answer
    # from the rollout (proposals are private-to-Colaberry by default;
    # admins can re-tag to community later).
    owning_company_id = (getattr(user, "company_id", "") or "").strip() or "community"

    # Compose the Submission. `how_to_use` is left for later edit; the
    # why_useful blurb lands in `payload` so reviewers see it.
    payload = {}
    if why_useful:
        payload["what_its_for"] = why_useful
    body = args.get("body") or ""
    if isinstance(body, str) and body.strip():
        payload["readme_markdown"] = body.strip()
    if args.get("install_command"):
        payload["install_command"] = str(args["install_command"]).strip()
    if args.get("docs_url"):
        payload["docs_url"] = str(args["docs_url"]).strip()

    try:
        sub = store.submit(
            workspace="global",
            category=category,
            submitted_by=getattr(user, "email", "") or "claude-proposal",
            name=name,
            description=description,
            how_to_use="",
            example="",
            tags=tags,
            source=source_url or "claude-proposal",
            payload=payload,
            owning_company_id=owning_company_id,
        )
    except Exception as e:
        return {"ok": False, "error": f"submission_failed: {e}"}

    asset_id = ""
    auto_approve = (os.environ.get("LIBRARY_AUTO_APPROVE_ON_SUBMIT", "") or "").strip() in ("1", "true", "yes", "on")
    auto_approved = False
    if auto_approve:
        try:
            store.review_submission(
                workspace="global",
                submission_id=sub.submission_id,
                decision="accepted",
                reviewer=getattr(user, "email", "") or "claude-proposal",
                notes="auto-approved per LIBRARY_AUTO_APPROVE_ON_SUBMIT rollout policy (propose_asset)",
            )
            asset_id = f"sub-{sub.submission_id}"
            auto_approved = True
            # Also flip the tenancy approval row so the asset's visibility
            # opens for the owner's company without needing an admin pass.
            try:
                from . import tenancy
                tenancy.record_approval(
                    item_kind="library_asset",
                    item_id=asset_id,
                    category=category,
                    company_id=owning_company_id,
                    approved_by_user_id=getattr(user, "user_id", "system"),
                    status="approved",
                    notes="auto-approved per LIBRARY_AUTO_APPROVE_ON_SUBMIT rollout policy (propose_asset)",
                )
            except Exception:
                pass
        except Exception as e:
            return {"ok": True, "submission_id": sub.submission_id,
                    "auto_approved": False,
                    "warning": f"auto_approve_failed: {e}",
                    "owning_company_id": owning_company_id}

    return {
        "ok": True,
        "submission_id": sub.submission_id,
        "asset_id": asset_id,
        "category": category,
        "owning_company_id": owning_company_id,
        "auto_approved": auto_approved,
        "library_url": (f"/library/{category}/{asset_id}" if asset_id
                                else f"/library/pending"),
    }


TOOLS.append(Tool(
    name="colaberry_propose_asset",
    description=(
        "Propose adding a reusable thing (skill / agent / prompt / MCP server / "
        "template / workflow / policy / etc.) to the operator's Colaberry library "
        "WHILE they're working in any project. Fire this whenever the user authors "
        "a new asset OR invokes an existing 3rd-party asset that's not yet in their "
        "Colaberry catalog -- the goal is fast, opportunistic capture so the library "
        "grows naturally from real work. Keep arguments minimal: name, category, "
        "description, plus a one-line why_useful and (if you have it) source_url. "
        "Don't pre-write extensive docs -- the operator can flesh those out from the "
        "asset detail page later. Server auto-approves into the operator's company "
        "library when the rollout flag is on."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string",
                                  "description": "Category key: skills, agents, prompts, mcp, capabilities, "
                                                              "templates, workflows, policies, governance, recovery, chaos, "
                                                              "projections, evals, connectors, adapters."},
            "name": {"type": "string",
                              "description": "Human-readable name (Title Case ok)."},
            "description": {"type": "string",
                                      "description": "One sentence on what this asset does."},
            "why_useful": {"type": "string",
                                      "description": "Optional one-liner on why an operator would reach for this."},
            "source_url": {"type": "string",
                                      "description": "Optional URL where the asset lives (GitHub, npm, PyPI, docs page)."},
            "body": {"type": "string",
                              "description": "Optional fuller markdown body (readme, instructions). Skip if you don't have it -- the operator can fill it in later."},
            "install_command": {"type": "string",
                                          "description": "Optional install command for MCP servers / packaged tools."},
            "docs_url": {"type": "string",
                                  "description": "Optional documentation URL."},
            "tags": {"type": "array",
                              "items": {"type": "string"},
                              "description": "Optional list of tag strings for filtering."},
        },
        "required": ["category", "name", "description"],
    },
    handler=_tool_propose_asset,
))

TOOLS.append(Tool(
    name="colaberry_list_assets",
    description=(
        "List library assets visible to the caller's company in a given "
        "category (skills, agents, prompts, mcp, workflows, capabilities, "
        "templates, policies, governance, recovery, chaos, projections, "
        "evals, connectors, adapters). Optional `query` filters by name / "
        "description / tag substring. Use this to discover assets before "
        "fetching one with colaberry_get_asset."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string",
                                  "description": "Category key, e.g. 'skills'"},
            "query": {"type": "string",
                              "description": "Optional substring filter"},
            "limit": {"type": "integer",
                              "description": "Max rows to return (1-100, default 20)"},
        },
        "required": ["category"],
    },
    handler=_tool_list_assets,
))

TOOLS.append(Tool(
    name="colaberry_get_asset",
    description=(
        "Fetch the full content of one library asset (metadata + readme "
        "body + install command + code samples + ...) so Claude can read + "
        "apply it in the current session. Respects company-scoped visibility: "
        "the caller can only fetch their own company's assets and community "
        "assets. The user-facing 'Copy Claude prompt' button on every asset "
        "detail page generates an instruction that invokes this tool."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "category": {"type": "string",
                                  "description": "Category key, e.g. 'skills'"},
            "asset_id": {"type": "string",
                                  "description": "The asset's stable id (from list_assets)"},
        },
        "required": ["category", "asset_id"],
    },
    handler=_tool_get_asset,
))


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
