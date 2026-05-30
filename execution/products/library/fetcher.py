"""URL + GitHub fetcher for Library ingestion.

Three modes:
    fetch_url(url) -> RawDocument            (any HTTP URL)
    fetch_github_file(owner, repo, path) -> RawDocument
    walk_github_repo(owner, repo, ref=None) -> list[RawDocument]

GitHub fetches use the public REST API for tree-walking + raw.githubusercontent
for file content. No git clone required. If a GITHUB_TOKEN env var is present,
it's used to raise the rate limit from 60 → 5000 req/hr.

Network calls are wrapped in a configurable timeout and never crash the caller.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

LAYER = "product"
PRODUCT = "library"

DEFAULT_TIMEOUT = 15
MAX_FILE_BYTES = 1_000_000   # 1 MB per file


@dataclass
class RawDocument:
    """One fetched document — could be HTML, Markdown, or JSON manifest."""

    source_url: str
    content: str
    content_type: str = "text/html"
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    path: str | None = None   # relative path inside a repo (None for single URLs)
    repo: str | None = None   # "owner/repo" if from GitHub


@dataclass
class FetchResult:
    ok: bool
    document: RawDocument | None = None
    error: str | None = None


# ── Generic URL fetch ──────────────────────────────────────────────────


def _safe_request(url: str, timeout: int = DEFAULT_TIMEOUT,
                       headers: dict[str, str] | None = None) -> FetchResult:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ColaberryLibrary/1.0 (+https://colaberry.ai)",
                **(headers or {}),
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_FILE_BYTES + 1)
            if len(body) > MAX_FILE_BYTES:
                return FetchResult(False, error=f"file > {MAX_FILE_BYTES} bytes; refusing")
            content = body.decode("utf-8", errors="replace")
            doc = RawDocument(
                source_url=url,
                content=content,
                content_type=resp.headers.get("Content-Type", "text/html"),
                status=resp.status,
                headers={k.lower(): v for k, v in resp.headers.items()},
            )
            return FetchResult(True, document=doc)
    except urllib.request.HTTPError as e:
        return FetchResult(False, error=f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        return FetchResult(False, error=f"{type(e).__name__}: {e}")


def fetch_url(url: str) -> FetchResult:
    """Fetch any URL. Auto-rewrites GitHub blob URLs to raw."""
    if not url:
        return FetchResult(False, error="empty URL")
    # github.com/owner/repo/blob/ref/path → raw.githubusercontent.com/owner/repo/ref/path
    if "github.com" in url and "/blob/" in url:
        url = url.replace("github.com", "raw.githubusercontent.com", 1).replace("/blob/", "/", 1)
    return _safe_request(url)


# ── GitHub-specific helpers ────────────────────────────────────────────


GH_REPO_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/\s.]+)(?:\.git)?(?:/tree/(?P<ref>[^/]+)(?:/(?P<path>.*))?)?/?$"
)


def parse_github_url(url: str) -> dict[str, str | None] | None:
    """Extract (owner, repo, ref, path) from a GitHub URL.

    Accepts:
        github.com/owner/repo
        github.com/owner/repo/tree/main
        github.com/owner/repo/tree/main/src/foo
        https://github.com/owner/repo
    """
    m = GH_REPO_PATTERN.match(url.strip())
    if not m:
        return None
    return {
        "owner": m.group("owner"),
        "repo": m.group("repo"),
        "ref": m.group("ref"),
        "path": m.group("path"),
    }


def _gh_headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def fetch_github_file(owner: str, repo: str, path: str,
                            ref: str = "main") -> FetchResult:
    """Fetch a single file from a GitHub repo via raw.githubusercontent."""
    safe_path = urllib.parse.quote(path)
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{safe_path}"
    res = _safe_request(url)
    if res.ok and res.document:
        res.document.repo = f"{owner}/{repo}"
        res.document.path = path
    return res


def _try_tree(owner: str, repo: str, ref: str) -> tuple[list[dict[str, Any]], str | None]:
    """Single tree fetch attempt. Returns (entries, error_msg)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
    try:
        req = urllib.request.Request(url, headers=_gh_headers())
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("tree"):
            return ([], "empty tree returned")
        return ([
            {"path": t["path"], "type": t["type"],
               "size": t.get("size", 0), "sha": t.get("sha", "")}
            for t in data["tree"]
        ], None)
    except urllib.request.HTTPError as e:
        return ([], f"HTTP {e.code}: {e.reason}")
    except Exception as e:  # noqa: BLE001
        return ([], f"{type(e).__name__}: {e}")


