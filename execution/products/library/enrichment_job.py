"""Asset enrichment — turn a thin URL-only record into a rich,
fetched-and-snapshotted asset.

For each asset:
    1. Resolve the source URL → fetch strategy (GitHub repo, raw file, webpage)
    2. Fetch the relevant artifacts:
         - GitHub repo: README.md, mcp.json/package.json/pyproject.toml,
           one sample code file (capped at 50 KB), repo stats via API
         - Raw markdown URL: just the markdown
         - Webpage: HTML, extracted main content
    3. Parse + extract structured fields (install steps, examples,
       license, dependencies)
    4. Snapshot raw content under output/library/_snapshots/
    5. Update AssetMetadata with enriched fields

Idempotent: calling enrich_asset on an already-enriched asset is a no-op
unless force=True. Refresh = force enrichment.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import fetcher, parser as parser_mod, store

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOTS_ROOT = ROOT / "output" / "library" / "_snapshots"
MAX_SNAPSHOT_BYTES = 50_000
MAX_CODE_SAMPLE_BYTES = 50_000


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── Snapshot I/O ──────────────────────────────────────────────────────


def _snapshot_path(workspace: str, category: str, asset_id: str,
                          suffix: str = "md") -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", asset_id)[:80]
    d = SNAPSHOTS_ROOT / workspace / category
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe_id}.{suffix}"


def _write_snapshot(workspace: str, category: str, asset_id: str,
                          content: str, suffix: str = "md") -> str:
    """Persist raw fetched content. Returns relative path for storage."""
    p = _snapshot_path(workspace, category, asset_id, suffix)
    truncated = content[:MAX_SNAPSHOT_BYTES]
    p.write_text(truncated, encoding="utf-8")
    return str(p.relative_to(ROOT)).replace("\\", "/")


# ── Source-URL strategy resolver ──────────────────────────────────────


def _resolve_strategy(source_url: str) -> dict[str, Any]:
    """Decide what to fetch for a given URL.

    Returns dict with keys:
        kind: "github_repo" | "github_file" | "raw_markdown" | "webpage"
        owner, repo, ref, path (for github_*)
        url (always)
    """
    url = (source_url or "").strip()
    if not url:
        return {"kind": "none", "url": ""}

    if "github.com" in url:
        gh = fetcher.parse_github_url(url)
        if gh:
            owner = gh.get("owner") or ""
            repo = gh.get("repo") or ""
            ref = gh.get("ref") or "main"
            path = gh.get("path") or ""
            # A path ending in a file extension = single file
            if path and "." in path.rsplit("/", 1)[-1] and not path.endswith("/"):
                return {"kind": "github_file", "owner": owner, "repo": repo,
                              "ref": ref, "path": path, "url": url}
            # A path that's a directory inside a repo (e.g. /tree/main/src/foo)
            if path:
                return {"kind": "github_subdir", "owner": owner, "repo": repo,
                              "ref": ref, "path": path.rstrip("/"), "url": url}
            # Bare repo root
            return {"kind": "github_repo", "owner": owner, "repo": repo,
                          "ref": ref, "path": "", "url": url}

    if url.endswith((".md", ".markdown")):
        return {"kind": "raw_markdown", "url": url}

    return {"kind": "webpage", "url": url}


# ── GitHub-specific fetch helpers ─────────────────────────────────────


_MANIFEST_CANDIDATES = [
    "mcp.json", "package.json", "pyproject.toml",
    "manifest.json", "skill.json", "agent.json",
]
_README_CANDIDATES = [
    "README.md", "README.MD", "Readme.md", "readme.md",
    "README.rst", "README.txt", "README",
]
_LICENSE_CANDIDATES = [
    "LICENSE", "LICENSE.md", "LICENSE.txt", "license", "license.md",
]
_INSTALL_SECTION_RE = re.compile(
    r"^#{1,3}\s+(install|installation|setup|getting started|quick ?start)\s*$",
    re.I | re.M,
)


def _try_fetch_github_file(owner: str, repo: str, ref: str,
                                  path: str) -> str | None:
    res = fetcher.fetch_github_file(owner, repo, path, ref)
    if res.ok and res.document:
        return res.document.content
    return None


def _fetch_repo_stats(owner: str, repo: str) -> dict[str, Any]:
    """Pull stars/forks/last-commit if GITHUB_TOKEN is available. Otherwise
    return only what we can infer."""
    out: dict[str, Any] = {}
    if not os.environ.get("GITHUB_TOKEN"):
        return out
    import urllib.request
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        out["stars"] = data.get("stargazers_count", 0)
        out["forks"] = data.get("forks_count", 0)
        out["last_commit"] = data.get("pushed_at", "")
        out["language"] = data.get("language", "")
        out["default_branch"] = data.get("default_branch", "")
        out["open_issues"] = data.get("open_issues_count", 0)
    except Exception:
        pass
    return out


def _pick_sample_code_file(tree: list[dict[str, Any]]) -> dict[str, Any] | None:
    """From a repo tree, pick one meaningful code file to snapshot."""
    if not tree:
        return None
    interesting_ext = (".py", ".ts", ".js", ".rs", ".go", ".java", ".rb")
    candidates = []
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        size = entry.get("size", 0)
        if size > MAX_CODE_SAMPLE_BYTES:
            continue
        if path.endswith(interesting_ext) and "/test" not in path.lower():
            # Score: prefer src/ or main file; smaller files better
            score = 0
            if "src/" in path: score += 5
            if path in ("index.ts", "index.js", "main.py", "server.py"): score += 3
            if path.count("/") <= 2: score += 2
            score -= min(size // 5000, 5)
            candidates.append((score, entry))
    if not candidates:
        return None
    candidates.sort(key=lambda t: -t[0])
    return candidates[0][1]


def _extract_install_steps(readme: str) -> list[str]:
    """Look for an 'Install' / 'Getting Started' section, extract bullet
    points or numbered steps, fall back to fenced code blocks."""
    m = _INSTALL_SECTION_RE.search(readme)
    if not m:
        return []
    rest = readme[m.end():]
    next_h = re.search(r"^#{1,6}\s+", rest, re.M)
    section = rest[: next_h.start()] if next_h else rest
    steps: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*+]\s+", stripped):
            steps.append(re.sub(r"^[-*+]\s+", "", stripped))
        elif re.match(r"^\d+[\.\)]\s+", stripped):
            steps.append(re.sub(r"^\d+[\.\)]\s+", "", stripped))
    if not steps:
        # Fall back to first fenced code block in install section
        code = re.search(r"```\w*\n(.*?)```", section, re.S)
        if code:
            for line in code.group(1).splitlines():
                if line.strip():
                    steps.append(line)
    return steps[:20]


def _extract_examples(readme: str, limit: int = 5) -> list[str]:
    blocks = re.findall(r"```\w*\n(.*?)```", readme, re.S)
    return [b.strip() for b in blocks[:limit]]


def _extract_license(content: str) -> str:
    """Look for an SPDX-style license identifier or first heading."""
    for line in content.splitlines()[:30]:
        if re.search(r"\b(MIT|Apache-?2|BSD-?[23]|GPL-?[23]|ISC|MPL|Unlicense|CC0)\b", line):
            m = re.search(r"\b(MIT|Apache-?2|BSD-?[23]|GPL-?[23]|ISC|MPL|Unlicense|CC0)\b", line)
            if m:
                return m.group(1).upper()
    return ""


# ── Action-layer extraction (install command, docs, what-it's-for) ──


_INSTALL_CMD_PATTERNS = [
    re.compile(r"^(npm install [^\n]+)", re.M),
    re.compile(r"^(yarn add [^\n]+)", re.M),
    re.compile(r"^(pnpm add [^\n]+)", re.M),
    re.compile(r"^(pip install [^\n]+)", re.M),
    re.compile(r"^(uv pip install [^\n]+)", re.M),
    re.compile(r"^(uvx [^\n]+)", re.M),
    re.compile(r"^(cargo install [^\n]+)", re.M),
    re.compile(r"^(go install [^\n]+)", re.M),
    re.compile(r"^(brew install [^\n]+)", re.M),
]


def _extract_install_command(install_steps: list[str],
                                       manifest: dict, code_samples: list[dict]) -> str:
    """Find a copy-pasteable install command."""
    # Search install steps first
    for step in install_steps:
        for pat in _INSTALL_CMD_PATTERNS:
            m = pat.search(step)
            if m:
                return m.group(1).strip()[:200]
    # Try from manifest
    if isinstance(manifest, dict):
        name = manifest.get("name")
        if name:
            # npm-style package
            if "version" in manifest or "scripts" in manifest:
                return f"npm install {name}"
            # python-style
            if any(k in manifest for k in ("requires-python", "dependencies", "project")):
                return f"pip install {name}"
    return ""


def _infer_install_url(manifest: dict, source_url: str) -> str:
    """Best-guess URL for the package on its registry."""
    if isinstance(manifest, dict):
        name = manifest.get("name")
        if name and "version" in manifest:
            # npm package
            return f"https://www.npmjs.com/package/{name}"
        if name and "requires-python" in manifest:
            return f"https://pypi.org/project/{name}/"
    return source_url


def _extract_docs_url(readme: str, manifest: dict) -> str:
    """Pull docs URL out of manifest or README."""
    if isinstance(manifest, dict):
        for k in ("homepage", "documentation", "docs"):
            v = manifest.get(k)
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                return v
    # Look for [Documentation](url) or [Docs](url) in README
    if readme:
        m = re.search(r"\[(?:docs|documentation|api docs|api reference)\]\((https?://[^)]+)\)",
                            readme, re.I)
        if m:
            return m.group(1)
    return ""


def _extract_homepage_url(manifest: dict, source_url: str) -> str:
    if isinstance(manifest, dict):
        for k in ("homepage", "url"):
            v = manifest.get(k)
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                return v
    return ""


def _distill_purpose(manifest: dict, readme: str, description: str) -> str:
    """Compose a clean 1-3 sentence "what it's used for" string.

    Priority: manifest.description (curated) > first README paragraph (often
    a strong one-liner) > the existing description field.
    """
    candidates: list[str] = []
    if isinstance(manifest, dict):
        d = manifest.get("description")
        if isinstance(d, str) and len(d) >= 20:
            candidates.append(d)
    if readme:
        # First non-heading, non-badge paragraph
        for para in readme.split("\n\n"):
            stripped = para.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "![" in stripped[:200] or "<img" in stripped[:200]:
                continue   # skip badge-only paragraphs
            if len(stripped) >= 30:
                candidates.append(stripped)
                break
    if description:
        candidates.append(description)
    # Pick the most informative — favor 80–500 char range
    best = ""
    for c in candidates:
        c = c.strip().replace("\n", " ")
        if 60 <= len(c) <= 500 and len(c) > len(best):
            best = c
    if not best and candidates:
        best = candidates[0][:500]
    return best


# ── Main enrichment function ─────────────────────────────────────────


def enrich_asset(workspace: str, category: str, asset_id: str,
                       source_url: str, enricher_id: str = "system",
                       force: bool = False) -> store.AssetMetadata:
    """Fetch, parse, snapshot, persist. Returns updated metadata."""
    meta = store.get_metadata(workspace, category, asset_id)
    if meta.enrichment_state == "enriched" and not force:
        return meta

    meta.enrichment_state = "enriching"
    meta.source_url = source_url or meta.source_url or meta.source
    store.save_metadata(meta)

    try:
        strat = _resolve_strategy(meta.source_url)
        kind = strat["kind"]

        readme_md = ""
        manifest_data: dict[str, Any] = {}
        code_samples: list[dict] = []
        repo_stats: dict[str, Any] = {}
        license_str = ""
        languages: list[str] = []
        dependencies: list[str] = []

        if kind in ("github_repo", "github_subdir"):
            owner, repo, ref = strat["owner"], strat["repo"], strat["ref"]
            base_path = strat.get("path") or ""
            prefix = (base_path + "/") if base_path else ""

            # README — try subdir first, then repo root as fallback
            search_paths = (
                [prefix + c for c in _README_CANDIDATES] + _README_CANDIDATES
                if base_path else list(_README_CANDIDATES)
            )
            for cand in search_paths:
                readme_md = _try_fetch_github_file(owner, repo, ref, cand) or ""
                if readme_md:
                    break
            # Manifest — same subdir-first strategy
            manifest_paths = (
                [prefix + c for c in _MANIFEST_CANDIDATES] + _MANIFEST_CANDIDATES
                if base_path else list(_MANIFEST_CANDIDATES)
            )
            for cand in manifest_paths:
                m = _try_fetch_github_file(owner, repo, ref, cand)
                if m:
                    try:
                        if cand.endswith(".json"):
                            manifest_data = json.loads(m)
                            if isinstance(manifest_data.get("dependencies"), dict):
                                dependencies = list(manifest_data["dependencies"].keys())[:20]
                        else:
                            manifest_data = {"raw": m[:2000]}
                    except Exception:
                        manifest_data = {"raw": m[:2000]}
                    break
            # License — repo root only
            for cand in _LICENSE_CANDIDATES:
                lic_text = _try_fetch_github_file(owner, repo, ref, cand)
                if lic_text:
                    license_str = _extract_license(lic_text) or "see LICENSE file"
                    break
            # Sample code file — full tree, filtered to subdir if applicable
            tree = fetcher.fetch_github_tree(owner, repo, ref)
            if base_path:
                tree = [t for t in tree if t.get("path", "").startswith(prefix)]
            sample = _pick_sample_code_file(tree)
            if sample:
                code = _try_fetch_github_file(owner, repo, ref, sample["path"])
                if code:
                    ext = sample["path"].rsplit(".", 1)[-1]
                    code_samples.append({
                        "path": sample["path"],
                        "language": {"py": "python", "ts": "typescript",
                                          "js": "javascript", "rs": "rust",
                                          "go": "go"}.get(ext, ext),
                        "content": code[:MAX_CODE_SAMPLE_BYTES],
                    })
                    languages.append(code_samples[0]["language"])
            # Repo stats
            repo_stats = _fetch_repo_stats(owner, repo)

        elif kind == "github_file":
            owner, repo, ref, path = (strat["owner"], strat["repo"],
                                                strat["ref"], strat["path"])
            content = _try_fetch_github_file(owner, repo, ref, path) or ""
            if path.endswith((".md", ".markdown")):
                readme_md = content
            elif path.endswith(".json"):
                try:
                    manifest_data = json.loads(content)
                except Exception:
                    manifest_data = {"raw": content[:2000]}
            else:
                readme_md = content
            repo_stats = _fetch_repo_stats(owner, repo)

        elif kind == "raw_markdown":
            res = fetcher.fetch_url(strat["url"])
            if res.ok and res.document:
                readme_md = res.document.content

        elif kind == "webpage":
            res = fetcher.fetch_url(strat["url"])
            if res.ok and res.document:
                parsed = parser_mod.parse_html(res.document.content)
                readme_md = (f"# {parsed.title}\n\n{parsed.description}\n\n"
                                  + parsed.body_text[:5000])

        # ── Extract structured fields from readme ───────────────────
        install_steps = _extract_install_steps(readme_md)
        examples = _extract_examples(readme_md)
        if not license_str and readme_md:
            license_str = _extract_license(readme_md)

        # ── Snapshot the raw README so we can show it offline ───────
        snapshot_path = ""
        if readme_md:
            snapshot_path = _write_snapshot(workspace, category, asset_id,
                                                          readme_md, "md")

        # ── Update metadata ────────────────────────────────────────
        meta.enrichment_state = "enriched"
        meta.enriched_at = _now()
        meta.enriched_by = enricher_id
        meta.enrichment_error = None
        meta.readme_markdown = readme_md[:MAX_SNAPSHOT_BYTES]
        meta.install_steps = install_steps
        meta.examples = examples
        meta.code_samples = code_samples
        meta.license = license_str
        meta.languages = languages
        meta.dependencies = dependencies
        meta.repo_stats = repo_stats
        meta.snapshot_path = snapshot_path

        # Also enrich the top-level fields if they're empty
        if not meta.how_to_use and install_steps:
            meta.how_to_use = "\n".join(install_steps[:10])
        if not meta.example and examples:
            meta.example = examples[0]
        if not meta.description and readme_md:
            first_para = next((p.strip() for p in readme_md.split("\n\n")
                                    if p.strip() and not p.strip().startswith("#")), "")
            meta.description = first_para[:600]

        # ── Actionable links + "what it's used for" ───────────────
        meta.install_command = _extract_install_command(
            install_steps, manifest_data, code_samples)
        meta.install_url = _infer_install_url(manifest_data, meta.source_url)
        meta.docs_url = _extract_docs_url(readme_md, manifest_data)
        meta.homepage_url = _extract_homepage_url(manifest_data, meta.source_url)
        # what_its_for: distilled purpose — distinct from description.
        # Prefer manifest description; fallback to first README paragraph.
        meta.what_its_for = _distill_purpose(
            manifest_data, readme_md, meta.description)

        store.save_metadata(meta)

    except Exception as e:
        meta.enrichment_state = "failed"
        meta.enrichment_error = f"{type(e).__name__}: {e}"
        meta.enriched_at = _now()
        store.save_metadata(meta)

    return meta


# ── Bulk enrichment ──────────────────────────────────────────────────


def enrich_batch(workspace: str, items: list[dict[str, Any]],
                       enricher_id: str = "system",
                       force: bool = False) -> dict[str, int]:
    """Enrich many items. Returns counts: {enriched, skipped, failed}."""
    out = {"enriched": 0, "skipped": 0, "failed": 0}
    for item in items:
        category = item.get("category") or "skills"
        # Slugify the name-fallback so new enrichment writes land at
        # slug-based paths. Pre-fix, raw item["name"] (e.g. "HTML to
        # Markdown") leaked through and produced literal-name .meta.json
        # files on disk -- the same bug the migration script now cleans
        # up. If asset_id is set explicitly, trust it verbatim.
        name = (item.get("name") or "").strip()
        asset_id = item.get("asset_id") or (store.slugify(name) if name else "")
        source_url = item.get("source_url") or item.get("source") or ""
        if not asset_id or not source_url:
            out["skipped"] += 1
            continue
        meta = enrich_asset(workspace, category, asset_id, source_url,
                                  enricher_id, force=force)
        if meta.enrichment_state == "enriched":
            out["enriched"] += 1
        elif meta.enrichment_state == "failed":
            out["failed"] += 1
        else:
            out["skipped"] += 1
    return out
