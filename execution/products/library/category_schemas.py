"""Per-category field schemas for /library/submit.

The submit form used to be one-size-fits-all (name + description + how_to_use
+ example + tags + source for everything). That's wrong: a workflow's natural
fields (steps, invocation pattern) are nothing like a prompt's (prompt body,
expected output), which are nothing like an MCP server's (install command,
config template).

This module defines, per category, the set of fields that are required and
optional at submit time. Field names match keys on store.AssetMetadata so a
form payload can be passed straight to AssetMetadata(**payload) without
remapping.

Usage:
    from execution.products.library import category_schemas as cs

    schema = cs.schema_for("skills")
    schema["required"]                 # ["name", "description", "how_to_use", "example"]
    schema["optional"]                 # ["docs_url", "install_command", "languages"]
    schema["field_labels"]["steps"]    # "Ordered steps (one per line)" (workflows)

The template iterates schema["required"] + schema["optional"] for the chosen
category and renders matching input controls. The submit handler validates
the body has every key in schema["required"] and persists everything else
into Submission.payload.
"""
from __future__ import annotations

from typing import Iterable

# Fields every category submits. Keep this list small -- if a category needs
# a base field absent, it must override via its own entry below.
COMMON_REQUIRED = ["name", "description"]
COMMON_OPTIONAL = ["tags", "source"]


