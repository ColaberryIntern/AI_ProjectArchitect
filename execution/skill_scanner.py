"""Daily skill scanner: discovers Claude-compatible skills from external sources.

Scans multiple public sources (GitHub repos, MCP registry, awesome lists, etc.)
to keep the skill registry up to date. Each scanner is isolated — if one fails,
others still contribute results.

Usage:
    from execution.skill_scanner import run_full_scan
    result = await run_full_scan()
"""

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

REGISTRY_PATH = PROJECT_ROOT / "config" / "skill_registry.json"

# GitHub API base — supports optional GITHUB_TOKEN for higher rate limits
GITHUB_API = "https://api.github.com"

# Scan sources: each has a name, a scanner function name, and metadata
SCAN_SOURCES = [
    {
        "name": "MCP Servers (Official)",
        "scanner": "scan_mcp_official",
        "url": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "name": "Awesome MCP Servers",
        "scanner": "scan_awesome_list",
        "url": "https://github.com/punkpeye/awesome-mcp-servers",
        "kwargs": {"category_prefix": "MCP Servers"},
    },
    {
        "name": "Awesome AI Agents",
        "scanner": "scan_awesome_list",
        "url": "https://github.com/e2b-dev/awesome-ai-agents",
        "kwargs": {"category_prefix": "AI Agent Frameworks"},
    },
    {
        "name": "LangChain Tools",
        "scanner": "scan_awesome_list",
        "url": "https://github.com/kyrolabs/awesome-langchain",
        "kwargs": {"category_prefix": "LLM Tool Libraries"},
    },
    {
        "name": "Semantic Kernel Skills",
        "scanner": "scan_github_search",
        "kwargs": {"query": "semantic-kernel skill plugin", "category": "AI Agent Frameworks"},
    },
    {
        "name": "n8n Nodes",
        "scanner": "scan_github_search",
        "kwargs": {"query": "n8n-nodes-", "category": "Automation & Integration"},
    },
    {
        "name": "Zapier Integrations",
        "scanner": "scan_github_search",
        "kwargs": {"query": "zapier integration claude ai", "category": "Automation & Integration"},
    },
    {
        "name": "Claude Tool Use Examples",
        "scanner": "scan_github_search",
        "kwargs": {"query": "anthropic claude tool-use", "category": "LLM Tool Libraries"},
    },
]

MAX_SKILLS = 500


