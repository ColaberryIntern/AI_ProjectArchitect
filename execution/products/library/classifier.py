"""Library asset classifier.

Decides which Library category a raw asset belongs to. Used by:
    - inventory.py (re-routing the existing catalog)
    - ingest.py (categorizing newly-fetched URLs / repos)

Rule precedence (first match wins):
    1. Explicit `kind` field on the raw item
    2. Explicit `category` field if it maps to a known Library category
    3. Source-URL host hints (modelcontextprotocol.io etc.)
    4. Name patterns (starts with 'MCP', contains 'Agent', etc.)
    5. Tag hints
    6. Manifest-shape hints (mcp.json, agent.md, prompt.md, etc.)
    7. Default: skills (with low confidence)

Returns ClassificationResult so curators can sanity-check the reasoning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

LAYER = "product"
PRODUCT = "library"

# ── Known Library categories (must match inventory.CATEGORIES keys) ─────
KNOWN_CATEGORIES: set[str] = {
    "skills", "agents", "prompts", "mcp", "capabilities", "templates",
    "policies", "workflows", "projections", "recovery", "chaos",
    "governance", "evals", "connectors", "adapters",
}


@dataclass
class ClassificationResult:
    category: str           # one of KNOWN_CATEGORIES
    confidence: float       # 0.0 to 1.0
    reasons: list[str] = field(default_factory=list)
    runner_up: str | None = None  # second-best guess (for ambiguous items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": round(self.confidence, 2),
            "reasons": list(self.reasons),
            "runner_up": self.runner_up,
        }


# ── Category mapping table (existing → canonical) ─────────────────────

_CATEGORY_NORMALIZE: dict[str, str] = {}
for canonical, aliases in {
    "mcp": [
        "mcp", "mcp servers", "mcp-server", "mcp_server",
        "mcp servers — 🌎 community servers", "mcp servers - community",
        "mcp servers — official", "mcp servers — official servers",
        "model context protocol", "modelcontextprotocol",
    ],
    "skills": ["skill", "skills"],
    "agents": [
        "agent", "agents", "ai agent", "ai agents", "autonomous agent",
        "agent persona", "personas",
    ],
    "prompts": [
        "prompt", "prompts", "prompt template", "prompt templates",
        "prompt-template", "prompt-templates",
        "system prompt", "user prompt",
    ],
    "capabilities": [
        "capability", "capabilities", "plugin", "plugins", "tool",
    ],
    "templates": [
        "template", "templates", "blueprint", "blueprints",
        "project template", "project blueprint",
    ],
    "policies": ["policy", "policies", "rule", "rules"],
    "workflows": [
        "workflow", "workflows", "orchestration", "orchestrations",
        "pipeline", "pipelines",
    ],
    "projections": ["projection", "projections", "read model"],
    "recovery": ["recovery", "recovery playbook", "runbook"],
    "chaos": ["chaos", "chaos drill", "fault injection"],
    "governance": [
        "governance", "scorecard", "scorecards", "compliance",
    ],
    "evals": [
        "eval", "evals", "evaluation", "evaluations",
        "evaluation dataset", "benchmark",
    ],
    "connectors": ["connector", "connectors", "integration"],
    "adapters": ["adapter", "adapters", "tool adapter"],
}.items():
    for a in aliases:
        _CATEGORY_NORMALIZE[a.lower()] = canonical


def _normalize_category_string(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.lower().strip()
    if key in _CATEGORY_NORMALIZE:
        return _CATEGORY_NORMALIZE[key]
    # Try prefix matches ("MCP Servers — Community" → "mcp servers" prefix)
    for prefix in ("mcp servers", "mcp server"):
        if key.startswith(prefix):
            return "mcp"
    for prefix in ("ai agent", "agent"):
        if key.startswith(prefix):
            return "agents"
    return None


# ── Source-URL hints ───────────────────────────────────────────────────

_URL_HINTS: list[tuple[str, str, str]] = [
    ("modelcontextprotocol.io", "mcp", "source URL is the MCP registry"),
    ("github.com/modelcontextprotocol/", "mcp", "lives in modelcontextprotocol GitHub org"),
    ("mcp.so", "mcp", "lives on mcp.so registry"),
    ("github.com/anthropics/anthropic-cookbook", "prompts", "Anthropic cookbook (prompts/recipes)"),
    ("/prompts/", "prompts", "URL path contains /prompts/"),
    ("/agents/", "agents", "URL path contains /agents/"),
    ("/skills/", "skills", "URL path contains /skills/"),
    ("/workflows/", "workflows", "URL path contains /workflows/"),
    ("/policies/", "policies", "URL path contains /policies/"),
    ("/templates/", "templates", "URL path contains /templates/"),
]


# ── Name patterns ──────────────────────────────────────────────────────

_NAME_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^MCP\b", re.I),           "mcp",       "name starts with 'MCP'"),
    (re.compile(r"\bMCP server\b", re.I),   "mcp",       "name contains 'MCP server'"),
    (re.compile(r"\bagent\b", re.I),        "agents",    "name contains 'agent'"),
    (re.compile(r"\bpersona\b", re.I),      "agents",    "name contains 'persona'"),
    (re.compile(r"\bprompt\b", re.I),       "prompts",   "name contains 'prompt'"),
    (re.compile(r"\bworkflow\b", re.I),     "workflows", "name contains 'workflow'"),
    (re.compile(r"\borchestration\b", re.I),"workflows", "name contains 'orchestration'"),
    (re.compile(r"\bblueprint\b", re.I),    "templates", "name contains 'blueprint'"),
    (re.compile(r"\btemplate\b", re.I),     "templates", "name contains 'template'"),
    (re.compile(r"\bpolicy\b", re.I),       "policies",  "name contains 'policy'"),
    (re.compile(r"\bconnector\b", re.I),    "connectors","name contains 'connector'"),
    (re.compile(r"\badapter\b", re.I),      "adapters",  "name contains 'adapter'"),
    (re.compile(r"\bplaybook\b", re.I),     "recovery",  "name contains 'playbook'"),
    (re.compile(r"\bchaos drill\b", re.I),  "chaos",     "name contains 'chaos drill'"),
]


# ── Tag hints ──────────────────────────────────────────────────────────

_TAG_HINTS: dict[str, str] = {
    "mcp": "mcp", "mcp-server": "mcp", "model-context-protocol": "mcp",
    "agent": "agents", "ai-agent": "agents", "persona": "agents",
    "prompt": "prompts", "system-prompt": "prompts",
    "workflow": "workflows", "orchestration": "workflows",
    "template": "templates", "blueprint": "templates",
    "policy": "policies", "governance": "governance",
    "connector": "connectors", "adapter": "adapters",
    "playbook": "recovery", "runbook": "recovery",
    "chaos": "chaos", "fault-injection": "chaos",
    "eval": "evals", "evaluation": "evals", "benchmark": "evals",
    "projection": "projections", "read-model": "projections",
    "capability": "capabilities", "plugin": "capabilities",
}


# ── Manifest-shape hints (for ingest pipeline) ────────────────────────

def _shape_hint(item: dict[str, Any]) -> tuple[str, str] | None:
    """Look at manifest-style keys; e.g. mcp.json has 'mcp_servers' field."""
    if "mcp_servers" in item or item.get("mcp_server") is True:
        return ("mcp", "manifest declares mcp_server")
    if "agent" in item and isinstance(item["agent"], dict):
        return ("agents", "manifest has 'agent' object")
    if "prompt_template" in item or "system_prompt" in item:
        return ("prompts", "manifest has prompt-template fields")
    if "workflow" in item and isinstance(item["workflow"], (dict, list)):
        return ("workflows", "manifest has 'workflow' object")
    if "policy_rules" in item:
        return ("policies", "manifest has 'policy_rules'")
    if "chaos_scenarios" in item:
        return ("chaos", "manifest has 'chaos_scenarios'")
    return None


# ── Main entry point ───────────────────────────────────────────────────


def classify(item: dict[str, Any], source_url: str | None = None) -> ClassificationResult:
    """Classify a raw item into a Library category.

    `item` is the raw asset dict (from registry or ingest). `source_url`
    is the canonical source URL (optional — uses item['source_url'] if missing).
    """
    reasons: list[str] = []
    runner_up: str | None = None
    src = source_url or item.get("source_url") or item.get("source") or ""

    # 1 — Explicit kind
    if (kind := item.get("kind")) and isinstance(kind, str):
        norm = _normalize_category_string(kind)
        if norm in KNOWN_CATEGORIES:
            reasons.append(f"explicit kind='{kind}' → {norm}")
            return ClassificationResult(norm, 0.99, reasons)

    # 2 — Existing category field
    if (cat := item.get("category")):
        norm = _normalize_category_string(cat)
        if norm in KNOWN_CATEGORIES:
            reasons.append(f"category='{cat}' → {norm}")
            return ClassificationResult(norm, 0.95, reasons)

    # 3 — Source-URL host/path hints
    src_lower = (src or "").lower()
    for hint, cat, reason in _URL_HINTS:
        if hint in src_lower:
            reasons.append(reason)
            return ClassificationResult(cat, 0.88, reasons)

    # 4 — Name patterns
    name = (item.get("name") or item.get("title") or item.get("id") or "")
    if isinstance(name, str) and name:
        for pat, cat, reason in _NAME_PATTERNS:
            if pat.search(name):
                reasons.append(f"{reason} (name='{name}')")
                return ClassificationResult(cat, 0.80, reasons)

    # 5 — Tag hints
    tags = item.get("tags") or []
    if isinstance(tags, list):
        for t in tags:
            if not isinstance(t, str):
                continue
            cat = _TAG_HINTS.get(t.lower())
            if cat:
                reasons.append(f"tag='{t}' → {cat}")
                return ClassificationResult(cat, 0.75, reasons)

    # 6 — Manifest-shape hints
    shape = _shape_hint(item)
    if shape:
        cat, reason = shape
        reasons.append(reason)
        return ClassificationResult(cat, 0.85, reasons)

    # 7 — Fallback
    reasons.append("no positive signal; defaulting to skills")
    return ClassificationResult("skills", 0.20, reasons, runner_up="capabilities")


def classify_many(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bucket a list of items by category. Each item is augmented with
    `_classification` key carrying the ClassificationResult dict."""
    buckets: dict[str, list[dict[str, Any]]] = {c: [] for c in KNOWN_CATEGORIES}
    for it in items:
        if not isinstance(it, dict):
            continue
        result = classify(it)
        it_out = {**it, "_classification": result.to_dict()}
        buckets[result.category].append(it_out)
    return buckets
