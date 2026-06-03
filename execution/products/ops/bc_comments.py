"""Pull the most-recent comments + thread context off a Basecamp todo.

Used as input to llm_suggest so the per-ticket action plan is grounded
in what people have actually said on the thread, not just the title.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from html.parser import HTMLParser

logger = logging.getLogger(__name__)
USER_AGENT = "Advisor My Day (ali@colaberry.com)"
HTTP_TIMEOUT = 8


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
    def handle_data(self, data):
        self.parts.append(data)


def _strip_html(html: str) -> str:
    if not html:
        return ""
    p = _Strip()
    try:
        p.feed(html)
    except Exception:
        return html[:1000]
    return "".join(p.parts).strip()


def fetch_recent_comments(todo, token: str, limit: int = 5) -> str:
    """Pull last `limit` comments on a BC todo, return plaintext joined.

    Best-effort: 5s timeout via Promise.race-style behavior is just a short
    socket timeout. Returns empty string on any failure so the caller can
    still LLM the title+description alone.
    """
    if not token or not todo.bc_id or not todo.bc_project_id:
        return ""
    url = (
        f"https://3.basecampapi.com/3945211/buckets/"
        f"{todo.bc_project_id}/recordings/{todo.bc_id}/comments.json"
    )
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            comments = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.info("bc_comments fetch %s -> %s", url, e.code)
        return ""
    except Exception as e:  # noqa: BLE001
        logger.info("bc_comments fetch %s -> err %s", url, type(e).__name__)
        return ""

    if not isinstance(comments, list):
        return ""
    # BC returns oldest-first by default; take the last N
    tail = comments[-limit:] if len(comments) > limit else comments
    out: list[str] = []
    for c in tail:
        who = (c.get("creator") or {}).get("name", "?")
        when = (c.get("created_at") or "")[:10]
        body = _strip_html(c.get("content") or "")[:600]
        if body:
            out.append(f"[{who} · {when}]\n{body}")
    return "\n\n".join(out)
