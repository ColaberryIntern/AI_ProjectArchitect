"""Per-item Subscribe-to-updates state. [Workflow 3b spec gap 2]

Tracks per-(user, item) subscribe-to-updates intent. Separate from
tenancy.FollowEvent which tracks author-following: different semantics
(item updates vs author publishes), different fan-out, different revoke key.

When a user opts in at install time, the workspace_install module records
a subscription here. When the source asset gets an approved version bump,
a future notifier scans subscriptions for that (item_kind, item_id) and
opens an upgrade PR against each subscriber's target_repo.

Storage: output/library/_subscriptions/subscriptions.jsonl (append-on-write,
rewritten on unsubscribe/reactivate). Fine for the expected volume; revisit
if subscription count crosses ~10k rows.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
SUB_DIR = ROOT / "output" / "library" / "_subscriptions"
SUB_FILE = SUB_DIR / "subscriptions.jsonl"

VALID_KINDS = {
    "skill", "agent", "mcp", "prompt", "use_case", "capability",
    # Plural forms accepted too because route paths and catalog keys vary;
    # callers should pass whatever matches their detail-view path.
    "skills", "agents", "prompts", "use_cases", "capabilities",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class ItemSubscription:
    user_id: str
    item_kind: str
    item_id: str
    subscribed_at: str
    target_repo: str        # the workspace_repo where upgrade PRs will land
    last_notified_at: Optional[str] = None
    unsubscribed_at: Optional[str] = None  # tombstone


def _read_all() -> list[ItemSubscription]:
    if not SUB_FILE.exists():
        return []
    out: list[ItemSubscription] = []
    for line in SUB_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(ItemSubscription(**d))
        except Exception:
            # Malformed line: skip rather than crash the whole subscriber set.
            continue
    return out


def _write_all(rows: list[ItemSubscription]) -> None:
    SUB_DIR.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(asdict(r)) for r in rows)
    if rows:
        body += "\n"
    SUB_FILE.write_text(body, encoding="utf-8")


def subscribe(user_id: str, item_kind: str, item_id: str,
                       target_repo: str) -> ItemSubscription:
    """Idempotent. Existing active subscription returned as-is. Tombstoned
    subscription is re-activated with a fresh subscribed_at + target_repo.
    Otherwise a new row is appended."""
    if not user_id or not item_kind or not item_id or not target_repo:
        raise ValueError("subscribe requires user_id, item_kind, item_id, target_repo")
    rows = _read_all()
    for r in rows:
        if r.user_id == user_id and r.item_kind == item_kind and r.item_id == item_id:
            if r.unsubscribed_at:
                r.unsubscribed_at = None
                r.subscribed_at = _now()
                r.target_repo = target_repo
                _write_all(rows)
            return r
    sub = ItemSubscription(
        user_id=user_id, item_kind=item_kind, item_id=item_id,
        subscribed_at=_now(), target_repo=target_repo,
    )
    rows.append(sub)
    _write_all(rows)
    return sub


def unsubscribe(user_id: str, item_kind: str, item_id: str) -> bool:
    """Mark every matching active row as unsubscribed (tombstone). Returns
    True iff at least one row was changed. Preserves the historical row
    so audit / re-subscribe is possible."""
    rows = _read_all()
    changed = False
    for r in rows:
        if (r.user_id == user_id and r.item_kind == item_kind
                  and r.item_id == item_id and not r.unsubscribed_at):
            r.unsubscribed_at = _now()
            changed = True
    if changed:
        _write_all(rows)
    return changed


def is_subscribed(user_id: str, item_kind: str, item_id: str) -> bool:
    for r in _read_all():
        if (r.user_id == user_id and r.item_kind == item_kind
                  and r.item_id == item_id and not r.unsubscribed_at):
            return True
    return False


def list_subscriptions_for_item(item_kind: str, item_id: str) -> list[ItemSubscription]:
    """Return every active subscriber to this item. Used by the upgrade
    notifier when an asset is bumped."""
    return [r for r in _read_all()
                  if r.item_kind == item_kind and r.item_id == item_id
                  and not r.unsubscribed_at]


def list_subscriptions_for_user(user_id: str) -> list[ItemSubscription]:
    """Return every active subscription for this user. Used by the
    profile page to render a 'your subscriptions' list."""
    return [r for r in _read_all()
                  if r.user_id == user_id and not r.unsubscribed_at]


def mark_notified(user_id: str, item_kind: str, item_id: str) -> bool:
    """Stamp last_notified_at on the active row. Returns True iff a row
    was updated. Called by the notifier after a successful upgrade-PR
    open."""
    rows = _read_all()
    changed = False
    for r in rows:
        if (r.user_id == user_id and r.item_kind == item_kind
                  and r.item_id == item_id and not r.unsubscribed_at):
            r.last_notified_at = _now()
            changed = True
    if changed:
        _write_all(rows)
    return changed
