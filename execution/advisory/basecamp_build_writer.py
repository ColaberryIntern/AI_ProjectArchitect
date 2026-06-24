"""Turn a project's requirements into a Basecamp build plan.

Creates ONE to-do list in the chosen Basecamp project and one to-do per
requirement. Every to-do is:
  - **assigned** to the creating operator (so My Day picks it up), and
  - **due-dated** by spreading across the chosen pace window in build order
    (My Day drops todos that have neither a due date nor recent activity).

Each task is classified **AI-doable vs human** and that kind is encoded in the
to-do itself — a leading 🤖/🧑 in the title plus an ``[AI]``/``[Human]`` tag in
the description — so it survives every re-sync (which rebuilds the local todo
from Basecamp) and the My Day ``tier=human``/``tier=ai`` split works by design
(see ``execution.products.ops.scorer.task_kind``).

All Basecamp calls reuse the operator-scoped helpers in
``execution.products.library.mcp_tools`` (``_bc_request`` / ``_bc_account`` and
the existing ``_tool_create_todolist``), so posts appear authored by the
operator's connected Basecamp identity.
"""
from __future__ import annotations

import html
from datetime import date, timedelta

from execution.products.library import mcp_tools

PACE_DAYS = {"sprint": 7, "standard": 30, "relaxed": 90}
PACE_LABELS = {
    "sprint": "Sprint (~1 week)",
    "standard": "Standard (~1 month)",
    "relaxed": "Relaxed (~3 months)",
}

AI_EMOJI = "🤖"
HUMAN_EMOJI = "🧑"

# Keywords that mark a task as needing a human (decisions, approvals, access,
# business inputs). Everything else is treated as AI-doable (Claude Code can
# build it). Kept deterministic + testable.
_HUMAN_SIGNALS = (
    "decide", "decision", "approve", "approval", "sign off", "sign-off", "signoff",
    "stakeholder", "interview", "credential", "api key", "budget", "hire", "legal",
    "compliance", "contract", "kickoff", "kick-off", "gather requirement",
    "select a vendor", "choose a vendor", "provide access",
)


def spread_due_dates(n: int, pace: str, start: date | None = None) -> list[str]:
    """Return ``n`` ISO due dates spread across the pace window in order.

    Task i (0-based) is due at ``round((i+1)/n * window)`` days from start, so
    the first task is at least 1 day out and the last lands exactly on the
    window edge. Monotonic non-decreasing.
    """
    window = PACE_DAYS.get(pace, PACE_DAYS["standard"])
    start = start or date.today()
    if n <= 0:
        return []
    if n == 1:
        return [(start + timedelta(days=window)).isoformat()]
    offsets = [max(1, round((i + 1) / n * window)) for i in range(n)]
    for i in range(1, n):
        if offsets[i] < offsets[i - 1]:
            offsets[i] = offsets[i - 1]
    return [(start + timedelta(days=o)).isoformat() for o in offsets]


def classify_task_kind(req: dict) -> str:
    """Classify a requirement as ``"ai"`` or ``"human"`` (default ai)."""
    text = f"{req.get('name', '')} {req.get('description', '')}".lower()
    for sig in _HUMAN_SIGNALS:
        if sig in text:
            return "human"
    return "ai"


def resolve_operator_bc_person_id(user, bc_project_id: int) -> int | None:
    """The operator's Basecamp person id within a project (for assignee_ids).

    Matches the project's people list by the operator's email; falls back to
    ``user.bc_user_id`` if present. Returns None if it can't be resolved.
    """
    email = (getattr(user, "email", "") or "").strip().lower()
    try:
        people = mcp_tools._bc_request(
            "GET",
            f"https://3.basecampapi.com/{mcp_tools._bc_account()}/projects/{bc_project_id}/people.json",
            user=user,
        )
    except RuntimeError:
        people = None
    if isinstance(people, list) and email:
        for p in people:
            if (p.get("email_address") or "").strip().lower() == email:
                pid = int(p.get("id") or 0)
                if pid:
                    return pid
    bid = getattr(user, "bc_user_id", None)
    try:
        return int(bid) if bid else None
    except (TypeError, ValueError):
        return None


