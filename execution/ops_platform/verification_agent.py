"""Verification agent — reviews a workflow run, returns structured verification JSON.

Two-tier verification:
  1. Structural — pure-Python checks against the persisted RunRecord that need
     no LLM. Catches obvious gaps (no files touched, no tests, missing routes,
     known-issue severity = blocker).
  2. Semantic — optional LLM-judged review that compares the response against
     the capability manifest's declared outputs + the user's input requirements.

Both produce a payload conforming to config/schemas/ops/verification.schema.json.
The structural tier always runs; the semantic tier runs only when the LLM is
available AND ``use_llm=True``. When the semantic tier runs, its findings are
MERGED with the structural ones (recommendations and architecture_issues
concat; deployment_readiness downgraded to the more conservative of the two).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from config.settings import SCHEMAS_DIR
from execution import llm_client
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry
from execution.ops_platform.response_contract import extract_json
from execution.ops_platform.workflow_runner import RunRecord, get_run

logger = logging.getLogger(__name__)

_SCHEMA_PATH = SCHEMAS_DIR / "ops" / "verification.schema.json"

_VERIFIER_SYSTEM_PROMPT = """You are the Verification Agent for the Colaberry AI
Operations Platform. You review a workflow execution and report what was
completed, what is partial, what is missing, and what shape the implementation
is in. Be strict but fair. Reference concrete fields and paths from the
provided response. Do not invent issues.

