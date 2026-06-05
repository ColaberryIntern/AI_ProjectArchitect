"""Pull the most-recent comments + thread context off a Basecamp todo.

Used as input to llm_suggest so the per-ticket action plan is grounded
in what people have actually said on the thread, not just the title.

Phase 6 adds post() to write a comment on a BC recording so the Extract
surface can echo "this ticket was converted to a <output_type>" back to
the source ticket.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Optional

logger = logging.getLogger(__name__)
USER_AGENT = "Advisor My Day (ali@colaberry.com)"
HTTP_TIMEOUT = 8
BC_ACCOUNT_ID = os.environ.get("BC_ACCOUNT_ID", "3945211")


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


def post(bucket_id: int, recording_id: int, html_body: str, token: str,
              account_id: Optional[str] = None) -> dict:
    """Post a comment on a BC recording (todo, message, etc.).

    Returns the BC comment object on success. Raises RuntimeError on HTTP
    failure so the caller can decide to retry / log / surface to the user.

    `html_body` is sent as-is to BC; include <bc-attachment> mention tags
    where you want a real notification.

    Phase 6 callers (my_day extract surface) catch errors so the file commit
    is the source of truth, not the side-effect echo.
    """
    acc = account_id or BC_ACCOUNT_ID
    url = (
        f"https://3.basecampapi.com/{acc}/buckets/{bucket_id}"
        f"/recordings/{recording_id}/comments.json"
    )
    payload = json.dumps({"content": html_body}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"BC POST {url} -> HTTP {e.code} {e.reason}: {msg}") from e


def render_assignee_mention(assignee_id: int, assignee_name: str = "") -> str:
    """Render BC's <bc-attachment> mention element. Recipient gets an email
    notification when they're mentioned this way in a comment.

    BC mention markup format: <bc-attachment sgid="..." content-type="...">
    For person mentions: <bc-attachment sgid="" type="Person::Mention" data-id="<id>">@Name</bc-attachment>.
    The `sgid` can be empty in v3 API; BC will resolve from data-id.
    """
    name = (assignee_name or f"#{assignee_id}").strip()
    return (
        f'<bc-attachment sgid="" type="Person::Mention" '
        f'data-id="{assignee_id}">@{name}</bc-attachment>'
    )
