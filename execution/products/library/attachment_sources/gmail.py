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


def _find_part(payload: dict, attachment_id: str) -> dict | None:
    """Walk the message MIME tree looking for the part matching attachment_id."""
    if not payload:
        return None
    body = payload.get("body") or {}
    if body.get("attachmentId") == attachment_id:
        return payload
    for sub in (payload.get("parts") or []):
        found = _find_part(sub, attachment_id)
        if found is not None:
            return found
    return None


def fetch(message_id: str, attachment_id: str,
                access_token: str) -> FetchedAttachment:
    """Fetch a single attachment + its parent-message sender.

    Two Gmail calls (sequentially):
      1. GET /messages/{message_id} -- resolve sender + filename + mime
         from the message payload + the matching part.
      2. GET /messages/{message_id}/attachments/{attachment_id} -- bytes.

    Raises GmailError on any unrecoverable failure.
    """
    if not message_id or not attachment_id:
        raise GmailError("missing_required",
                                   "message_id and attachment_id are required")

    # Step 1: message metadata
    msg_url = f"{API_BASE}/users/me/messages/{urllib.parse.quote(message_id)}"
    message = _request_json(msg_url, access_token)
    sender = _extract_header(message, "From") or _extract_header(message, "Sender") or "(unknown)"
    part = _find_part(message.get("payload") or {}, attachment_id)
    if part is None:
        # Attachment id doesn't appear in the message's MIME tree. Either
        # caller passed the wrong id or the message changed. Treat as 404.
        raise GmailError(
            "gmail_attachment_not_in_message",
            f"attachment_id={attachment_id} not present in message {message_id}",
        )
    filename = part.get("filename") or "attachment"
    mime_type = part.get("mimeType") or "application/octet-stream"

    # Step 2: attachment payload
    att_url = (
        f"{API_BASE}/users/me/messages/{urllib.parse.quote(message_id)}"
        f"/attachments/{urllib.parse.quote(attachment_id)}"
    )
    att = _request_json(att_url, access_token)
    data_b64url = att.get("data")
    if not data_b64url:
        raise GmailError("gmail_empty_attachment",
                                   "attachment response had no `data` field")

    # Gmail uses base64url (RFC 4648 §5), not standard base64. urlsafe_b64decode
    # needs the padding fixed up.
    padded = data_b64url + "=" * (-len(data_b64url) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except Exception:
        raise GmailError("gmail_base64_decode_failed",
                                   "attachment `data` was not valid base64url")

    return FetchedAttachment(
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(raw),
        sender=sender,
        data=raw,
    )
