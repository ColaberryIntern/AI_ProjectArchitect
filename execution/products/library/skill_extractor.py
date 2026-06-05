"""Phase 4: source-to-artifact extraction engine.

Public entry point:

    extract(source_kind, bc_id, output_type, slug=None, *, commit=True,
            bc_token="", account_id="", bucket_id="", repo="", created_by="")
        -> dict (ExtractedArtifact + content_preview)

Called by:
  - Phase 4 onboarding hook in admin.user_new() (per fresh-user provisioning)
  - Phase 6 My Day Extract surface (manual extract from any past BC todo)

Both paths render through the same Jinja templates in app/templates/extracted/
so the output is identical regardless of who triggered it.

This module owns "read from source" (the source_kind adapters). The "render
template + write file + commit branch" half is in extracted_writer.py.

Stdlib only on the source-fetch side. urllib for BC API; no httpx dependency.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional

from . import extracted_writer

USER_AGENT = "Colaberry skill_extractor (ali@colaberry.com)"
HTTP_TIMEOUT = 20

DEFAULT_BC_ACCOUNT_ID = os.environ.get("BASECAMP_ACCOUNT_ID", "3945211")


# ── Normalized source dataclass ──────────────────────────────────────


@dataclass
class ExtractedSource:
    """The normalized, render-agnostic output of any source_kind adapter.

    Templates iterate `metadata.comments` (list of {author, body, created_at})
    and use `metadata.bc_url` etc. Add fields conservatively; templates may
    reference them and a removal breaks rendering.
    """
    source_kind: str
    source_id: str
    title: str
    body: str
    metadata: dict = field(default_factory=dict)


# ── HTML helpers (BC content fields come back as HTML fragments) ─────


class _StripHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li"):
            self.parts.append("\n")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    p = _StripHTML()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()
    text = "".join(p.parts)
    # Collapse runs of blank lines and trim horizontal whitespace per line
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── BC API helpers (local — sync.py's _bc_get isn't easily reusable) ─


def _bc_request(method: str, url: str, bc_token: str,
                       payload: Optional[dict] = None) -> dict:
    """Thin BC API caller. Returns parsed JSON. Raises on non-2xx."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, method=method, data=data,
        headers={
            "Authorization": f"Bearer {bc_token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    if data is not None:
        req.add_header("Content-Type", "application/json")
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
        raise RuntimeError(f"BC {method} {url} -> HTTP {e.code} {e.reason}: {msg}") from e


def _fetch_todo(bc_id: str, account_id: str, bucket_id: str, bc_token: str) -> dict:
    url = f"https://3.basecampapi.com/{account_id}/buckets/{bucket_id}/todos/{bc_id}.json"
    return _bc_request("GET", url, bc_token)


def _fetch_comments(bc_id: str, account_id: str, bucket_id: str, bc_token: str,
                            limit: int = 50) -> list[dict]:
    """Pull comments off a BC recording (todo). Returns oldest-first list."""
    url = (
        f"https://3.basecampapi.com/{account_id}/buckets/{bucket_id}"
        f"/recordings/{bc_id}/comments.json"
    )
    try:
        comments = _bc_request("GET", url, bc_token)
    except RuntimeError:
        return []
    if not isinstance(comments, list):
        return []
    if len(comments) > limit:
        comments = comments[-limit:]
    out = []
    for c in comments:
        creator = c.get("creator") or {}
        out.append({
            "author": creator.get("name") or creator.get("email_address") or "?",
            "created_at": c.get("created_at") or "",
            "body": _strip_html(c.get("content") or ""),
        })
    return out


def _discover_bucket_id(bc_id: str, account_id: str, bc_token: str) -> str:
    """When the caller doesn't know the bucket_id (project id) for a todo, find it.

    Strategy: hit /my/profile and /projects, then for each project query the
    todoset. This is expensive but only happens when the caller hasn't already
    threaded the bucket id through. Use sparingly.

    For Phase 4 onboarding the personal BC project id is always known, so this
    path is rarely hit.
    """
    raise RuntimeError(
        "bucket_id required: pass bucket_id=<bc_project_id> explicitly. "
        "Project discovery from a bare todo id is not implemented in v01."
    )


# ── Source adapters (one per source_kind) ────────────────────────────


def extract_from_bc_ticket(bc_id: str, *, account_id: str = "",
                                          bucket_id: str = "",
                                          bc_token: str = "") -> ExtractedSource:
    """Pull a BC todo + its comments. Returns an ExtractedSource.

    Required: bc_id, bucket_id (the BC project id that contains the todo),
              bc_token.
    Optional: account_id (defaults to BASECAMP_ACCOUNT_ID env var).
    """
    account_id = account_id or DEFAULT_BC_ACCOUNT_ID
    if not bc_token:
        bc_token = os.environ.get("BASECAMP_ACCESS_TOKEN", "")
    if not bc_token:
        raise RuntimeError("bc_token required (or set BASECAMP_ACCESS_TOKEN)")
    if not bucket_id:
        bucket_id = _discover_bucket_id(bc_id, account_id, bc_token)

    todo = _fetch_todo(bc_id, account_id, bucket_id, bc_token)
    comments = _fetch_comments(bc_id, account_id, bucket_id, bc_token)

    title = (todo.get("title") or todo.get("content") or f"BC todo {bc_id}").strip()
    description_html = todo.get("description") or todo.get("content") or ""
    body = _strip_html(description_html) or title

    metadata = {
        "bc_url": todo.get("app_url") or todo.get("url") or "",
        "bc_status": todo.get("status", "?"),
        "bc_completed": bool(todo.get("completed")),
        "bc_assignee_names": [
            a.get("name", "?") for a in (todo.get("assignees") or [])
        ],
        "bc_created_at": todo.get("created_at", ""),
        "bc_updated_at": todo.get("updated_at", ""),
        "bc_project_id": bucket_id,
        "comments": comments,
    }
    return ExtractedSource(
        source_kind="bc_ticket",
        source_id=str(bc_id),
        title=title,
        body=body,
        metadata=metadata,
    )


# Future adapters (placeholders documenting the contract — uncomment + implement):
#
# def extract_from_transcript(transcript_id: str, **opts) -> ExtractedSource: ...
# def extract_from_session(session_id: str, **opts) -> ExtractedSource: ...


# ── Slug generation ──────────────────────────────────────────────────


_SLUG_BAD = re.compile(r"[^a-z0-9-]+")


def slugify(title: str, max_len: int = 60) -> str:
    """Convert a title to a filesystem/branch-safe slug.

    Lowercase, alphanumeric + dashes only. Collapses runs of dashes. Truncates
    to max_len. Falls back to "untitled" if empty.
    """
    s = (title or "").strip().lower()
    s = _SLUG_BAD.sub("-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "untitled"


# ── Public facade ────────────────────────────────────────────────────


def extract(source_kind: str,
                  bc_id: str,
                  output_type: str,
                  slug: Optional[str] = None,
                  *,
                  commit: bool = True,
                  bc_token: str = "",
                  account_id: str = "",
                  bucket_id: str = "",
                  repo: str = "",
                  created_by: str = "") -> dict:
    """Extract from a source -> render template -> optionally commit to library.

    Args:
        source_kind: which adapter to use (currently only "bc_ticket")
        bc_id: id within the source (BC todo id for "bc_ticket")
        output_type: which template under app/templates/extracted/ to render
                              (e.g. "skill", "directive")
        slug: filesystem/branch-safe name; auto-derived from the source title
                if omitted
        commit: if True, push the rendered file to skill-extracted/<slug>
                       branch in the library repo. If False, only render +
                       write to local disk (used by Phase 6 preview).
        bc_token: BC API token (defaults to BASECAMP_ACCESS_TOKEN env)
        account_id: BC account id (defaults to BASECAMP_ACCOUNT_ID env)
        bucket_id: BC project id containing the todo (required for "bc_ticket")
        repo: override the target library repo (defaults to GITHUB_LIBRARY_REPO)
        created_by: user_id of the operator triggering the extract (for audit)

    Returns: dict with keys:
        ok            -- bool
        source_kind, source_id, output_type, slug
        content_preview  -- the first ~2000 chars of the rendered output
        local_path    -- absolute path on disk where the file was written
        branch        -- the GitHub branch (only if commit=True)
        file_path     -- the path within the library repo (only if commit=True)
        raw_url       -- GitHub raw URL on the branch (only if commit=True)
        error         -- error string if ok=False
    """
    # 1. Adapter
    if source_kind == "bc_ticket":
        src = extract_from_bc_ticket(
            bc_id, account_id=account_id, bucket_id=bucket_id, bc_token=bc_token
        )
    else:
        return {
            "ok": False,
            "source_kind": source_kind,
            "source_id": str(bc_id),
            "output_type": output_type,
            "error": f"unsupported source_kind={source_kind!r} "
                            f"(supported: bc_ticket)",
        }

    # 2. Slug
    final_slug = slug or slugify(src.title)

    # 3. Render + write (and optionally commit)
    try:
        if commit:
            artifact = extracted_writer.write_and_commit(
                src, output_type, final_slug,
                repo=repo, created_by=created_by,
            )
            content = extracted_writer.render(src, output_type, final_slug,
                                                                created_at=artifact.created_at)
            return {
                "ok": True,
                "source_kind": src.source_kind,
                "source_id": src.source_id,
                "output_type": output_type,
                "slug": final_slug,
                "content_preview": content[:2000],
                "local_path": artifact.local_path,
                "branch": artifact.branch,
                "file_path": artifact.file_path,
                "raw_url": artifact.raw_url,
                "created_at": artifact.created_at,
            }
        # Preview-only mode (Phase 6 preview route uses this)
        content = extracted_writer.render(src, output_type, final_slug)
        local_path = extracted_writer.write_to_disk(content, output_type, final_slug)
        return {
            "ok": True,
            "source_kind": src.source_kind,
            "source_id": src.source_id,
            "output_type": output_type,
            "slug": final_slug,
            "content_preview": content[:2000],
            "local_path": str(local_path),
            "branch": "",
            "file_path": "",
            "raw_url": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "source_kind": src.source_kind,
            "source_id": src.source_id,
            "output_type": output_type,
            "slug": final_slug,
            "error": f"{type(e).__name__}: {e}",
        }
