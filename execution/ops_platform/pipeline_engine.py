"""Pipeline engine — composes multiple workflow runs into a single executable flow.

A pipeline:
- has its own manifest (validated against pipeline_manifest.schema.json)
- declares N steps, each pointing at a capability_id (workflow or agent)
- threads outputs between steps via input_bindings
- can be built-in (under /plugins/pipelines/<slug>/manifest.json) OR
  user-created (saved under output/ops_platform/pipelines/{pipeline_id}.json)
- emits a PipelineRunRecord per execution that references the underlying
  per-step RunRecord ids

Execution model:
- Sequential by default. parallel_independent / fan_out reserved for future.
- Each step runs through workflow_runner.run_workflow, so the response
  contract, persistence, citation injector etc all still apply.
- Failed steps honor on_failure: abort | skip | retry_once.
- The pipeline run is persisted at output/ops_platform/pipeline_runs/{id}.json.
- Steps include co-occurrence info for the operational graph builder.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

from config.settings import OUTPUT_DIR, PROJECT_ROOT, SCHEMAS_DIR
from execution.ops_platform.capability_registry import CapabilityRegistry, default_registry
from execution.ops_platform import workflow_runner

logger = logging.getLogger(__name__)

_PIPELINES_DIR = OUTPUT_DIR / "ops_platform" / "pipelines"          # user-created
_PLUGIN_PIPELINES_DIR = PROJECT_ROOT / "plugins" / "pipelines"      # built-in
_PIPELINE_RUNS_DIR = OUTPUT_DIR / "ops_platform" / "pipeline_runs"
_MANIFEST_SCHEMA = SCHEMAS_DIR / "ops" / "pipeline_manifest.schema.json"
_RUN_SCHEMA = SCHEMAS_DIR / "ops" / "pipeline_run.schema.json"


# ── Data shapes ─────────────────────────────────────────────────────────


@dataclass
class StepRunRecord:
    step_id: str
    capability_id: str
    run_id: str | None
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineRunRecord:
    pipeline_run_id: str
    pipeline_id: str
    started_at: str
    finished_at: str | None
    status: str
    initiator: dict = field(default_factory=dict)
    pipeline_inputs: dict = field(default_factory=dict)
    step_runs: list[StepRunRecord] = field(default_factory=list)
    duration_ms: int | None = None
    outputs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["step_runs"] = [s if isinstance(s, dict) else s for s in out["step_runs"]]
        return out


@dataclass
class PipelineInvalid(Exception):
    errors: list[str]

    def __str__(self) -> str:
        return f"pipeline manifest invalid: {self.errors}"


# ── Schema validation ──────────────────────────────────────────────────


def _load_manifest_schema() -> dict:
    global _M_CACHE
    try:
        return _M_CACHE
    except NameError:
        _M_CACHE = json.loads(_MANIFEST_SCHEMA.read_text(encoding="utf-8"))
        return _M_CACHE


def _load_run_schema() -> dict:
    global _R_CACHE
    try:
        return _R_CACHE
    except NameError:
        _R_CACHE = json.loads(_RUN_SCHEMA.read_text(encoding="utf-8"))
        return _R_CACHE


def _validate_manifest(manifest: dict) -> list[str]:
    schema = _load_manifest_schema()
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(manifest), key=lambda e: e.absolute_path)
    ]


def _validate_run(record: dict) -> list[str]:
    schema = _load_run_schema()
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(record), key=lambda e: e.absolute_path)
    ]


# ── Manifest CRUD ───────────────────────────────────────────────────────


def save_pipeline(manifest: dict) -> dict:
    """Persist a user-created pipeline. Raises PipelineInvalid on schema failure."""
    errors = _validate_manifest(manifest)
    if errors:
        raise PipelineInvalid(errors)
    _PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    target = _PIPELINES_DIR / f"{manifest['pipeline_id']}.json"
    fd, tmp_path = tempfile.mkstemp(dir=str(_PIPELINES_DIR), suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return manifest


def load_pipeline(pipeline_id: str) -> dict | None:
    """Return manifest by id, checking user-created first, then built-in."""
    user_path = _PIPELINES_DIR / f"{pipeline_id}.json"
    if user_path.exists():
        try:
            return json.loads(user_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if _PLUGIN_PIPELINES_DIR.exists():
        for sub in _PLUGIN_PIPELINES_DIR.iterdir():
            if not sub.is_dir():
                continue
            candidate = sub / "manifest.json"
            if not candidate.exists():
                continue
            try:
                m = json.loads(candidate.read_text(encoding="utf-8"))
                if m.get("pipeline_id") == pipeline_id:
                    return m
            except (OSError, json.JSONDecodeError):
                continue
    return None


def list_pipelines() -> list[dict]:
    """Return all pipeline manifests (built-in + user-created)."""
    out: list[dict] = []
    seen: set[str] = set()
    # built-in first
    if _PLUGIN_PIPELINES_DIR.exists():
        for sub in sorted(_PLUGIN_PIPELINES_DIR.iterdir()):
            if not sub.is_dir():
                continue
            candidate = sub / "manifest.json"
            if not candidate.exists():
                continue
            try:
                m = json.loads(candidate.read_text(encoding="utf-8"))
                if "pipeline_id" in m and m["pipeline_id"] not in seen:
                    m["_source"] = "built_in"
                    out.append(m)
                    seen.add(m["pipeline_id"])
            except (OSError, json.JSONDecodeError):
                continue
    # user-created
    if _PIPELINES_DIR.exists():
        for p in sorted(_PIPELINES_DIR.glob("*.json")):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
                if "pipeline_id" in m and m["pipeline_id"] not in seen:
                    m["_source"] = "user_created"
                    out.append(m)
                    seen.add(m["pipeline_id"])
            except (OSError, json.JSONDecodeError):
                continue
    return out


# ── Execution ───────────────────────────────────────────────────────────


def run_pipeline(
    pipeline_id: str,
    pipeline_inputs: dict | None = None,
    *,
    initiator: dict | None = None,
    registry: CapabilityRegistry | None = None,
) -> PipelineRunRecord:
    """Execute a pipeline end-to-end. Always returns a PipelineRunRecord."""
    reg = registry or default_registry()
    manifest = load_pipeline(pipeline_id)

    if manifest is None:
        return _record_error(pipeline_id, "pipeline not found", pipeline_inputs, initiator)

    errors = _validate_manifest(manifest)
    if errors:
        return _record_error(
            pipeline_id, f"manifest invalid: {errors[:2]}", pipeline_inputs, initiator
        )

    record = PipelineRunRecord(
        pipeline_run_id=str(uuid.uuid4()),
        pipeline_id=pipeline_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
        status="running",
        initiator=dict(initiator or {}),
        pipeline_inputs=dict(pipeline_inputs or {}),
    )

    start_mono = datetime.now(timezone.utc)
    step_outputs: dict[str, dict] = {}
    pipeline_failed = False
    abort = False

    execution_strategy = manifest.get("execution_strategy", "sequential")
    steps_by_id = {s["step_id"]: s for s in manifest["steps"]}

    # Wave-based execution: at each iteration, find every step whose
    # dependencies have all reached a terminal state (succeeded / failed /
    # skipped). Sequential strategy still runs each wave serially in
    # declaration order, but the dep graph is honored either way.
    completed: set[str] = set()
    ordered_step_ids = [s["step_id"] for s in manifest["steps"]]

    while True:
        if abort:
            for sid in ordered_step_ids:
                if sid not in completed:
                    record.step_runs.append(StepRunRecord(
                        step_id=sid,
                        capability_id=steps_by_id[sid]["capability_id"],
                        run_id=None,
                        status="skipped",
                    ))
                    completed.add(sid)
            break

        # Pick the wave: steps not yet completed whose deps are all completed.
        wave: list[dict] = []
        for sid in ordered_step_ids:
            if sid in completed:
                continue
            deps = steps_by_id[sid].get("depends_on") or []
            if all(d in completed for d in deps):
                wave.append(steps_by_id[sid])
        if not wave:
            break

        # Conditional evaluation: drop steps whose `when` clause is falsy.
        runnable: list[dict] = []
        for step in wave:
            cond = step.get("when")
            if cond and not _evaluate_condition(cond, pipeline_inputs=record.pipeline_inputs,
                                                step_outputs=step_outputs):
                record.step_runs.append(StepRunRecord(
                    step_id=step["step_id"],
                    capability_id=step["capability_id"],
                    run_id=None,
                    status="skipped",
                    error_message="condition not met",
                ))
                completed.add(step["step_id"])
            elif any(_dep_failed(record.step_runs, d) for d in (step.get("depends_on") or [])):
                record.step_runs.append(StepRunRecord(
                    step_id=step["step_id"],
                    capability_id=step["capability_id"],
                    run_id=None,
                    status="skipped",
                    error_message="upstream dependency failed",
                ))
                completed.add(step["step_id"])
            else:
                runnable.append(step)

        if not runnable:
            continue

        # parallel_independent only triggers when the wave has multiple
        # steps that share no inter-wave dependency. For sequential, the
        # wave still runs but one-at-a-time.
        if execution_strategy in ("parallel_independent", "fan_out") and len(runnable) > 1:
            results = _execute_wave_parallel(runnable, reg, pipeline_inputs=record.pipeline_inputs,
                                             step_outputs=step_outputs)
        else:
            results = []
            for step in runnable:
                results.append(_execute_one(step, reg,
                                            pipeline_inputs=record.pipeline_inputs,
                                            step_outputs=step_outputs))

        for step, sr in zip(runnable, results):
            record.step_runs.append(sr)
            completed.add(step["step_id"])
            if sr.status in ("succeeded", "retried_succeeded"):
                run = workflow_runner.get_run(sr.run_id) if sr.run_id else None
                step_outputs[step["step_id"]] = (run.response if run and run.response else {})
            else:
                pipeline_failed = True
                policy = step.get("on_failure", "abort")
                if policy == "abort":
                    abort = True

    record.finished_at = datetime.now(timezone.utc).isoformat()
    record.duration_ms = int((datetime.now(timezone.utc) - start_mono).total_seconds() * 1000)
    record.outputs = _resolve_pipeline_outputs(
        manifest.get("output_mappings") or [], step_outputs
    )
    record.status = _final_status(record.step_runs, pipeline_failed, abort)
    _persist_run(record)
    return record


def replay_pipeline_from(
    pipeline_run_id: str,
    *,
    from_step_id: str,
    registry: CapabilityRegistry | None = None,
) -> PipelineRunRecord | None:
    """Recovery: rerun a pipeline starting at ``from_step_id``, reusing the
    inputs and any prior step outputs that exist. Returns a fresh
    PipelineRunRecord with the recovery run id."""
    prior = get_pipeline_run(pipeline_run_id)
    if prior is None:
        return None
    manifest = load_pipeline(prior.pipeline_id)
    if manifest is None:
        return None
    step_ids = [s["step_id"] for s in manifest["steps"]]
    if from_step_id not in step_ids:
        return None
    idx = step_ids.index(from_step_id)
    # Build a shadow manifest where the prefix steps are stubbed to "skip"
    # and the suffix runs normally. Simplest implementation: trim manifest
    # to steps[idx:] and seed pipeline_inputs with the prior outputs.
    trimmed = dict(manifest)
    trimmed["steps"] = manifest["steps"][idx:]
    # Patch out depends_on references to dropped predecessors.
    kept = {s["step_id"] for s in trimmed["steps"]}
    for s in trimmed["steps"]:
        s["depends_on"] = [d for d in (s.get("depends_on") or []) if d in kept]
    # Stash the patched manifest under a recovery-only id, then run.
    recovery_id = f"{manifest['pipeline_id']}_recover_{uuid.uuid4().hex[:8]}"
    trimmed["pipeline_id"] = recovery_id
    save_pipeline(trimmed)
    return run_pipeline(recovery_id, prior.pipeline_inputs, initiator={"name": "recovery"},
                        registry=registry)


def get_pipeline_run(pipeline_run_id: str) -> PipelineRunRecord | None:
    path = _PIPELINE_RUNS_DIR / f"{pipeline_run_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    step_runs = [StepRunRecord(**s) for s in data.pop("step_runs", [])]
    return PipelineRunRecord(**data, step_runs=step_runs) if False else _rebuild_run(data, step_runs)


def list_pipeline_runs(*, pipeline_id: str | None = None, limit: int = 50) -> list[PipelineRunRecord]:
    if not _PIPELINE_RUNS_DIR.exists():
        return []
    paths = sorted(_PIPELINE_RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[PipelineRunRecord] = []
    for p in paths:
        if len(out) >= limit:
            break
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if pipeline_id and data.get("pipeline_id") != pipeline_id:
            continue
        step_runs = [StepRunRecord(**s) for s in data.pop("step_runs", [])]
        out.append(_rebuild_run(data, step_runs))
    return out


# ── Internal helpers ────────────────────────────────────────────────────


def _rebuild_run(data: dict, step_runs: list[StepRunRecord]) -> PipelineRunRecord:
    return PipelineRunRecord(
        pipeline_run_id=data["pipeline_run_id"],
        pipeline_id=data["pipeline_id"],
        started_at=data["started_at"],
        finished_at=data.get("finished_at"),
        status=data["status"],
        initiator=data.get("initiator") or {},
        pipeline_inputs=data.get("pipeline_inputs") or {},
        step_runs=step_runs,
        duration_ms=data.get("duration_ms"),
        outputs=data.get("outputs") or {},
    )


def _execute_step(step: dict, capability_id: str, resolved_inputs: dict,
                  registry: CapabilityRegistry) -> StepRunRecord:
    """Run a single step, honoring on_failure (retry_once, retry_n)."""
    started = datetime.now(timezone.utc).isoformat()
    run = workflow_runner.run_workflow(capability_id, resolved_inputs, registry=registry)
    sr = StepRunRecord(
        step_id=step["step_id"],
        capability_id=capability_id,
        run_id=run.run_id,
        status=run.status,
        started_at=started,
        finished_at=datetime.now(timezone.utc).isoformat(),
        error_message=run.error_message,
    )
    # Retry policy: on_failure can be "retry_once" or a {"retry": N} dict
    policy = step.get("on_failure") or "abort"
    retries = 0
    if policy == "retry_once":
        retries = 1
    elif isinstance(policy, dict):
        retries = int(policy.get("retry", 0))
    for _ in range(retries):
        if sr.status in ("succeeded", "retried_succeeded"):
            break
        run2 = workflow_runner.run_workflow(capability_id, resolved_inputs, registry=registry)
        if run2.status == "succeeded":
            sr = StepRunRecord(
                step_id=step["step_id"],
                capability_id=capability_id,
                run_id=run2.run_id,
                status="retried_succeeded",
                started_at=started,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            break
    return sr


def _execute_one(step: dict, registry: CapabilityRegistry, *,
                 pipeline_inputs: dict, step_outputs: dict) -> StepRunRecord:
    resolved_inputs, errors = _resolve_inputs(
        step.get("input_bindings") or {},
        pipeline_inputs=pipeline_inputs,
        step_outputs=step_outputs,
    )
    if errors:
        return StepRunRecord(
            step_id=step["step_id"],
            capability_id=step["capability_id"],
            run_id=None, status="error",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            error_message=f"input binding error: {errors[0]}",
        )
    return _execute_step(step, step["capability_id"], resolved_inputs, registry)


def _execute_wave_parallel(steps: list[dict], registry: CapabilityRegistry, *,
                            pipeline_inputs: dict, step_outputs: dict) -> list[StepRunRecord]:
    """Run a wave of steps concurrently using a small thread pool. Bounded
    parallelism so we never accidentally drop 50 LLM calls at once."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    max_workers = min(4, len(steps))
    results: dict[str, StepRunRecord] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _execute_one, step, registry,
                pipeline_inputs=pipeline_inputs, step_outputs=step_outputs,
            ): step["step_id"]
            for step in steps
        }
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                results[sid] = fut.result()
            except Exception as e:
                step = next(s for s in steps if s["step_id"] == sid)
                results[sid] = StepRunRecord(
                    step_id=sid, capability_id=step["capability_id"],
                    run_id=None, status="error",
                    error_message=f"parallel execution exception: {e}",
                )
    # Return in the order of the original wave so callers can zip().
    return [results[s["step_id"]] for s in steps]


