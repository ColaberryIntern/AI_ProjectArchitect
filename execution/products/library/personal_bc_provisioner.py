"""Personal Basecamp project provisioner for Op 2 (mandatory ticket doctrine).

Implements the contract from docs/specs/operator-02-mandatory-ticket-doctrine.md
(BC todo 9967247783).

Each provisioned user gets a personal Basecamp project named `{Display Name} Personal`
in the Colaberry account. Every Claude Code session anchored to a ticket in that
project (or another project the user has access to). This module owns the
provisioning side; ticket_creation_flow.py owns the per-session ticket lifecycle.

Stdlib only. Uses urllib for the BC API calls.

Usage:

    from execution.products.library import personal_bc_provisioner

    result = personal_bc_provisioner.provision_user_personal_bc(
        user_email="karun@colaberry.com",
        display_name="Karun Swaroop",
        account_id="3945211",
        bc_token=os.environ["BASECAMP_ACCESS_TOKEN"],
    )
    # result: {"action": "created"|"reused", "project_id": ..., "url": ..., "name": ...}
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

USER_AGENT = "Colaberry Operator Provisioner (ali@colaberry.com)"
PROJECT_NAME_SUFFIX = " Personal"


@dataclass
class ProvisioningResult:
    action: str             # "created" | "reused" | "failed"
    project_id: Optional[int] = None
    url: Optional[str] = None
    name: Optional[str] = None
    todolist_id: Optional[int] = None  # Phase 5 fix: default todo list id (anchor for ticket_creation_flow)
    error: Optional[str] = None


def _bc_get(url: str, bc_token: str, timeout: float = 15.0) -> tuple[bool, object]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bc_token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"
    except json.JSONDecodeError as e:
        return False, f"JSON parse error: {e}"


def _bc_post(url: str, body: dict, bc_token: str, timeout: float = 30.0) -> tuple[bool, object]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {bc_token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return False, f"HTTP {e.code}: {e.reason} {err_body[:200]}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"


def find_personal_project(
    display_name: str,
    account_id: str,
    bc_token: str,
) -> Optional[dict]:
    """Look up an existing personal project by name. Returns the project dict or None.

    Paginated through `/projects.json` (BC defaults 15 per page). Stops at a soft
    cap of 30 pages so a runaway never spins forever.
    """
    target_name = f"{display_name}{PROJECT_NAME_SUFFIX}".strip().lower()
    page = 1
    while page <= 30:
        ok, projects = _bc_get(
            f"https://3.basecampapi.com/{account_id}/projects.json?page={page}",
            bc_token,
        )
        if not ok or not projects:
            break
        for proj in projects:
            if (proj.get("name") or "").strip().lower() == target_name:
                return proj
        if len(projects) < 15:
            break
        page += 1
    return None


def provision_user_personal_bc(
    user_email: str,
    display_name: str,
    account_id: str,
    bc_token: str,
    description_override: Optional[str] = None,
) -> ProvisioningResult:
    """Create or reuse the user's personal Basecamp project.

    Idempotent: if a project named `{display_name} Personal` exists, returns it
    unchanged. Otherwise creates a new one.

    Returns a ProvisioningResult with action='created'|'reused'|'failed'.

    NOTE: Granting access to the user requires looking up the user's BC person id
    (via /people.json) and PATCHing the project's people roster. v01 creates the
    project but does NOT auto-grant access — that's a v02 follow-up since it
    requires resolving the email -> BC person id which needs additional API calls.
    For now the project is created under the bot/admin account; admin manually
    adds the user from the BC UI. v02 will close this loop.
    """
    target_name = f"{display_name}{PROJECT_NAME_SUFFIX}"

    # Idempotency check
    existing = find_personal_project(display_name, account_id, bc_token)
    if existing:
        todolist_id = _ensure_default_todolist(existing["id"], account_id, bc_token)
        return ProvisioningResult(
            action="reused",
            project_id=existing["id"],
            url=existing.get("app_url"),
            name=existing.get("name"),
            todolist_id=todolist_id,
        )

    # Create
    description = description_override or (
        f"Personal workspace for {display_name} ({user_email}). "
        "All Claude Code work flows through this project per Op 2 (mandatory ticket doctrine). "
        "Each session creates one ticket here; faithful progress updates land as comments "
        "(Op 3); auto-close fires when work is done with confidence >= 0.85 (Op 4)."
    )
    ok, body = _bc_post(
        f"https://3.basecampapi.com/{account_id}/projects.json",
        {"name": target_name, "description": description},
        bc_token,
    )
    if not ok:
        return ProvisioningResult(action="failed", error=str(body))
    project_id = body["id"]
    # Phase 5 fix: BC projects come up with an empty todoset; create a default
    # "To-dos" list so ticket_creation_flow has a place to anchor.
    todolist_id = _ensure_default_todolist(project_id, account_id, bc_token)
    return ProvisioningResult(
        action="created",
        project_id=project_id,
        url=body.get("app_url"),
        name=body.get("name"),
        todolist_id=todolist_id,
    )


def _ensure_default_todolist(project_id: int, account_id: str, bc_token: str,
                                                  list_name: str = "To-dos") -> Optional[int]:
    """Ensure the project has at least one todolist; return its id.

    BC creates a project with a todoset (in the dock) but the todoset is empty
    until something adds a list. Op 2's ticket_creation_flow.create_ticket_for_session()
    needs a todolist_id, so this fills the gap once at provision time.

    Idempotent: if a list named `list_name` already exists, returns its id.
    Returns None on hard failure rather than raising (provisioning continues;
    admin can fix manually).
    """
    # Step 1: find the todoset in the project's dock
    ok, project = _bc_get(
        f"https://3.basecampapi.com/{account_id}/projects/{project_id}.json",
        bc_token,
    )
    if not ok or not isinstance(project, dict):
        return None
    todoset = next(
        (d for d in (project.get("dock") or []) if d.get("name") == "todoset"),
        None,
    )
    if not todoset:
        return None

    # Step 2: GET the todoset to find the todolists endpoint
    ok, ts = _bc_get(todoset["url"], bc_token)
    if not ok or not isinstance(ts, dict):
        return None
    todolists_url = ts.get("todolists_url")
    if not todolists_url:
        return None

    # Step 3: list existing todolists. If one matching list_name exists, reuse.
    ok, todolists = _bc_get(todolists_url, bc_token)
    if ok and isinstance(todolists, list):
        for tl in todolists:
            if (tl.get("title") or tl.get("name") or "").strip().lower() == list_name.lower():
                return tl.get("id")
        if todolists:
            # Fall back to the first existing list rather than create another
            return todolists[0].get("id")

    # Step 4: create the default list
    ok, body = _bc_post(
        todolists_url,
        {"name": list_name, "description": (
            "Default working list. Op 2 (mandatory ticket doctrine) "
            "creates one todo here per Claude Code session."
        )},
        bc_token,
    )
    if not ok or not isinstance(body, dict):
        return None
    return body.get("id")
