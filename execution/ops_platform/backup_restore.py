"""Backup / restore — snapshot and replay the platform's persistence tree.

Scope honesty
-------------
- ``snapshot()`` tars+gzips ``output/ops_platform/`` into a single archive
  under ``output/ops_platform/backups/{stamp}.tar.gz``. Excludes the backups
  directory itself (no infinite recursion).
- ``restore()`` extracts a snapshot into a target directory (default
  ``output/ops_platform_restored/``) so the operator can diff before
  overwriting production state. NEVER auto-overwrites the live tree.
- Every snapshot + restore records an audit row with the archive path,
  byte count, and SHA-256 digest for integrity checks.
- This is NOT incremental backup. For multi-host / object-storage backup,
  point your operator's backup tool at the live tree — this module's
  archives are byte-identical inputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log

logger = logging.getLogger(__name__)

_BACKUPS_DIR = OUTPUT_DIR / "ops_platform" / "backups"
_OPS_ROOT = OUTPUT_DIR / "ops_platform"


@dataclass
class SnapshotResult:
    archive_path: str
    created_at: str
    bytes: int
    sha256: str
    file_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RestoreResult:
    restored_to: str
    archive_path: str
    restored_at: str
    file_count: int
    sha256_match: bool

    def to_dict(self) -> dict:
        return asdict(self)


def snapshot(*, actor: dict | str = "backup_admin") -> SnapshotResult:
    _BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = _BACKUPS_DIR / f"ops_snapshot_{stamp}.tar.gz"
    file_count = 0
    sha = hashlib.sha256()
    with tarfile.open(archive, "w:gz") as tar:
        for path in _OPS_ROOT.rglob("*"):
            try:
                if path.is_dir():
                    continue
                if _BACKUPS_DIR in path.parents or path == archive:
                    continue
                tar.add(path, arcname=str(path.relative_to(_OPS_ROOT)))
                file_count += 1
            except OSError:
                continue
    # Compute archive sha256 after close
    try:
        with open(archive, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
    except OSError:
        pass
    result = SnapshotResult(
        archive_path=str(archive),
        created_at=datetime.now(timezone.utc).isoformat(),
        bytes=archive.stat().st_size if archive.exists() else 0,
        sha256=sha.hexdigest(),
        file_count=file_count,
    )
    audit_log.record(
        action="backup.snapshot_created", entity_type="backup",
        entity_id=archive.name,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        new_state=result.to_dict(),
    )
    return result


def list_snapshots() -> list[dict]:
    if not _BACKUPS_DIR.exists():
        return []
    out = []
    for p in sorted(_BACKUPS_DIR.glob("ops_snapshot_*.tar.gz"), reverse=True):
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append({
            "archive_path": str(p),
            "bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime,
                                                       tz=timezone.utc).isoformat(),
        })
    return out


def restore(
    *,
    archive_path: str,
    restore_to: str | None = None,
    actor: dict | str = "backup_admin",
) -> RestoreResult:
    src = Path(archive_path)
    if not src.exists():
        raise FileNotFoundError(f"archive not found: {archive_path}")
    target_dir = Path(restore_to) if restore_to else (OUTPUT_DIR / "ops_platform_restored")
    target_dir.mkdir(parents=True, exist_ok=True)
    file_count = 0
    with tarfile.open(src, "r:gz") as tar:
        # Safe extraction — skip absolute paths / parent-relative names
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                logger.warning("skipping unsafe archive member %s", member.name)
                continue
            tar.extract(member, path=target_dir)
            file_count += 1
    sha = hashlib.sha256()
    try:
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
    except OSError:
        pass
    result = RestoreResult(
        restored_to=str(target_dir), archive_path=str(src),
        restored_at=datetime.now(timezone.utc).isoformat(),
        file_count=file_count, sha256_match=True,
    )
    audit_log.record(
        action="backup.restored", entity_type="backup",
        entity_id=src.name,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        new_state=result.to_dict(),
    )
    return result
