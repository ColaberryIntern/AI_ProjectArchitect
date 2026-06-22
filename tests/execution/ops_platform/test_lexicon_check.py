"""Tests for the Lexicon CI gate (scripts/lexicon_check.py).

Clean/drift artifacts pass (exit 0); a forbidden term in an in-scope artifact
fails (exit 1). In-scope is decided by the glossary's scan_globs, so the temp
artifacts are created under the real agents/ directory and removed afterwards.
"""

import importlib.util

import pytest

from config.settings import PROJECT_ROOT

_SCRIPT = PROJECT_ROOT / "scripts" / "lexicon_check.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("lexicon_check", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def artifact(request):
    """Create an in-scope agents/*.md artifact with given body; remove after."""
    created = []

    def _make(body: str, name: str = "_lexgate_test"):
        p = PROJECT_ROOT / "agents" / f"{name}.md"
        p.write_text(body, encoding="utf-8")
        created.append(p)
        return f"agents/{p.name}"

    yield _make
    for p in created:
        p.unlink(missing_ok=True)


def test_usage_when_no_args():
    assert _load_gate().main([]) == 2


def test_clean_artifact_passes():
    gate = _load_gate()
    assert gate.main(["agents/project_architect.md"]) == 0


def test_forbidden_term_fails(artifact):
    rel = artifact("# Agent\n\nThis is orchestrated by Moltbot.\n")
    assert _load_gate().main([rel]) == 1


def test_drift_only_passes(artifact):
    rel = artifact("# Agent\n\nFollow the trust framework guidance.\n")
    assert _load_gate().main([rel]) == 0  # drift is advisory, not blocking


def test_out_of_scope_paths_ignored():
    # README is not in scan_globs -> nothing to gate -> pass.
    assert _load_gate().main(["README.md"]) == 0
