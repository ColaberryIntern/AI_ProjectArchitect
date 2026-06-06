"""Drive passthrough adapter.

When source="drive", the caller already has a Drive file id and wants the
existing reference, not a re-upload. We hit Drive's metadata endpoint to
confirm the file exists (and is accessible under our `drive.file` scope)
+ return its current metadata.

Out of scope per the directive: arbitrary user-owned Drive files. The
`drive.file` scope ONLY grants access to files our OAuth app created.
A `drive_not_accessible` error surfaces cleanly when the operator points
at someone else's file.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from . import FetchedAttachment

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/drive/v3"
TIMEOUT = 15.0
MAX_RETRIES = 3
USER_AGENT = "Colaberry MCP Attachment Fetch (ali@colaberry.com)"


class DriveError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _request_json(url: str, access_token: str, *, attempt: int = 1) -> dict:
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
            time.sleep(2 ** (attempt - 1))
            return _request_json(url, access_token, attempt=attempt + 1)
        if code == 401:
            raise DriveError("drive_unauthorized",
                                       "access token rejected")
        if code in (403, 404):
            # 404 + `drive.file` scope == we're not allowed to see it.
            # Google returns 404 (not 403) for files outside the app's scope.
            raise DriveError(
                "drive_file_not_accessible",
                "drive.file scope limits us to files the advisor uploaded; "
                "this file isn't one of ours",
            )
        if code == 429:
            raise DriveError("drive_rate_limited",
                                       "exceeded retry budget for 429")
        raise DriveError(f"drive_http_{code}", f"HTTP {code}")
    except urllib.error.URLError as e:
        raise DriveError("drive_network_error",
                                   f"{type(e).__name__}: {e.reason}")
    except json.JSONDecodeError:
        raise DriveError("drive_malformed_response",
                                   "Drive returned non-JSON body")


def fetch(drive_file_id: str, access_token: str) -> FetchedAttachment:
    """Return the existing file's metadata; do not re-upload."""
    if not drive_file_id:
        raise DriveError("missing_required", "drive_file_id is required")
    fields = "id,name,mimeType,size,webViewLink,owners(emailAddress,displayName)"
    url = (
        f"{API_BASE}/files/{urllib.parse.quote(drive_file_id)}"
        f"?fields={urllib.parse.quote(fields)}"
    )
    meta = _request_json(url, access_token)
    owner_email = ""
    owners = meta.get("owners") or []
    if owners:
        first = owners[0]
        owner_email = first.get("emailAddress") or first.get("displayName") or ""
    size_str = meta.get("size") or "0"
    try:
        size_bytes = int(size_str)
    except ValueError:
        size_bytes = 0
    return FetchedAttachment(
        filename=meta.get("name") or "drive-file",
        mime_type=meta.get("mimeType") or "application/octet-stream",
        size_bytes=size_bytes,
        sender=owner_email or "(unknown drive owner)",
        data=None,
        drive_file_id=meta.get("id") or drive_file_id,
        drive_url=meta.get("webViewLink") or f"https://drive.google.com/file/d/{drive_file_id}/view",
    )
