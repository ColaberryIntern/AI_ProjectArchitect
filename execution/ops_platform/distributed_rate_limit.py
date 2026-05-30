"""Distributed rate limiting — single-host multi-process counters.

Scope honesty
-------------
Counters live in ``output/ops_platform/rate_limits/{bucket}.json`` and are
incremented under ``distributed_lock``. Multiple processes on the SAME HOST
see consistent counts. Multi-host requires a network-backed counter
(Redis INCR + EXPIRE) — this module's public API stays unchanged when that
swap happens.

Buckets
-------
A bucket is keyed by ``(kind, identifier, capability_id)``. Examples:
  user:alice:summarize_proposal
  workspace:sales:summarize_proposal
  capability:*:summarize_proposal

You declare a policy via ``set_policy()`` and call ``check_and_increment()``
on every gated action. The check is read-modify-write under one lock —
atomic across processes.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, distributed_lock

logger = logging.getLogger(__name__)

_RATE_DIR = OUTPUT_DIR / "ops_platform" / "rate_limits"
_POLICIES_PATH = _RATE_DIR / "_policies.json"


@dataclass
class Policy:
    kind: str                       # "user" | "workspace" | "capability"
    max_calls: int
    window_seconds: int
    burst_max: int | None = None    # optional burst cap (short-window)
    burst_window_seconds: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RateLimitDecision:
    allowed: bool
    bucket: str
    current_count: int
    limit: int
    window_seconds: int
    retry_after_seconds: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def set_policy(*, kind: str, max_calls: int, window_seconds: int,
                burst_max: int | None = None,
                burst_window_seconds: int | None = None) -> Policy:
    if kind not in ("user", "workspace", "capability"):
        raise ValueError("kind must be user|workspace|capability")
    _RATE_DIR.mkdir(parents=True, exist_ok=True)
    policies = _load_policies()
    policies[kind] = Policy(kind=kind, max_calls=max_calls,
                              window_seconds=window_seconds,
                              burst_max=burst_max,
                              burst_window_seconds=burst_window_seconds).to_dict()
    _POLICIES_PATH.write_text(json.dumps(policies, indent=2), encoding="utf-8")
    audit_log.record(
        action="rate_limit.policy_set", entity_type="rate_limit_policy",
        entity_id=kind,
        actor={"name": "rate_limit_admin", "system": True},
        new_state={"max_calls": max_calls, "window_seconds": window_seconds},
    )
    return Policy(**policies[kind])


def get_policy(kind: str) -> Policy | None:
    policies = _load_policies()
    raw = policies.get(kind)
    return Policy(**raw) if raw else None


def check_and_increment(
    *,
    kind: str,
    identifier: str,
    capability_id: str = "*",
) -> RateLimitDecision:
    """Read-modify-write under one lock. Returns a RateLimitDecision; the
    caller decides whether to honor ``allowed=False``."""
    policy = get_policy(kind)
    if policy is None:
        return RateLimitDecision(allowed=True, bucket=f"{kind}:{identifier}:{capability_id}",
                                   current_count=0, limit=0, window_seconds=0,
                                   reason="no policy configured")
    bucket = f"{kind}:{identifier}:{capability_id}"
    bucket_path = _RATE_DIR / f"{_slug(bucket)}.json"
    now = time.time()
    cutoff = now - policy.window_seconds
    lock_name = f"rate_limit.{_slug(bucket)}"
    with distributed_lock.held(lock_name, lease_seconds=10):
        history = _read_history(bucket_path)
        history = [t for t in history if t > cutoff]
        if len(history) >= policy.max_calls:
            decision = RateLimitDecision(
                allowed=False, bucket=bucket,
                current_count=len(history), limit=policy.max_calls,
                window_seconds=policy.window_seconds,
                retry_after_seconds=max(1, int(history[0] + policy.window_seconds - now)),
                reason=(f"{kind} rate limit {policy.max_calls}/"
                        f"{policy.window_seconds}s exceeded"),
            )
            audit_log.record(
                action="rate_limit.denied", entity_type="rate_limit",
                entity_id=bucket,
                actor={"name": identifier},
                metadata=decision.to_dict(),
            )
            return decision
        # Burst check
        if policy.burst_max and policy.burst_window_seconds:
            burst_cutoff = now - policy.burst_window_seconds
            burst_count = sum(1 for t in history if t > burst_cutoff)
            if burst_count >= policy.burst_max:
                decision = RateLimitDecision(
                    allowed=False, bucket=bucket,
                    current_count=burst_count, limit=policy.burst_max,
                    window_seconds=policy.burst_window_seconds,
                    retry_after_seconds=policy.burst_window_seconds,
                    reason=(f"burst limit {policy.burst_max}/"
                            f"{policy.burst_window_seconds}s exceeded"),
                )
                audit_log.record(
                    action="rate_limit.denied", entity_type="rate_limit",
                    entity_id=bucket, actor={"name": identifier},
                    metadata=decision.to_dict(),
                )
                return decision
        history.append(now)
        _write_history(bucket_path, history)
        return RateLimitDecision(
            allowed=True, bucket=bucket,
            current_count=len(history), limit=policy.max_calls,
            window_seconds=policy.window_seconds,
        )


def current_count(*, kind: str, identifier: str, capability_id: str = "*") -> int:
    policy = get_policy(kind)
    if policy is None:
        return 0
    bucket_path = _RATE_DIR / f"{_slug(f'{kind}:{identifier}:{capability_id}')}.json"
    now = time.time()
    cutoff = now - policy.window_seconds
    return sum(1 for t in _read_history(bucket_path) if t > cutoff)


# ── Internal ───────────────────────────────────────────────────────────


def _load_policies() -> dict:
    if not _POLICIES_PATH.exists():
        return {}
    try:
        return json.loads(_POLICIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_history(path: Path) -> list[float]:
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return []


def _write_history(path: Path, history: list[float]) -> None:
    _RATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history), encoding="utf-8")


def _slug(bucket: str) -> str:
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in bucket)
