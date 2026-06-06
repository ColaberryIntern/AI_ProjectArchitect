"""Upload bytes to the operator's Google Drive under a canonical folder path.

Path scheme (matches the directive):

    Drive:/Colaberry Inbound/<source>/<sender_or_project_slug>/<YYYY-MM>/<filename>

Folder creation is idempotent: we look up by name+parent before creating.
A small in-process cache avoids re-querying the same path within a single
worker lifetime.

Multipart upload (drive.files.create with uploadType=multipart) lets us send
metadata + bytes in one request for files up to ~5MB. For larger files we
use the resumable upload session protocol. The directive's 25MB Gmail cap
makes resumable mandatory for any non-trivial xlsx; for files >5MB we use
the simple-resumable variant (single PUT after session init).
"""
from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
TIMEOUT = 30.0   # uploads need more headroom than metadata calls
MAX_RETRIES = 3
USER_AGENT = "Colaberry MCP Drive Staging (ali@colaberry.com)"

ROOT_FOLDER_NAME = "Colaberry Inbound"
RESUMABLE_THRESHOLD_BYTES = 5 * 1024 * 1024  # 5MB

_FOLDER_CACHE: dict[str, str] = {}   # "<parent_id>/<name>" -> folder_id
_FOLDER_CACHE_LOCK = threading.Lock()


class DriveStagingError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


