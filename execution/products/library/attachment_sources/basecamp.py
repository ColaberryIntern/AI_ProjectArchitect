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
import re
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

# Characters that must not survive into a Drive filename (path separators and
# control chars). Document titles are free text, so sanitize before staging.
_DOC_FILENAME_BAD = re.compile(r"[\\/\x00-\x1f]+")


def _doc_filename(title: str) -> str:
    """Turn a BC document title into a safe `.html` filename for Drive staging."""
    cleaned = _DOC_FILENAME_BAD.sub(" ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return f"{cleaned or 'document'}.html"


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


def fetch_by_recording(project_id: int, recording_id: int,
                       bc_token: str,
                       *,
                       account_id: str | None = None,
                       project_name_for_audit: str | None = None) -> FetchedAttachment:
    """Fetch a BC upload addressed by its **recording id** (not a blob sgid).

    Brief / vault links in the BC web UI look like
        https://app.basecamp.com/{account}/buckets/{bucket}/uploads/{recording_id}
    where the trailing id is the Upload *recording* id, NOT the blob sgid that
    `fetch()` needs. The BC3 API exposes that upload recording at

        GET /{account_id}/buckets/{bucket_id}/uploads/{recording_id}.json

    whose JSON carries `filename`, `content_type`, `byte_size`, and a
    `download_url` we can follow with the same Authorization header. This lets
    an operator paste a brief link and read its content without first having to
    extract the sgid out of attachment HTML (the gap that left "Briefs to read
    first" links unreadable).

    Same retry/timeout/no-token-logging contract as `fetch()`.
    """
    if not project_id or not recording_id:
        raise BasecampError("missing_required",
                            "project_id and recording_id are required")
    acc = account_id or DEFAULT_BC_ACCOUNT_ID

    meta_url = (
        f"https://3.basecampapi.com/{acc}/buckets/{int(project_id)}"
        f"/uploads/{int(recording_id)}.json"
    )
    try:
        raw_meta, _ = _request(meta_url, bc_token, accept_json=True)
    except BasecampError as e:
        # A recording id that is NOT an upload — most commonly a vault Document
        # (a "Lab Spec" / brief linked in a comment) — 404s on the uploads
        # endpoint. Before giving up, fall back to the documents endpoint, which
        # returns the doc's rich-text HTML inline (no blob, no download_url).
        if e.code == "basecamp_blob_not_found":
            return fetch_document(
                project_id, recording_id, bc_token,
                account_id=account_id,
                project_name_for_audit=project_name_for_audit,
            )
        raise
    try:
        meta = json.loads(raw_meta.decode("utf-8"))
    except json.JSONDecodeError:
        raise BasecampError("basecamp_malformed_metadata",
                            "upload recording metadata was not valid JSON")

    filename = meta.get("filename") or "attachment"
    mime_type = meta.get("content_type") or "application/octet-stream"
    # An Upload recording nests the blob fields directly; some payload shapes
    # wrap them. Look top-level first, then a nested attachable, then parent.
    download_url = (
        meta.get("download_url")
        or meta.get("url")
        or (meta.get("attachable") or {}).get("download_url")
    )
    if not download_url:
        raise BasecampError("basecamp_no_download_url",
                            "upload recording metadata lacked download_url")

    body, _ = _request(download_url, bc_token, accept_json=False)

    return FetchedAttachment(
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(body),
        sender=project_name_for_audit or f"BC bucket {project_id}",
        data=body,
    )


def fetch_document(project_id: int, recording_id: int,
                   bc_token: str,
                   *,
                   account_id: str | None = None,
                   project_name_for_audit: str | None = None) -> FetchedAttachment:
    """Fetch a BC vault **Document** addressed by its recording id.

    Documents are NOT uploads/blobs. The BC3 API exposes them at

        GET /{account_id}/buckets/{bucket_id}/documents/{recording_id}.json

    whose JSON carries `title` and a `content` field holding the document's
    rich-text **HTML inline** — there is no `download_url` and no binary blob to
    follow. We materialize that HTML as a UTF-8 `.html` file so a document flows
    through the exact same Drive-staging pipeline as a file attachment, and the
    operator reads it via the Google Drive connector like any other staged file.

    This is the "Lab Spec / brief linked in a comment" case: such links carry a
    Document recording id, which 404s on the uploads endpoint, so
    `fetch_by_recording` falls back here automatically.

    Same retry/timeout/no-token-logging contract as `fetch()`.
    """
    if not project_id or not recording_id:
        raise BasecampError("missing_required",
                            "project_id and recording_id are required")
    acc = account_id or DEFAULT_BC_ACCOUNT_ID

    doc_url = (
        f"https://3.basecampapi.com/{acc}/buckets/{int(project_id)}"
        f"/documents/{int(recording_id)}.json"
    )
    try:
        raw_meta, _ = _request(doc_url, bc_token, accept_json=True)
    except BasecampError as e:
        # A 404 here means the id is neither an upload nor a document we can see
        # (deleted, or the operator's BC grant lacks access). Re-map to a
        # clearer code than the generic blob-not-found so the caller knows it
        # wasn't simply a blob lookup that missed.
        if e.code == "basecamp_blob_not_found":
            raise BasecampError(
                "basecamp_recording_not_found",
                "recording id is neither a file upload nor a readable document "
                "in this bucket (it may be deleted, or the operator may lack access)",
            )
        raise
    try:
        doc = json.loads(raw_meta.decode("utf-8"))
    except json.JSONDecodeError:
        raise BasecampError("basecamp_malformed_metadata",
                            "document metadata was not valid JSON")

    content_html = doc.get("content")
    if content_html is None:
        raise BasecampError("basecamp_document_no_content",
                            "document payload lacked a content field")
    title = (doc.get("title") or "document").strip() or "document"
    data = content_html.encode("utf-8")

    return FetchedAttachment(
        filename=_doc_filename(title),
        mime_type="text/html",
        size_bytes=len(data),
        sender=project_name_for_audit or f"BC bucket {project_id}",
        data=data,
    )
