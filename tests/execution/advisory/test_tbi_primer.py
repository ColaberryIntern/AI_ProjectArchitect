"""Tests for the canonical TBI primer used to ground the deep-plan generator."""

import execution.advisory.tbi_primer as tp
from execution.ops_platform.tbi_compliance import CURRENT_FRAMEWORK_VERSION


def test_framework_version_tracks_the_pinned_snapshot():
    # If the framework is refreshed, this fails → the primer must be re-derived
    # from the new snapshot (CLAUDE.md self-annealing loop).
    assert tp.FRAMEWORK_VERSION == CURRENT_FRAMEWORK_VERSION


def test_prompt_primer_grounds_all_dimensions():
    p = tp.prompt_primer()
    assert tp.FRAMEWORK_VERSION in p
    for dim, _, _ in tp.INPACT:
        assert dim in p
    for target, _ in tp.GOALS:
        assert target in p
    for n, name, _ in tp.LAYERS:
        assert f"L{n}" in p and name.split()[0] in p
    # the buildable controls are named so a trust scenario can assert one
    for control, _ in tp.BUILD_PATTERNS:
        assert control in p


def test_inpact_goals_layers_are_complete():
    assert len(tp.INPACT) == 6
    assert len(tp.GOALS) == 5
    assert [n for n, _, _ in tp.LAYERS] == [1, 2, 3, 4, 5, 6, 7]


def test_primer_markdown_is_self_contained_and_cited():
    md = tp.primer_markdown(" AcmeCo ")
    assert "AcmeCo" in md
    assert "INPACT" in md and "GOALS" in md and "7-Layer" in md
    assert "Audit log" in md and "Approval gate" in md and "Governance score" in md
    # cites the canonical source + the pin, so a builder knows where it came from
    assert "trust-before-intelligence" in md
    assert tp.FRAMEWORK_VERSION in md
