"""Tests for the canonical Lexicon checker (GOALS-Lexicon enforcement).

Verifies forbidden terms block, aliases warn (drift), clean text passes,
allow_in is honored, .tbi.json scans prose only, and the checker never raises.
"""

import json

from execution.ops_platform import lexicon as lx


# ── glossary loading ──


def test_load_lexicon_has_real_shape():
    g = lx.load_lexicon()
    assert isinstance(g.get("terms"), list) and g["terms"]
    assert isinstance(g.get("forbidden"), list) and g["forbidden"]
    assert isinstance(g.get("scan_globs"), list) and g["scan_globs"]
    assert g.get("framework_ref") == "TBI-2025.12.0"


def test_load_lexicon_bad_path_is_safe():
    g = lx.load_lexicon(path="/no/such/lexicon.json")
    assert g["terms"] == [] and g["forbidden"] == []  # safe skeleton, no raise


def test_accessors():
    assert {t["term"] for t in lx.canonical_terms()}  # non-empty set of terms
    assert any(f["term"] == "Moltbot" for f in lx.forbidden_terms())


# ── matching ──


def test_forbidden_term_blocks():
    v = lx.check_text("We orchestrate this via Moltbot nightly.", source="x.md")
    assert len(v) == 1
    assert v[0]["term"] == "Moltbot"
    assert v[0]["kind"] == "forbidden" and v[0]["severity"] == "block"


def test_alias_is_drift_warning_with_suggestion():
    v = lx.check_text("Read the trust framework for context.", source="x.md")
    drift = [x for x in v if x["kind"] == "drift"]
    assert drift and drift[0]["severity"] == "warn"
    assert drift[0]["suggestion"] == "Trust Before Intelligence"


def test_canonical_text_is_clean():
    assert lx.check_text(
        "This uses the Trust Before Intelligence attestation and the kill-switch.",
        source="x.md") == []


def test_empty_text_is_clean():
    assert lx.check_text("", source="x.md") == []


def test_allow_in_suppresses_forbidden():
    # Moltbot is allow_in CLAUDE.md (it says the project does NOT use Moltbot).
    assert lx.check_text("does not use Moltbot", source="CLAUDE.md") == []
    # ...but the same text is a block elsewhere.
    assert lx.check_text("does not use Moltbot", source="agents/x.md")


def test_word_boundary_avoids_substring_false_positive():
    # "Moltbot" must not match inside a larger token.
    assert lx.check_text("the Moltbots subsystem", source="x.md") == []


# ── file / attestation scanning ──


def test_tbi_json_scans_prose_only(tmp_path):
    # Forbidden term in a structural value (status) must NOT trip; in evidence it must.
    structural = tmp_path / "a.tbi.json"
    structural.write_text(json.dumps(
        {"goals": {"x": {"status": "Moltbot", "evidence": "clean canonical text"}}}),
        encoding="utf-8")
    assert lx.check_file(structural) == []

    prose = tmp_path / "b.tbi.json"
    prose.write_text(json.dumps(
        {"goals": {"x": {"status": "satisfied", "evidence": "runs on Moltbot"}}}),
        encoding="utf-8")
    v = lx.check_file(prose)
    assert any(x["term"] == "Moltbot" for x in v)


def test_check_file_missing_is_safe():
    assert lx.check_file("/no/such/file.md") == []


# ── repo-wide scan + summary ──


def test_repo_scans_clean():
    # The committed AI fleet must carry no forbidden/drift violations.
    assert lx.scan_artifacts() == {}
    assert lx.artifact_paths()  # something is in scope


def test_summary_shape_and_clean_status():
    s = lx.summary()
    assert {"version", "framework_ref", "term_count", "forbidden_count",
            "artifacts_scanned", "violations", "by_severity", "blocking",
            "status"} <= set(s)
    assert s["blocking"] == 0 and s["status"] == "clean"


def test_check_paths_only_in_scope():
    # A non-artifact path is ignored even if it would otherwise match.
    out = lx.check_paths(["README.md", "execution/ops_platform/lexicon.py"])
    assert out == {}  # neither is in scan_globs
