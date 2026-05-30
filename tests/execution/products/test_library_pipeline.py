"""Pipeline tests — parser, enricher, fetcher URL parsing, trusted-sources,
and end-to-end ingest with a monkeypatched fetcher (no network).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.products.library import (
    enricher, fetcher, ingest, parser, store, trusted,
)
from execution.products.library.fetcher import RawDocument, FetchResult


# ── Parser ─────────────────────────────────────────────────────────────


def test_parse_html_extracts_title_meta_h1():
    html_doc = """<!doctype html><html><head>
        <title>RFP Analyzer</title>
        <meta name="description" content="Summarizes a 20-page RFP into 3 paragraphs.">
        </head><body><h1>RFP Analyzer</h1><p>Built for sales.</p></body></html>"""
    p = parser.parse_html(html_doc)
    assert p.title == "RFP Analyzer"
    assert "Summarizes" in p.description
    assert p.h1 == "RFP Analyzer"


def test_parse_markdown_extracts_h1_paragraph_codeblocks():
    md = "# Sales Prompt\n\nUse this for cold outreach.\n\n```python\nprint('hi')\n```\n"
    p = parser.parse_markdown(md)
    assert p.h1 == "Sales Prompt"
    assert "cold outreach" in p.description
    assert any("print" in c for c in p.code_blocks)


def test_parse_markdown_handles_frontmatter():
    md = "---\ntitle: Cool Prompt\ntags: sales, gpt-4\nversion: 2.0\n---\n# Body H1\n\nFirst paragraph."
    p = parser.parse_markdown(md)
    assert p.title == "Cool Prompt"
    assert "sales" in p.tags and "gpt-4" in p.tags
    assert p.version == "2.0"


def test_parse_json_manifest_extracts_fields():
    j = json.dumps({"name": "MCP X", "description": "thing",
                          "version": "1.2", "tags": ["mcp", "a"], "author": "alice"})
    p = parser.parse_json_manifest(j)
    assert p.title == "MCP X"
    assert p.description == "thing"
    assert p.version == "1.2"
    assert "mcp" in p.tags
    assert p.owner == "alice"


def test_parse_dispatcher_picks_right_parser():
    assert parser.parse("# H1", "", "/x.md").h1 == "H1"
    j = parser.parse('{"name":"X","description":"d"}', "application/json")
    assert j.title == "X"
    h = parser.parse("<html><title>T</title></html>", "text/html")
    assert h.title == "T"


# ── Enricher ───────────────────────────────────────────────────────────


def test_enricher_routes_to_correct_category_via_classifier():
    p = parser.parse_markdown("# MCP Slack Server\n\nMCP server for Slack.")
    a = enricher.enrich(p, raw_content="# MCP Slack Server\n\nMCP server for Slack.",
                              source_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack")
    assert a.category == "mcp"
    assert a.name == "MCP Slack Server"
    assert "Slack" in a.description


def test_enricher_extracts_how_to_use_from_usage_section():
    md = """# Tool

A tool.

## How to Use

Run `python tool.py --input file.txt`. It writes to stdout.

## Other section

unrelated text"""
    p = parser.parse_markdown(md)
    a = enricher.enrich(p, raw_content=md, source_url="https://x.com/tool.md")
    assert "tool.py" in a.how_to_use


def test_enricher_warns_when_description_is_too_short():
    p = parser.parse_markdown("# X\n\nshort")
    a = enricher.enrich(p, raw_content="# X\n\nshort", source_url="x")
    assert any("description" in w for w in a.warnings)


def test_enricher_computes_quality_score():
    md = """---
title: Rich Asset
version: 2.0
owner: alice
tags: a, b, c
---
# Rich Asset

This is a sufficiently long description that meets the 40-character bar for quality scoring.

## How to Use

Detailed usage instructions also exceeding the 20-character bar.

## Example