# ── Field meta (label, hint, control type) ────────────────────────────
# Keys here MUST match AssetMetadata field names so the form payload can
# be applied to AssetMetadata directly.
FIELD_META: dict[str, dict] = {
    "name":            {"label": "Name", "control": "text",
                                  "placeholder": "e.g. RFP Summary v2"},
    "description":     {"label": "What it does", "control": "textarea", "rows": 3,
                                  "placeholder": "One paragraph -- what this accomplishes, "
                                                             "who it's for, why it's worth keeping."},
    "what_its_for":    {"label": "What it's for (one-liner)", "control": "text",
                                  "placeholder": "Short answer to 'why would I use this?'"},
    "how_to_use":      {"label": "How to use it", "control": "textarea", "rows": 4,
                                  "placeholder": "Step-by-step or invocation. Keep it concrete."},
    "example":         {"label": "Example", "control": "textarea", "rows": 4,
                                  "placeholder": "Input + output, or sample code, or a screenshot URL."},
    "tags":            {"label": "Tags (comma-separated)", "control": "text",
                                  "placeholder": "sales, summarization, gpt-4"},
    "source":          {"label": "Source (URL or path, optional)", "control": "text",
                                  "placeholder": "https://... or path/to/file.md"},

    # Skills / MCPs / Capabilities / Adapters / Connectors
    "install_command": {"label": "Install command", "control": "text",
                                  "placeholder": "npm install ... / pip install ... / uvx ..."},
    "install_url":     {"label": "Install URL (package page)", "control": "text",
                                  "placeholder": "https://npmjs.com/package/... or PyPI URL"},
    "docs_url":        {"label": "Documentation URL", "control": "text",
                                  "placeholder": "https://..."},
    "homepage_url":    {"label": "Homepage URL", "control": "text",
                                  "placeholder": "https://..."},
    "languages":       {"label": "Languages (comma-separated)", "control": "text",
                                  "placeholder": "python, typescript"},
    "dependencies":    {"label": "Dependencies (one per line)", "control": "textarea", "rows": 3,
                                  "placeholder": "@modelcontextprotocol/server-filesystem >= 0.6"},
    "license":         {"label": "License", "control": "text",
                                  "placeholder": "MIT / Apache-2.0 / proprietary"},

    # Workflows
    "steps":           {"label": "Ordered steps (one per line)", "control": "textarea", "rows": 6,
                                  "placeholder": "1. Fetch ...\n2. Validate ...\n3. Commit ..."},
    "invocation_pattern": {"label": "How it's invoked", "control": "textarea", "rows": 3,
                                  "placeholder": "Cron, on-demand, triggered by event X, ..."},
    "success_criteria":   {"label": "Success criteria", "control": "textarea", "rows": 3,
                                  "placeholder": "How do you know it worked end-to-end?"},

    # Prompts
    "prompt_body":     {"label": "Prompt body", "control": "textarea", "rows": 8,
                                  "placeholder": "The actual prompt text. Use {{ placeholders }} for substitution."},
    "expected_output": {"label": "Expected output", "control": "textarea", "rows": 4,
                                  "placeholder": "What a good response looks like."},
    "model_hint":      {"label": "Model hint", "control": "text",
                                  "placeholder": "claude-opus-4-7, gpt-4o, ... (optional)"},

    # Agents
    "role":            {"label": "Role / persona", "control": "text",
                                  "placeholder": "e.g. Code reviewer, Sales SDR, Curator"},
    "system_prompt":   {"label": "System prompt", "control": "textarea", "rows": 6,
                                  "placeholder": "You are ..."},
    "autonomy_level":  {"label": "Autonomy", "control": "select",
                                  "options": ["recommend", "approve-then-run",
                                                      "low-risk-auto", "full-auto"]},
    "allowed_tools":   {"label": "Allowed tools (comma-separated)", "control": "text",
                                  "placeholder": "colaberry_post_progress, colaberry_attachment_fetch"},
    "guardrails":      {"label": "Guardrails", "control": "textarea", "rows": 3,
                                  "placeholder": "What this agent must NOT do."},

    # Templates
    "blueprint_path":  {"label": "Blueprint path", "control": "text",
                                  "placeholder": "config/blueprints/<your-blueprint>"},
    "scaffolding_config": {"label": "Scaffolding notes", "control": "textarea", "rows": 4,
                                          "placeholder": "What files this blueprint generates, defaults, etc."},

    # Policies / Governance
    "rule_text":       {"label": "Rule text", "control": "textarea", "rows": 5,
                                  "placeholder": "The policy in human-readable terms."},
    "enforcement_point": {"label": "Where it's enforced", "control": "text",
                                          "placeholder": "policy_engine / governance_scorecards / ..."},

    # Recovery / Chaos
    "trigger_condition": {"label": "Trigger condition", "control": "textarea", "rows": 3,
                                          "placeholder": "When does this playbook fire?"},
    "mitigation_action": {"label": "Mitigation action", "control": "textarea", "rows": 4,
                                          "placeholder": "What to do once triggered."},
    "fault_scenario":  {"label": "Fault scenario", "control": "textarea", "rows": 4,
                                  "placeholder": "What this chaos drill simulates."},

    # Projections / Evals
    "event_source":    {"label": "Event source", "control": "text",
                                  "placeholder": "Topic / stream / database whose events feed this."},
    "rebuild_strategy": {"label": "Rebuild strategy", "control": "textarea", "rows": 3,
                                          "placeholder": "How to reconstruct from history."},
    "dataset_url":     {"label": "Dataset URL", "control": "text",
                                  "placeholder": "https://... or s3://..."},
    "scoring_method":  {"label": "Scoring method", "control": "textarea", "rows": 3,
                                  "placeholder": "How responses are graded."},
}


# ── Per-category schemas ──────────────────────────────────────────────
# required[] lists fields the form REQUIRES + the schema VALIDATES.
# optional[] lists fields the form shows but doesn't require.
# The common base (name, description, tags, source) is added implicitly
# unless the category overrides it.

