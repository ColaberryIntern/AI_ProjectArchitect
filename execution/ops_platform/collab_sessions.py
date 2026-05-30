"""Collaborative editing sessions — multi-user document locks, revision
tracking, edit intents, inline comments.

Scope honesty
-------------
- All coordination is **single-host multi-process** via the file-locked
  primitives from Phase 6 (``distributed_lock``). Multi-host coordination
  requires the Redis adapters from Phase 7C; this module's lock acquisition
  goes through ``distributed_lock.acquire`` which is the seam.
- Document state is held in JSON files; this is NOT a CRDT — it's a lock-
  based "one editor at a time, multiple viewers" model. CRDT-style merging
  is out of scope for Phase 8.

Persistence
-----------
``output/ops_platform/collab/sessions/{session_id}.json``    — open editing session
``output/ops_platform/collab/revisions/{entity_id}.jsonl``   — revision history
``output/ops_platform/collab/comments/{entity_id}.jsonl``    — inline comments
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import OUTPUT_DIR
from execution.ops_platform import (
    audit_log, distributed_lock, optimistic_concurrency, realtime_bus,
)
from execution.ops_platform.identity import IdentityContext

logger = logging.getLogger(__name__)

_COLLAB_DIR = OUTPUT_DIR / "ops_platform" / "collab"
_SESSIONS_DIR = _COLLAB_DIR / "sessions"
_REVISIONS_DIR = _COLLAB_DIR / "revisions"
_COMMENTS_DIR = _COLLAB_DIR / "comments"

DEFAULT_LEASE_SECONDS = 300


@dataclass
class CollabSession:
    session_id: str
    entity_type: str
    entity_id: str
    editor: dict                    # who holds the edit lock
    opened_at: str
    expires_at: str
    intent: str = "edit"            # "edit" | "view"
    cursor_position: str | None = None
    revision_id: str | None = None  # revision when the editor opened

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Revision:
    revision_id: str
    entity_type: str
    entity_id: str
    author: dict
    timestamp: str
    summary: str
    diff: dict                      # free-form change description

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Comment:
    comment_id: str
    entity_type: str
    entity_id: str
    author: dict
    body: str
    anchor: str | None              # e.g. "line:42" | "field:status"
    posted_at: str
    resolved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ── Sessions ───────────────────────────────────────────────────────────


def open_session(
    *,
    entity_type: str,
    entity_id: str,
    editor: IdentityContext,
    intent: str = "edit",
    current_revision: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> CollabSession:
    """Open a collaborative editing session. Acquires an editing lock via
    distributed_lock when ``intent=='edit'``. View-intent sessions don't
    take the lock — they're tracked for presence but don't block editors."""
    if not editor.authenticated:
        raise PermissionError("anonymous identities cannot open editing sessions")
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    lock_name = _slug_lock(entity_type, entity_id)
    if intent == "edit":
        try:
            distributed_lock.acquire(lock_name, owner_id=editor.user_id,
                                       lease_seconds=lease_seconds,
                                       acquire_timeout_seconds=2)
        except distributed_lock.LockAcquisitionError:
            existing = distributed_lock.is_held(lock_name)
            raise EditLockHeld(
                f"edit lock on {entity_type}:{entity_id} held by "
                f"{(existing or {}).get('owner_id', 'unknown')}"
            )
    session = CollabSession(
        session_id=f"sess_{uuid.uuid4().hex[:12]}",
        entity_type=entity_type, entity_id=entity_id,
        editor=editor.as_actor(),
        opened_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc)
                       + timedelta(seconds=lease_seconds)).isoformat(),
        intent=intent,
        revision_id=current_revision,
    )
    _persist_session(session)
    audit_log.record(
        action="collab.session_opened", entity_type="collab_session",
        entity_id=session.session_id, actor=editor.as_actor(),
        new_state={"entity_type": entity_type, "entity_id": entity_id,
                   "intent": intent},
    )
    realtime_bus.emit("collab.session_opened",
                        actor=editor.as_actor(),
                        correlation_id=session.session_id,
                        payload={"entity_type": entity_type,
                                   "entity_id": entity_id, "intent": intent},
                        mirror_to_audit=False)
    return session


def close_session(session_id: str, *, editor: IdentityContext) -> bool:
    session = get_session(session_id)
    if session is None:
        return False
    if session.editor.get("name") != editor.user_id:
        return False
    if session.intent == "edit":
        distributed_lock.release(_slug_lock(session.entity_type, session.entity_id),
                                    owner_id=editor.user_id)
    try:
        (_SESSIONS_DIR / f"{session_id}.json").unlink()
    except OSError:
        return False
    audit_log.record(
        action="collab.session_closed", entity_type="collab_session",
        entity_id=session_id, actor=editor.as_actor(),
    )
    realtime_bus.emit("collab.session_closed",
                        actor=editor.as_actor(),
                        correlation_id=session_id,
                        payload={"entity_type": session.entity_type,
                                   "entity_id": session.entity_id},
                        mirror_to_audit=False)
    return True


def heartbeat_session(session_id: str, *, editor: IdentityContext,
                        cursor_position: str | None = None,
                        lease_seconds: int = DEFAULT_LEASE_SECONDS) -> CollabSession | None:
    session = get_session(session_id)
    if session is None or session.editor.get("name") != editor.user_id:
        return None
    if session.intent == "edit":
        try:
            distributed_lock.heartbeat(
                _slug_lock(session.entity_type, session.entity_id),
                owner_id=editor.user_id, lease_seconds=lease_seconds,
            )
        except distributed_lock.LockAcquisitionError:
            return None
    session.expires_at = (datetime.now(timezone.utc)
                            + timedelta(seconds=lease_seconds)).isoformat()
    if cursor_position is not None:
        session.cursor_position = cursor_position
    _persist_session(session)
    realtime_bus.emit("collab.cursor_moved", actor=editor.as_actor(),
                        correlation_id=session_id,
                        payload={"cursor": cursor_position},
                        mirror_to_audit=False)
    return session


def get_session(session_id: str) -> CollabSession | None:
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return CollabSession(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_sessions(*, entity_type: str | None = None,
                    entity_id: str | None = None) -> list[CollabSession]:
    if not _SESSIONS_DIR.exists():
        return []
    out: list[CollabSession] = []
    for p in _SESSIONS_DIR.glob("sess_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            session = CollabSession(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if entity_type and session.entity_type != entity_type:
            continue
        if entity_id and session.entity_id != entity_id:
            continue
        out.append(session)
    out.sort(key=lambda s: s.opened_at, reverse=True)
    return out


# ── Revisions ─────────────────────────────────────────────────────────


def record_revision(
    *,
    entity_type: str,
    entity_id: str,
    author: dict | str,
    summary: str,
    diff: dict | None = None,
) -> Revision:
    actor = author if isinstance(author, dict) else {"name": str(author)}
    rev = Revision(
        revision_id=optimistic_concurrency.new_revision(),
        entity_type=entity_type, entity_id=entity_id,
        author=actor,
        timestamp=datetime.now(timezone.utc).isoformat(),
        summary=summary, diff=dict(diff or {}),
    )
    _REVISIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _REVISIONS_DIR / f"{entity_id}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rev.to_dict(), ensure_ascii=False) + "\n")
    audit_log.record(
        action="collab.revision_recorded", entity_type="collab_revision",
        entity_id=rev.revision_id, actor=actor,
        new_state={"entity_type": entity_type, "entity_id": entity_id,
                   "summary": summary},
    )
    realtime_bus.emit("collab.revision_recorded", actor=actor,
                        correlation_id=rev.revision_id,
                        payload={"entity_type": entity_type,
                                   "entity_id": entity_id,
                                   "summary": summary},
                        mirror_to_audit=False)
    return rev


def list_revisions(entity_id: str, *, limit: int = 200) -> list[Revision]:
    path = _REVISIONS_DIR / f"{entity_id}.jsonl"
    if not path.exists():
        return []
    out: list[Revision] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                out.append(Revision(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
    return out[-limit:][::-1]


# ── Comments ──────────────────────────────────────────────────────────


def post_comment(
    *,
    entity_type: str,
    entity_id: str,
    author: IdentityContext,
    body: str,
    anchor: str | None = None,
) -> Comment:
    if not author.authenticated:
        raise PermissionError("anonymous identities cannot post comments")
    if not body.strip():
        raise ValueError("comment body cannot be empty")
    cm = Comment(
        comment_id=f"cmt_{uuid.uuid4().hex[:12]}",
        entity_type=entity_type, entity_id=entity_id,
        author=author.as_actor(),
        body=body.strip(), anchor=anchor,
        posted_at=datetime.now(timezone.utc).isoformat(),
    )
    _COMMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _COMMENTS_DIR / f"{entity_id}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(cm.to_dict(), ensure_ascii=False) + "\n")
    audit_log.record(
        action="collab.comment_posted", entity_type="collab_comment",
        entity_id=cm.comment_id, actor=author.as_actor(),
        new_state={"entity_type": entity_type, "entity_id": entity_id,
                   "anchor": anchor},
    )
    realtime_bus.emit("collab.comment_posted", actor=author.as_actor(),
                        correlation_id=cm.comment_id,
                        payload={"entity_type": entity_type,
                                   "entity_id": entity_id, "anchor": anchor},
                        mirror_to_audit=False)
    return cm


def resolve_comment(comment_id: str, *, entity_id: str,
                      actor: dict | str) -> bool:
    path = _COMMENTS_DIR / f"{entity_id}.jsonl"
    if not path.exists():
        return False
    actor_norm = actor if isinstance(actor, dict) else {"name": str(actor)}
    rows = []
    found = False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("comment_id") == comment_id and not row.get("resolved"):
            row["resolved"] = True
            found = True
        rows.append(row)
    if not found:
        return False
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                      encoding="utf-8")
    audit_log.record(
        action="collab.comment_resolved", entity_type="collab_comment",
        entity_id=comment_id, actor=actor_norm,
    )
    return True


def list_comments(entity_id: str, *, include_resolved: bool = False) -> list[Comment]:
    path = _COMMENTS_DIR / f"{entity_id}.jsonl"
    if not path.exists():
        return []
    out: list[Comment] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            cm = Comment(**json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue
        if cm.resolved and not include_resolved:
            continue
        out.append(cm)
    out.sort(key=lambda c: c.posted_at, reverse=True)
    return out


# ── Internal ───────────────────────────────────────────────────────────


class EditLockHeld(Exception):
    pass


def _slug_lock(entity_type: str, entity_id: str) -> str:
    """Lock name without filesystem-illegal chars (Windows-safe)."""
    safe_type = "".join(c if c.isalnum() or c in "_-" else "_" for c in entity_type)
    safe_id = "".join(c if c.isalnum() or c in "_-" else "_" for c in entity_id)
    return f"collab.{safe_type}.{safe_id}"


def _persist_session(session: CollabSession) -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _SESSIONS_DIR / f"{session.session_id}.json"
    path.write_text(json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
                      encoding="utf-8")
