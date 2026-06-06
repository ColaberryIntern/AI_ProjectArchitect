"""Gmail attachment fetch via the Gmail v1 REST API.

Endpoints:
    GET /users/me/messages/{message_id}                 # to resolve sender + part metadata
    GET /users/me/messages/{message_id}/attachments/{attachment_id}

Per the directive's failure-first design: 15s timeout, 3 retries with
exponential backoff on 429/5xx, no silent swallows. Never logs the
access_token.

Stdlib only (urllib). No google-api-python-client.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from . import FetchedAttachment

logger = logging.getLogger(__name__)

API_BASE = "https://gmail.googleapis.com/gmail/v1"
TIMEOUT = 15.0
MAX_RETRIES = 3
USER_AGENT = "Colaberry MCP Attachment Fetch (ali@colaberry.com)"


class GmailError(Exception):
    """Machine-readable Gmail fetch failure."""
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _request_json(url: str, access_token: str, *, attempt: int = 1) -> dict:
    """GET + parse JSON. Handles 429/5xx retry; raises GmailError on terminal."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        code = e.code
        if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
            backoff = 2 ** (attempt - 1)  # 1s, 2s, 4s
            logger.info("gmail %s retry attempt=%d sleeping=%ds url=%s",
                                  code, attempt, backoff, _redact_url_path(url))
            time.sleep(backoff)
            return _request_json(url, access_token, attempt=attempt + 1)
        if code == 401:
            raise GmailError("gmail_unauthorized",
                                       "access token rejected; refresh + retry once")
        if code == 404:
            raise GmailError("gmail_not_found",
                                       "message or attachment id not found")
        if code == 429:
            raise GmailError("gmail_rate_limited",
                                       "exceeded retry budget for 429")
        raise GmailError(f"gmail_http_{code}", f"HTTP {code}")
    except urllib.error.URLError as e:
        raise GmailError("gmail_network_error",
                                   f"{type(e).__name__}: {e.reason}")
    except json.JSONDecodeError:
        raise GmailError("gmail_malformed_response",
                                   "response was not valid JSON")


def _redact_url_path(url: str) -> str:
    """Strip query string so retry logs don't include attachment ids in
    a way that could be mistaken for credentials.
    """
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _extract_header(message: dict, name: str) -> str:
    headers = (message.get("payload") or {}).get("headers") or []
    target = name.lower()
    for h in headers:
        if (h.get("name") or "").lower() == target:
            return h.get("value") or ""
    return ""


def _find_part_by_attachment_id(payload: dict, attachment_id: str) -> dict | None:
    """Walk the message MIME tree looking for the part matching attachment_id."""
    if not payload:
        return None
    body = payload.get("body") or {}
    if body.get("attachmentId") == attachment_id:
        return payload
    for sub in (payload.get("parts") or []):
        found = _find_part_by_attachment_id(sub, attachment_id)
        if found is not None:
            return found
    return None


def _find_part_by_filename(payload: dict, filename: str) -> dict | None:
    """Walk the MIME tree looking for the first part whose filename matches.

    Match is case-insensitive on the basename so callers can pass
    'Report.PDF' and match 'report.pdf' or vice versa. Exact filename only
    -- no glob, no substring -- to avoid false positives.
    """
    if not payload:
        return None
    target = (filename or "").strip().lower()
    if not target:
        return None
    own_name = (payload.get("filename") or "").strip().lower()
    own_body = payload.get("body") or {}
    if own_name == target and own_body.get("attachmentId"):
        return payload
    for sub in (payload.get("parts") or []):
        found = _find_part_by_filename(sub, filename)
        if found is not None:
            return found
    return None


def _list_attachments(payload: dict) -> list[dict]:
    """Flatten the payload MIME tree into a list of attachment-bearing parts.
    Used only for error messages (so the operator can see what filenames
    ARE in the message when their filename arg didn't match).
    """
    out: list[dict] = []
    if not payload:
        return out
    body = payload.get("body") or {}
    if body.get("attachmentId"):
        out.append({
            "filename": payload.get("filename") or "(none)",
            "mimeType": payload.get("mimeType") or "(unknown)",
        })
    for sub in (payload.get("parts") or []):
        out.extend(_list_attachments(sub))
    return out


