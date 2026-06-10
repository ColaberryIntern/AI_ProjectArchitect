"""Per-user file-backed store for the Ops Command Center.

Layout under output/ops/{user_id}/:
    todos.json      — mirrored Basecamp todos, enriched with urgency + category
    projects.json   — mirrored BC projects the user is on
    state.json      — last_sync_at, last_scored_at, sync stats

Each json file is atomically written via tempfile+replace so partial-write
corruption can't happen on a crash mid-sync.

Concurrency: a per-user threading.Lock serializes every read-modify-write
sequence (upsert_todos, update_todo, upsert_projects). Without this, a
cron-triggered sync and a concurrent Mark Done can both load the file,
each merge their change in isolation, and the second writer silently
overwrites the first — the lost-update class flagged as H3 in the
2026-06-09 sync chain audit. The lock is *internal* (no caller change
needed) so every existing mutation path is automatically protected.

Note on remaining race surface: this lock serializes file-level writes,
which closes the lost-update race. It does NOT semantically reconcile a
Mark Done that races a mid-walk full sync — that race exists at a
higher level (the sync's already-fetched BC snapshot is from before the
completion) and needs either targeted re-walk on Mark Done's path or a
last_local_change_at field on OpsTodo. Tracked as a follow-up.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT

OPS_ROOT = PROJECT_ROOT / "output" / "ops"

# Per-user write lock for serializing read-modify-write sequences.
# Initialized lazily — _get_write_lock pulls or creates the per-user
# Lock under a single guard. Production processes have ~5 users max; the
# dict never grows past low-tens. No GC needed.
_WRITE_LOCKS: dict[str, threading.Lock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _get_write_lock(user_id: str) -> threading.Lock:
    """Return the per-user write lock, creating it if needed. Atomic
    under concurrent first-access."""
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(user_id, threading.Lock())


@dataclass
class OpsTodo:
    bc_id: int
    bc_project_id: int
    bc_project_name: str
    bc_todolist_id: int
    bc_todolist_name: str
    title: str
    description: str = ""
    status: str = "active"            # active | archived | trashed | completed
    due_on: str | None = None         # YYYY-MM-DD
    assignee_ids: list[int] = field(default_factory=list)
    assignee_names: list[str] = field(default_factory=list)
    # How this todo got into the user's queue:
    #   "assigned"   - assigned to the user directly
    #   "due"        - has a future due date in a project they're on
    #   "unassigned" - unassigned + recent activity in a project they're on
    inclusion_reason: str = "assigned"
    bc_app_url: str = ""
    bc_created_at: str = ""
    bc_updated_at: str = ""
    # Completion metadata (only populated when status == "completed")
    completed_by_id: int | None = None
    completed_by_name: str = ""
    completed_at: str = ""
    cycle_seconds: int = 0          # completed_at - bc_created_at, in seconds
    last_synced_at: str = ""
    # Enriched by scorer
    urgency_score: int = 0
    category: str = "unscored"        # human_required | waiting_dependency | unscored
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    # Soft-dismiss (local only, doesn't touch BC)
    is_dismissed: bool = False
    dismissed_at: str = ""
    dismissed_by: str = ""
    dismissed_reason: str = ""


@dataclass
class OpsProject:
    bc_id: int
    name: str
    description: str = ""
    is_managed: bool = True             # included in queue by default
    weight: float = 1.0                 # priority multiplier (0.0-2.0)
    last_synced_at: str = ""


@dataclass
class OpsState:
    user_id: str
    last_sync_at: str = ""
    last_sync_status: str = ""          # ok | partial | failed
    last_sync_error: str = ""
    last_scored_at: str = ""
    todos_synced: int = 0
    projects_synced: int = 0
    # Per-project targeted sync timestamp (Mark Done path). Set by
    # pull_todos_for_project independently of last_sync_at so the UI
    # can distinguish "no full sync ever" from "targeted touch within
    # the last few minutes". Audit L5 (2026-06-09).
    last_targeted_sync_at: str = ""
    # M6 (2026-06-09 audit): timestamp of the most recent stale-row
    # purge sweep. Used by the scheduler to decide when to run the next
    # sweep (default: once per OPS_PURGE_INTERVAL_HOURS=24).
    last_purge_at: str = ""
    last_purge_status: str = ""         # ok | partial | failed
    last_purge_archived: int = 0


def _user_dir(user_id: str) -> Path:
    return OPS_ROOT / user_id


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.name + ".")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
        Path(tmp_path).replace(path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Todos ──────────────────────────────────────────────────────────────────


def load_todos(user_id: str) -> list[OpsTodo]:
    p = _user_dir(user_id) / "todos.json"
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[OpsTodo] = []
    fields = set(OpsTodo.__dataclass_fields__)
    for d in raw:
        out.append(OpsTodo(**{k: v for k, v in d.items() if k in fields}))
    return out


def save_todos(user_id: str, todos: list[OpsTodo]) -> None:
    _atomic_write_json(_user_dir(user_id) / "todos.json", [asdict(t) for t in todos])


def upsert_todos(user_id: str, fresh: list[OpsTodo]) -> tuple[int, int]:
    """Merge fresh todos with existing; returns (created, updated).

    Match by bc_id. Existing local-only fields (is_dismissed, etc.) survive.
    Items present locally but absent in `fresh` are kept (no auto-purge);
    they're filtered by status during reads.

    Holds the per-user write lock for the entire load-merge-save so a
    concurrent update_todo (Mark Done) or another upsert (cron + manual
    sync overlap) can't sneak a write in between our read and write.
    """
    with _get_write_lock(user_id):
        by_id = {t.bc_id: t for t in load_todos(user_id)}
        created = updated = 0
        for f in fresh:
            if f.bc_id in by_id:
                local = by_id[f.bc_id]
                # Preserve dismiss flag + scoring (re-scored separately)
                preserved = {
                    "is_dismissed": local.is_dismissed,
                    "dismissed_at": local.dismissed_at,
                    "dismissed_by": local.dismissed_by,
                    "dismissed_reason": local.dismissed_reason,
                    "urgency_score": local.urgency_score,
                    "category": local.category,
                    "score_breakdown": local.score_breakdown,
                }
                for k, v in preserved.items():
                    setattr(f, k, v)
                by_id[f.bc_id] = f
                updated += 1
            else:
                by_id[f.bc_id] = f
                created += 1
        save_todos(user_id, list(by_id.values()))
        return created, updated


def get_todo(user_id: str, bc_id: int) -> OpsTodo | None:
    return next((t for t in load_todos(user_id) if t.bc_id == bc_id), None)


def list_completed_for_user(user_id: str, days: int = 30) -> list[OpsTodo]:
    """Phase 6: return the user's completed BC todos in the last N days.

    Reuses the local ops store (already populated by pull_todos_for_user, which
    pulls both active + completed). No separate BC fetch needed; this is a
    pure filter. Sorted newest-completed first so the Extract surface shows
    fresh work at the top.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[OpsTodo] = []
    for t in load_todos(user_id):
        if t.status != "completed" or not t.completed_at:
            continue
        try:
            ts = t.completed_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dt < cutoff:
            continue
        out.append(t)
    out.sort(key=lambda t: t.completed_at or "", reverse=True)
    return out


