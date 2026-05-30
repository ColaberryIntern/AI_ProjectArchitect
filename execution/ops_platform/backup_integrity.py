"""Snapshot integrity — deterministic manifest with per-file SHA-256,
partial-restore modes, snapshot lineage graph, corruption detection.

Layered on top of Phase 8H ``backup_restore``. Existing snapshots remain
compatible — this module produces enhanced manifests under
``output/ops_platform/backups/{stamp}.manifest.json`` alongside the
.tar.gz archive.

Partial restore modes:
  - ``projection-only``     restores ``projections/``
  - ``orchestration-only``  restores ``orchestrations/`` + ``orchestration_claims/``
  - ``audit-only``          restores ``audit/`` + ``audit_signed/``
"""

from __future__ import annotations

import hashlib
import json
import logging
import tarfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, backup_restore

logger = logging.getLogger(__name__)

_MANIFESTS_DIR = OUTPUT_DIR / "ops_platform" / "backup_manifests"

PARTIAL_RESTORE_PROFILES = {
    "projection-only":     ["projections/"],
    "orchestration-only":  ["orchestrations/", "orchestration_claims/"],
    "audit-only":          ["audit/", "audit_signed/"],
    "outbox-only":         ["outbox/", "outbox_dlq/", "outbox_dedup/"],
}


@dataclass
class FileChecksum:
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SnapshotManifest:
    manifest_id: str
    archive_path: str
    created_at: str
    archive_sha256: str
    file_count: int
    files: list                            # list[FileChecksum]
    parent_snapshot_id: str | None
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["files"] = [f if isinstance(f, dict) else f.to_dict() for f in d["files"]]
        return d


# ── Public API ─────────────────────────────────────────────────────────


def snapshot_with_manifest(
    *,
    actor: dict | str = "backup_admin",
    notes: str = "",
    parent_snapshot_id: str | None = None,
) -> SnapshotManifest:
    """Create a full snapshot + record per-file SHA-256 in a manifest."""
    result = backup_restore.snapshot(actor=actor)
    archive = Path(result.archive_path)
    files: list[FileChecksum] = []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                sha = hashlib.sha256()
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    sha.update(chunk)
                files.append(FileChecksum(
                    relative_path=member.name,
                    sha256=sha.hexdigest(),
                    size_bytes=int(member.size),
                ))
    except OSError as e:
        logger.warning("manifest read failed: %s", e)

    manifest = SnapshotManifest(
        manifest_id=f"snap_{uuid.uuid4().hex[:12]}",
        archive_path=str(archive),
        created_at=result.created_at,
        archive_sha256=result.sha256,
        file_count=len(files),
        files=files,
        parent_snapshot_id=parent_snapshot_id,
        notes=notes,
    )
    _persist_manifest(manifest)
    audit_log.record(
        action="backup.manifest_created", entity_type="backup_manifest",
        entity_id=manifest.manifest_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        new_state={"file_count": manifest.file_count,
                   "archive_path": manifest.archive_path,
                   "parent_snapshot_id": parent_snapshot_id},
    )
    return manifest


def verify_snapshot(manifest_id: str) -> dict:
    """Walk every file in the manifest, recompute SHA-256, report mismatches."""
    manifest = get_manifest(manifest_id)
    if manifest is None:
        return {"verified": False, "reason": "manifest not found"}
    archive = Path(manifest.archive_path)
    if not archive.exists():
        return {"verified": False, "reason": "archive file missing",
                  "archive_path": str(archive)}
    expected_by_path = {
        (f if isinstance(f, dict) else f.to_dict())["relative_path"]:
        (f if isinstance(f, dict) else f.to_dict())["sha256"]
        for f in manifest.files
    }
    mismatches: list[dict] = []
    seen = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                seen += 1
                f = tar.extractfile(member)
                if f is None:
                    continue
                sha = hashlib.sha256()
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    sha.update(chunk)
                expected = expected_by_path.get(member.name)
                if expected and expected != sha.hexdigest():
                    mismatches.append({"file": member.name,
                                          "expected": expected,
                                          "actual": sha.hexdigest()})
    except (OSError, tarfile.TarError) as e:
        return {"verified": False, "reason": f"archive read failed: {e}"}
    return {"verified": not mismatches,
              "files_checked": seen,
              "mismatch_count": len(mismatches),
              "mismatches": mismatches[:25]}


def partial_restore(
    *,
    manifest_id: str,
    profile: str,
    restore_to: str | None = None,
    actor: dict | str = "backup_admin",
) -> dict:
    """Restore only the directories listed in the named profile."""
    if profile not in PARTIAL_RESTORE_PROFILES:
        return {"restored": False,
                  "reason": f"unknown profile '{profile}'",
                  "available": sorted(PARTIAL_RESTORE_PROFILES.keys())}
    manifest = get_manifest(manifest_id)
    if manifest is None:
        return {"restored": False, "reason": "manifest not found"}
    archive = Path(manifest.archive_path)
    if not archive.exists():
        return {"restored": False, "reason": "archive file missing"}
    prefixes = tuple(PARTIAL_RESTORE_PROFILES[profile])
    target = Path(restore_to) if restore_to else (
        OUTPUT_DIR / f"ops_platform_partial_{profile.replace('-', '_')}"
    )
    target.mkdir(parents=True, exist_ok=True)
    extracted = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not any(member.name.startswith(p) for p in prefixes):
                    continue
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    continue
                tar.extract(member, path=target)
                extracted += 1
    except (OSError, tarfile.TarError) as e:
        return {"restored": False, "reason": f"extract failed: {e}"}
    audit_log.record(
        action="backup.partial_restored", entity_type="backup_manifest",
        entity_id=manifest_id,
        actor=actor if isinstance(actor, dict) else {"name": str(actor)},
        metadata={"profile": profile, "file_count": extracted,
                  "restore_to": str(target)},
    )
    return {"restored": True, "profile": profile,
              "file_count": extracted, "restore_to": str(target)}


def list_manifests() -> list[dict]:
    if not _MANIFESTS_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(_MANIFESTS_DIR.glob("snap_*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def get_manifest(manifest_id: str) -> SnapshotManifest | None:
    path = _MANIFESTS_DIR / f"{manifest_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # files come back as dicts; that's fine for to_dict() round-trip
        return SnapshotManifest(**data)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def lineage_graph() -> dict:
    """Build a parent → children graph of snapshots for the operator dashboard."""
    nodes = list_manifests()
    children: dict = {}
    for n in nodes:
        parent = n.get("parent_snapshot_id")
        if parent:
            children.setdefault(parent, []).append(n["manifest_id"])
    return {"nodes": nodes, "children_by_parent": children}


def orphan_snapshots() -> list[dict]:
    """Find archive files on disk that have no manifest record."""
    known = {m["archive_path"] for m in list_manifests()}
    backup_dir = backup_restore._BACKUPS_DIR
    if not backup_dir.exists():
        return []
    out: list[dict] = []
    for p in backup_dir.glob("ops_snapshot_*.tar.gz"):
        if str(p) not in known:
            try:
                out.append({"archive_path": str(p),
                              "bytes": p.stat().st_size,
                              "reason": "no manifest"})
            except OSError:
                continue
    return out


# ── Internal ───────────────────────────────────────────────────────────


def _persist_manifest(m: SnapshotManifest) -> None:
    _MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    (_MANIFESTS_DIR / f"{m.manifest_id}.json").write_text(
        json.dumps(m.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