def _github_headers() -> dict:
    """Build GitHub API headers, including auth token if available."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_awesome_list(markdown: str, source: str, category_prefix: str = "") -> list[dict]:
    """Parse an awesome-list markdown file to extract skill entries.

    Matches lines like: - [Name](url) - Description
    or: - **[Name](url)** - Description
    """
    skills = []
    # Pattern: optional bold markers, [Name](url), then dash or colon, then description
    pattern = re.compile(
        r"[-*]\s+\*{0,2}\[([^\]]+)\]\(([^)]+)\)\*{0,2}\s*[-–:]\s*(.+)",
        re.IGNORECASE,
    )
    current_category = category_prefix or "Uncategorized"

    for line in markdown.splitlines():
        line = line.strip()

        # Detect category headings (## or ###)
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading and len(heading) < 80:
                current_category = f"{category_prefix} — {heading}" if category_prefix else heading

        match = pattern.match(line)
        if match:
            name = match.group(1).strip()
            url = match.group(2).strip()
            description = match.group(3).strip()

            # Generate a stable ID from the name
            skill_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if not skill_id:
                continue

            skills.append({
                "id": skill_id,
                "name": name,
                "description": description[:300],
                "category": current_category[:100],
                "source": source,
                "source_url": url,
                "tags": _extract_tags(name, description),
                "last_verified": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            })

    return skills


def _extract_tags(name: str, description: str) -> list[str]:
    """Extract simple keyword tags from name and description."""
    text = f"{name} {description}".lower()
    tag_keywords = [
        "mcp", "api", "database", "file", "search", "ai", "agent",
        "llm", "rag", "vector", "embedding", "chat", "auth", "security",
        "monitor", "log", "deploy", "docker", "kubernetes", "cloud",
        "slack", "github", "notion", "email", "webhook", "automation",
        "workflow", "integration", "testing", "code", "browser", "scrape",
    ]
    return [kw for kw in tag_keywords if kw in text][:8]


def _deduplicate_skills(skills: list[dict]) -> list[dict]:
    """Deduplicate skills by ID, keeping first occurrence."""
    seen = set()
    unique = []
    for skill in skills:
        sid = skill.get("id", "")
        if sid and sid not in seen:
            seen.add(sid)
            unique.append(skill)
    return unique


async def scan_mcp_official(client: httpx.AsyncClient) -> list[dict]:
    """Scan the official MCP servers repository for server directories."""
    skills = []
    try:
        # Fetch the README which lists all official servers
        url = f"{GITHUB_API}/repos/modelcontextprotocol/servers/readme"
        resp = await client.get(url, headers=_github_headers())
        resp.raise_for_status()
        import base64
        content = base64.b64decode(resp.json()["content"]).decode("utf-8")
        skills = _parse_awesome_list(content, "modelcontextprotocol.io", "MCP Servers")
    except Exception:
        logger.warning("Failed to scan MCP official servers", exc_info=True)
    return skills


async def scan_awesome_list(
    client: httpx.AsyncClient,
    url: str = "",
    category_prefix: str = "",
) -> list[dict]:
    """Scan an awesome-list GitHub repo README for skill entries."""
    skills = []
    try:
        # Convert GitHub URL to API URL for README
        parts = url.rstrip("/").split("/")
        if len(parts) >= 5:
            owner, repo = parts[3], parts[4]
        else:
            return skills

        api_url = f"{GITHUB_API}/repos/{owner}/{repo}/readme"
        resp = await client.get(api_url, headers=_github_headers())
        resp.raise_for_status()
        import base64
        content = base64.b64decode(resp.json()["content"]).decode("utf-8")
        skills = _parse_awesome_list(content, f"github.com/{owner}/{repo}", category_prefix)
    except Exception:
        logger.warning(f"Failed to scan awesome list: {url}", exc_info=True)
    return skills


async def scan_github_search(
    client: httpx.AsyncClient,
    query: str = "",
    category: str = "Uncategorized",
) -> list[dict]:
    """Search GitHub repositories for Claude-compatible tools/skills."""
    skills = []
    try:
        url = f"{GITHUB_API}/search/repositories"
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": 20,
        }
        resp = await client.get(url, headers=_github_headers(), params=params)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            name = item.get("name", "")
            description = item.get("description") or name
            skill_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if not skill_id:
                continue

            skills.append({
                "id": skill_id,
                "name": name,
                "description": description[:300],
                "category": category,
                "source": "github.com",
                "source_url": item.get("html_url", ""),
                "tags": _extract_tags(name, description),
                "last_verified": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            })
    except Exception:
        logger.warning(f"Failed GitHub search: {query}", exc_info=True)
    return skills


def _save_registry(skills: list[dict], scan_status: str, sources_scanned: int) -> None:
    """Atomic write of the skill registry file."""
    registry = {
        "version": 1,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "last_scan_status": scan_status,
        "sources_scanned": sources_scanned,
        "skills": skills[:MAX_SKILLS],
    }

    # Atomic write via temp file
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=REGISTRY_PATH.parent, suffix=".tmp", prefix="skill_registry_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
        Path(tmp_path).replace(REGISTRY_PATH)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_existing_skills() -> list[dict]:
    """Load existing skills from registry to merge with scan results."""
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("skills", [])
    except Exception:
        return []


async def run_full_scan() -> dict:
    """Run all scanners and merge results into the registry.

    Returns a summary dict with counts and status.
    """
    all_skills = []
    sources_ok = 0
    sources_failed = 0

    scanner_map = {
        "scan_mcp_official": scan_mcp_official,
        "scan_awesome_list": scan_awesome_list,
        "scan_github_search": scan_github_search,
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for source in SCAN_SOURCES:
            scanner_name = source["scanner"]
            scanner_fn = scanner_map.get(scanner_name)
            if not scanner_fn:
                logger.warning(f"Unknown scanner: {scanner_name}")
                sources_failed += 1
                continue

            try:
                kwargs = dict(source.get("kwargs", {}))
                if "url" in source and scanner_name != "scan_mcp_official":
                    kwargs["url"] = source["url"]

                results = await scanner_fn(client, **kwargs)
                all_skills.extend(results)
                sources_ok += 1
                logger.info(f"Scanned {source['name']}: {len(results)} skills found")
            except Exception:
                sources_failed += 1
                logger.warning(f"Scanner failed: {source['name']}", exc_info=True)

    # Merge with existing seed skills (seed skills take priority for dedup)
    existing = _load_existing_skills()
    merged = _deduplicate_skills(existing + all_skills)

    # Determine status
    total_sources = len(SCAN_SOURCES)
    if sources_ok == total_sources:
        status = "success"
    elif sources_ok > 0:
        status = "partial"
    else:
        status = "failed"

    try:
        _save_registry(merged, status, sources_ok)
    except Exception:
        logger.error("Failed to save registry after scan", exc_info=True)
        status = "save_failed"

    summary = {
        "status": status,
        "sources_scanned": sources_ok,
        "sources_failed": sources_failed,
        "skills_found": len(all_skills),
        "total_skills": len(merged),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"Scan complete: {summary}")
    return summary
