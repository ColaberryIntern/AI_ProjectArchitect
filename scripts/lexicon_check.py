"""CI gate for the canonical Lexicon (GOALS-Lexicon enforcement).

Scans the AI artifacts passed on argv against the canonical glossary
(config/lexicon.json) for terminology drift:

  - **forbidden** terms (banned / deprecated vocabulary)  -> FAIL (exit 1)
  - **drift** (a non-canonical alias of a canonical term) -> warning (exit 0)

Only in-scope files are checked (the glossary's ``scan_globs`` plus any
``*.tbi.json`` attestation); other files passed by CI are ignored. Mirrors the
output/exit contract of scripts/tbi_compliance_check.py.

Exit 0 = pass (clean or drift-only), non-zero = block PR merge.

Usage:
    python scripts/lexicon_check.py agents/project_architect.md [more files...]

Mandated by CLAUDE.md (GOALS-Lexicon). Maintenance: directives/compliance/lexicon.md
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from execution.ops_platform import lexicon  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: lexicon_check.py <artifact> [more...]", file=sys.stderr)
        return 2

    scan = lexicon.check_paths(argv)
    if not scan:
        print("OK - no in-scope AI artifacts with lexicon violations.")
        return 0

    blocking: list[str] = []
    drift: list[str] = []
    for rel, viols in scan.items():
        for v in viols:
            line = (f"{rel}: '{v['term']}' - {v.get('reason') or v['kind']}"
                    + (f" (use '{v['suggestion']}')" if v.get("suggestion") else "")
                    + (f"  ...{v['excerpt']}..." if v.get("excerpt") else ""))
            (blocking if v.get("severity") == "block" else drift).append(line)

    for d in drift:
        print("DRIFT (warning):", d)

    if blocking:
        for b in blocking:
            print("FAIL:", b, file=sys.stderr)
        print(f"\nLexicon gate FAILED: {len(blocking)} forbidden-term "
              f"violation(s) across {len(scan)} file(s).", file=sys.stderr)
        return 1

    print(f"OK - lexicon gate passed ({len(drift)} drift warning(s), 0 blocking).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
