"""Parse a generated Build Guide markdown into the plan's chapter spine.

Per ``docs/specs/myday-project-build-thin-chapter-stress-test.md``: the only
reliable structure in a generated Build Guide is the chapter heading
``# Chapter {N}: {Title}``. The ``##`` subsections are a closed structure
vocabulary (Feature Specifications, Acceptance Criteria, …) — they are NOT
features. So this parser extracts the **initiative spine** (chapters) plus each
chapter's prose body and heading anchors; the actual features + BUILD/BREAK/
HARDEN todos are *generated* per chapter by ``feature_task_generator`` (the
"spine from guide + generate tasks" decision), which yields a plan that passes
the validation gate automatically.

Pure + deterministic: no I/O beyond the passed markdown string.
"""
from __future__ import annotations

import hashlib
import re

_CHAPTER_RE = re.compile(r"^#\s+Chapter\s+(\d+)\s*[:–\-]\s*(.+?)\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_ANCHOR_STRIP_RE = re.compile(r"[^\w\s-]")


def github_anchor(heading_text: str) -> str:
    """GitHub-style heading anchor: '#' + lower, punctuation dropped, spaces→'-'."""
    a = _ANCHOR_STRIP_RE.sub("", (heading_text or "").strip().lower())
    a = re.sub(r"\s+", "-", a)
    return "#" + a


def parse_build_guide(md: str) -> list[dict]:
    """Return chapters in document order: ``[{order, title, anchor, body}]``.

    ``anchor`` matches the chapter heading's GitHub anchor (so it validates
    against ``doc_anchors``); ``body`` is the prose between this chapter heading
    and the next (context for task generation).
    """
    matches = list(_CHAPTER_RE.finditer(md or ""))
    chapters: list[dict] = []
    for i, m in enumerate(matches):
        order = int(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = (md[start:end] or "").strip()
        chapters.append({
            "order": order,
            "title": title,
            "anchor": github_anchor(f"Chapter {order}: {title}"),
            "body": body,
        })
    return chapters


def doc_anchors(md: str) -> set[str]:
    """Every heading anchor present in the doc (for docAnchor validation)."""
    return {github_anchor(m.group(1)) for m in _HEADING_RE.finditer(md or "")}


def source_sha256(md: str) -> str:
    return "sha256:" + hashlib.sha256((md or "").encode("utf-8")).hexdigest()


def first_sentence(body: str, limit: int = 200) -> str:
    """A short charter line from a chapter body (first sentence, capped)."""
    text = " ".join((body or "").split())
    # drop a leading enterprise '> blockquote' purpose line if present
    text = re.sub(r"^>+\s*", "", text)
    if not text:
        return ""
    dot = text.find(". ")
    sent = text[: dot + 1] if 0 < dot < limit else text[:limit]
    return sent.strip()
