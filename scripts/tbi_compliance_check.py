"""CI gate for Trust Before Intelligence (TBI) compliance.

Every AI artifact added or changed in a PR MUST carry a sidecar attestation
`<artifact>.tbi.json` that:
  1. is valid JSON,
  2. validates against config/schemas/ops/tbi_attestation.schema.json,
  3. scores as `compliant` or `conditional` (NOT `non_compliant`) via the
     deterministic scorer execution/ops_platform/tbi_compliance.py.

Exit 0 = pass, non-zero = block PR merge.

Usage:
    python scripts/tbi_compliance_check.py agents/project_architect.md [more files...]

Mandated by CLAUDE.md. Procedure: directives/compliance/tbi-compliance-gate.md
"""

from __future__ import annotations

import json
import sys
from fnmatch import fnmatch
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from execution.ops_platform.tbi_compliance import evaluate_attestation  # noqa: E402

_SCHEMA_PATH = _REPO_ROOT / "config" / "schemas" / "ops" / "tbi_attestation.schema.json"
_RUNTIME_DECL_PATH = _REPO_ROOT / "config" / "tbi_runtime_agents.json"

# Path globs (repo-relative, posix) that count as AI artifacts requiring attestation.
ARTIFACT_GLOBS = (
    "agents/*.md",
    "agents/**/*.md",
    "docs/personas/*.md",
    "docs/personas/**/*.md",
    "config/blueprints/*.json",
    "config/skill_registry.json",
    "library/**/*.md",
)

_RUNTIME_ENTRYPOINTS_CACHE: set[str] | None = None


def _runtime_entrypoints() -> set[str]:
    """Runtime AI entrypoints declared in config/tbi_runtime_agents.json. These are
    gated like declarative artifacts (each needs <entrypoint>.tbi.json)."""
    global _RUNTIME_ENTRYPOINTS_CACHE
    if _RUNTIME_ENTRYPOINTS_CACHE is None:
        eps: set[str] = set()
        try:
            raw = json.loads(_RUNTIME_DECL_PATH.read_text(encoding="utf-8"))
            for a in raw.get("agents") or []:
                ep = a.get("entrypoint")
                if ep:
                    eps.add(Path(ep).as_posix())
        except (OSError, json.JSONDecodeError):
            pass
        _RUNTIME_ENTRYPOINTS_CACHE = eps
    return _RUNTIME_ENTRYPOINTS_CACHE


def _rel_posix(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def is_ai_artifact(path: Path) -> bool:
    rel = _rel_posix(path)
    if rel.endswith(".tbi.json"):
        return False  # the attestation itself is not an artifact
    if rel in _runtime_entrypoints():
        return True  # declared runtime AI agent
    return any(fnmatch(rel, g) for g in ARTIFACT_GLOBS)


def _load_schema() -> dict | None:
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError:
        return None


def check_artifact(path: Path, schema: dict | None) -> list[str]:
    """Return a list of failure strings for one artifact (empty = pass)."""
    errors: list[str] = []
    sidecar = path.with_name(path.name + ".tbi.json")
    if not sidecar.exists():
        return [f"{_rel_posix(path)}: missing TBI attestation ({sidecar.name})"]

    try:
        attestation = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"{_rel_posix(sidecar)}: not valid JSON ({e})"]

    # Schema validation (defense-in-depth; the scorer also checks structure).
    if schema is not None:
        try:
            import jsonschema
            v = jsonschema.Draft202012Validator(schema)
            for err in sorted(v.iter_errors(attestation), key=lambda e: e.absolute_path):
                loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
                errors.append(f"{_rel_posix(sidecar)}: schema {loc}: {err.message}")
        except ImportError:
            errors.append("jsonschema not installed — cannot validate attestation schema")

    verdict = evaluate_attestation(attestation)
    if verdict.verdict == "non_compliant":
        for issue in verdict.blocking_issues:
            errors.append(f"{_rel_posix(path)}: {issue}")
    return errors


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: tbi_compliance_check.py <artifact> [more...]", file=sys.stderr)
        return 2

    schema = _load_schema()
    all_errors: list[str] = []
    conditional: list[str] = []
    checked = 0

    for arg in argv:
        p = Path(arg)
        if not is_ai_artifact(p):
            continue  # CI passes many files; only AI artifacts are gated
        if not p.exists():
            continue  # deleted in the PR — nothing to attest
        checked += 1
        errs = check_artifact(p, schema)
        if errs:
            all_errors.extend(errs)
        else:
            v = evaluate_attestation(json.loads(
                p.with_name(p.name + ".tbi.json").read_text(encoding="utf-8")))
            if v.verdict == "conditional":
                conditional.append(f"{_rel_posix(p)}: conditional — {'; '.join(v.warnings)}")

    if checked == 0:
        print("No AI artifacts in arglist — TBI gate not required.")
        return 0

    for c in conditional:
        print("CONDITIONAL (passes, flagged):", c)

    if all_errors:
        for e in all_errors:
            print("FAIL:", e, file=sys.stderr)
        print(f"\nTBI compliance gate FAILED: {len(all_errors)} issue(s) across "
              f"{checked} artifact(s).", file=sys.stderr)
        return 1

    print(f"OK — {checked} AI artifact(s) passed the TBI compliance gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
