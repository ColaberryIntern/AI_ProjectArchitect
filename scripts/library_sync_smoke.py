"""[Infra 2] CI smoke-test gate for library sync PRs.

A synced library artifact MUST:
  1. Be valid UTF-8
  2. Have YAML frontmatter delimited by `---` on the first line
  3. Frontmatter contains required keys: title, kind, slug, version, owner
  4. Body after frontmatter is non-empty
  5. No accidental secret patterns (BEARER, ghp_, sk-, AKIA...)

Exit 0 = pass, non-zero = block PR merge.

Usage:
    python scripts/library_sync_smoke.py library/skills/foo.md [more files...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_KEYS = ("title", "kind", "slug", "version", "owner")
SECRET_PATTERNS = [
    (re.compile(r"\bBEARER\s+[A-Za-z0-9._-]{20,}", re.I), "Bearer token"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),               "GitHub PAT"),
    (re.compile(r"\bsk-[A-Za-z0-9]{30,}\b"),                "OpenAI/Anthropic-style key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                   "AWS access key"),
    (re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),     "Private key"),
]


def _split_frontmatter(text: str) -> tuple[dict, str] | tuple[None, str]:
    if not text.startswith("---"):
        return (None, text)
    # find closing ---
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return (None, text)
    raw = rest[:end].strip()
    body = rest[end + 4:].lstrip("\n")
    fm = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        fm[key.strip()] = val.strip().strip("\"'")
    return (fm, body)


def check_file(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"{path}: file not found"]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        return [f"{path}: not valid UTF-8 ({e})"]
    fm, body = _split_frontmatter(text)
    if fm is None:
        errors.append(f"{path}: missing YAML frontmatter delimited by '---'")
        return errors  # remaining checks need fm
    for key in REQUIRED_KEYS:
        if key not in fm or not fm[key]:
            errors.append(f"{path}: frontmatter missing required key '{key}'")
    if not body.strip():
        errors.append(f"{path}: body after frontmatter is empty")
    # Secret scanning — surface only the pattern name, never the matched text
    for pat, label in SECRET_PATTERNS:
        if pat.search(text):
            errors.append(f"{path}: possible secret detected ({label}) — refusing to sync")
    return errors


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: library_sync_smoke.py <file.md> [more.md ...]", file=sys.stderr)
        return 2
    all_errors: list[str] = []
    files_checked = 0
    for arg in argv:
        p = Path(arg)
        if not p.suffix == ".md":
            # CI may pass non-markdown files; skip silently
            continue
        files_checked += 1
        all_errors.extend(check_file(p))
    if files_checked == 0:
        print("no .md files in arglist — pass-through OK")
        return 0
    if all_errors:
        for e in all_errors:
            print("FAIL:", e, file=sys.stderr)
        print(f"\n{len(all_errors)} smoke-test failure(s) across {files_checked} file(s)",
                  file=sys.stderr)
        return 1
    print(f"OK — {files_checked} file(s) passed library sync smoke test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
