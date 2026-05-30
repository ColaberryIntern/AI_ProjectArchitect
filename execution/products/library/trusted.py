"""Trusted-source allowlist for auto-vetting on ingest.

Driven by config/library_trusted_sources.json. If a source URL matches any
allowlisted pattern (and the classifier's confidence >= 0.75), the resulting
asset bypasses the pending-review queue and gets auto-marked Colaberry-vetted.

Default: empty allowlist → nothing auto-vets. Operator must explicitly add
sources they trust.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
ALLOWLIST_PATH = ROOT / "config" / "library_trusted_sources.json"

# Minimum classifier confidence to auto-vet a trusted-source asset.
AUTOVET_CONFIDENCE_THRESHOLD = 0.75


def _load_allowlist() -> list[dict[str, str]]:
    if not ALLOWLIST_PATH.exists():
        return []
    try:
        data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("pattern")]
    except Exception:
        pass
    return []


def is_trusted(source_url: str) -> tuple[bool, str | None]:
    """Return (trusted?, reason) for a given source URL."""
    if not source_url:
        return (False, None)
    for entry in _load_allowlist():
        pattern = entry.get("pattern", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, source_url):
                return (True, entry.get("reason") or pattern)
        except re.error:
            # Treat as literal substring match if regex is malformed
            if pattern in source_url:
                return (True, entry.get("reason") or pattern)
    return (False, None)


def should_auto_vet(source_url: str, classifier_confidence: float) -> tuple[bool, str | None]:
    """Combine trust + confidence to decide whether to auto-vet."""
    trusted, reason = is_trusted(source_url)
    if not trusted:
        return (False, None)
    if classifier_confidence < AUTOVET_CONFIDENCE_THRESHOLD:
        return (False,
                  f"trusted source ({reason}) but classifier confidence {classifier_confidence:.2f} "
                  f"< {AUTOVET_CONFIDENCE_THRESHOLD} threshold")
    return (True, f"trusted source: {reason}")
