"""Basecamp attachment fetch via the BC API blob endpoint.

Basecamp 3 stores binary attachments as "blobs" identified by a signed
global id (sgid). The API path:

    GET /{account_id}/buckets/{bucket_id}/uploads/{sgid}

returns a JSON envelope with `byte_size`, `content_type`, `filename`, and a
redirect-style `download_url` that needs the same Authorization header to
follow. Some BC payloads embed the blob URL directly in attachment comment
HTML as `<bc-attachment>` elements; this adapter trusts the caller to have
extracted the sgid already.

Per the directive: 15s timeout, 3 retries on 429/5xx, never logs the token.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import FetchedAttachment

logger = logging.getLogger(__name__)

TIMEOUT = 15.0
MAX_RETRIES = 3
USER_AGENT = "Colaberry MCP Attachment Fetch (ali@colaberry.com)"
DEFAULT_BC_ACCOUNT_ID = os.environ.get("BASECAMP_ACCOUNT_ID", "3945211")


class BasecampError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _request(url: str, bc_token: str, *,
                    method: str = "GET",
                    attempt: int = 1,
                    accept_json: bool = True) -> tuple[bytes, dict]:
    """Generic BC request with retry. Returns (body_bytes, headers_dict).

    The caller decides whether to parse as JSON. We use this for both the
    metadata call (JSON) and the binary download (octet-stream).
    """
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {bc_token}",
            "Accept": "application/json" if accept_json else "*/*",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return body, headers
    except urllib.error.HTTPError as e:
        code = e.code
        if code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
            backoff = 2 ** (attempt - 1)
            logger.info("basecamp %s retry attempt=%d sleeping=%ds", code, attempt, backoff)
            time.sleep(backoff)
            return _request(url, bc_token, method=method, attempt=attempt + 1,
                                       accept_json=accept_json)
        if code == 401:
            raise BasecampError("basecamp_unauthorized",
                                              "BC token rejected")
        if code == 404:
            raise BasecampError("basecamp_blob_not_found",
                                              "sgid not found in this bucket")
        if code == 429:
            raise BasecampError("basecamp_rate_limited",
                                              "exceeded retry budget for 429")
        raise BasecampError(f"basecamp_http_{code}", f"HTTP {code}")
    except urllib.error.URLError as e:
        raise BasecampError("basecamp_network_error",
                                          f"{type(e).__name__}: {e.reason}")


def fetch(project_id: int, recording_id: int, sgid: str,
                bc_token: str,
                *,
                account_id: str | None = None,
                project_name_for_audit: str | None = None) -> FetchedAttachment:
    """Fetch a BC blob attachment.

    `recording_id` is captured for the audit chain but isn't part of the
    blob URL itself; BC blobs are addressed by sgid within a bucket.

    `project_name_for_audit` is the human-readable bucket name we'd like to
    surface as the "sender" in the staged path; if the caller doesn't have
    it, we fall back to "BC bucket {bucket_id}".
    """
    if not project_id or not sgid:
        raise BasecampError("missing_required",
                                          "project_id and attachment_sgid are required")
    acc = account_id or DEFAULT_BC_ACCOUNT_ID

    # Step 1: metadata endpoint. Gives us byte_size, content_type, filename,
    # and an authenticated download URL.
    meta_url = (
        f"https://3.basecampapi.com/{acc}/buckets/{project_id}"
        f"/uploads/{urllib.parse.quote(sgid)}.json"
    )
    raw_meta, _ = _request(meta_url, bc_token, accept_json=True)
    try:
        meta = json.loads(raw_meta.decode("utf-8"))
    except json.JSONDecodeError:
        raise BasecampError("basecamp_malformed_metadata",
                                          "upload metadata was not valid JSON")
    filename = meta.get("filename") or "attachment"
    mime_type = meta.get("content_type") or "application/octet-stream"
    download_url = meta.get("download_url") or meta.get("url")
    if not download_url:
        raise BasecampError("basecamp_no_download_url",
                                          "upload metadata lacked download_url")

    # Step 2: actually download. BC returns the binary stream; we just want bytes.
    body, _ = _request(download_url, bc_token, accept_json=False)

    return FetchedAttachment(
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(body),
        sender=project_name_for_audit or f"BC bucket {project_id}",
        data=body,
    )