def _repo_default_branch(owner: str, repo: str) -> str | None:
    """Ask the repo metadata for its real default branch."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        req = urllib.request.Request(url, headers=_gh_headers())
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("default_branch")
    except Exception:
        return None


def fetch_github_tree(owner: str, repo: str,
                            ref: str = "main") -> list[dict[str, Any]]:
    """List every file in a repo. Resilient to wrong default branch.

    Order: provided ref → main → master → repo's real default_branch.
    Returns first non-empty result.
    """
    tried: list[str] = []
    for candidate in [ref, "main", "master"]:
        if candidate in tried:
            continue
        tried.append(candidate)
        entries, _ = _try_tree(owner, repo, candidate)
        if entries:
            return entries
    # Last resort: ask the API for the real default
    real = _repo_default_branch(owner, repo)
    if real and real not in tried:
        entries, _ = _try_tree(owner, repo, real)
        if entries:
            return entries
    return []


def resolve_default_branch(owner: str, repo: str,
                                  preferred: str = "main") -> str:
    """Returns the branch name we should actually use for this repo.

    Same fallback chain as fetch_github_tree, but exposed so the ingest
    layer can pass the right ref into fetch_github_file."""
    for candidate in (preferred, "main", "master"):
        entries, _ = _try_tree(owner, repo, candidate)
        if entries:
            return candidate
    real = _repo_default_branch(owner, repo)
    return real or preferred


# ── File-pattern matcher (recognizes asset-bearing files) ─────────────

INTERESTING_FILE_PATTERNS = [
    # (suffix or regex, expected category hint, weight)
    (re.compile(r"(?:^|/)mcp\.json$", re.I),        "mcp",       9),
    (re.compile(r"(?:^|/)server\.json$", re.I),     "mcp",       8),
    (re.compile(r"(?:^|/)agent\.md$", re.I),        "agents",    9),
    (re.compile(r"(?:^|/)agents?/[^/]+\.md$", re.I),"agents",    8),
    (re.compile(r"(?:^|/)agents?/[^/]+\.json$", re.I),"agents",  8),
    (re.compile(r"(?:^|/)prompts?/[^/]+\.md$", re.I),"prompts",  9),
    (re.compile(r"(?:^|/)prompts?/[^/]+\.txt$", re.I),"prompts", 8),
    (re.compile(r"(?:^|/)skills?/[^/]+\.json$", re.I),"skills",  9),
    (re.compile(r"(?:^|/)skills?/[^/]+\.md$", re.I),"skills",    8),
    (re.compile(r"(?:^|/)workflows?/[^/]+\.ya?ml$", re.I),"workflows", 9),
    (re.compile(r"(?:^|/)workflows?/[^/]+\.json$", re.I),"workflows", 8),
    (re.compile(r"(?:^|/)templates?/[^/]+\.ya?ml$", re.I),"templates", 9),
    (re.compile(r"(?:^|/)templates?/[^/]+\.json$", re.I),"templates", 8),
    (re.compile(r"(?:^|/)policies/[^/]+\.json$", re.I),"policies", 9),
    (re.compile(r"(?:^|/)connectors?/[^/]+\.json$", re.I),"connectors", 9),
    (re.compile(r"(?:^|/)adapters?/[^/]+\.py$", re.I),"adapters", 8),
    (re.compile(r"(?:^|/)manifest\.json$", re.I),     "capabilities", 7),
    # README at the repo root → ingest as one "main" asset for the repo
    (re.compile(r"^readme(\.md|\.rst|\.txt)?$", re.I),"capabilities", 4),
    # README in any subdirectory (awesome-list style — each dir = one asset)
    (re.compile(r"/readme\.md$", re.I),               "skills",       5),
    # Any markdown in a directory that looks like a category, treated as a skill
    (re.compile(r"^[a-z][\w-]+/[\w-]+\.md$", re.I),   "skills",       3),
]


def match_path_pattern(path: str) -> tuple[str, int] | None:
    """If `path` matches a known asset pattern, return (category_hint, weight)."""
    for pat, hint, w in INTERESTING_FILE_PATTERNS:
        if pat.search(path):
            return (hint, w)
    return None


def filter_interesting_files(tree: list[dict[str, Any]],
                                    max_files: int = 200) -> list[dict[str, Any]]:
    """Pick files in the tree that match known asset patterns."""
    interesting = []
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        if entry.get("size", 0) > MAX_FILE_BYTES:
            continue
        hint = match_path_pattern(entry["path"])
        if hint:
            interesting.append({**entry, "_category_hint": hint[0],
                                       "_weight": hint[1]})
    # Sort by weight (strongest signals first), then path
    interesting.sort(key=lambda e: (-e["_weight"], e["path"]))
    return interesting[:max_files]
