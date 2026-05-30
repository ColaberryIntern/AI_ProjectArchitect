"""Apply the deterministic citation injector to an existing build.

This validates the injector on real LLM-generated chapter output without
spending another $0.01 / 11 minutes on a fresh build. Reports
before/after citation density per chapter.

Usage: python -m scripts.apply_citations_to_existing_build [slug]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import OUTPUT_DIR
from execution.citation_injector import inject_citations
from execution.requirements_writer import read_requirements


def count_citations(text: str) -> dict:
    return {
        "req": len(re.findall(r"\[REQ-\d+\]", text)),
        "req_unique": len(set(re.findall(r"\[REQ-\d+\]", text))),
        "ac": len(re.findall(r"\[AC-\d+-\d+\]", text)),
    }


def main(slug: str = "spec-test-freight") -> int:
    requirements_doc = read_requirements(slug)
    if not requirements_doc:
        print(f"ERROR: no requirements.json found for {slug}")
        return 1

    requirements = requirements_doc["requirements"]
    chapters_dir = OUTPUT_DIR / slug / "chapters"
    if not chapters_dir.exists():
        print(f"ERROR: no chapters dir at {chapters_dir}")
        return 1

    print("=" * 72)
    print(f"CITATION INJECTOR — {slug}")
    print("=" * 72)
    print(f"Loaded {len(requirements)} Requirements from {chapters_dir.parent}/specs/requirements.json")
    print()

    print(f"{'chapter':<8}  {'words':>6}  before          after           injected")
    print("-" * 72)

    total_before_req = 0
    total_after_req = 0
    total_before_ac = 0
    total_after_ac = 0
    total_words = 0

    for chapter_path in sorted(chapters_dir.glob("ch*.md")):
        idx = int(re.findall(r"\d+", chapter_path.stem)[0])
        text = chapter_path.read_text(encoding="utf-8")
        before = count_citations(text)
        word_count = len(text.split())
        total_before_req += before["req"]
        total_before_ac += before["ac"]
        total_words += word_count

        # Find Requirements traced to this chapter (by chapter_ids match)
        linked = [
            r for r in requirements
            if str(idx) in ((r.get("traces_to") or {}).get("chapter_ids") or [])
        ]

        new_text, report = inject_citations(text, linked)
        after = count_citations(new_text)
        total_after_req += after["req"]
        total_after_ac += after["ac"]

        # Write back
        chapter_path.write_text(new_text, encoding="utf-8")

        print(
            f"ch{idx:<6}  {word_count:>6}  "
            f"{before['req']} REQ + {before['ac']} AC".ljust(15) + "  "
            f"{after['req']} REQ + {after['ac']} AC".ljust(15) + "  "
            f"+{len(report.injected)} REQ, +{len(report.ac_injected)} AC"
            + (f", unmatched: {report.unmatched}" if report.unmatched else "")
        )

    print("-" * 72)
    print(
        f"TOTAL   {total_words:>6}  "
        f"{total_before_req} REQ + {total_before_ac} AC".ljust(15) + "  "
        f"{total_after_req} REQ + {total_after_ac} AC".ljust(15) + "  "
        f"+{total_after_req - total_before_req} REQ, "
        f"+{total_after_ac - total_before_ac} AC"
    )
    print()
    print("Citation density (per 1000 words):")
    print(f"  before: {1000 * total_before_req / total_words:.2f} REQ refs / 1K words")
    print(f"  after:  {1000 * total_after_req / total_words:.2f} REQ refs / 1K words")
    print()
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "spec-test-freight"))
