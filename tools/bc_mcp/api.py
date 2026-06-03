"""Thin HTTP wrapper around the Basecamp 3 API.

Handles auth header injection, the required User-Agent, paginated GETs, and
401 → refresh-and-retry-once.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .auth import get_token

ACCOUNT_ID = "3945211"
DEFAULT_BUCKET = "7463955"  # AI_ProjectArchitect
BASE = f"https://3.basecampapi.com/{ACCOUNT_ID}"
USER_AGENT = "Advisor Claude Code MCP (ali@colaberry.com)"


class BasecampError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"BC {status}: {message[:300]}")


def _headers(token: str, *, json_body: bool = False) -> dict:
    h = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _request(method: str, url: str, body: dict | None = None, _retried: bool = False):
    token = get_token()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers=_headers(token, json_body=body is not None), method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
            raw = r.read()
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read()
        # 401: token may have rotated under us — refresh and retry once.
        if status == 401 and not _retried:
            get_token(force_refresh=True)
            return _request(method, url, body, _retried=True)
        if status >= 400:
            raise BasecampError(status, raw.decode("utf-8", errors="replace"))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")


def get(path: str, params: dict | None = None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _request("GET", url)


def post(path: str, body: dict | None = None):
    return _request("POST", f"{BASE}{path}", body)


def put(path: str, body: dict | None = None):
    return _request("PUT", f"{BASE}{path}", body)


def delete(path: str):
    return _request("DELETE", f"{BASE}{path}")


def paginated_get(path: str, params: dict | None = None, max_pages: int = 20) -> list:
    """Iterate a paginated list endpoint until empty or max_pages."""
    out = []
    p = dict(params or {})
    for page in range(1, max_pages + 1):
        p["page"] = page
        chunk = get(path, p)
        if not isinstance(chunk, list) or not chunk:
            break
        out.extend(chunk)
    return out
