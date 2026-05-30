"""Signed audit chain — HMAC-SHA256 over (previous_chain_hash + row_json).

Scope honesty
-------------
Phase 4 audit rows are append-only JSONL but unsigned. This module adds an
explicit signature pass that hashes every row's content together with the
previous row's chain hash, producing a tamper-evident chain. The HMAC key
is read from ``secrets.read("OPS_AUDIT_HMAC_KEY")``; without a key, the
signer falls back to a plain SHA-256 chain (still detects tampering, just
not authenticated against the operator).

Signed rows persist under ``output/ops_platform/audit_signed/{date}.jsonl``.
Each row carries:
  - ``chain_hash``: hex digest of (previous_chain_hash + canonical_row_json)
  - ``signature``: HMAC-SHA256 hex digest (if key present)
  - ``signed_at``: timestamp

``verify_chain()`` recomputes every hash and reports the first break.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import audit_log, secrets

logger = logging.getLogger(__name__)

_SIGNED_DIR = OUTPUT_DIR / "ops_platform" / "audit_signed"
_HMAC_SECRET_NAME = "OPS_AUDIT_HMAC_KEY"


@dataclass
class SignedRow:
    audit_entry_id: str
    chain_hash: str
    previous_chain_hash: str
    signature: str | None
    signed_at: str
    canonical_row_json: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationReport:
    rows_inspected: int
    valid: bool
    broken_at: str | None
    broken_reason: str | None
    signing_mode: str            # "hmac" | "plain_chain"
    last_chain_hash: str

    def to_dict(self) -> dict:
        return asdict(self)


def signing_mode() -> str:
    return "hmac" if secrets.read(_HMAC_SECRET_NAME) else "plain_chain"


def sign_pending(*, days_back: int = 1) -> int:
    """Iterate raw audit rows from the lookback window and append signed
    counterparts. Already-signed audit_entry_ids are skipped (idempotent).
    Returns the number of rows newly signed."""
    raw_rows = audit_log.list_entries(days=days_back, limit=10000)
    raw_rows.sort(key=lambda r: r.get("timestamp", ""))
    already = _already_signed_ids()
    secret = secrets.read(_HMAC_SECRET_NAME)
    last_hash = _latest_chain_hash()
    written = 0
    for row in raw_rows:
        eid = row.get("entry_id")
        if not eid or eid in already:
            continue
        canonical = json.dumps(row, sort_keys=True, ensure_ascii=False)
        prev = last_hash
        chain_hash = hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()
        signature = None
        if secret:
            signature = hmac.new(secret.encode("utf-8"),
                                    (prev + canonical).encode("utf-8"),
                                    hashlib.sha256).hexdigest()
        signed = SignedRow(
            audit_entry_id=eid, chain_hash=chain_hash,
            previous_chain_hash=prev, signature=signature,
            signed_at=datetime.now(timezone.utc).isoformat(),
            canonical_row_json=canonical,
        )
        _append(signed)
        last_hash = chain_hash
        written += 1
    return written


def verify_chain(*, days: int = 30) -> VerificationReport:
    """Walk every signed row, recompute chain_hash + (when key present)
    signature, return on the first break."""
    secret = secrets.read(_HMAC_SECRET_NAME)
    rows = _read_signed(days=days)
    prev = ""
    inspected = 0
    for r in rows:
        canonical = r.get("canonical_row_json", "")
        expected_chain = hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()
        if r.get("chain_hash") != expected_chain:
            return VerificationReport(
                rows_inspected=inspected, valid=False,
                broken_at=r.get("audit_entry_id"),
                broken_reason="chain_hash mismatch — content or order altered",
                signing_mode="hmac" if secret else "plain_chain",
                last_chain_hash=prev,
            )
        if secret:
            expected_sig = hmac.new(secret.encode("utf-8"),
                                       (prev + canonical).encode("utf-8"),
                                       hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected_sig, r.get("signature") or ""):
                return VerificationReport(
                    rows_inspected=inspected, valid=False,
                    broken_at=r.get("audit_entry_id"),
                    broken_reason="HMAC signature mismatch — row forged or key rotated",
                    signing_mode="hmac",
                    last_chain_hash=prev,
                )
        prev = r["chain_hash"]
        inspected += 1
    return VerificationReport(
        rows_inspected=inspected, valid=True, broken_at=None,
        broken_reason=None,
        signing_mode="hmac" if secret else "plain_chain",
        last_chain_hash=prev,
    )


# ── Internal ───────────────────────────────────────────────────────────


def _append(row: SignedRow) -> None:
    _SIGNED_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    path = _SIGNED_DIR / f"{day}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")


def _already_signed_ids() -> set:
    out: set = set()
    if not _SIGNED_DIR.exists():
        return out
    for p in _SIGNED_DIR.glob("*.jsonl"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    eid = data.get("audit_entry_id")
                    if eid:
                        out.add(eid)
        except OSError:
            continue
    return out


def _latest_chain_hash() -> str:
    if not _SIGNED_DIR.exists():
        return ""
    files = sorted(_SIGNED_DIR.glob("*.jsonl"))
    if not files:
        return ""
    try:
        with open(files[-1], "r", encoding="utf-8") as f:
            last = ""
            for line in f:
                last = line
            if not last.strip():
                return ""
            return json.loads(last).get("chain_hash", "")
    except (OSError, json.JSONDecodeError):
        return ""


def _read_signed(*, days: int) -> list:
    if not _SIGNED_DIR.exists():
        return []
    rows: list = []
    files = sorted(_SIGNED_DIR.glob("*.jsonl"))[-days:]
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return rows