def update_todo(user_id: str, bc_id: int, **fields: Any) -> OpsTodo | None:
    """Mutate a single todo's fields and persist. Used by Mark Done,
    Skip, and the scorer's per-todo writes. Lock-wrapped so a
    concurrent upsert_todos can't drop our update on the floor."""
    with _get_write_lock(user_id):
        todos = load_todos(user_id)
        found = None
        for t in todos:
            if t.bc_id == bc_id:
                for k, v in fields.items():
                    setattr(t, k, v)
                found = t
                break
        if found is not None:
            save_todos(user_id, todos)
        return found


# ── Projects ────────────────────────────────────────────────────────────────


def load_projects(user_id: str) -> list[OpsProject]:
    p = _user_dir(user_id) / "projects.json"
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out: list[OpsProject] = []
    fields = set(OpsProject.__dataclass_fields__)
    for d in raw:
        out.append(OpsProject(**{k: v for k, v in d.items() if k in fields}))
    return out


def save_projects(user_id: str, projects: list[OpsProject]) -> None:
    _atomic_write_json(_user_dir(user_id) / "projects.json", [asdict(p) for p in projects])


def upsert_projects(user_id: str, fresh: list[OpsProject]) -> tuple[int, int]:
    """Merge fresh project list with existing; preserves operator
    overrides (is_managed, weight). Lock-wrapped same as upsert_todos."""
    with _get_write_lock(user_id):
        by_id = {p.bc_id: p for p in load_projects(user_id)}
        created = updated = 0
        for f in fresh:
            if f.bc_id in by_id:
                local = by_id[f.bc_id]
                f.is_managed = local.is_managed
                f.weight = local.weight
                by_id[f.bc_id] = f
                updated += 1
            else:
                by_id[f.bc_id] = f
                created += 1
        save_projects(user_id, list(by_id.values()))
        return created, updated


# ── State ───────────────────────────────────────────────────────────────────


def load_state(user_id: str) -> OpsState:
    p = _user_dir(user_id) / "state.json"
    if not p.exists():
        return OpsState(user_id=user_id)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        fields = set(OpsState.__dataclass_fields__)
        return OpsState(**{k: v for k, v in raw.items() if k in fields})
    except (json.JSONDecodeError, OSError):
        return OpsState(user_id=user_id)


def save_state(state: OpsState) -> None:
    _atomic_write_json(_user_dir(state.user_id) / "state.json", asdict(state))
