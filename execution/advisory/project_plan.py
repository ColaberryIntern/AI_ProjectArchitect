"""`project-plan.json` — schema, the ID Law, content hashing, and validation.

The Basecamp build-out is driven by a desired-state plan
(``cb-project-plan/v1``): ``initiatives[] → lists[] → todos[]`` plus
``designs[]`` and a ``peopleMap``. This module owns the three pure, deterministic
pieces the rest of the system depends on:

  * **The ID Law** — an ``id`` is *computed* from a node's position in the
    requirement tree, never authored. Identical titles map to identical ids;
    genuine duplicates get a deterministic ``-2``/``-3`` tiebreak in document
    order. The chapter number is the spine (survives title renames).
  * **The content hash** — independent of the id. The id answers "is this the
    same node?"; the hash answers "did it change?". This separation lets a
    reconciler update a Basecamp todo in place (preserving comments/completion)
    when only its text changed.
  * **The validation gate** — fail-loud checks run BEFORE any Basecamp write
    (the "requirements created and *verified*" step). Governance rules — notably
    "every feature has a BUILD and a BREAK todo" (Failure-First Design) — are
    structural constraints here, not hopes.

No I/O, no LLM: deterministic and unit-testable.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata

SCHEMA = "cb-project-plan/v1"
PHASES = ("BUILD", "BREAK", "HARDEN")
VALID_STATUS = ("active", "proposed", "retired")

_PHASE_TAG_RE = re.compile(r"^\s*\[(BUILD|BREAK|HARDEN)\]\s*", re.IGNORECASE)
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── The ID Law ──────────────────────────────────────────────────────

def slug(text: str, max_len: int = 40) -> str:
    """NFKD-normalize → strip accents → lowercase → collapse non-[a-z0-9] to a
    single '-' → trim → truncate to ``max_len`` at a word boundary."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    norm = _NON_SLUG_RE.sub("-", norm.lower()).strip("-")
    if len(norm) <= max_len:
        return norm
    cut = norm[:max_len]
    if "-" in cut:
        cut = cut[: cut.rfind("-")]  # back off to the last whole word
    return cut.strip("-")


def strip_phase_tag(title: str) -> str:
    """Drop a leading [BUILD]/[BREAK]/[HARDEN] tag so re-tagging a todo doesn't
    change its id."""
    return _PHASE_TAG_RE.sub("", title or "").strip()


def _ch(chapter_number) -> str:
    try:
        return f"ch{int(chapter_number):02d}"
    except (TypeError, ValueError):
        return "ch00"


def init_id(chapter_number, chapter_title: str) -> str:
    return f"INIT.{_ch(chapter_number)}-{slug(chapter_title)}"


def list_id(chapter_number, feature_title: str) -> str:
    return f"LIST.{_ch(chapter_number)}.{slug(feature_title)}"


def todo_id(parent_list_id: str, todo_title: str) -> str:
    base = parent_list_id.replace("LIST.", "TODO.", 1)
    return f"{base}.{slug(strip_phase_tag(todo_title))}"


def design_id(title: str) -> str:
    return f"DESIGN.{slug(title)}"


def resolve_collisions(ids: list[str]) -> list[str]:
    """Append -2,-3,… to repeats within a parent, in document order. The first
    occurrence keeps the bare id. Deterministic for a fixed document order."""
    counts: dict[str, int] = {}
    out: list[str] = []
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
        out.append(i if counts[i] == 1 else f"{i}-{counts[i]}")
    return out


def assign_ids(plan: dict) -> dict:
    """Set every node's ``id`` to its derived, collision-resolved value, in
    document order. Mutates and returns ``plan``. Parsers call this so ids are
    correct by construction; the validator recomputes to catch hand-edits."""
    initiatives = plan.get("initiatives") or []
    init_ids = resolve_collisions([init_id(i.get("order"), i.get("title", "")) for i in initiatives])
    for init, iid in zip(initiatives, init_ids):
        init["id"] = iid
        ch = init.get("order")
        lists = init.get("lists") or []
        lst_ids = resolve_collisions([list_id(ch, l.get("title", "")) for l in lists])
        for lst, lid in zip(lists, lst_ids):
            lst["id"] = lid
            todos = lst.get("todos") or []
            t_ids = resolve_collisions([todo_id(lid, t.get("title", "")) for t in todos])
            for todo, tid in zip(todos, t_ids):
                todo["id"] = tid
    designs = plan.get("designs") or []
    d_ids = resolve_collisions([design_id(d.get("title", "")) for d in designs])
    for d, did in zip(designs, d_ids):
        d["id"] = did
    return plan


