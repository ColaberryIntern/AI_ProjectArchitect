"""Optimistic concurrency — revision_id + compare-and-swap helpers.

Pattern
-------
Phase 5+ writes silently overwrite on save. Phase 7A introduces revision IDs
that change on every persist; readers carry the revision they observed, and
the next write must present it via ``compare_revision()``. Stale revisions
raise ``ConcurrencyConflict`` and an ``optimistic.conflict`` audit row is
written.

This is a thin helper module — every domain module (capability_versions,
approvals, incidents, scheduler, marketplace, policies) can opt in by:

   1. Storing ``revision_id`` in its dataclass.
   2. Calling ``optimistic_concurrency.new_revision()`` before each persist.
   3. Calling ``optimistic_concurrency.compare(observed, current)`` before
      overwrite to detect stale writes.

The helper itself stores no state. Backwards compatible: existing rows
without ``revision_id`` are treated as the initial revision.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from execution.ops_platform import audit_log


@dataclass
class ConcurrencyConflict(Exception):
    entity_type: str
    entity_id: str
    observed_revision: str | None
    current_revision: str | None

    def __str__(self) -> str:
        return (f"concurrency conflict on {self.entity_type}:{self.entity_id} — "
                f"observed {self.observed_revision}, current {self.current_revision}")


def new_revision() -> str:
    """Return a fresh revision id."""
    return uuid.uuid4().hex


def compare(
    *,
    entity_type: str,
    entity_id: str,
    observed_revision: str | None,
    current_revision: str | None,
    actor: dict | str | None = None,
) -> None:
    """Raise ConcurrencyConflict + audit row when the observed revision does
    not match the current one.

    ``observed_revision=None`` is treated as "create-only" — succeeds when the
    target has no revision yet.

    ``current_revision=None`` (target doesn't exist yet) succeeds.
    """
    if current_revision is None:
        return
    if observed_revision is None:
        # Caller did not declare a revision — treat as unsafe; record conflict
        audit_log.record(
            action="optimistic.conflict", entity_type=entity_type,
            entity_id=entity_id,
            actor=actor if isinstance(actor, dict) else {"name": str(actor or "anonymous")},
            metadata={"reason": "writer did not declare observed_revision"},
        )
        raise ConcurrencyConflict(entity_type=entity_type, entity_id=entity_id,
                                    observed_revision=None,
                                    current_revision=current_revision)
    if observed_revision != current_revision:
        audit_log.record(
            action="optimistic.conflict", entity_type=entity_type,
            entity_id=entity_id,
            actor=actor if isinstance(actor, dict) else {"name": str(actor or "anonymous")},
            metadata={"observed": observed_revision, "current": current_revision},
        )
        raise ConcurrencyConflict(entity_type=entity_type, entity_id=entity_id,
                                    observed_revision=observed_revision,
                                    current_revision=current_revision)
