"""Parser — pulls structured fields out of fetched documents.

Handles:
    - HTML (title, meta description, h1, og:title)
    - Markdown (first H1, first paragraph, fenced code blocks)
    - JSON manifests (mcp.json, package.json, manifest.json, agent.json, etc.)

No external dependencies: uses stdlib html.parser + plain regex for Markdown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

LAYER = "product"
PRODUCT = "library"


@dataclass
class ParsedSurface:
    """Best-effort fields pulled from a raw document."""

    title: str = ""
    description: str = ""
    body_text: str = ""
    h1: str = ""
    code_blocks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    version: str = ""
    owner: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)


# ── HTML parser ───────────────────────────────────────────────────────


class _HTMLExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.og_title = ""
        self.h1 = ""
        self.in_title = False
        self.in_h1 = False
        self.body_buf: list[str] = []
        self.in_body = False
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
        elif tag == "h1" and not self.h1:
            self.in_h1 = True
        elif tag == "body":
            self.in_body = True
        elif tag == "meta":
            name = (a.get("name") or "").lower()
            prop = (a.get("property") or "").lower()
            content = a.get("content") or ""
            if name == "description" and not self.meta_description:
                self.meta_description = content
            if prop == "og:title" and not self.og_title:
                self.og_title = content
            if prop == "og:description" and not self.meta_description:
                self.meta_description = content

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag == "title":
            self.in_title = False
        elif tag == "h1":
            self.in_h1 = False

    def handle_data(self, data):
        if self.skip_depth > 0:
            return
        if self.in_title and len(self.title) < 200:
            self.title += data
        if self.in_h1 and len(self.h1) < 200:
            self.h1 += data
        if self.in_body and len(self.body_buf) < 200:
            stripped = data.strip()
            if stripped:
                self.body_buf.append(stripped)


def parse_html(content: str) -> ParsedSurface:
    p = _HTMLExtractor()
    try:
        p.feed(content)
    except Exception:
        pass
    body_text = " ".join(p.body_buf)[:1000]
    return ParsedSurface(
        title=(p.title or p.og_title or p.h1).strip(),
        description=p.meta_description.strip() or body_text[:400],
        body_text=body_text,
        h1=p.h1.strip(),
    )


# ── Markdown parser ───────────────────────────────────────────────────


_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.S)
_H1_RE = re.compile(r"^#\s+(.+)$", re.M)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_markdown(content: str) -> ParsedSurface:
    # Strip YAML frontmatter if present, but extract relevant fields
    fm: dict[str, Any] = {}
    m = _FRONTMATTER_RE.match(content)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip().lower()] = v.strip().strip('"').strip("'")
        content = content[m.end():]

    # First H1
    h1_m = _H1_RE.search(content)
    h1 = h1_m.group(1).strip() if h1_m else ""

    # First non-empty paragraph
    after_h1 = content[h1_m.end():] if h1_m else content
    paragraph = ""
    for chunk in after_h1.split("\n\n"):
        cleaned = chunk.strip()
        # Skip code blocks and obvious headings
        if cleaned and not cleaned.startswith("```") and not cleaned.startswith("#"):
            paragraph = cleaned[:500]
            break

    code_blocks = [b.strip() for b in _FENCE_RE.findall(content)][:5]
    tags = []
    if "tags" in fm:
        tags = [t.strip() for t in fm["tags"].split(",") if t.strip()]

    return ParsedSurface(
        title=fm.get("title") or fm.get("name") or h1,
        description=fm.get("description") or paragraph,
        body_text=content[:1500],
        h1=h1,
        code_blocks=code_blocks,
        tags=tags,
        version=fm.get("version", ""),
        owner=fm.get("owner") or fm.get("author", ""),
    )


# ── JSON manifest parser ──────────────────────────────────────────────


def parse_json_manifest(content: str) -> ParsedSurface:
    try:
        data = json.loads(content)
    except Exception:
        return ParsedSurface()
    if not isinstance(data, dict):
        return ParsedSurface(manifest={"value": data})

    title = (data.get("name") or data.get("title") or data.get("id") or "").strip()
    desc = (data.get("description") or data.get("summary") or "").strip()
    tags = data.get("tags") or data.get("keywords") or []
    if not isinstance(tags, list):
        tags = []
    version = str(data.get("version") or "")
    owner = (data.get("owner") or data.get("author") or
                  (data.get("authors") or [{}])[0].get("name", "")
                  if isinstance(data.get("authors"), list) else
                  data.get("author") or "")
    if isinstance(owner, dict):
        owner = owner.get("name", "")

    return ParsedSurface(
        title=title,
        description=desc,
        body_text=json.dumps(data, indent=2)[:1500],
        tags=[str(t) for t in tags][:10],
        version=version,
        owner=str(owner),
        manifest=data,
    )


# ── Dispatcher ────────────────────────────────────────────────────────


def parse(content: str, content_type: str = "",
            path: str | None = None) -> ParsedSurface:
    """Pick the right parser based on content type / file extension."""
    ct = (content_type or "").lower()
    p = (path or "").lower()

    if "json" in ct or p.endswith(".json"):
        return parse_json_manifest(content)
    if "markdown" in ct or p.endswith((".md", ".markdown", ".mdx")):
        return parse_markdown(content)
    if "html" in ct or p.endswith((".html", ".htm")) or "<html" in content[:200].lower():
        return parse_html(content)

    # Default: try markdown then HTML then JSON as fallback chain
    md = parse_markdown(content)
    if md.title or md.description:
        return md
    return parse_html(content)
