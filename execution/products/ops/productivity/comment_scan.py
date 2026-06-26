"""Build per-person comment-authorship stats — the data behind the AI Share metric.

The report's AI Share is comment-based: of a person's Basecamp comments in the window, how
many were AI-authored (Claude Code / system) vs typed by hand. This module gathers that:

  - `tally_threads()` is PURE: given fetched comments (any shape with author + body +
    created_at), it filters to the window and tallies {person: {ai, human}} via the
    comment_attribution classifier. Fully unit-tested.
  - `fetch_project_comments()` / `build_comment_stats()` are the thin, best-effort I/O edge:
    they page the Basecamp per-project Comment recordings via the shared CB System token and
    persist output/ops/_productivity/comment_stats.json. Guarded — any failure yields {} so
    the report simply falls back to completion-based attribution (honest, never crashes).

The daily runner loads comment_stats.json into AiSignals.comment_counts.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import PROJECT_ROOT

from .comment_attribution import classify_comment

logger = logging.getLogger(__name__)

STATS_PATH = PROJECT_ROOT / "output" / "ops" / "_productivity" / "comment_stats.json"
WINDOW_DAYS = int(os.environ.get("PRODUCTIVITY_COMMENT_WINDOW_DAYS", "7"))


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _author(c) -> str:
    a = c.get("author") or c.get("creator")
    if isinstance(a, dict):
        a = a.get("name", "")
    return (a or "").strip()


def _body(c) -> str:
    return c.get("content_text") or c.get("content") or c.get("content_html") or c.get("body") or ""


def tally_threads(comments: list, *, ai_actors: set | None = None,
                  since: datetime | None = None, exclude: set | None = None) -> dict:
    """Pure: tally a flat list of comments into {person: {ai, human, total, ai_share}}.

    `since` drops comments older than the window; `exclude` drops author names (e.g. the AI
    actor accounts) from the per-person breakdown (their comments still count as AI for
    whoever they are attributed to, but the bot itself is not an operator row).
    """
    actors = ai_actors if ai_actors is not None else {"CB System"}
    excl = exclude or set()
    out: dict = {}
    for c in comments:
        author = _author(c)
        if not author or author in excl:
            continue
        cdt = _parse_iso(c.get("created_at"))
        if since is not None and cdt is not None and cdt < since:
            continue
        bucket = classify_comment(author, _body(c), actors)
        row = out.setdefault(author, {"ai": 0, "human": 0, "ambient": 0})
        row[bucket] += 1
    for row in out.values():
        authored = row["ai"] + row["human"]   # ambient automation excluded from the ratio
        row["total"] = authored
        row["ai_share"] = round(row["ai"] / authored, 3) if authored else None
    return out


def fetch_project_comments(project_id: int, *, since: datetime, max_pages: int = 50) -> list:
    """Best-effort: page a project's Comment recordings via the shared BC token.
    Returns [] on any error (missing client/token/network) so callers degrade gracefully."""
    try:
        from execution.products.library.mcp_tools import _bc_account, _bc_request, _html_to_text
    except Exception:  # pragma: no cover - library/token not available in this context
        return []
    out: list = []
    try:
        base = f"https://3.basecampapi.com/{_bc_account()}/buckets/{project_id}/recordings.json"
        url = f"{base}?type=Comment&sort=created_at&direction=desc"
        page = 1
        while page <= max_pages:
            sep = "&" if "?" in url else "?"
            batch = _bc_request("GET", f"{url}{sep}page={page}")
            if not isinstance(batch, list) or not batch:
                break
            stop = False
            for c in batch:
                cdt = _parse_iso(c.get("created_at"))
                if cdt is not None and cdt < since:
                    stop = True
                    break
                html = c.get("content") or ""
                out.append({
                    "author": (c.get("creator") or {}).get("name", ""),
                    "created_at": c.get("created_at"),
                    "content_text": _html_to_text(html),
                })
            if stop:
                break
            page += 1
    except Exception as e:  # pragma: no cover - network/auth guard
        logger.warning("comment scan failed for project %s: %s", project_id, e)
    return out


def build_comment_stats(project_ids: list, *, now: datetime | None = None,
                        window_days: int = WINDOW_DAYS, ai_actors: set | None = None,
                        write: bool = True) -> dict:
    """Scan the given BC projects and persist {person: {ai, human}} for the window."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    actors = ai_actors if ai_actors is not None else {"CB System"}
    comments: list = []
    for pid in project_ids:
        comments.extend(fetch_project_comments(int(pid), since=since))
    counts = tally_threads(comments, ai_actors=actors, since=since, exclude=actors)
    payload = {"built_at": now.isoformat(), "window_days": window_days, "per_person": counts}
    if write:
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_comment_counts() -> dict:
    """Return {person: {ai, human}} from comment_stats.json, or {} if absent/stale-safe."""
    if not STATS_PATH.exists():
        return {}
    try:
        payload = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload.get("per_person", {}) if isinstance(payload, dict) else {}
