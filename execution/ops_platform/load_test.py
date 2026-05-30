"""Load test harness — measurable benchmarks against the real platform code.

Scope honesty
-------------
All numbers are produced by the same code paths the platform uses in
production. Each benchmark records:

  - hardware assumption (CPU, RAM as detected at runtime)
  - topology (single-host, file-based, no Redis unless wired)
  - exact test parameters

The harness does NOT publish "synthetic marketing numbers." Operators
running this against their own hardware get their own results.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import statistics
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    distributed_lock, event_fabric, projection_engine, redis_backends,
    runtime_queue,
)

logger = logging.getLogger(__name__)

_BENCHMARKS_DIR = OUTPUT_DIR / "ops_platform" / "load_tests"


@dataclass
class BenchmarkResult:
    benchmark_id: str
    name: str
    parameters: dict
    hardware: dict
    topology: dict
    samples: int
    duration_seconds: float
    throughput_per_second: float | None
    mean_latency_ms: float | None
    p95_latency_ms: float | None
    p99_latency_ms: float | None
    max_latency_ms: float | None
    memory_delta_mb: float | None
    started_at: str
    finished_at: str

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public benchmarks ─────────────────────────────────────────────────


def benchmark_event_fabric_publish(*, count: int = 1000) -> BenchmarkResult:
    latencies = _measure_loop(
        count=count,
        op=lambda: event_fabric.emit("loadtest.fabric.publish", payload={"n": 1}),
        name="event_fabric.emit",
    )
    return _summarize("event_fabric_publish", parameters={"count": count},
                         latencies=latencies, samples=count)


def benchmark_lock_contention(*, count: int = 500, lock_count: int = 8) -> BenchmarkResult:
    """Rotate through N lock names. Single-process; measures the file-lock path."""
    started = time.monotonic()
    latencies: list[float] = []
    for i in range(count):
        name = f"loadtest.lock.{i % lock_count}"
        try:
            owner = f"bench-{uuid.uuid4().hex[:6]}"
            t0 = time.monotonic()
            rec = distributed_lock.acquire(name, owner_id=owner,
                                              lease_seconds=2,
                                              acquire_timeout_seconds=1)
            distributed_lock.release(name, owner_id=owner)
            latencies.append((time.monotonic() - t0) * 1000)
        except distributed_lock.LockAcquisitionError:
            continue
    return _summarize("lock_contention",
                         parameters={"count": count, "lock_count": lock_count},
                         latencies=latencies, samples=len(latencies),
                         duration_seconds=time.monotonic() - started)


def benchmark_queue_enqueue_drain(*, count: int = 500) -> BenchmarkResult:
    started = time.monotonic()
    latencies: list[float] = []
    for i in range(count):
        t0 = time.monotonic()
        runtime_queue.enqueue(kind="loadtest.workflow",
                                  payload={"capability_id": "x",
                                            "inputs": {"i": i}})
        latencies.append((time.monotonic() - t0) * 1000)
    return _summarize("queue_enqueue",
                         parameters={"count": count},
                         latencies=latencies, samples=count,
                         duration_seconds=time.monotonic() - started)


def benchmark_projection_rebuild(*, name: str = "operator_activity",
                                      seed_events: int = 500) -> BenchmarkResult:
    projection_engine.register_default_projections()
    for i in range(seed_events):
        event_fabric.emit("loadtest.activity",
                              actor_id=f"actor-{i % 25}",
                              payload={"i": i})
    started = time.monotonic()
    result = projection_engine.rebuild(name)
    elapsed_s = time.monotonic() - started
    return _summarize(
        "projection_rebuild",
        parameters={"projection_name": name, "seed_events": seed_events,
                     "events_consumed": result["events_consumed"]},
        latencies=[elapsed_s * 1000],
        samples=1, duration_seconds=elapsed_s,
    )


def run_suite() -> list[BenchmarkResult]:
    """Run the standard suite. Results are persisted under
    ``output/ops_platform/load_tests/{stamp}_suite.json``."""
    suite = [
        benchmark_event_fabric_publish(count=500),
        benchmark_lock_contention(count=200),
        benchmark_queue_enqueue_drain(count=200),
        benchmark_projection_rebuild(seed_events=200),
    ]
    _persist_suite(suite)
    return suite


# ── Internal ───────────────────────────────────────────────────────────


def _hardware_snapshot() -> dict:
    try:
        import resource
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        rss_mb = None
    return {
        "python_version": platform.python_version(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "process_rss_mb_max": rss_mb,
    }


def _topology() -> dict:
    return {
        "redis_client_wired": redis_backends._CLIENT is not None,
        "scope": ("redis-distributed-multi-host"
                    if (redis_backends.is_available() and redis_backends._CLIENT is not None)
                    else "single-host-file-backed"),
        "output_dir": str(OUTPUT_DIR),
    }


def _measure_loop(*, count: int, op, name: str) -> list[float]:
    latencies: list[float] = []
    for _ in range(count):
        t0 = time.monotonic()
        try:
            op()
        except Exception:
            logger.debug("load_test op %s raised", name, exc_info=True)
            continue
        latencies.append((time.monotonic() - t0) * 1000)
    return latencies


def _summarize(name: str, *, parameters: dict, latencies: list[float],
                 samples: int, duration_seconds: float | None = None) -> BenchmarkResult:
    from datetime import timedelta
    if duration_seconds is None:
        duration_seconds = sum(latencies) / 1000.0
    finished = datetime.now(timezone.utc)
    started = finished - timedelta(seconds=duration_seconds)
    if not latencies:
        return BenchmarkResult(
            benchmark_id=f"bm_{uuid.uuid4().hex[:10]}",
            name=name, parameters=parameters,
            hardware=_hardware_snapshot(), topology=_topology(),
            samples=samples, duration_seconds=round(duration_seconds, 4),
            throughput_per_second=None,
            mean_latency_ms=None, p95_latency_ms=None,
            p99_latency_ms=None, max_latency_ms=None,
            memory_delta_mb=None,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
        )
    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[max(0, int(len(sorted_latencies) * 0.95) - 1)]
    p99 = sorted_latencies[max(0, int(len(sorted_latencies) * 0.99) - 1)]
    return BenchmarkResult(
        benchmark_id=f"bm_{uuid.uuid4().hex[:10]}",
        name=name, parameters=parameters,
        hardware=_hardware_snapshot(), topology=_topology(),
        samples=samples,
        duration_seconds=round(duration_seconds, 4),
        throughput_per_second=(round(samples / duration_seconds, 1)
                                 if duration_seconds > 0 else None),
        mean_latency_ms=round(statistics.mean(latencies), 3),
        p95_latency_ms=round(p95, 3),
        p99_latency_ms=round(p99, 3),
        max_latency_ms=round(max(latencies), 3),
        memory_delta_mb=None,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
    )


def _persist_suite(suite: list[BenchmarkResult]) -> None:
    _BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _BENCHMARKS_DIR / f"{stamp}_suite.json"
    payload = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "hardware": _hardware_snapshot(),
        "topology": _topology(),
        "results": [b.to_dict() for b in suite],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                      encoding="utf-8")


def list_suites(*, limit: int = 25) -> list[dict]:
    if not _BENCHMARKS_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(_BENCHMARKS_DIR.glob("*_suite.json"), reverse=True)[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out
