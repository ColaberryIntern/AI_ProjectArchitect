"""Drift-guard: the TBI primer must faithfully reflect the pinned vendored
framework snapshot (and, opt-in, the upstream GitHub book).

The primer (execution/advisory/tbi_primer.py) is what the generator injects into
its prompts. Its content is a hand-transcribed distillation of the canonical
snapshot directives/compliance/trust-before-intelligence.md (vendored from
github.com/colaberry/trust-before-intelligence-book @ a296fe9). This test makes
that fidelity machine-enforced: if either the primer or the snapshot changes the
INPACT / GOALS / 7-layer names (or the framework version) without the other, it
fails — so "TBI is picked up from the actual source" is verified, not assumed.
"""
import os
import re
from pathlib import Path

import pytest

import execution.advisory.tbi_primer as tp

_DIRECTIVE = Path(__file__).resolve().parents[3] / "directives" / "compliance" / "trust-before-intelligence.md"
_PINNED_COMMIT = "a296fe96910349b910655c3b6c2c857ca9d2ea23"


def _section(md: str, keyword: str) -> str:
    """Body of the first ``## ...`` section whose header contains ``keyword``."""
    for chunk in re.split(r"(?m)^##\s+", md):
        head = chunk.splitlines()[0] if chunk.splitlines() else ""
        if keyword.lower() in head.lower():
            return chunk
    return ""


def _bold_named_rows(section: str) -> set:
    """Names bolded in the 2nd column of a single-letter-keyed table row."""
    return set(re.findall(r"\|\s*[A-Z]\s*\|\s*\*\*([^*|]+?)\*\*", section))


def test_primer_framework_version_matches_snapshot_frontmatter():
    md = _DIRECTIVE.read_text(encoding="utf-8")
    m = re.search(r"(?m)^framework_version:\s*(\S+)", md)
    assert m, "snapshot frontmatter missing framework_version"
    assert m.group(1) == tp.FRAMEWORK_VERSION


def test_primer_inpact_matches_snapshot():
    md = _DIRECTIVE.read_text(encoding="utf-8")
    snapshot = _bold_named_rows(_section(md, "INPACT"))
    primer = {d for d, _, _ in tp.INPACT}
    assert snapshot == primer, f"INPACT drift: snapshot={snapshot} primer={primer}"


def test_primer_goals_matches_snapshot():
    md = _DIRECTIVE.read_text(encoding="utf-8")
    snapshot = _bold_named_rows(_section(md, "GOALS"))
    primer = {t for t, _ in tp.GOALS}
    assert snapshot == primer, f"GOALS drift: snapshot={snapshot} primer={primer}"


def test_primer_layers_match_snapshot():
    md = _DIRECTIVE.read_text(encoding="utf-8")
    section = _section(md, "7-Layer")
    snapshot = {int(n): name.strip() for n, name in re.findall(r"\|\s*L(\d)\s*\|\s*([^|]+?)\s*\|", section)}
    primer = {n: name for n, name, _ in tp.LAYERS}
    assert snapshot == primer, f"7-layer drift: snapshot={snapshot} primer={primer}"


@pytest.mark.skipif(os.environ.get("TBI_VERIFY_GITHUB") != "1",
                    reason="opt-in network check; run with TBI_VERIFY_GITHUB=1")
def test_primer_inpact_matches_upstream_github():
    """Opt-in: fetch the pinned INPACT chapter from the public book repo and
    confirm all six dimension names are present. Run in a refresh workflow to
    re-validate the snapshot against upstream before re-pinning."""
    import urllib.request
    url = ("https://raw.githubusercontent.com/colaberry/trust-before-intelligence-book/"
           f"{_PINNED_COMMIT}/archive/03_chapter_2_inpact_framework_v3_7.md")
    txt = urllib.request.urlopen(url, timeout=20).read().decode("utf-8", "replace")
    for name, _, _ in tp.INPACT:
        assert name in txt, f"INPACT dimension {name!r} not found in upstream chapter"
