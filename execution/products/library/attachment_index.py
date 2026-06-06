"""Per-operator idempotency index for colaberry_attachment_fetch.

Lookup table: idempotency_key -> stored AttachmentRef. First call uploads to
Drive + writes the entry; subsequent calls with the same key short-circuit to
the stored ref. Verification of "is the Drive file still alive?" happens at
the call site (lookup() returns the stored ref; the tool re-fetches Drive
metadata to confirm before claiming `reused_existing: true`).

Storage: output/library/_attachment_index/<safe_email>.json. JSON, atomically
written via tempfile+replace (same pattern as ops/store.py).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
INDEX_DIR = ROOT / "output" / "library" / "_attachment_index"

# In-flight call dedupe -- prevents two concurrent identical requests from
# both uploading. Keyed by (operator_email, idempotency_key).
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[tuple[str, str], float] = {}
INFLIGHT_TIMEOUT_SEC = 60.0


@dataclass
class AttachmentRef:
    """One entry in the per-operator index."""
    idempotency_key: str
    source: str
    drive_file_id: str
    drive_url: str
    mime_type: str
    size_bytes: int
    filename: str
    sender: str
    saved_at: str            # ISO 8601 UTC
    source_message_id: str = ""   # gmail: message_id; bc: recording_id
    source_attachment_id: str = ""  # gmail: attachment_id; bc: sgid; drive: drive_file_id


def _index_path(operator_email: str) -> Path:
    safe = operator_email.replace("/", "_").replace("\\", "_")
    return INDEX_DIR / f"{safe}.json"


def _read_index(operator_email: str) -> dict[str, dict]:
    p = _index_path(operator_email)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return raw
    except (json.JSONDecodeError, OSError):
        return {}


def _write_index(operator_email: str, data: dict[str, dict]) -> None:
    p = _index_path(operator_email)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=p.parent,
                                                                 prefix=p.name + ".",
                                                                 suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        Path(tmp_path).replace(p)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Public API ──────────────────────────────────────────────────────────


def compute_key(*, source: str, message_id: str = "", attachment_id: str = "",
                          project_id: int | str = "", recording_id: int | str = "",
                          sgid: str = "", drive_file_id: str = "") -> str:
    """Stable idempotency key per source family.

    Documented mapping (also in the directive):
      gmail    -> "gmail:{message_id}:{attachment_id}"
      basecamp -> "basecamp:{project_id}:{recording_id}:{sgid}"
      drive    -> "drive:{drive_file_id}"
    """
    if source == "gmail":
        return f"gmail:{message_id}:{attachment_id}"
    if source == "basecamp":
        return f"basecamp:{project_id}:{recording_id}:{sgid}"
    if source == "drive":
        return f"drive:{drive_file_id}"
    return f"unknown:{source}"


def lookup(operator_email: str, idempotency_key: str) -> AttachmentRef | None:
    data = _read_index(operator_email)
    row = data.get(idempotency_key)
    if not row:
        return None
    # Filter out fields not in the dataclass (defensive against schema drift)
    fields_known = set(AttachmentRef.__dataclass_fields__.keys())
    cleaned = {k: v for k, v in row.items() if k in fields_known}
    try:
        return AttachmentRef(**cleaned)
    except TypeError:
        return None


def record(operator_email: str, ref: AttachmentRef) -> None:
    data = _read_index(operator_email)
    data[ref.idempotency_key] = asdict(ref)
    _write_index(operator_email, data)


def begin_inflight(operator_email: str, idempotency_key: str) -> bool:
    """Try to claim the in-flight slot. Returns True if claimed, False if
    another call is already in flight for this key.

    Stale entries (>INFLIGHT_TIMEOUT_SEC) auto-expire so a crashed worker
    doesn't permanently block re-tries.
    """
    now = time.time()
    k = (operator_email, idempotency_key)
    with _INFLIGHT_LOCK:
        prev = _INFLIGHT.get(k)
        if prev and (now - prev) < INFLIGHT_TIMEOUT_SEC:
            return False
        _INFLIGHT[k] = now
        return True


def end_inflight(operator_email: str, idempotency_key: str) -> None:
    """Release the in-flight slot. Idempotent."""
    k = (operator_email, idempotency_key)
    with _INFLIGHT_LOCK:
        _INFLIGHT.pop(k, None)