def iter_nodes(plan: dict):
    """Yield (level, node, parent) for every node. level ∈ initiative|list|todo|design."""
    for init in plan.get("initiatives") or []:
        yield "initiative", init, None
        for lst in init.get("lists") or []:
            yield "list", lst, init
            for todo in lst.get("todos") or []:
                yield "todo", todo, lst
    for d in plan.get("designs") or []:
        yield "design", d, None


# ── Content hash (independent of the id) ────────────────────────────

# Fields that define "did this node change?" — deliberately excludes the id.
# ``kind`` (ai|human) is included so a re-classification re-syncs the rendered
# [AI]/[Human] marker that drives the My Day tier split.
_HASH_FIELDS = (
    "title", "charter", "successMetric", "acceptance", "phase", "kind", "steps",
    "dueOffsetDays", "assignee", "order", "status", "designs", "deps",
)


def canonicalize(node: dict) -> str:
    out: dict = {}
    for k in _HASH_FIELDS:
        v = node.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
        elif isinstance(v, list):
            v = sorted(str(x) for x in v)  # designs/deps are order-independent
        out[k] = v
    return json.dumps(out, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(node: dict) -> str:
    return "sha256:" + hashlib.sha256(canonicalize(node).encode("utf-8")).hexdigest()


# ── Validation gate (fail-loud before any Basecamp write) ───────────

def validate_plan(plan: dict, doc_anchors: set[str] | None = None) -> list[str]:
    """Return a list of human-readable violations; empty list == valid.

    Enforces the contract from the spec:
      1. every id equals its computed value (no hand-edited ids)
      2. every id is globally unique
      3. every todo has phase ∈ {BUILD,BREAK,HARDEN} and a non-null acceptance
      4. every list.designs[] / todo.deps[] references an existing node id
      5. every node's docAnchor exists in the source doc (only if doc_anchors given)
      6. no active node references a retired node
      7. every feature has ≥1 active BUILD and ≥1 active BREAK todo
    """
    errors: list[str] = []

    # Rule 1: ids match the ID Law (recompute on a copy, compare by position).
    expected = assign_ids(copy.deepcopy(plan))
    for (lvl, node, _), (_, exp, _) in zip(iter_nodes(plan), iter_nodes(expected)):
        if node.get("id") != exp.get("id"):
            errors.append(
                f"{lvl} id '{node.get('id')}' != computed '{exp.get('id')}' "
                f"(ids are derived, not authored)"
            )

    # Rule 2: global uniqueness; collect all ids + their statuses.
    id_status: dict[str, str] = {}
    seen: set[str] = set()
    for lvl, node, _ in iter_nodes(plan):
        nid = node.get("id")
        if not nid:
            errors.append(f"{lvl} node missing an id")
            continue
        if nid in seen:
            errors.append(f"duplicate id '{nid}'")
        seen.add(nid)
        id_status[nid] = node.get("status", "active")

    # Rules 3-7 walk the tree.
    for lvl, node, parent in iter_nodes(plan):
        status = node.get("status", "active")
        # Rule 5: docAnchor exists.
        if doc_anchors is not None:
            anchor = node.get("docAnchor")
            if anchor and anchor not in doc_anchors:
                errors.append(f"{lvl} '{node.get('id')}' docAnchor '{anchor}' not in source doc")

        if lvl == "todo":
            # Rule 3
            if node.get("phase") not in PHASES:
                errors.append(f"todo '{node.get('id')}' has invalid phase {node.get('phase')!r}")
            if not (node.get("acceptance") or "").strip():
                errors.append(f"todo '{node.get('id')}' has no acceptance criterion")
            # Rule 4 + 6: deps
            for dep in node.get("deps") or []:
                if dep not in id_status:
                    errors.append(f"todo '{node.get('id')}' deps references missing id '{dep}'")
                elif status == "active" and id_status[dep] == "retired":
                    errors.append(f"active todo '{node.get('id')}' depends on retired '{dep}'")

        if lvl == "list":
            # Rule 4 + 6: designs
            for des in node.get("designs") or []:
                if des not in id_status:
                    errors.append(f"list '{node.get('id')}' designs references missing id '{des}'")
                elif status == "active" and id_status[des] == "retired":
                    errors.append(f"active list '{node.get('id')}' references retired design '{des}'")
            # Rule 7: every feature has ≥1 active BUILD and ≥1 active BREAK todo.
            if status == "active":
                phases = {
                    (t.get("phase") or "").upper()
                    for t in (node.get("todos") or [])
                    if t.get("status", "active") == "active"
                }
                if "BUILD" not in phases:
                    errors.append(f"feature '{node.get('id')}' has no active BUILD todo")
                if "BREAK" not in phases:
                    errors.append(
                        f"feature '{node.get('id')}' has no active BREAK todo "
                        f"(Failure-First Design)"
                    )

        # status validity
        if status not in VALID_STATUS:
            errors.append(f"{lvl} '{node.get('id')}' has invalid status {status!r}")

    return errors