# ── Folder management ───────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Filesystem-friendly slug for a sender / project name path segment.

    Keeps the original characters when possible (we want recognizable names
    in the Drive folder list) but strips characters that confuse Drive
    search queries (apostrophes especially).
    """
    s = (name or "").strip()
    if not s:
        return "unknown"
    # Drop quote chars that break Drive's search query syntax. Keep most
    # punctuation since Drive folder names tolerate spaces, periods, etc.
    s = s.replace("'", "").replace('"', "").replace("\\", "/")
    s = re.sub(r"\s+", " ", s)[:120]
    return s or "unknown"


def _find_or_create_folder(name: str, parent_id: str | None,
                                          access_token: str) -> str:
    """Find a folder by exact name under parent_id; create it if missing.

    Returns the Drive folder id. Idempotent + cached per worker process.
    """
    cache_key = f"{parent_id or 'root'}/{name}"
    with _FOLDER_CACHE_LOCK:
        cached = _FOLDER_CACHE.get(cache_key)
    if cached:
        return cached

    safe_name = name.replace("'", "\\'")
    query_parts = [
        f"name='{safe_name}'",
        "mimeType='application/vnd.google-apps.folder'",
        "trashed=false",
    ]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")
    q = " and ".join(query_parts)
    url = f"{DRIVE_API}/files?q={urllib.parse.quote(q)}&fields=files(id,name)&pageSize=10"
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
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DriveStagingError(f"drive_search_http_{e.code}",
                                                  f"folder search HTTP {e.code}")
    except urllib.error.URLError as e:
        raise DriveStagingError("drive_search_network_error",
                                                  f"{type(e).__name__}: {e.reason}")

    matches = body.get("files") or []
    if matches:
        fid = matches[0]["id"]
        with _FOLDER_CACHE_LOCK:
            _FOLDER_CACHE[cache_key] = fid
        return fid

    # Create
    create_body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        create_body["parents"] = [parent_id]
    creq = urllib.request.Request(
        f"{DRIVE_API}/files?fields=id,name",
        data=json.dumps(create_body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(creq, timeout=TIMEOUT) as resp:
            created = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DriveStagingError(f"drive_folder_create_http_{e.code}",
                                                  f"folder create HTTP {e.code}")
    except urllib.error.URLError as e:
        raise DriveStagingError("drive_folder_create_network_error",
                                                  f"{type(e).__name__}: {e.reason}")
    fid = created["id"]
    with _FOLDER_CACHE_LOCK:
        _FOLDER_CACHE[cache_key] = fid
    return fid


def _ensure_path(path_segments: list[str], access_token: str) -> str:
    """Walk a path of folder names, creating each as needed. Returns the
    Drive id of the leaf folder.
    """
    parent: str | None = None
    for seg in path_segments:
        parent = _find_or_create_folder(seg, parent, access_token)
    if parent is None:
        raise DriveStagingError("drive_path_empty", "no path segments supplied")
    return parent


# ── File upload ─────────────────────────────────────────────────────────


def _upload_multipart(data: bytes, filename: str, mime_type: str,
                                  parent_id: str, access_token: str) -> dict:
    """Single-request multipart upload. Used for files <=5MB."""
    boundary = "==COLABERRY_BOUNDARY=="
    metadata = {
        "name": filename,
        "parents": [parent_id],
        "mimeType": mime_type,
    }
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--".encode("utf-8")

    url = f"{UPLOAD_API}?uploadType=multipart&fields=id,name,mimeType,size,webViewLink"
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if e.code == 403 and "quota" in msg.lower():
            raise DriveStagingError("drive_quota_exceeded",
                                                      "operator's Drive quota is full")
        raise DriveStagingError(f"drive_upload_http_{e.code}",
                                                  f"upload HTTP {e.code}")
    except urllib.error.URLError as e:
        raise DriveStagingError("drive_upload_network_error",
                                                  f"{type(e).__name__}: {e.reason}")


def _upload_resumable(data: bytes, filename: str, mime_type: str,
                                  parent_id: str, access_token: str) -> dict:
    """Resumable upload session for files >5MB. Single PUT after init since
    we have the whole payload in memory and there's no need to chunk.
    """
    # Step 1: initiate session
    metadata = {
        "name": filename,
        "parents": [parent_id],
        "mimeType": mime_type,
    }
    init_url = f"{UPLOAD_API}?uploadType=resumable&fields=id,name,mimeType,size,webViewLink"
    init_req = urllib.request.Request(
        init_url,
        data=json.dumps(metadata).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(len(data)),
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(init_req, timeout=TIMEOUT) as resp:
            session_url = resp.headers.get("Location") or resp.headers.get("location")
    except urllib.error.HTTPError as e:
        raise DriveStagingError(f"drive_resumable_init_http_{e.code}",
                                                  f"resumable init HTTP {e.code}")
    except urllib.error.URLError as e:
        raise DriveStagingError("drive_resumable_init_network_error",
                                                  f"{type(e).__name__}: {e.reason}")
    if not session_url:
        raise DriveStagingError("drive_resumable_no_session_url",
                                                  "init response lacked Location header")

    # Step 2: single PUT with the full bytes
    put_req = urllib.request.Request(
        session_url,
        data=data,
        method="PUT",
        headers={
            "Content-Type": mime_type,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(put_req, timeout=TIMEOUT * 2) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if e.code == 403 and "quota" in msg.lower():
            raise DriveStagingError("drive_quota_exceeded",
                                                      "operator's Drive quota is full")
        raise DriveStagingError(f"drive_resumable_put_http_{e.code}",
                                                  f"resumable PUT HTTP {e.code}")
    except urllib.error.URLError as e:
        raise DriveStagingError("drive_resumable_put_network_error",
                                                  f"{type(e).__name__}: {e.reason}")


def upload(*, data: bytes, filename: str, mime_type: str,
                  source: str, sender_slug: str, year_month: str,
                  access_token: str,
                  destination_subpath: str | None = None) -> dict:
    """Public entry. Resolves the destination folder, uploads bytes, returns
    the Drive file metadata dict (id, name, mimeType, size, webViewLink).

    Path:
        Drive:/<ROOT_FOLDER_NAME>/<source>/<sender_slug>/<year_month>/<filename>

    If `destination_subpath` is supplied, it overrides the `<year_month>`
    segment (the directive's escape hatch for non-month-based grouping).
    """
    if not data:
        raise DriveStagingError("missing_required",
                                                  "data is empty; nothing to upload")
    segments = [
        ROOT_FOLDER_NAME,
        source,
        _slugify(sender_slug),
        destination_subpath or year_month,
    ]
    parent_id = _ensure_path(segments, access_token)

    if len(data) <= RESUMABLE_THRESHOLD_BYTES:
        return _upload_multipart(data, filename, mime_type, parent_id, access_token)
    return _upload_resumable(data, filename, mime_type, parent_id, access_token)


def reset_folder_cache() -> None:
    """Test hook -- forces re-resolution of folder ids."""
    with _FOLDER_CACHE_LOCK:
        _FOLDER_CACHE.clear()
