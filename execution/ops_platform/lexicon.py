"""Lexicon enforcement — the deterministic check behind GOALS-Lexicon.

The TBI framework defines **Lexicon** as "a consistent, shared vocabulary;
terminology does not drift across artifacts". This module turns that from prose
into signal: it loads the canonical glossary (``config/lexicon.json``) and scans
AI artifacts for

  - **forbidden** terms (banned / deprecated vocabulary)  -> severity ``block``
  - **drift**: a non-canonical *alias* of a canonical term -> severity ``warn``
    (suggests the canonical term)

Design (per CLAUDE.md "prefer deterministic verification"): pure-Python, LLM-free,
and it **never raises** — every public function returns a structured result even
when the glossary is missing or a file is unreadable. This is the contract the CI
gate (``scripts/lexicon_check.py``) and the Trust Command Center depend on.

Canonical glossary:  config/lexicon.json
Framework:           directives/compliance/trust-before-intelligence.md
Maintenance:         directives/compliance/lexicon.md
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

_LEXICON_PATH = PROJECT_ROOT / "config" / "lexicon.json"

# Keys whose string values are prose worth scanning inside a .tbi.json attestation
# (avoids matching structural enum values like "satisfied" / "compliant").
_PROSE_KEY = re.compile(r"evidence|note|summary|justif|rationale|desc|reason", re.IGNORECASE)

_EXCERPT_PAD = 40

_LEXICON_CACHE: dict | None = None


# ── Glossary loading ────────────────────────────────────────────────────


def load_lexicon(path: str | Path | None = None) -> dict:
    """Return the parsed glossary. Cached for the default path; an explicit
    ``path`` (used by tests) bypasses the cache. Never raises — returns an empty
    glossary skeleton on any failure."""
    global _LEXICON_CACHE
    if path is None and _LEXICON_CACHE is not None:
        return _LEXICON_CACHE
    target = Path(path) if path is not None else _LEXICON_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("lexicon root is not an object")
    except Exception:
        logger.warning("lexicon: could not load %s", target, exc_info=True)
        data = {"version": "0", "framework_ref": None, "scan_globs": [],
                "terms": [], "forbidden": []}
    if path is None:
        _LEXICON_CACHE = data
    return data


def canonical_terms() -> list[dict]:
    """The preferred-vocabulary entries (term + definition + aliases)."""
    return list(load_lexicon().get("terms") or [])


def forbidden_terms() -> list[dict]:
    """The banned/deprecated entries (term + reason + allow_in)."""
    return list(load_lexicon().get("forbidden") or [])


# ── Matching ────────────────────────────────────────────────────────────


@lru_cache(maxsize=512)
def _term_regex(term: str) -> "re.Pattern[str]":
    """Case-insensitive, whitespace-flexible, token-boundary-aware matcher for a
    term or alias. Word boundaries are applied only on alphanumeric edges so a
    punctuation-led token like ``+ai`` still matches correctly."""
    parts = [re.escape(p) for p in term.split()]
    body = r"\s+".join(parts) if parts else re.escape(term)
    left = r"(?<![0-9A-Za-z_])" if term[:1].isalnum() or term[:1] == "_" else ""
    right = r"(?![0-9A-Za-z_])" if term[-1:].isalnum() or term[-1:] == "_" else ""
    return re.compile(left + body + right, re.IGNORECASE)


def _excerpt(text: str, match: "re.Match[str]") -> str:
    start = max(0, match.start() - _EXCERPT_PAD)
    end = min(len(text), match.end() + _EXCERPT_PAD)
    return " ".join(text[start:end].split())


def check_text(text: str, *, source: str | None = None) -> list[dict]:
    """Scan a blob of text against the glossary. Returns a list of violations:
    ``{term, kind: 'forbidden'|'drift', severity: 'block'|'warn', suggestion,
    reason, excerpt, source}``. One hit per term/alias is enough signal."""
    if not text:
        return []
    out: list[dict] = []
    lex = load_lexicon()

    for entry in lex.get("forbidden") or []:
        term = (entry or {}).get("term")
        if not term:
            continue
        if source and source in (entry.get("allow_in") or []):
            continue
        try:
            m = _term_regex(term).search(text)
        except re.error:
            continue
        if m:
            out.append({
                "term": term, "kind": "forbidden", "severity": "block",
                "suggestion": None, "reason": entry.get("reason"),
                "excerpt": _excerpt(text, m), "source": source,
            })

    for entry in lex.get("terms") or []:
        canonical = (entry or {}).get("term")
        for alias in entry.get("aliases") or []:
            if not alias:
                continue
            try:
                m = _term_regex(alias).search(text)
            except re.error:
                continue
            if m:
                out.append({
                    "term": alias, "kind": "drift", "severity": "warn",
                    "suggestion": canonical,
                    "reason": f"non-canonical term; prefer '{canonical}'",
                    "excerpt": _excerpt(text, m), "source": source,
                })
    return out


# ── File / artifact scanning ────────────────────────────────────────────


def _rel_posix(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return Path(path).as_posix()


def _prose_strings(obj) -> list[str]:
    """Collect prose string values (evidence/notes/etc.) from a parsed
    attestation, ignoring structural enum values."""
    found: list[str] = []

    def walk(node, key_hint: str | None):
        if isinstance(node, str):
            if key_hint and _PROSE_KEY.search(key_hint):
                found.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for item in node:
                walk(item, key_hint)

    walk(obj, None)
    return found


def check_file(path: str | Path) -> list[dict]:
    """Scan one file. For ``*.tbi.json`` only the prose (evidence/notes) is
    scanned; for other artifacts the whole text is scanned. Honors ``allow_in``
    via the relative source path. Never raises."""
    p = Path(path)
    rel = _rel_posix(p)
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    if rel.endswith(".tbi.json"):
        try:
            parsed = json.loads(raw)
            text = "\n".join(_prose_strings(parsed))
        except Exception:
            text = raw
    else:
        text = raw
    return check_text(text, source=rel)


def artifact_paths(root: str | Path | None = None) -> list[str]:
    """Repo-relative posix paths of every in-scope artifact (per scan_globs)."""
    base = Path(root) if root is not None else PROJECT_ROOT
    lex = load_lexicon()
    seen: list[str] = []
    seen_set: set[str] = set()
    for glob in lex.get("scan_globs") or []:
        try:
            matches = sorted(base.glob(glob))
        except Exception:
            continue
        for m in matches:
            if not m.is_file():
                continue
            rel = _rel_posix(m)
            if rel not in seen_set:
                seen_set.add(rel)
                seen.append(rel)
    return seen


def scan_artifacts(root: str | Path | None = None) -> dict[str, list[dict]]:
    """Scan every in-scope artifact. Returns {rel_path: [violations]} for files
    that have at least one violation."""
    base = Path(root) if root is not None else PROJECT_ROOT
    out: dict[str, list[dict]] = {}
    for rel in artifact_paths(base):
        v = check_file(base / rel)
        if v:
            out[rel] = v
    return out


def check_paths(paths) -> dict[str, list[dict]]:
    """Scan an explicit list of paths (used by the CI gate over changed files).
    Only in-scope files (scan_globs / .tbi.json) are checked."""
    lex = load_lexicon()
    globs = lex.get("scan_globs") or []
    out: dict[str, list[dict]] = {}
    for path in paths or []:
        p = Path(path)
        rel = _rel_posix(p)
        in_scope = rel.endswith(".tbi.json") or any(
            __import__("fnmatch").fnmatch(rel, g) for g in globs)
        if not in_scope or not p.exists():
            continue
        v = check_file(p)
        if v:
            out[rel] = v
    return out


def summary(root: str | Path | None = None) -> dict:
    """Glossary stats + a live violation scan, for the Trust Command Center."""
    lex = load_lexicon()
    try:
        scan = scan_artifacts(root)
        scanned = len(artifact_paths(root))
    except Exception:
        logger.warning("lexicon.summary scan failed", exc_info=True)
        scan, scanned = {}, 0
    all_v = [v for vs in scan.values() for v in vs]
    by_sev: dict[str, int] = {}
    for v in all_v:
        sev = v.get("severity", "warn")
        by_sev[sev] = by_sev.get(sev, 0) + 1
    blocking = by_sev.get("block", 0)
    return {
        "version": lex.get("version"),
        "framework_ref": lex.get("framework_ref"),
        "term_count": len(lex.get("terms") or []),
        "forbidden_count": len(lex.get("forbidden") or []),
        "artifacts_scanned": scanned,
        "files_with_violations": len(scan),
        "violations": len(all_v),
        "by_severity": by_sev,
        "blocking": blocking,
        "drift": by_sev.get("warn", 0),
        "status": "clean" if blocking == 0 else "violations",
    }