def _evaluate_condition(condition, *, pipeline_inputs: dict,
                         step_outputs: dict[str, dict]) -> bool:
    """Evaluate a step's `when` clause. Three forms supported:

        - "$pipeline.foo" / "$step.s.field"  → truthy lookup
        - {"equals": ["$step.s.field", "X"]} → equality test
        - {"truthy": "$step.s.field"}        → presence test

    Anything we can't parse evaluates as True (fail-open — we'd rather run a
    step than silently skip it because a condition shape changed)."""
    if condition is None:
        return True
    if isinstance(condition, str):
        return bool(_lookup_ref(condition, pipeline_inputs=pipeline_inputs, step_outputs=step_outputs))
    if isinstance(condition, dict):
        if "equals" in condition:
            args = condition["equals"]
            if isinstance(args, list) and len(args) == 2:
                left = _lookup_ref(args[0], pipeline_inputs=pipeline_inputs, step_outputs=step_outputs)
                right = _lookup_ref(args[1], pipeline_inputs=pipeline_inputs, step_outputs=step_outputs)
                return left == right
        if "truthy" in condition:
            return bool(_lookup_ref(condition["truthy"], pipeline_inputs=pipeline_inputs,
                                    step_outputs=step_outputs))
    return True


def _lookup_ref(value, *, pipeline_inputs: dict, step_outputs: dict[str, dict]):
    """Resolve "$pipeline.x" / "$step.s.field" or pass through literal."""
    if not isinstance(value, str):
        return value
    m = _REF_RE.match(value)
    if not m:
        return value
    step_ref, field, pipe_ref = m.group(1), m.group(2), m.group(3)
    if pipe_ref is not None:
        return pipeline_inputs.get(pipe_ref)
    if step_ref in step_outputs:
        return _follow_path(step_outputs[step_ref], field)
    return None


