"""Session-state file for Op 2 (mandatory ticket doctrine).

Implements the contract from docs/specs/operator-02-mandatory-ticket-doctrine.md
(BC todo 9967247783).

Each Claude Code session writes a `.claude/session-state.json` at the workspace
root that anchors the session to exactly one Basecamp ticket. Subsequent turns
in the same session re-read this file so the active ticket persists across
multi-turn conversations.

Schema (JSON on disk):

    {
      "session_id": "CC-20260605-4w8q",
      "active_ticket": {
        "bucket_id": "7463955",
        "todo_id": "9999999999",
        "url": "https://app.basecamp.com/3945211/buckets/7463955/todos/9999999999",
        "title": "Investigate stale-task sync bug",
        "started_at": "2026-06-05T14:30:00Z"
      } | null,
      "ticket_bypass": {
        "active": false,
        "reason": null,
        "at": null
      },
      "first_seen_at": "2026-06-05T14:30:00Z",
      "last_updated_at": "2026-06-05T14:30:00Z"
    }

The file is stdlib-only JSON. No new dependencies.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

SESSION_STATE_RELPATH = Path(".claude") / "session-state.json"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class ActiveTicket:
    """Anchor for the current Claude Code session."""
    bucket_id: str
    todo_id: str
    url: str
    title: str
    started_at: str = field(default_factory=_now_iso)

    @classmethod
    def from_bc_todo(cls, bc_todo: dict, started_at: Optional[str] = None) -> "ActiveTicket":
        """Build from a Basecamp API todo response."""
        return cls(
            bucket_id=str(bc_todo.get("bucket", {}).get("id", "") or bc_todo.get("bucket_id", "")),
            todo_id=str(bc_todo["id"]),
            url=bc_todo["app_url"],
            title=bc_todo["content"],
            started_at=started_at or _now_iso(),
        )


@dataclass
class TicketBypass:
    """Tracks whether the operator invoked `--no-ticket` this session."""
    active: bool = False
    reason: Optional[str] = None
    at: Optional[str] = None


@dataclass
class SessionState:
    """The complete contract serialized to .claude/session-state.json."""
    session_id: str
    active_ticket: Optional[ActiveTicket] = None
    ticket_bypass: TicketBypass = field(default_factory=TicketBypass)
    first_seen_at: str = field(default_factory=_now_iso)
    last_updated_at: str = field(default_factory=_now_iso)

    # ----- Serialization -----

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict already handles nested dataclasses + None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        at = d.get("active_ticket")
        active_ticket = ActiveTicket(**at) if at else None
        bp = d.get("ticket_bypass") or {}
        bypass = TicketBypass(
            active=bool(bp.get("active", False)),
            reason=bp.get("reason"),
            at=bp.get("at"),
        )
        return cls(
            session_id=d["session_id"],
            active_ticket=active_ticket,
            ticket_bypass=bypass,
            first_seen_at=d.get("first_seen_at", _now_iso()),
            last_updated_at=d.get("last_updated_at", _now_iso()),
        )

    def write(self, workspace_dir: Path) -> Path:
        """Persist to <workspace>/.claude/session-state.json. Returns the path."""
        target = workspace_dir / SESSION_STATE_RELPATH
        target.parent.mkdir(parents=True, exist_ok=True)
        self.last_updated_at = _now_iso()
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def read(cls, workspace_dir: Path) -> Optional["SessionState"]:
        """Load from <workspace>/.claude/session-state.json. None if missing."""
        target = workspace_dir / SESSION_STATE_RELPATH
        if not target.exists():
            return None
        try:
            return cls.from_dict(json.loads(target.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    # ----- Mutators -----

    def set_active_ticket(self, ticket: ActiveTicket) -> None:
        self.active_ticket = ticket
        # Mutating active_ticket implicitly clears bypass (the operator now has a real ticket)
        self.ticket_bypass = TicketBypass(active=False)

    def set_bypass(self, reason: str) -> None:
        self.ticket_bypass = TicketBypass(active=True, reason=reason, at=_now_iso())
        # Bypass and active_ticket are mutually exclusive — clear ticket on bypass
        self.active_ticket = None

    def has_active_ticket(self) -> bool:
        return self.active_ticket is not None and not self.ticket_bypass.active

    def is_bypassed(self) -> bool:
        return self.ticket_bypass.active
