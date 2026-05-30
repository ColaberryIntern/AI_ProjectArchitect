"""Weekly Library scanner — discovers new candidate assets from external
sources and writes them to a 'candidates' queue for curator review.

Triggered weekly by APScheduler (already wired in the app lifespan). Idempotent:
running twice in the same week does not double-queue the same candidate.

Sources scanned today:
    - File system: skill_catalog seed file (catches additions made by hand)
    - File system: plugins/* directories (catches new plugin folders)
    - Configurable seed files for MCP server registries (read from
      config/library_sources.json if present)

Each source returns a list of CandidateAsset dicts. The scanner:
    1. Diffs against the current Library inventory + existing candidates
    2. Persists new candidates to output/library/_candidates/{date}.jsonl
    3. Surfaces an aggregated count for the Library home dashboard
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import inventory

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
CANDIDATES_ROOT = ROOT / "output" / "library" / "_candidates"
SOURCES_FILE = ROOT / "config" / "library_sources.json"
LAST_SCAN_FILE = ROOT / "output" / "library" / "_candidates" / "_last_scan.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _stable_id(category: str, name: str) -> str:
    """Deterministic ID — same input → same candidate ID, so re-runs dedupe."""
    raw = f"{category}|{name}".encode()
    return hashlib.sha256(raw).hexdigest()[:10]


@dataclass
class CandidateAsset:
    candidate_id: str
    category: str
    name: str
    description: str = ""
    source: str = ""
    discovered_at: str = ""
    discovered_by: str = "scanner"
    tags: list[str] = field(default_factory=list)
    status: str = "new"  # new | accepted | rejected


# ── Source loaders ───────────────────────────────────────────────────


def _load_external_sources() -> list[dict[str, Any]]:
    if not SOURCES_FILE.exists():
        return []
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _scan_skill_catalog() -> list[CandidateAsset]:
    """Catches skills that appear in the catalog but not in Library yet."""
    try:
        from execution.skill_catalog import load_skills
        skills = load_skills() or []
    except Exception:
        return []

    # The Library "skills" inventory already pulls from skill_catalog,
    # so anything in the catalog is in the Library by definition.
    # The interesting candidate set is skills the scanner discovered
    # but the registry hasn't yet been refreshed with — surface them.
    discovered = []
    seen_in_inventory = {s.get("name") for s in inventory.list_skills()}
    for s in skills:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("id")
        if not name or name in seen_in_inventory:
            continue
        discovered.append(CandidateAsset(
            candidate_id=_stable_id("skills", name),
            category="skills", name=name,
            description=(s.get("description") or "")[:200],
            source="skill_catalog scan",
            discovered_at=_now(),
            tags=s.get("tags") or [],
        ))
    return discovered


def _scan_plugins() -> list[CandidateAsset]:
    """Catches new plugin folders dropped under /plugins/ without manifests
    yet — surfaces them as candidates for capability registration."""
    plugins_root = ROOT / "plugins"
    if not plugins_root.exists():
        return []
    out: list[CandidateAsset] = []
    for kind_dir in plugins_root.iterdir():
        if not kind_dir.is_dir():
            continue
        for plugin_dir in kind_dir.iterdir():
            if not plugin_dir.is_dir():
                continue
            manifest = plugin_dir / "manifest.json"
            if manifest.exists():
                continue  # already a registered capability
            name = plugin_dir.name
            out.append(CandidateAsset(
                candidate_id=_stable_id("capabilities", name),
                category="capabilities", name=name,
                description=f"Plugin folder {plugin_dir.relative_to(ROOT)} has no manifest.json yet.",
                source="plugin folder scan",
                discovered_at=_now(),
                tags=["needs-manifest"],
            ))
    return out


def _scan_configured_sources() -> list[CandidateAsset]:
    """Pull from config/library_sources.json — list of {category, source_url,
    name, description}. Lets ops add new MCP/agent/prompt registries
    declaratively without code changes."""
    out: list[CandidateAsset] = []
    for src in _load_external_sources():
        if not isinstance(src, dict):
            continue
        cat = src.get("category")
        name = src.get("name")
        if not cat or not name:
            continue
        out.append(CandidateAsset(
            candidate_id=_stable_id(cat, name),
            category=cat, name=name,
            description=src.get("description", ""),
            source=src.get("source_url", "config/library_sources.json"),
            discovered_at=_now(),
            tags=src.get("tags") or [],
        ))
    return out


# ── Scanner driver ──────────────────────────────────────────────────


def _candidates_file() -> Path:
    CANDIDATES_ROOT.mkdir(parents=True, exist_ok=True)
    return CANDIDATES_ROOT / f"{_today()}.jsonl"


def list_candidates(status: str | None = None) -> list[CandidateAsset]:
    """Aggregate across all date-scoped files."""
    out: list[CandidateAsset] = []
    if not CANDIDATES_ROOT.exists():
        return out
    for p in sorted(CANDIDATES_ROOT.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                c = CandidateAsset(**d)
                if status is None or c.status == status:
                    out.append(c)
            except Exception:
                pass
    return out


def already_seen() -> set[str]:
    return {c.candidate_id for c in list_candidates()}


def scan_once() -> dict[str, Any]:
    """Run all source scans, dedupe, persist new candidates, return summary."""
    seen = already_seen()
    discovered: list[CandidateAsset] = []
    discovered += _scan_skill_catalog()
    discovered += _scan_plugins()
    discovered += _scan_configured_sources()
    new = [c for c in discovered if c.candidate_id not in seen]

    if new:
        out = _candidates_file()
        with out.open("a", encoding="utf-8") as f:
            for c in new:
                f.write(json.dumps(asdict(c)) + "\n")

    summary = {
        "scanned_at": _now(),
        "sources": ["skill_catalog", "plugins", "configured_sources"],
        "new_candidates": len(new),
        "total_candidates": len(seen) + len(new),
    }
    # Persist last-scan summary so the home page can read it
    CANDIDATES_ROOT.mkdir(parents=True, exist_ok=True)
    LAST_SCAN_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def last_scan_summary() -> dict[str, Any] | None:
    if not LAST_SCAN_FILE.exists():
        return None
    try:
        return json.loads(LAST_SCAN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def candidates_this_week() -> int:
    """Cheap count for the Library home — # of new candidates in the last 7 days."""
    cutoff = time.time() - 7 * 86400
    n = 0
    if not CANDIDATES_ROOT.exists():
        return 0
    for p in CANDIDATES_ROOT.glob("*.jsonl"):
        if p.stat().st_mtime >= cutoff:
            try:
                n += sum(1 for line in p.read_text(encoding="utf-8").splitlines()
                              if line.strip())
            except Exception:
                pass
    return n