def _dep_failed(step_runs: list[StepRunRecord], dep_id: str) -> bool:
    for sr in step_runs:
        if sr.step_id == dep_id:
            return sr.status not in ("succeeded", "retried_succeeded")
    return True  # dep never ran = treat as failed


_REF_RE = re.compile(r"^\$step\.([a-z0-9_]+)\.(.+)$|^\$pipeline\.([a-zA-Z0-9_]+)$")


def _resolve_inputs(
    bindings: dict,
    *,
    pipeline_inputs: dict,
    step_outputs: dict[str, dict],
) -> tuple[dict, list[str]]:
    """Resolve a step's input_bindings dict into concrete values.

    Binding values can be:
      - a literal string/number/bool/null   → pass through
      - "$pipeline.<input_name>"            → look up pipeline_inputs
      - "$step.<step_id>.<field>"           → look up step_outputs[step_id][field]

    Returns (resolved_inputs_dict, errors).
    """
    out: dict = {}
    errors: list[str] = []
    for name, value in bindings.items():
        if isinstance(value, str):
            m = _REF_RE.match(value)
            if m:
                step_ref, field, pipe_ref = m.group(1), m.group(2), m.group(3)
                if pipe_ref is not None:
                    if pipe_ref not in pipeline_inputs:
                        errors.append(f"pipeline input '{pipe_ref}' missing")
                        continue
                    out[name] = pipeline_inputs[pipe_ref]
                else:
                    if step_ref not in step_outputs:
                        errors.append(f"step '{step_ref}' has no output yet")
                        continue
                    out[name] = _follow_path(step_outputs[step_ref], field)
                continue
        out[name] = value
    return out, errors