SCHEMAS: dict[str, dict[str, list[str]]] = {
    "skills": {
        "required": ["name", "description", "how_to_use", "example"],
        "optional": ["what_its_for", "install_command", "docs_url",
                            "languages", "license"],
    },
    "agents": {
        "required": ["name", "description", "role", "system_prompt"],
        "optional": ["autonomy_level", "allowed_tools", "guardrails", "example"],
    },
    "prompts": {
        "required": ["name", "description", "prompt_body"],
        "optional": ["expected_output", "model_hint", "example"],
    },
    "mcp": {
        "required": ["name", "description", "install_command"],
        "optional": ["what_its_for", "install_url", "docs_url",
                            "homepage_url", "dependencies", "license"],
    },
    "capabilities": {
        "required": ["name", "description", "how_to_use"],
        "optional": ["docs_url", "example"],
    },
    "templates": {
        "required": ["name", "description", "blueprint_path"],
        "optional": ["scaffolding_config", "docs_url"],
    },
    "workflows": {
        "required": ["name", "description", "steps", "invocation_pattern"],
        "optional": ["success_criteria", "dependencies"],
    },
    "policies": {
        "required": ["name", "description", "rule_text"],
        "optional": ["enforcement_point", "docs_url"],
    },
    "governance": {
        "required": ["name", "description", "rule_text"],
        "optional": ["enforcement_point", "docs_url"],
    },
    "recovery": {
        "required": ["name", "description", "trigger_condition", "mitigation_action"],
        "optional": ["example"],
    },
    "chaos": {
        "required": ["name", "description", "fault_scenario"],
        "optional": ["example", "mitigation_action"],
    },
    "projections": {
        "required": ["name", "description", "event_source"],
        "optional": ["rebuild_strategy", "docs_url"],
    },
    "evals": {
        "required": ["name", "description", "dataset_url"],
        "optional": ["scoring_method", "docs_url"],
    },
    "connectors": {
        "required": ["name", "description", "how_to_use"],
        "optional": ["install_command", "docs_url", "homepage_url"],
    },
    "adapters": {
        "required": ["name", "description", "how_to_use"],
        "optional": ["install_command", "docs_url", "languages"],
    },
}

# Default schema for any category not explicitly listed -- mirrors the
# legacy form so existing flows still work.
DEFAULT_SCHEMA = {
    "required": ["name", "description"],
    "optional": ["how_to_use", "example"],
}


def schema_for(category: str) -> dict:
    """Return the schema for a category, with common base fields layered in.

    Output shape:
        {
            "required":      ["name", "description", ...],
            "optional":      ["tags", "source", ...],
            "field_labels":  {"name": "Name", "description": "What it does", ...},
            "field_meta":    {"name": {"label": ..., "control": "text", ...}, ...},
        }
    """
    raw = SCHEMAS.get(category, DEFAULT_SCHEMA)
    req = list(raw["required"])
    opt = list(raw["optional"])
    # Layer in common optional fields (tags, source) at the end if not
    # already listed.
    for f in COMMON_OPTIONAL:
        if f not in req and f not in opt:
            opt.append(f)
    return {
        "category": category,
        "required": req,
        "optional": opt,
        "field_labels": {f: FIELD_META.get(f, {}).get("label", f) for f in req + opt},
        "field_meta": {f: FIELD_META.get(f, {"label": f, "control": "text"})
                                  for f in req + opt},
    }


def all_field_keys(category: str) -> list[str]:
    """All fields the form for `category` collects, in render order
    (required first, then optional)."""
    s = schema_for(category)
    return list(s["required"]) + list(s["optional"])


def validate_payload(category: str, payload: dict) -> list[str]:
    """Return a list of missing required field names. Empty list = valid."""
    s = schema_for(category)
    missing: list[str] = []
    for f in s["required"]:
        v = payload.get(f)
        if v is None or (isinstance(v, str) and not v.strip()) or v == []:
            missing.append(f)
    return missing


def split_into_metadata_kwargs(category: str, payload: dict) -> dict:
    """Filter `payload` down to fields that AssetMetadata accepts. The form
    payload may contain category-specific keys (e.g. `steps`, `prompt_body`)
    that aren't yet AssetMetadata columns -- those land in a JSON blob field
    so the data survives the round-trip even though the schema hasn't grown
    a column.

    Returns:
        {
            "base":  dict of keys that map 1:1 to AssetMetadata fields,
            "extras": dict of keys that don't (preserved verbatim),
        }
    """
    from . import store
    valid_keys = {f.name for f in store.AssetMetadata.__dataclass_fields__.values()}
    base = {}
    extras = {}
    for k, v in payload.items():
        if k in valid_keys:
            base[k] = v
        else:
            extras[k] = v
    return {"base": base, "extras": extras}


def normalize_list_field(raw: str, sep: str = "\n") -> list[str]:
    """Convert a textarea / comma-separated input into a clean list[str]."""
    if not raw:
        return []
    items: Iterable[str]
    if sep == "\n":
        items = raw.replace("\r", "").split("\n")
    else:
        items = raw.split(sep)
    return [x.strip() for x in items if x.strip()]
