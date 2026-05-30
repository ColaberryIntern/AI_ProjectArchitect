"""Workflow runner — executes a workflow plugin end-to-end.

Pipeline per run:
1. Look up the capability by id (must be type='workflow' or 'agent').
2. Load the prompt file declared by the manifest (prompt_path).
3. Build the user message from the capability's inputs + caller-provided values.
4. Call the LLM via execution.llm_client.chat() with the prompt + the response
   contract addendum.
5. Parse + validate the response against the contract schema. Coerce missing
   fields when ``response_contract_required`` is True; otherwise reject hard.
6. Persist the run as a JSON file under output/ops_platform/runs/{run_id}.json.
7. Increment usage_count in the capability registry.
8. Hand the response off to requirements_intelligence for pattern extraction
   (non-blocking — failures are logged, not raised).

Every run produces a stable run_id (UUID v4). Downstream consumers
(verification_agent, training_agent, feedback_store, search_index) reference
that ID.

The runner is sync and returns the full RunRecord. The router wraps it in
async-friendly fashion.
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution import llm_client
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry
from execution.ops_platform.response_contract import (
    ContractFailure,
    contract_prompt_addendum,
    parse_and_validate,
)

logger = logging.getLogger(__name__)

_RUNS_DIR = OUTPUT_DIR / "ops_platform" / "runs"

# Models that can handle the response contract without thrashing. Default to
# the project default LLM; callers can override per run.
DEFAULT_RUNNER_TEMPERATURE = 0.3
DEFAULT_RUNNER_MAX_TOKENS = 4096


@dataclass
class RunRecord:
    """Persisted form of a single workflow execution."""

    run_id: str
    capability_id: str
    started_at: str
    finished_at: str | None
    status: str  # "succeeded" | "contract_failed" | "llm_unavailable" | "error"
    inputs: dict = field(default_factory=dict)
    response: dict | None = None
    raw_response_text: str | None = None
    contract_errors: list[str] = field(default_factory=list)
    error_message: str | None = None
    llm_usage: dict = field(default_factory=dict)
    duration_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ──────────────────────────────────────────────────────────


def run_workflow(
    capability_id: str,
    inputs: dict | None = None,
    *,
    registry: CapabilityRegistry | None = None,
    model: str | None = None,
    temperature: float = DEFAULT_RUNNER_TEMPERATURE,
    max_tokens: int = DEFAULT_RUNNER_MAX_TOKENS,
) -> RunRecord:
    """Execute a workflow plugin end-to-end. Always returns a RunRecord; never
    raises out of the LLM/contract path so the UI can render the failure.
    """
    reg = registry or default_registry()
    capability = reg.get(capability_id)

    inputs = inputs or {}

    run = _new_record(capability_id, inputs)

    try:
        from execution.ops_platform import realtime_bus
        realtime_bus.emit(
            "workflow.started", actor={"name": "workflow_runner", "system": True},
            workspace_id=(inputs.get("__workspace_id") if isinstance(inputs, dict) else None),
            correlation_id=run.run_id,
            payload={"run_id": run.run_id, "capability_id": capability_id},
            mirror_to_audit=False,
        )
    except Exception:
        pass

    if capability is None:
        run.status = "error"
        run.error_message = f"capability '{capability_id}' is not registered"
        _persist(run)
        return run

    # ── Phase 5G: respect operational controls (freeze / quarantine) ──
    try:
        from execution.ops_platform import controls
        block_reason = controls.is_blocked(capability_id)
        if block_reason:
            run.status = "blocked"
            run.error_message = f"capability blocked by operational control: {block_reason}"
            _finalize_timing(run)
            _persist(run)
            return run
    except Exception:
        logger.debug("controls check failed", exc_info=True)

    # ── Phase 5C: runtime version routing ──
    # If a session id is provided, route via the runtime router. The chosen
    # version's manifest_snapshot becomes the effective capability for this
    # call. When no versions exist, this is a no-op (falls through).
    session_id = (inputs.get("__session_id") if isinstance(inputs, dict) else "") or ""
    if session_id:
        try:
            from execution.ops_platform import runtime_router
            decision = runtime_router.route(capability_id, session_id=session_id)
            if decision.selected_version_id:
                from execution.ops_platform import capability_versions
                chosen = capability_versions.get_version(decision.selected_version_id)
                if chosen and chosen.manifest_snapshot:
                    capability = dict(chosen.manifest_snapshot)
                    # Keep the original _meta so prompt loading still works
                    original_meta = capability.get("_meta") or (reg.get(capability_id) or {}).get("_meta")
                    if original_meta:
                        capability["_meta"] = original_meta
                    inputs = dict(inputs)
                    inputs["__capability_version_id"] = decision.selected_version_id
                    inputs["__routing_correlation_id"] = decision.correlation_id
                    run.inputs = dict(inputs)
        except Exception:
            logger.debug("runtime routing failed", exc_info=True)

    if capability.get("type") not in ("workflow", "agent"):
        run.status = "error"
        run.error_message = (
            f"capability '{capability_id}' is type '{capability.get('type')}', "
            "only 'workflow' and 'agent' are runnable"
        )
        _persist(run)
        return run

    prompt_text, prompt_load_error = _load_prompt(capability)
    if prompt_load_error:
        run.status = "error"
        run.error_message = prompt_load_error
        _persist(run)
        return run

    if not llm_client.is_available():
        run.status = "llm_unavailable"
        run.error_message = "LLM is not configured (OPENAI_API_KEY unset)"
        _finalize_timing(run)
        _persist(run)
        return run

    system_prompt = _system_prompt_for(capability)
    user_message = _format_user_message(prompt_text, capability, inputs)

    start_monotonic = datetime.now(timezone.utc)
    try:
        response = llm_client.chat(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        run.raw_response_text = response.content
        run.llm_usage = response.usage or {}
    except llm_client.LLMUnavailableError as e:
        run.status = "llm_unavailable"
        run.error_message = str(e)
        _finalize_timing(run, start_monotonic)
        _persist(run)
        return run
    except llm_client.LLMClientError as e:
        run.status = "error"
        run.error_message = f"LLM call failed: {e}"
        _finalize_timing(run, start_monotonic)
        _persist(run)
        return run

    contract_required = capability.get("response_contract_required", True)
    try:
        validated = parse_and_validate(response.content, strict=False)
        run.response = validated
        run.status = "succeeded"
    except ContractFailure as e:
        if contract_required:
            run.status = "contract_failed"
            run.contract_errors = list(e.errors)
            run.error_message = (
                "response did not satisfy the structured contract; "
                "see contract_errors for the per-field failures"
            )
        else:
            # Plugin opted out; record the raw response without enforcement.
            run.status = "succeeded"
            run.response = {"raw": response.content}
            run.contract_errors = list(e.errors)

    _finalize_timing(run, start_monotonic)
    _persist(run)

    # Side effects: only on successful (or opt-out-success) runs.
    if run.status == "succeeded":
        try:
            reg.record_usage(capability_id)
        except Exception:
            logger.warning("Failed to record usage for %s", capability_id, exc_info=True)
        try:
            # Lazy import to avoid a circular reference at module load.
            from execution.ops_platform.requirements_intelligence import extract_from_run
            extract_from_run(run)
        except Exception:
            logger.warning("Requirements intelligence extract failed for %s", run.run_id, exc_info=True)

    try:
        from execution.ops_platform import cache_bus
        cache_bus.emit(cache_bus.Topic.RUN_RECORDED, {
            "run_id": run.run_id,
            "capability_id": capability_id,
            "status": run.status,
        })
    except Exception:
        logger.warning("cache_bus emit failed for RUN_RECORDED", exc_info=True)

    # ── Phase 7A: realtime workflow lifecycle events ──
    try:
        from execution.ops_platform import realtime_bus
        event_kind = "workflow.completed" if run.status == "succeeded" else "workflow.failed"
        realtime_bus.emit(
            event_kind, actor={"name": "workflow_runner", "system": True},
            workspace_id=(inputs.get("__workspace_id") if isinstance(inputs, dict) else None),
            correlation_id=(inputs.get("__routing_correlation_id") if isinstance(inputs, dict) else None) or run.run_id,
            payload={
                "run_id": run.run_id, "capability_id": capability_id,
                "status": run.status, "duration_ms": run.duration_ms,
            },
            mirror_to_audit=False,
        )
    except Exception:
        logger.debug("realtime_bus emit failed for workflow lifecycle", exc_info=True)

    return run


def get_run(run_id: str) -> RunRecord | None:
    """Load a previously persisted run from disk."""
    path = _RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RunRecord(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        logger.warning("Failed to load run %s", run_id, exc_info=True)
        return None


# ── Phase 6A: Async / queued execution ─────────────────────────────────


def run_workflow_async(
    capability_id: str,
    inputs: dict | None = None,
    *,
    queue: str = "default",
    priority: int = 0,
    delay_seconds: int = 0,
    correlation_id: str | None = None,
    enqueued_by: dict | str | None = None,
) -> dict:
    """Enqueue a workflow run for async processing. Returns the queue job
    descriptor; status is pollable via runtime_queue.status(job_id).

    The sync ``run_workflow`` API is unchanged. This is a parallel additive
    entry point. A queue worker (see scheduler / worker_coordination) pulls
    the job and invokes ``run_workflow`` synchronously inside its own process.
    """
    from execution.ops_platform import runtime_queue
    job = runtime_queue.enqueue(
        kind="workflow_run", queue=queue, priority=priority,
        delay_seconds=delay_seconds, correlation_id=correlation_id,
        enqueued_by=enqueued_by,
        payload={"capability_id": capability_id, "inputs": dict(inputs or {})},
    )
    return {"job_id": job.job_id, "status": job.status, "queue": queue,
            "correlation_id": job.correlation_id}


def drain_queue_once(
    *,
    worker_id: str,
    queue: str = "default",
    registry: CapabilityRegistry | None = None,
) -> dict | None:
    """Worker loop iteration: claim one job, run the workflow synchronously,
    ack on success, nack on failure. Returns the resulting run dict or None
    when the queue is empty.

    Crash semantics: if the worker process dies between claim and ack, the
    claim lease expires and ``runtime_queue.reclaim_stale()`` re-pends the
    job. Idempotent handlers handle that re-execution cleanly.
    """
    from execution.ops_platform import runtime_queue
    job = runtime_queue.claim(queue=queue, worker_id=worker_id)
    if job is None:
        return None
    if job.kind != "workflow_run":
        runtime_queue.nack(job.job_id, worker_id=worker_id,
                            error=f"unsupported job kind {job.kind}")
        return None
    try:
        run = run_workflow(
            job.payload.get("capability_id", ""),
            job.payload.get("inputs") or {},
            registry=registry,
        )
        runtime_queue.ack(job.job_id, worker_id=worker_id,
                            result={"run_id": run.run_id, "status": run.status})
        return run.to_dict()
    except Exception as e:
        runtime_queue.nack(job.job_id, worker_id=worker_id, error=str(e)[:300])
        return None


def list_runs(*, capability_id: str | None = None, limit: int = 50) -> list[RunRecord]:
    """Return persisted runs newest-first."""
    if not _RUNS_DIR.exists():
        return []
    paths = sorted(
        _RUNS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[RunRecord] = []
    for p in paths:
        if len(out) >= limit:
            break
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if capability_id and data.get("capability_id") != capability_id:
                continue
            out.append(RunRecord(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


# ── Internal helpers ────────────────────────────────────────────────────


def _new_record(capability_id: str, inputs: dict) -> RunRecord:
    return RunRecord(
        run_id=str(uuid.uuid4()),
        capability_id=capability_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        status="pending",
        inputs=dict(inputs),
    )


def _load_prompt(capability: dict) -> tuple[str, str | None]:
    """Read the capability's primary prompt file, or return an error string.

    plugin_dir may be stored either as a path relative to PROJECT_ROOT (the
    normal case) or as an absolute path (when loaded from outside PROJECT_ROOT,
    e.g. tmp directories during tests). The loader writes the absolute path
    into ``_meta.plugin_dir_absolute``; we prefer that when present.
    """
    relative = capability.get("prompt_path")
    if not relative:
        return "", "manifest has no prompt_path; cannot run"
    from config.settings import PROJECT_ROOT
    meta = capability.get("_meta") or {}
    abs_dir = meta.get("plugin_dir_absolute")
    if abs_dir:
        plugin_dir = Path(abs_dir)
    else:
        plugin_dir_raw = Path(meta.get("plugin_dir", ""))
        plugin_dir = plugin_dir_raw if plugin_dir_raw.is_absolute() else (PROJECT_ROOT / plugin_dir_raw)
    prompt_file = plugin_dir / relative
    if not prompt_file.exists():
        return "", f"prompt file not found at {prompt_file}"
    try:
        return prompt_file.read_text(encoding="utf-8"), None
    except OSError as e:
        return "", f"prompt file unreadable: {e}"


def _system_prompt_for(capability: dict) -> str:
    """Return the system prompt for a workflow run. Combines a stable platform
    preamble with the manifest's business_value/description.
    """
    preamble = (
        "You are a workflow agent in the Colaberry AI Operations Platform. "
        "You execute a single, well-scoped task end-to-end and report results "
        "via a strict JSON response contract. Be concrete, specific, and "
        "actionable. Cite real file paths, real route patterns, real component "
        "names. Never invent identifiers."
    )
    bv = (capability.get("business_value") or "").strip()
    desc = (capability.get("description") or "").strip()
    extras = []
    if bv:
        extras.append(f"Business value: {bv}")
    if desc:
        extras.append(f"What this workflow does: {desc}")
    return preamble + ("\n\n" + "\n\n".join(extras) if extras else "")


def _format_user_message(prompt_text: str, capability: dict, inputs: dict) -> str:
    """Combine the plugin prompt with the resolved inputs and the contract addendum."""
    # Substitute inputs into the prompt using simple {placeholder} replacement.
    rendered = prompt_text
    for name, value in (inputs or {}).items():
        placeholder = "{" + str(name) + "}"
        rendered = rendered.replace(placeholder, str(value))

    inputs_section = _format_inputs_table(capability.get("inputs") or [], inputs)
    return f"{rendered}\n\n{inputs_section}\n{contract_prompt_addendum()}"


def _format_inputs_table(input_schema: list[dict], inputs: dict) -> str:
    if not input_schema:
        return ""
    lines = ["## Provided Inputs", ""]
    for spec in input_schema:
        name = spec.get("name", "")
        value = inputs.get(name, spec.get("default", ""))
        lines.append(f"- **{name}** ({spec.get('type', 'string')}): {value}")
    return "\n".join(lines)


def _finalize_timing(run: RunRecord, start: datetime | None = None) -> None:
    now = datetime.now(timezone.utc)
    run.finished_at = now.isoformat()
    if start is not None:
        run.duration_ms = int((now - start).total_seconds() * 1000)


def _persist(run: RunRecord) -> None:
    """Atomic write to output/ops_platform/runs/{run_id}.json."""
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    target = _RUNS_DIR / f"{run.run_id}.json"
    fd, tmp_path = tempfile.mkstemp(dir=str(_RUNS_DIR), suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(run.to_dict(), f, indent=2, ensure_ascii=False)
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