def _follow_path(obj: Any, path: str) -> Any:
    """Tiny dotted/bracketed path resolver: a.b[0].c → obj['a']['b'][0]['c']."""
    cur = obj
    for part in re.split(r"\.|(?=\[)", path):
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            try:
                idx = int(part[1:-1])
                cur = cur[idx]
            except (TypeError, ValueError, IndexError):
                return None
        else:
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
    return cur


def _resolve_pipeline_outputs(mappings: list[dict], step_outputs: dict[str, dict]) -> dict:
    out: dict = {}
    for m in mappings:
        src = m.get("source", "")
        tgt = m.get("target", "")
        if not src or not tgt:
            continue
        match = _REF_RE.match(src)
        if not match:
            out[tgt] = src
            continue
        step_ref, field, _ = match.group(1), match.group(2), match.group(3)
        if step_ref and step_ref in step_outputs:
            out[tgt] = _follow_path(step_outputs[step_ref], field)
    return out


def _final_status(step_runs: list[StepRunRecord], any_failed: bool, aborted: bool) -> str:
    if aborted:
        return "aborted"
    succeeded = sum(1 for s in step_runs if s.status in ("succeeded", "retried_succeeded"))
    total = len(step_runs)
    if succeeded == total:
        return "succeeded"
    if succeeded == 0:
        return "failed"
    return "partial_failure"


