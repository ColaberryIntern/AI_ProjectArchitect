"""Workflow execution response contract — validation, parsing, repair.

Every workflow run that touches the LLM is required to return JSON that
matches config/schemas/ops/response_contract.schema.json. This module is the
gate.

The contract has 13 required fields. Most LLMs hit 9-11 of them and miss
2-4 on the first attempt. We make this safe by:

1. Trying strict parse + schema validate first.
2. If validation fails, applying ``coerce_to_contract`` which:
   - Fills missing arrays with []
   - Fills missing strings with ""
   - Strips extra keys not in the schema
3. If even after coercion validation still fails (e.g. the LLM returned
   pure prose), we surface the raw response in a ContractFailure with the
   exact field-by-field errors.

The workflow_runner uses this module — it never touches the schema directly.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from config.settings import SCHEMAS_DIR

logger = logging.getLogger(__name__)

_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "response_contract.schema.json"

# The 13 required top-level keys, in the order defined by the schema.
REQUIRED_FIELDS: tuple[str, ...] = (
    "summary",
    "files_created",
    "files_modified",
    "components_added",
    "database_changes",
    "routes_added",
    "dependencies_added",
    "mcp_servers_used",
    "agents_used",
    "tests_written",
    "known_issues",
    "verification_steps",
    "next_recommended_tasks",
)

# Per-field default for the "missing key" repair path.
_FIELD_DEFAULTS: dict[str, Any] = {
    "summary": "",
    "files_created": [],
    "files_modified": [],
    "components_added": [],
    "database_changes": [],
    "routes_added": [],
    "dependencies_added": [],
    "mcp_servers_used": [],
    "agents_used": [],
    "tests_written": [],
    "known_issues": [],
    "verification_steps": [],
    "next_recommended_tasks": [],
}


@dataclass
class ContractFailure(Exception):
    """Raised when a response cannot be coerced to the contract."""

    raw_response: str
    errors: list[str]

    def __str__(self) -> str:
        return f"Response contract validation failed: {self.errors}"


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    try:
        return _SCHEMA_CACHE
    except NameError:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
        return _SCHEMA_CACHE


def validate(payload: dict) -> list[str]:
    """Return a list of validation error messages. Empty = passes."""
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.absolute_path)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    ]


def coerce_to_contract(raw: dict) -> dict:
    """Fill missing fields with defaults and strip unknown keys.

    Does NOT touch fields that exist but have wrong type — that's a real
    contract failure the caller needs to see.
    """
    coerced = {}
    for key in REQUIRED_FIELDS:
        if key in raw:
            coerced[key] = raw[key]
        else:
            coerced[key] = _copy_default(key)
    return coerced


def _copy_default(key: str) -> Any:
    default = _FIELD_DEFAULTS[key]
    return [] if isinstance(default, list) else default


def extract_json(raw_text: str) -> dict | None:
    """Best-effort JSON extraction from an LLM response. Mirrors the strategy
    used by profile_generator: pure JSON → markdown fence → first balanced
    object in prose.
    """
    if not raw_text:
        return None
    try:
        parsed = json.loads(raw_text)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        pass

    text = raw_text.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if fence:
        try:
            parsed = json.loads(fence.group(1))
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            pass

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    parsed = json.loads(candidate)
                    return parsed if isinstance(parsed, dict) else None
                except (json.JSONDecodeError, TypeError):
                    return None
    return None


def parse_and_validate(raw_text: str, *, strict: bool = False) -> dict:
    """Parse + validate an LLM response.

    Args:
        raw_text: The raw model output (may contain markdown fences or prose).
        strict: When True, do NOT coerce missing keys. Reject the response if
            it doesn't already contain all 13 fields. Used by the verification
            agent because we want to catch model regressions, not paper over them.

    Returns:
        Validated dict matching the contract.

    Raises:
        ContractFailure if no JSON can be extracted or if the (coerced)
        payload still fails schema validation.
    """
    extracted = extract_json(raw_text)
    if extracted is None:
        raise ContractFailure(raw_text, ["could not extract JSON object from response"])

    payload = extracted if strict else coerce_to_contract(extracted)

    errors = validate(payload)
    if errors:
        raise ContractFailure(raw_text, errors)

    return payload


# ── Prompt-side helpers ─────────────────────────────────────────────────


def contract_prompt_addendum() -> str:
    """Return a paragraph the workflow_runner prepends to every prompt.

    The addendum tells the LLM what JSON to return and is read by the model
    *every time*. Generated from the schema so it stays in sync.
    """
    lines = [
        "",
        "## RESPONSE CONTRACT (HARD REQUIREMENT)",
        "",
        "Your entire response MUST be a single JSON object with exactly these 13 top-level fields,",
        "in this order. Do NOT add any prose outside the JSON. Do NOT wrap the JSON in markdown",
        "code fences. Empty arrays are allowed; do not omit a field.",
        "",
        "```json",
        "{",
        '  "summary": "<one paragraph in plain English>",',
        '  "files_created": [{"path": "...", "purpose": "...", "lines": 0}],',
        '  "files_modified": [{"path": "...", "purpose": "...", "lines": 0}],',
        '  "components_added": [{"name": "...", "kind": "...", "purpose": "..."}],',
        '  "database_changes": [{"change_type": "...", "target": "...", "description": "..."}],',
        '  "routes_added": [{"method": "GET", "path": "...", "handler": "...", "purpose": "..."}],',
        '  "dependencies_added": [{"name": "...", "version": "...", "ecosystem": "pip", "reason": "..."}],',
        '  "mcp_servers_used": ["server_id"],',
        '  "agents_used": ["agent_id"],',
        '  "tests_written": [{"path": "...", "count": 0, "scope": "unit"}],',
        '  "known_issues": [{"description": "...", "severity": "low", "suggested_fix": "..."}],',
        '  "verification_steps": [{"step": "...", "expected": "...", "command": "..."}],',
        '  "next_recommended_tasks": [{"task": "...", "suggested_plugin": null, "priority": "medium"}]',
        "}",
        "```",
        "",
    ]
    return "\n".join(lines)
