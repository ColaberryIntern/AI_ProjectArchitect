"""Rabbit-hole walker: given a Basecamp URL, recursively collect linked
context until depth/time/budget caps hit.

Output shape (the "context bundle" passed to plan_inference):

  {
    "root": {
      "kind": "todo|message|document|upload|...",
      "bucket_id": int,
      "id": int,
      "title": str,
      "description": str,        # HTML-stripped plaintext
      "comments": [str, ...],    # last N, HTML-stripped, prefixed with author
      "app_url": str
    },
    "linked": [
      { same shape as root, plus "depth": int, "linked_from": str }
    ],
    "external_urls": [
      {"url": str, "where_mentioned": "root|linked:<id>", "context_snippet": str}
    ],
    "stopped_reason": "max_depth|max_time|max_items|no_more_links",
    "elapsed_seconds": float,
    "items_walked": int
  }

Bounded by env (sane defaults):
  OPS_CRAWL_MAX_DEPTH         (default 2)   — depth 0 = root, 2 = root+1+2
  OPS_CRAWL_MAX_TIME_SECONDS  (default 25)
  OPS_CRAWL_MAX_ITEMS         (default 20)
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
from html.parser import HTMLParser

from .sync import HTTP_THROTTLE_SECONDS, HTTP_TIMEOUT, USER_AGENT, _bc_get

logger = logging.getLogger(__name__)

MAX_DEPTH = int(os.environ.get("OPS_CRAWL_MAX_DEPTH", "2"))
MAX_TIME_SECONDS = float(os.environ.get("OPS_CRAWL_MAX_TIME_SECONDS", "25"))
MAX_ITEMS = int(os.environ.get("OPS_CRAWL_MAX_ITEMS", "20"))

# BC URL patterns we recognize
_BC_URL_RE = re.compile(
    r"https?://(?:3\.basecamp\.com|3\.basecampapi\.com)"
    r"/(?P<acct>\d+)/(?:buckets|projects)/(?P<bucket>\d+)/"
    r"(?P<kind>todos|messages|documents|uploads|vaults|todolists|todosets|comments)"
    r"/(?P<id>\d+)",
    re.IGNORECASE,
)
# External URLs (non-Basecamp)
_EXTERNAL_URL_RE = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)


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
        return html[:2000]
    return "".join(p.parts).strip()


def _parse_bc_url(url: str) -> dict | None:
    """Parse a BC URL into {bucket_id, kind, id}. Returns None if not BC."""
    m = _BC_URL_RE.search(url)
    if not m:
        return None
    return {
        "account_id": int(m.group("acct")),
        "bucket_id": int(m.group("bucket")),
        "kind": m.group("kind").lower(),
        "id": int(m.group("id")),
    }


def _api_path_for(parsed: dict) -> str | None:
    kind = parsed["kind"]
    bucket = parsed["bucket_id"]
    item_id = parsed["id"]
    if kind == "todos":
        return f"/buckets/{bucket}/todos/{item_id}.json"
    if kind == "messages":
        return f"/buckets/{bucket}/messages/{item_id}.json"
    if kind == "documents":
        return f"/buckets/{bucket}/documents/{item_id}.json"
    if kind == "uploads":
        return f"/buckets/{bucket}/uploads/{item_id}.json"
    if kind == "vaults":
        return f"/buckets/{bucket}/vaults/{item_id}.json"
    if kind in ("todolists", "todosets", "comments"):
        return f"/buckets/{bucket}/{kind}/{item_id}.json"
    return None


def _extract_one(parsed: dict, payload: dict, token: str) -> dict:
    """Normalize a BC API response into our context-bundle shape.

    For todos, also pulls the last N comments. For messages, pulls the
    body. For documents, pulls the rendered text. For uploads, just
    metadata (filename + bytesize).
    """
    out = {
        "kind": parsed["kind"].rstrip("s"),    # 'todos' -> 'todo'
        "bucket_id": parsed["bucket_id"],
        "id": parsed["id"],
        "title": payload.get("title") or payload.get("subject") or payload.get("name") or "(no title)",
        "description": _strip_html(payload.get("description") or payload.get("content") or "")[:4000],
        "app_url": payload.get("app_url", ""),
        "comments": [],
    }
    # If this item has a comments URL, pull recent ones
    comments_url = None
    if isinstance(payload.get("comments_url"), str):
        comments_url = payload["comments_url"]
    elif parsed["kind"] in ("todos", "messages"):
        comments_url = (
            f"https://3.basecampapi.com/{parsed['account_id']}/buckets/"
            f"{parsed['bucket_id']}/recordings/{parsed['id']}/comments.json"
        )
    if comments_url:
        try:
            # _bc_get expects path relative to BC_API_BASE — strip the prefix
            from .sync import BC_API_BASE
            if comments_url.startswith(BC_API_BASE):
                comments_path = comments_url[len(BC_API_BASE):]
            else:
                comments_path = None
            if comments_path:
                cs = _bc_get(comments_path, token)
                if isinstance(cs, list):
                    tail = cs[-5:]
                    for c in tail:
                        who = (c.get("creator") or {}).get("name", "?")
                        when = (c.get("created_at") or "")[:10]
                        body = _strip_html(c.get("content") or "")[:800]
                        if body:
                            out["comments"].append(f"[{who} · {when}]\n{body}")
        except Exception:
            pass
    return out


def _find_links(text: str) -> tuple[list[str], list[str]]:
    """Return (bc_urls, external_urls) found inside text."""
    if not text:
        return [], []
    bc, ext = set(), set()
    for m in _EXTERNAL_URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:)>")
        if _parse_bc_url(url):
            bc.add(url)
        else:
            ext.add(url)
    return sorted(bc), sorted(ext)


def collect(seed_url: str, token: str) -> dict:
    """Walk the rabbit-hole starting from a Basecamp URL. Returns the
    context bundle described in this module's docstring.
    """
    started = time.monotonic()

    root_parsed = _parse_bc_url(seed_url)
    if not root_parsed:
        return {
            "root": None, "linked": [], "external_urls": [],
            "stopped_reason": "seed_url_not_basecamp",
            "elapsed_seconds": 0.0, "items_walked": 0,
        }

    visited: set[tuple[int, int]] = set()        # (bucket, id)
    queue: list[tuple[str, int, str]] = []        # (url, depth, linked_from)
    linked: list[dict] = []
    external: list[dict] = []
    stopped_reason = "no_more_links"

    # Seed: fetch root
    root_path = _api_path_for(root_parsed)
    if not root_path:
        return {
            "root": None, "linked": [], "external_urls": [],
            "stopped_reason": "unsupported_seed_kind",
            "elapsed_seconds": 0.0, "items_walked": 0,
        }
    try:
        root_raw = _bc_get(root_path, token)
    except Exception as e:
        logger.warning("Crawl seed fetch failed: %s", e)
        return {
            "root": None, "linked": [], "external_urls": [],
            "stopped_reason": "seed_fetch_failed",
            "elapsed_seconds": time.monotonic() - started, "items_walked": 0,
        }
    if not root_raw:
        return {
            "root": None, "linked": [], "external_urls": [],
            "stopped_reason": "seed_not_found",
            "elapsed_seconds": time.monotonic() - started, "items_walked": 0,
        }
    root_node = _extract_one(root_parsed, root_raw, token)
    root_node["depth"] = 0
    root_node["linked_from"] = "(root)"
    visited.add((root_parsed["bucket_id"], root_parsed["id"]))

    # Enqueue links from root
    root_text = root_node["description"] + "\n" + "\n".join(root_node["comments"])
    bc_links, ext_links = _find_links(root_text)
    for u in bc_links:
        queue.append((u, 1, f"root:{root_parsed['id']}"))
    for u in ext_links:
        external.append({"url": u, "where_mentioned": "root", "context_snippet": ""})

    # BFS walk
    while queue:
        if time.monotonic() - started > MAX_TIME_SECONDS:
            stopped_reason = "max_time"
            break
        if len(linked) >= MAX_ITEMS:
            stopped_reason = "max_items"
            break

        url, depth, from_id = queue.pop(0)
        if depth > MAX_DEPTH:
            stopped_reason = "max_depth"
            break

        parsed = _parse_bc_url(url)
        if not parsed:
            continue
        key = (parsed["bucket_id"], parsed["id"])
        if key in visited:
            continue
        visited.add(key)

        path = _api_path_for(parsed)
        if not path:
            continue
        try:
            payload = _bc_get(path, token)
        except Exception:
            continue
        if not payload:
            continue

        node = _extract_one(parsed, payload, token)
        node["depth"] = depth
        node["linked_from"] = from_id
        linked.append(node)

        # Surface further links
        text = node["description"] + "\n" + "\n".join(node["comments"])
        bc_links, ext_links = _find_links(text)
        for u in bc_links:
            queue.append((u, depth + 1, f"linked:{parsed['id']}"))
        for u in ext_links:
            external.append({
                "url": u,
                "where_mentioned": f"linked:{parsed['id']}",
                "context_snippet": "",
            })

    return {
        "root": root_node,
        "linked": linked,
        "external_urls": external,
        "stopped_reason": stopped_reason,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "items_walked": 1 + len(linked),
    }


def render_for_llm(bundle: dict, max_chars: int = 12000) -> str:
    """Compact, LLM-friendly rendering of the context bundle.

    Drops the noisier fields, deduplicates, caps total chars.
    """
    if not bundle.get("root"):
        return "(no context — seed URL could not be resolved)"
    parts = ["=== ROOT TICKET ==="]
    r = bundle["root"]
    parts.append(f"[{r['kind']}] {r['title']}")
    parts.append(f"URL: {r['app_url']}")
    if r.get("description"):
        parts.append(f"\nDescription:\n{r['description']}")
    if r.get("comments"):
        parts.append("\nRecent comments:")
        for c in r["comments"]:
            parts.append(c)
    if bundle.get("linked"):
        parts.append("\n=== LINKED ITEMS (rabbit-hole) ===")
        for i, node in enumerate(bundle["linked"], 1):
            parts.append(f"\n--- ({i}) depth={node['depth']} from={node['linked_from']} ---")
            parts.append(f"[{node['kind']}] {node['title']}")
            if node.get("app_url"):
                parts.append(f"URL: {node['app_url']}")
            if node.get("description"):
                parts.append(f"Description: {node['description'][:1000]}")
            if node.get("comments"):
                parts.append(f"Latest comment: {node['comments'][-1][:600]}")
    if bundle.get("external_urls"):
        parts.append("\n=== EXTERNAL URLS REFERENCED ===")
        for ext in bundle["external_urls"][:10]:
            parts.append(f"- {ext['url']}  (from {ext['where_mentioned']})")
    parts.append(
        f"\n=== CRAWL META === stopped={bundle['stopped_reason']} "
        f"items={bundle['items_walked']} time={bundle['elapsed_seconds']}s"
    )
    rendered = "\n".join(parts)
    if len(rendered) > max_chars:
        return rendered[: max_chars - 100] + "\n... [context truncated]"
    return rendered
