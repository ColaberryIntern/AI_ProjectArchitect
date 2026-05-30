"""Retention policy — controlled archival + rotation of persistence dirs.

Scope honesty
-------------
Single-host filesystem. Compresses old files into ``.gz`` archives;
deletes after ``hard_delete_days``. NOT a tiered storage adapter — point
at S3 / GCS at the operator's boot script if needed.

Policies are loaded from env ``OPS_RETENTION_POLICY`` (JSON) or applied
explicitly per call.

Each rotation writes an audit row with the file count + bytes affected.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)


@dataclass
class RotationResult:
    directory: str
    archived_count: int
    deleted_count: int
    bytes_archived: int
    bytes_deleted: int

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_POLICY = {
    "audit": {"archive_days": 30, "hard_delete_days": 180},
    "realtime/events": {"archive_days": 7, "hard_delete_days": 30},
    "tracing": {"archive_days": 7, "hard_delete_days": 30},
    "notifications": {"archive_days": 14, "hard_delete_days": 60},
    "alerts/history": {"archive_days": 30, "hard_delete_days": 365},
}


def apply_policy(*, policy: dict | None = None,
                   actor: dict | str = "retention_sweeper") -> list[RotationResult]:
    """Apply the policy across all configured directories. Returns one
    RotationResult per directory."""
    policy = policy or _load_policy()
    actor_norm = actor if isinstance(actor, dict) else {"name": str(actor), "system": True}
    out: list[RotationResult] = []
    for relative, rule in policy.items():
        target = OUTPUT_DIR / "ops_platform" / relative
        if not target.exists():
            continue
        result = _rotate(target, archive_days=int(rule.get("archive_days", 30)),
                            hard_delete_days=int(rule.get("hard_delete_days", 365)))
        audit_log.record(
            action="retention.applied", entity_type="retention",
            entity_id=str(target),
            actor=actor_norm,
            metadata={
                "archived_count": result.archived_count,
                "deleted_count": result.deleted_count,
                "bytes_archived": result.bytes_archived,
                "bytes_deleted": result.bytes_deleted,
            },
        )
        out.append(result)
    return out


def list_policy() -> dict:
    return _load_policy()


def _load_policy() -> dict:
    raw = os.environ.get("OPS_RETENTION_POLICY")
    if not raw:
        return dict(DEFAULT_POLICY)
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass
    return dict(DEFAULT_POLICY)


def _rotate(directory: Path, *, archive_days: int, hard_delete_days: int) -> RotationResult:
    now = datetime.now(timezone.utc)
    archive_cutoff = now - timedelta(days=archive_days)
    delete_cutoff = now - timedelta(days=hard_delete_days)
    archived = 0
    deleted = 0
    bytes_archived = 0
    bytes_deleted = 0
    for p in list(directory.iterdir()):
        if not p.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            size = p.stat().st_size
        except OSError:
            continue
        if p.suffix == ".gz":
            if mtime < delete_cutoff:
                try:
                    p.unlink()
                    deleted += 1
                    bytes_deleted += size
                except OSError:
                    pass
            continue
        if mtime < delete_cutoff:
            try:
                p.unlink()
                deleted += 1
                bytes_deleted += size
            except OSError:
                pass
            continue
        if mtime < archive_cutoff:
            archive_path = p.with_suffix(p.suffix + ".gz")
            try:
                with open(p, "rb") as src, gzip.open(archive_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                p.unlink()
                archived += 1
                bytes_archived += size
            except OSError:
                continue
    return RotationResult(
        directory=str(directory), archived_count=archived,
        deleted_count=deleted, bytes_archived=bytes_archived,
        bytes_deleted=bytes_deleted,
    )