```python
example_code()
```"""
    p = parser.parse_markdown(md)
    a = enricher.enrich(p, raw_content=md,
                              source_url="https://github.com/colaberry/repo/blob/main/x.md")
    assert a.quality_score >= 0.7


# ── Fetcher URL parsing ────────────────────────────────────────────────


def test_parse_github_url_basic():
    p = fetcher.parse_github_url("https://github.com/owner/repo")
    assert p == {"owner": "owner", "repo": "repo", "ref": None, "path": None}


def test_parse_github_url_with_ref_and_path():
    p = fetcher.parse_github_url("https://github.com/owner/repo/tree/main/src/foo")
    assert p["owner"] == "owner"
    assert p["repo"] == "repo"
    assert p["ref"] == "main"
    assert p["path"] == "src/foo"


def test_parse_github_url_rejects_non_github():
    assert fetcher.parse_github_url("https://example.com/foo") is None


def test_match_path_pattern_recognizes_known_layouts():
    assert fetcher.match_path_pattern("src/skills/foo.json")[0] == "skills"
    assert fetcher.match_path_pattern("prompts/sales.md")[0] == "prompts"
    assert fetcher.match_path_pattern("agents/coder.md")[0] == "agents"
    assert fetcher.match_path_pattern("workflows/deploy.yaml")[0] == "workflows"
    assert fetcher.match_path_pattern("mcp.json")[0] == "mcp"
    assert fetcher.match_path_pattern("manifest.json")[0] == "capabilities"
    assert fetcher.match_path_pattern("random/file.txt") is None


def test_filter_interesting_files_caps_and_sorts_by_weight():
    tree = [
        {"path": "skills/a.json",     "type": "blob", "size": 1000, "sha": "s1"},
        {"path": "README.md",         "type": "blob", "size": 5000, "sha": "s2"},
        {"path": "ignore/me.txt",     "type": "blob", "size": 500,  "sha": "s3"},
        {"path": "mcp.json",          "type": "blob", "size": 800,  "sha": "s4"},
        {"path": "prompts/sales.md",  "type": "blob", "size": 1200, "sha": "s5"},
    ]
    f = fetcher.filter_interesting_files(tree, max_files=10)
    paths = [e["path"] for e in f]
    assert "ignore/me.txt" not in paths
    assert "mcp.json" in paths
    assert "skills/a.json" in paths
    # mcp.json has weight 9; README has weight 4 — mcp should come first
    assert paths.index("mcp.json") < paths.index("README.md")


# ── Trusted sources ────────────────────────────────────────────────────


def test_trusted_allows_known_pattern(monkeypatch, tmp_path):
    allow = tmp_path / "trusted.json"
    allow.write_text(json.dumps([
        {"pattern": "github\\.com/colaberry/", "reason": "in-house"},
    ]))
    monkeypatch.setattr(trusted, "ALLOWLIST_PATH", allow)
    ok, reason = trusted.is_trusted("https://github.com/colaberry/foo")
    assert ok and reason == "in-house"
    ok2, _ = trusted.is_trusted("https://github.com/random/foo")
    assert not ok2


def test_should_auto_vet_combines_trust_and_confidence(monkeypatch, tmp_path):
    allow = tmp_path / "trusted.json"
    allow.write_text(json.dumps([{"pattern": "trusted\\.com", "reason": "yes"}]))
    monkeypatch.setattr(trusted, "ALLOWLIST_PATH", allow)
    # Trusted + high confidence → auto-vet
    ok, _ = trusted.should_auto_vet("https://trusted.com/x", 0.9)
    assert ok
    # Trusted + low confidence → no
    ok2, _ = trusted.should_auto_vet("https://trusted.com/x", 0.3)
    assert not ok2
    # Untrusted → no regardless
    ok3, _ = trusted.should_auto_vet("https://other.com/x", 0.99)
    assert not ok3


# ── End-to-end ingest with monkeypatched fetcher ───────────────────────


@pytest.fixture
def isolated_library(tmp_path, monkeypatch):
    """Point all Library persistence at a tmp dir."""
    monkeypatch.setattr(store, "LIB_ROOT", tmp_path / "lib")
    monkeypatch.setattr(ingest, "INGEST_ROOT", tmp_path / "lib" / "_ingestion")
    (tmp_path / "lib" / "_ingestion").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def test_ingest_url_single_md(monkeypatch, isolated_library):
    md = ("# Cool Prompt\n\nSummarize a 20-page RFP into 3 paragraphs. "
            "Built for sales teams.\n\n## How to Use\n\nReplace {rfp_text}.\n\n"
            "```python\nprompt.format(rfp_text=text)\n```\n")
    monkeypatch.setattr(fetcher, "fetch_url", lambda url: FetchResult(
        ok=True, document=RawDocument(
            source_url=url, content=md, content_type="text/markdown"),
    ))
    h = ingest.ingest_url("global", "tester@example.com", "https://example.com/cool.md")
    # Wait for the background thread
    import time
    for _ in range(40):
        status = ingest.batch_status(h.batch_id)
        if status["header"]["status"] == "done":
            break
        time.sleep(0.1)
    status = ingest.batch_status(h.batch_id)
    assert status["header"]["status"] == "done"
    assert status["total"] == 1
    items = status["recent"]
    submitted = [i for i in items if i["status"] == "submitted"]
    assert len(submitted) == 1
    s = submitted[0]
    assert s["category"] == "prompts"   # classifier sees 'Prompt' in the name
    assert s["asset_name"] == "Cool Prompt"


def test_ingest_handles_fetch_failure(monkeypatch, isolated_library):
    monkeypatch.setattr(fetcher, "fetch_url", lambda url: FetchResult(
        ok=False, error="HTTP 404: Not Found",
    ))
    h = ingest.ingest_url("global", "x@y.com", "https://example.com/missing")
    import time
    for _ in range(40):
        if ingest.batch_status(h.batch_id)["header"]["status"] == "done":
            break
        time.sleep(0.1)
    status = ingest.batch_status(h.batch_id)
    items = status["recent"]
    failed = [i for i in items if i["status"] == "failed"]
    assert len(failed) == 1
    assert "404" in failed[0]["error"]