def fetch(message_id: str, access_token: str,
                *,
                attachment_id: str = "",
                filename: str = "") -> FetchedAttachment:
    """Fetch a single attachment + its parent-message sender.

    Resolves the canonical Gmail attachment id server-side from the message
    payload, then downloads. Caller must provide ONE of:
      - filename (preferred -- robust against wrapper-API id-format drift,
                   e.g. the claude.ai Gmail connector hands back IDs that
                   the Gmail v1 attachments.get endpoint won't accept)
      - attachment_id (legacy, expects the canonical Gmail v1 id directly)

    Filename wins if both are given. Match is case-insensitive on the basename.

    Two Gmail calls:
      1. GET /messages/{message_id}?format=full -- find sender + the
         attachment-bearing part by filename or attachment_id.
      2. GET /messages/{message_id}/attachments/{canonical_id} -- bytes.

    Error codes are deliberately granular so callers can tell which lookup
    leg failed:
      - gmail_message_not_found        -- the message GET itself 404'd
      - gmail_filename_not_in_message  -- message has attachments but none
                                                                  match the filename arg
      - gmail_attachment_id_not_in_message -- attachment_id arg didn't match
                                                                          any part's body.attachmentId
      - gmail_no_attachment_in_message -- message has zero attachment-bearing parts
    """
    if not message_id:
        raise GmailError("missing_required", "message_id is required")
    if not attachment_id and not filename:
        raise GmailError(
            "missing_required",
            "at least one of `filename` (preferred) or `attachment_id` is required",
        )

    # Step 1: message metadata. `format=full` returns the full MIME tree
    # with attachmentId fields on each leaf part.
    msg_url = (
        f"{API_BASE}/users/me/messages/{urllib.parse.quote(message_id)}"
        f"?format=full"
    )
    try:
        message = _request_json(msg_url, access_token)
    except GmailError as e:
        if e.code == "gmail_not_found":
            # Re-raise as the more specific code so callers can distinguish
            # "wrong message id" from "wrong attachment id within a message".
            raise GmailError(
                "gmail_message_not_found",
                f"message {message_id} not found (or not accessible to this token's mailbox)",
            )
        raise

    sender = _extract_header(message, "From") or _extract_header(message, "Sender") or "(unknown)"
    payload = message.get("payload") or {}

    # Find the part. Filename wins; fall back to attachment_id.
    part: dict | None = None
    resolution_hint = ""
    if filename:
        part = _find_part_by_filename(payload, filename)
        resolution_hint = f"by filename={filename!r}"
        if part is None:
            present = _list_attachments(payload)
            present_names = [a["filename"] for a in present]
            if not present:
                raise GmailError(
                    "gmail_no_attachment_in_message",
                    f"message {message_id} has no attachment parts",
                )
            raise GmailError(
                "gmail_filename_not_in_message",
                f"filename {filename!r} not found in message {message_id}; "
                f"present attachments: {present_names}",
            )
    elif attachment_id:
        part = _find_part_by_attachment_id(payload, attachment_id)
        resolution_hint = f"by attachment_id={attachment_id[:24]}..."
        if part is None:
            present = _list_attachments(payload)
            present_names = [a["filename"] for a in present]
            raise GmailError(
                "gmail_attachment_id_not_in_message",
                f"attachment_id did not match any part in message {message_id}; "
                f"present attachments (try `filename=` instead): {present_names}",
            )

    assert part is not None  # one of the above branches raised or set part
    resolved_filename = part.get("filename") or "attachment"
    mime_type = part.get("mimeType") or "application/octet-stream"
    canonical_attachment_id = (part.get("body") or {}).get("attachmentId") or ""
    if not canonical_attachment_id:
        raise GmailError(
            "gmail_part_has_no_attachment_id",
            f"matched part ({resolution_hint}) had no body.attachmentId; "
            "this is unusual -- the part may be an inline body",
        )

    # Step 2: download the canonical attachment.
    att_url = (
        f"{API_BASE}/users/me/messages/{urllib.parse.quote(message_id)}"
        f"/attachments/{urllib.parse.quote(canonical_attachment_id)}"
    )
    att = _request_json(att_url, access_token)
    data_b64url = att.get("data")
    if not data_b64url:
        raise GmailError(
            "gmail_empty_attachment",
            "attachment response had no `data` field",
        )

    # Gmail uses base64url (RFC 4648 §5), not standard base64.
    padded = data_b64url + "=" * (-len(data_b64url) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except Exception:
        raise GmailError(
            "gmail_base64_decode_failed",
            "attachment `data` was not valid base64url",
        )

    return FetchedAttachment(
        filename=resolved_filename,
        mime_type=mime_type,
        size_bytes=len(raw),
        sender=sender,
        data=raw,
    )