def _build_todo_payload(req: dict, kind: str, assignee_id: int | None, due_on: str) -> dict:
    """The Basecamp create-todo body for one requirement."""
    name = (req.get("name") or req.get("description") or "Build task").strip()
    emoji = AI_EMOJI if kind == "ai" else HUMAN_EMOJI
    tag = "[AI]" if kind == "ai" else "[Human]"

    parts: list[str] = []
    desc = (req.get("description") or "").strip()
    if desc and desc != name:
        parts.append(f"<p>{html.escape(desc)}</p>")
    meta: list[str] = []
    if req.get("priority"):
        meta.append(f"<strong>Priority:</strong> {html.escape(str(req['priority']))}")
    rtype = req.get("requirement_type") or req.get("type")
    if rtype:
        meta.append(f"<strong>Type:</strong> {html.escape(str(rtype))}")
    if meta:
        parts.append("<p>" + " &middot; ".join(meta) + "</p>")
    criteria = req.get("acceptance_criteria") or []
    if criteria:
        items = "".join(f"<li>{html.escape(str(c))}</li>" for c in criteria)
        parts.append(f"<p><strong>Acceptance criteria:</strong></p><ul>{items}</ul>")
    parts.append(f"<p>{tag}</p>")

    payload: dict = {"content": f"{emoji} {name}", "description": "".join(parts)}
    if assignee_id:
        payload["assignee_ids"] = [assignee_id]
    if due_on:
        payload["due_on"] = due_on
    return payload


def create_todolist(user, bc_project_id: int, name: str, description: str = "") -> dict:
    """Create a to-do list; returns {todolist_id, url}. Raises on failure."""
    res = mcp_tools._tool_create_todolist(
        user, {"name": name, "description": description, "bc_project_id": bc_project_id}
    )
    if not res.get("ok"):
        raise RuntimeError(
            f"create_todolist failed: {res.get('error')} {res.get('detail', '')}".strip()
        )
    return {"todolist_id": res.get("todolist_id"), "url": res.get("url", "")}


def create_todo(user, bc_project_id: int, todolist_id, payload: dict) -> dict:
    """POST a single to-do into a list."""
    url = (
        f"https://3.basecampapi.com/{mcp_tools._bc_account()}"
        f"/buckets/{bc_project_id}/todolists/{todolist_id}/todos.json"
    )
    return mcp_tools._bc_request("POST", url, payload=payload, user=user)


def publish_to_basecamp(user, bc_project_id: int, list_name: str,
                        requirements: list[dict], pace: str) -> dict:
    """Create the list + one assigned, due-dated to-do per requirement.

    Returns {todolist_id, url, tasks_created, assignee_id}. Raises if the
    operator's Basecamp person id can't be resolved (every task must be
    assigned).
    """
    assignee_id = resolve_operator_bc_person_id(user, bc_project_id)
    if not assignee_id:
        raise RuntimeError(
            "could not resolve the operator's Basecamp person id in project "
            f"{bc_project_id}; every task must be assigned"
        )
    listed = create_todolist(
        user, bc_project_id, list_name,
        description=f"<p>AI Project Architect build plan &middot; pace: "
                    f"{html.escape(PACE_LABELS.get(pace, pace))}.</p>",
    )
    todolist_id = listed["todolist_id"]
    reqs = sorted(requirements, key=lambda r: r.get("build_order", 999))
    dues = spread_due_dates(len(reqs), pace)
    created = 0
    for req, due_on in zip(reqs, dues):
        kind = classify_task_kind(req)
        payload = _build_todo_payload(req, kind, assignee_id, due_on)
        create_todo(user, bc_project_id, todolist_id, payload)
        created += 1
    return {
        "todolist_id": todolist_id,
        "url": listed["url"],
        "tasks_created": created,
        "assignee_id": assignee_id,
    }
