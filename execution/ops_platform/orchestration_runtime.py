"""Distributed orchestration runtime — adds multi-worker step claims with
lease + fencing on top of Phase 8E's ``orchestration_engine``.

Scope honesty
-------------
- File-locked claims: single-host multi-process.
- Redis-backed claims (when ``redis_backends._CLIENT`` is wired):
  multi-host coordination via ``distributed_lock_v2``.
- Every claim records the fencing token so any downstream system can
  reject a stale write from a worker whose lease expired.

Claim lifecycle
---------------
   pending → claimed(owner_token, fencing_token, lease_until)
            → released → completed
                       → reclaimed (lease expired; new owner picks up)
                       → failed
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    audit_log, distributed_lock, event_fabric, orchestration_engine,
    redis_backends,
)

logger = logging.getLogger(__name__)

_CLAIMS_DIR = OUTPUT_DIR / "ops_platform" / "orchestration_claims"

DEFAULT_LEASE_SECONDS = 120


@dataclass
class StepClaim:
    orchestration_id: str
    step_id: str
    owner_token: str
    fencing_token: int
    lease_until_epoch: float
    coordination_scope: str          # "single-host" | "redis-distributed"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Public API ─────────────────────────────────────────────────────────


def claim_step(
    orchestration_id: str,
    *,
    step_id: str,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> StepClaim | None:
    """Claim the step for exclusive execution. Returns None if another worker
    holds the claim (and its lease hasn't expired).

    Uses Redis-backed v2 lock when available — multi-host. Otherwise falls
    back to file-based distributed_lock — single-host multi-process.
    """
    lock_name = _lock_name(orchestration_id, step_id)
    if redis_backends.is_available() and redis_backends._CLIENT is not None:
        from execution.ops_platform import distributed_lock_v2
        try:
            lease = distributed_lock_v2.acquire(
                lock_name, lease_seconds=lease_seconds,
                acquire_timeout_seconds=1,
            )
        except distributed_lock_v2.LockBusy:
            return None
        claim = StepClaim(
            orchestration_id=orchestration_id, step_id=step_id,
            owner_token=lease.owner_token,
            fencing_token=lease.fencing_token,
            lease_until_epoch=lease.expires_at_epoch,
            coordination_scope="redis-distributed",
        )
    else:
        try:
            rec = distributed_lock.acquire(
                lock_name, owner_id=worker_id,
                lease_seconds=lease_seconds, acquire_timeout_seconds=1,
            )
        except distributed_lock.LockAcquisitionError:
            return None
        claim = StepClaim(
            orchestration_id=orchestration_id, step_id=step_id,
            owner_token=worker_id,
            fencing_token=0,  # no fencing in file-based mode
            lease_until_epoch=time.time() + lease_seconds,
            coordination_scope="single-host",
        )
    _persist_claim(claim)
    audit_log.record(
        action="orchestration.step_claimed",
        entity_type="orchestration_step",
        entity_id=f"{orchestration_id}:{step_id}",
        actor={"name": worker_id, "system": True},
        new_state=claim.to_dict(),
    )
    event_fabric.emit(
        "orchestration.step_claimed",
        actor_id=worker_id,
        correlation_id=orchestration_id,
        payload=claim.to_dict(),
        durability_scope=("redis-distributed" if claim.coordination_scope == "redis-distributed"
                           else "single-host"),
        consistency_scope="at-least-once",
    )
    return claim


def heartbeat_claim(claim: StepClaim, *, worker_id: str,
                      lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    if claim.coordination_scope == "redis-distributed":
        from execution.ops_platform import distributed_lock_v2
        ok = distributed_lock_v2.heartbeat(
            _lock_name(claim.orchestration_id, claim.step_id),
            owner_token=claim.owner_token, lease_seconds=lease_seconds,
        )
    else:
        try:
            distributed_lock.heartbeat(
                _lock_name(claim.orchestration_id, claim.step_id),
                owner_id=worker_id, lease_seconds=lease_seconds,
            )
            ok = True
        except distributed_lock.LockAcquisitionError:
            ok = False
    if ok:
        claim.lease_until_epoch = time.time() + lease_seconds
        _persist_claim(claim)
    return ok


def release_claim(claim: StepClaim, *, worker_id: str) -> bool:
    if claim.coordination_scope == "redis-distributed":
        from execution.ops_platform import distributed_lock_v2
        ok = distributed_lock_v2.release(
            _lock_name(claim.orchestration_id, claim.step_id),
            owner_token=claim.owner_token,
        )
    else:
        ok = distributed_lock.release(
            _lock_name(claim.orchestration_id, claim.step_id),
            owner_id=worker_id,
        )
    if ok:
        try:
            (_CLAIMS_DIR / _claim_filename(claim)).unlink()
        except OSError:
            pass
        audit_log.record(
            action="orchestration.step_released",
            entity_type="orchestration_step",
            entity_id=f"{claim.orchestration_id}:{claim.step_id}",
            actor={"name": worker_id, "system": True},
        )
    return ok


def reclaim_expired(*, now_epoch: float | None = None) -> list[StepClaim]:
    """Sweep claims whose lease has passed. Removes the stale claim record
    so a new worker can acquire on its next ``claim_step()`` call."""
    now = now_epoch or time.time()
    expired: list[StepClaim] = []
    if not _CLAIMS_DIR.exists():
        return expired
    for p in _CLAIMS_DIR.glob("claim_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            claim = StepClaim(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if claim.lease_until_epoch <= now:
            try:
                p.unlink()
            except OSError:
                continue
            audit_log.record(
                action="orchestration.step_claim_expired",
                entity_type="orchestration_step",
                entity_id=f"{claim.orchestration_id}:{claim.step_id}",
                actor={"name": "claim_sweeper", "system": True},
                metadata={"previous_owner": claim.owner_token,
                          "coordination_scope": claim.coordination_scope},
            )
            expired.append(claim)
    return expired


def list_active_claims() -> list[StepClaim]:
    if not _CLAIMS_DIR.exists():
        return []
    out: list[StepClaim] = []
    for p in _CLAIMS_DIR.glob("claim_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(StepClaim(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return out


def coordination_mode() -> dict:
    return {
        "scope": ("redis-distributed-multi-host"
                    if (redis_backends.is_available() and redis_backends._CLIENT is not None)
                    else "single-host-multi-process"),
        "fencing_tokens_enabled": (redis_backends.is_available()
                                       and redis_backends._CLIENT is not None),
        "active_claim_count": len(list_active_claims()),
    }


# ── Worker loop integration ────────────────────────────────────────────


def run_one_step(*, worker_id: str) -> dict | None:
    """Pick one orchestration that has a pending step, claim that step,
    advance one tick, release. Returns the resulting state dict or None
    when nothing was claimable."""
    candidates = orchestration_engine.list_orchestrations(state="running")
    candidates.extend(orchestration_engine.list_orchestrations(state="created"))
    for orch in candidates:
        if orch.current_step_index >= len(orch.steps):
            continue
        step_def = orch.steps[orch.current_step_index]
        step_record = orch.step_records[orch.current_step_index]
        if step_record.get("status") in ("succeeded", "failed", "skipped"):
            continue
        claim = claim_step(orch.orchestration_id, step_id=step_def["step_id"],
                              worker_id=worker_id)
        if claim is None:
            continue
        try:
            advanced = orchestration_engine.advance(orch.orchestration_id)
            return advanced.to_dict() if advanced else None
        finally:
            release_claim(claim, worker_id=worker_id)
    return None


def stuck_orchestrations(*, age_minutes: int = 30) -> list[dict]:
    """Find orchestrations whose updated_at is older than the threshold
    but whose state isn't terminal. These need operator attention."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    out: list[dict] = []
    for o in orchestration_engine.list_orchestrations():
        if o.state in ("completed", "failed", "compensated"):
            continue
        try:
            updated = datetime.fromisoformat(o.updated_at)
        except ValueError:
            continue
        if updated < cutoff:
            out.append({
                "orchestration_id": o.orchestration_id,
                "name": o.name, "state": o.state,
                "updated_at": o.updated_at,
                "current_step_index": o.current_step_index,
            })
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _lock_name(orchestration_id: str, step_id: str) -> str:
    safe_orch = "".join(c if c.isalnum() or c in "_-" else "_" for c in orchestration_id)
    safe_step = "".join(c if c.isalnum() or c in "_-" else "_" for c in step_id)
    return f"orch.{safe_orch}.{safe_step}"


def _claim_filename(claim: StepClaim) -> str:
    safe = "".join(c if c.isalnum() or c in "_-" else "_"
                     for c in f"{claim.orchestration_id}_{claim.step_id}")
    return f"claim_{safe}.json"


def _persist_claim(claim: StepClaim) -> None:
    _CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
    (_CLAIMS_DIR / _claim_filename(claim)).write_text(
        json.dumps(claim.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