def _record_error(pipeline_id: str, msg: str, inputs: dict | None,
                  initiator: dict | None) -> PipelineRunRecord:
    rec = PipelineRunRecord(
        pipeline_run_id=str(uuid.uuid4()),
        pipeline_id=pipeline_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        status="failed",
        initiator=dict(initiator or {}),
        pipeline_inputs=dict(inputs or {}),
        step_runs=[],
        duration_ms=0,
        outputs={"_error": msg},
    )
    _persist_run(rec)
    return rec


def _persist_run(rec: PipelineRunRecord) -> None:
    _PIPELINE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    target = _PIPELINE_RUNS_DIR / f"{rec.pipeline_run_id}.json"
    payload = rec.to_dict()
    # serialize step_runs which are dataclasses
    payload["step_runs"] = [
        sr if isinstance(sr, dict) else asdict(sr) for sr in rec.step_runs
    ]
    fd, tmp_path = tempfile.mkstemp(dir=str(_PIPELINE_RUNS_DIR), suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    try:
        from execution.ops_platform import cache_bus
        cache_bus.emit(cache_bus.Topic.PIPELINE_RUN_RECORDED, {
            "pipeline_run_id": rec.pipeline_run_id,
            "pipeline_id": rec.pipeline_id,
            "status": rec.status,
        })
    except Exception:
        logger.warning("cache_bus emit failed for PIPELINE_RUN_RECORDED", exc_info=True)


def save_pipeline(manifest: dict) -> Path:
    """Persist a user-created pipeline manifest under output/ops_platform/pipelines/.
    Validates the manifest before write. Raises ValueError on schema failure."""
    errors = _validate_manifest(manifest)
    if errors:
        raise ValueError(f"manifest invalid: {errors}")
    pipeline_id = manifest["pipeline_id"]
    _PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    target = _PIPELINES_DIR / f"{pipeline_id}.json"
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from execution.ops_platform import cache_bus
        cache_bus.emit(cache_bus.Topic.PIPELINE_CREATED, {"pipeline_id": pipeline_id})
    except Exception:
        logger.warning("cache_bus emit failed for PIPELINE_CREATED", exc_info=True)
    return target