Your entire response MUST be a single JSON object matching exactly these 8
keys: completed_requirements, partial_requirements, missing_requirements,
architecture_issues, ui_issues, technical_debt, recommendations,
deployment_readiness. deployment_readiness must be one of: green, yellow, red.
"""


@dataclass
class VerificationResult:
    payload: dict
    structural_findings: dict = field(default_factory=dict)
    llm_used: bool = False
    errors: list[str] = field(default_factory=list)


# ── Public API ──────────────────────────────────────────────────────────


def verify_run(
    run_id: str,
    *,
    use_llm: bool = True,
    registry: CapabilityRegistry | None = None,
) -> VerificationResult:
    """Verify a previously-executed workflow run.

    Returns a VerificationResult whose ``payload`` is guaranteed to validate
    against the verification schema. On any failure (run not found, LLM
    failure, schema mismatch), falls back to a structural-only payload with
    deployment_readiness=red and the failure recorded in ``errors``.
    """
    reg = registry or default_registry()
    run = get_run(run_id)
    if run is None:
        return _empty_result(errors=[f"run '{run_id}' not found"])

    capability = reg.get(run.capability_id)
    structural = _structural_review(run, capability)

    result = VerificationResult(payload=structural, structural_findings=structural)

    if use_llm and llm_client.is_available() and run.response:
        llm_findings, llm_errors = _llm_review(run, capability)
        if llm_findings is not None:
            result.payload = _merge_findings(structural, llm_findings)
            result.llm_used = True
        result.errors.extend(llm_errors)

    # Final schema validation. If our own merge ever produces an invalid
    # payload (shouldn't happen) we fall back to the structural result.
    schema_errors = _validate(result.payload)
    if schema_errors:
        logger.error("verification payload failed schema: %s", schema_errors)
        result.errors.extend(schema_errors)
        result.payload = structural

    return result


# ── Structural tier ─────────────────────────────────────────────────────


def _structural_review(run: RunRecord, capability: dict | None) -> dict:
    """Pure-Python checks that always run. Builds a baseline payload."""
    payload = {
        "completed_requirements": [],
        "partial_requirements": [],
        "missing_requirements": [],
        "architecture_issues": [],
        "ui_issues": [],
        "technical_debt": [],
        "recommendations": [],
        "deployment_readiness": "green",
    }

    if run.status != "succeeded":
        payload["missing_requirements"].append(
            f"Run did not complete cleanly (status={run.status})"
        )
        payload["deployment_readiness"] = "red"
        if run.error_message:
            payload["architecture_issues"].append({
                "issue": run.error_message,
                "severity": "high",
                "suggested_fix": "Re-run with the LLM available; check capability manifest paths.",
            })
        return payload

    response = run.response or {}

    # Manifest declared outputs vs what came back.
    declared = capability.get("outputs") if capability else None
    if declared:
        for out in declared:
            name = out.get("name", "")
            if not name:
                continue
            # Heuristic: an output is "completed" if a non-empty value with the
            # same name appears anywhere in the response.
            if _output_present(response, name):
                payload["completed_requirements"].append(name)
            else:
                payload["missing_requirements"].append(name)

    # Blocker-severity known_issues drop readiness to red.
    for issue in response.get("known_issues") or []:
        if issue.get("severity") == "blocker":
            payload["deployment_readiness"] = "red"
            payload["architecture_issues"].append({
                "issue": issue.get("description", ""),
                "severity": "blocker",
                "suggested_fix": issue.get("suggested_fix", ""),
            })

    # No tests written = yellow (best-effort heuristic, never red on its own).
    tests = response.get("tests_written") or []
    if not tests and capability and capability.get("type") == "workflow":
        payload["technical_debt"].append(
            "No tests were written for this run. Add at least one unit test."
        )
        payload["deployment_readiness"] = _downgrade(payload["deployment_readiness"], "yellow")

    # Empty verification_steps is a red flag for any production-bound run.
    if not (response.get("verification_steps") or []):
        payload["recommendations"].append(
            "Add at least one concrete verification step the reviewer can execute."
        )
        payload["deployment_readiness"] = _downgrade(payload["deployment_readiness"], "yellow")

    # Contract failures from the runner.
    if run.contract_errors:
        payload["architecture_issues"].append({
            "issue": (
                "Response did not fully satisfy the structured contract on first attempt; "
                f"errors: {', '.join(run.contract_errors[:3])}"
                + (" ..." if len(run.contract_errors) > 3 else "")
            ),
            "severity": "medium",
            "suggested_fix": "Tighten the workflow's prompt to enforce the contract explicitly.",
        })
        payload["deployment_readiness"] = _downgrade(payload["deployment_readiness"], "yellow")

    return payload


def _output_present(response: dict, output_name: str) -> bool:
    """True if a response field with the same name has non-empty content."""
    if output_name not in response:
        return False
    val = response[output_name]
    if isinstance(val, (list, dict)):
        return bool(val)
    if isinstance(val, str):
        return val.strip() != ""
    return val is not None


def _downgrade(current: str, candidate: str) -> str:
    """green < yellow < red. Returns the more-severe of the two."""
    order = {"green": 0, "yellow": 1, "red": 2}
    return current if order[current] >= order[candidate] else candidate


# ── Semantic (LLM) tier ─────────────────────────────────────────────────


def _llm_review(run: RunRecord, capability: dict | None) -> tuple[dict | None, list[str]]:
    """Ask the model to review the response. Returns (payload, errors)."""
    user_message = _build_review_prompt(run, capability)
    try:
        response = llm_client.chat(
            system_prompt=_VERIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            temperature=0.0,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
    except (llm_client.LLMUnavailableError, llm_client.LLMClientError) as e:
        return None, [f"llm verification call failed: {e}"]

    extracted = extract_json(response.content)
    if not extracted:
        return None, ["llm verification returned non-JSON"]

    errors = _validate(extracted)
    if errors:
        return None, [f"llm verification payload invalid: {errors[0]}"]
    return extracted, []


def _build_review_prompt(run: RunRecord, capability: dict | None) -> str:
    parts = [
        "Review the following workflow execution and produce a verification payload.",
        "",
        f"Capability: {run.capability_id}",
    ]
    if capability:
        parts.append(f"Capability description: {capability.get('description', '')}")
        outputs = capability.get("outputs") or []
        if outputs:
            parts.append("Declared outputs:")
            for o in outputs:
                parts.append(f"  - {o.get('name')}: {o.get('description', '')}")
    parts.append("")
    parts.append("Run inputs (JSON):")
    parts.append(json.dumps(run.inputs, indent=2)[:2000])
    parts.append("")
    parts.append("Run response (JSON):")
    parts.append(json.dumps(run.response, indent=2)[:6000])
    parts.append("")
    parts.append("Return ONLY the verification JSON object.")
    return "\n".join(parts)


# ── Schema helpers ──────────────────────────────────────────────────────


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    try:
        return _SCHEMA_CACHE
    except NameError:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _SCHEMA_CACHE = json.load(f)
        return _SCHEMA_CACHE


def _validate(payload: dict) -> list[str]:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.absolute_path)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    ]


def _merge_findings(structural: dict, llm: dict) -> dict:
    """Merge structural + LLM findings, picking the more conservative readiness."""
    merged = {
        "completed_requirements": _dedupe(structural["completed_requirements"] + llm.get("completed_requirements", [])),
        "partial_requirements": structural["partial_requirements"] + llm.get("partial_requirements", []),
        "missing_requirements": _dedupe(structural["missing_requirements"] + llm.get("missing_requirements", [])),
        "architecture_issues": structural["architecture_issues"] + llm.get("architecture_issues", []),
        "ui_issues": structural["ui_issues"] + llm.get("ui_issues", []),
        "technical_debt": _dedupe(structural["technical_debt"] + llm.get("technical_debt", [])),
        "recommendations": _dedupe(structural["recommendations"] + llm.get("recommendations", [])),
        "deployment_readiness": _downgrade(
            structural["deployment_readiness"], llm.get("deployment_readiness", "green")
        ),
    }
    return merged


def _dedupe(items: list[Any]) -> list[Any]:
    seen: list[Any] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return seen


def _empty_result(errors: list[str]) -> VerificationResult:
    payload = {
        "completed_requirements": [],
        "partial_requirements": [],
        "missing_requirements": [],
        "architecture_issues": [],
        "ui_issues": [],
        "technical_debt": [],
        "recommendations": errors,
        "deployment_readiness": "red",
    }
    return VerificationResult(payload=payload, errors=errors)
