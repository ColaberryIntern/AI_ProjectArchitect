"""Provision a per-user 'X AI' Basecamp account via BC's API.

The endgame Ali wants: every Colaberry operator has a separate BC
identity (e.g. karun-ai@) that Claude Code posts as, so BC's
authorship header literally shows "Karun AI" instead of "Karun".

Two halves to provisioning:

  1. The half WE automate (this module):
     - Derive the AI email + display name from the human user
     - Call BC's grant API (PUT /projects/<id>/people/users.json with
       a 'create' payload) to invite the AI account
     - Store the new bc_user_id + bc_user_email on the human's tenancy
       record (bc_ai_user_id / bc_ai_user_email / bc_ai_provisioned_at)
     - Live-status check that distinguishes 4 states: not provisioned,
       invited (pending accept), accepted but no OAuth, OAuth granted

  2. The half BC's design forces the USER to do:
     - Accept the invite email + set a password
     - Sign into BC as the AI account in an Incognito window
     - Run /profile/connect-basecamp to grant OAuth as that account

Caveat on cost: each new BC user invited consumes a seat on the
account. Caller's responsibility to check the BC plan + seat count
before bulk-provisioning. This module surfaces BC errors honestly;
seat-exhaustion shows up as a clear "seat_limit_reached" error code.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from . import tenancy

logger = logging.getLogger(__name__)


# ── Identity derivation ───────────────────────────────────────────────

# Hardcoded overrides for users whose AI account doesn't follow the
# `<local>+ai@` / `-ai@` / `.ai@` convention. Keyed by the human's
# email. Ali's AI account is the pre-existing CB System BC user
# (vishnu@colaberry.com, name "CB System"); the system treats that
# identity as Ali's AI for both display + AI-detection purposes.
HARDCODED_AI_OVERRIDES: dict[str, dict] = {
    "ali@colaberry.com": {
        "ai_email": "vishnu@colaberry.com",
        "ai_user_id": 37708014,
        "ai_display_name": "CB System",
    },
}


def derive_ai_email(human_email: str) -> str:
    """Return the canonical AI-account email for this human.

    Default scheme: <local>+ai@<domain>. Gmail aliases route +ai@ back
    to the same inbox, so no Workspace alias provisioning is needed.

    Overridable per-deploy via COLABERRY_AI_EMAIL_SCHEME env:
      - 'plus'  (default): local+ai@domain
      - 'dash':            local-ai@domain
      - 'dot':             local.ai@domain
    """
    if not human_email or "@" not in human_email:
        return ""
    he = human_email.strip().lower()
    if he in HARDCODED_AI_OVERRIDES:
        return HARDCODED_AI_OVERRIDES[he]["ai_email"]
    local, _, domain = he.partition("@")
    scheme = (os.environ.get("COLABERRY_AI_EMAIL_SCHEME") or "plus").strip().lower()
    if scheme == "dash":
        return f"{local}-ai@{domain}"
    if scheme == "dot":
        return f"{local}.ai@{domain}"
    return f"{local}+ai@{domain}"


def derive_ai_display_name(human_user) -> str:
    """Return the AI account's display name from the human user.

    Examples:
      "Ali Muwwakkil" -> "Ali Muwwakkil AI"
      "Karun Vellanki" -> "Karun Vellanki AI"

    Override: certain users have a pre-existing non-conforming AI
    account (Ali -> "CB System"). Those return the hardcoded name.

    If the user has no display_name, falls back to the email local part.
    """
    email = (getattr(human_user, "email", "") or "").strip().lower()
    if email in HARDCODED_AI_OVERRIDES:
        return HARDCODED_AI_OVERRIDES[email]["ai_display_name"]
    name = (getattr(human_user, "display_name", "") or "").strip()
    if not name:
        if "@" in email:
            name = email.split("@", 1)[0].replace(".", " ").replace("-", " ").title()
        else:
            name = "Unknown"
    return f"{name} AI"


def derive_ai_title(human_user) -> str:
    return "Claude Code AI persona"


# ── BC API call ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of a single provisioning attempt."""
    ok: bool
    bc_user_id: Optional[int] = None
    bc_user_email: str = ""
    bc_user_name: str = ""
    project_id: int = 0
    error_code: str = ""
    error_detail: str = ""
    invite_status: str = ""  # "created" | "already_existed" | "failed"


def _bc_admin_token() -> str:
    """The shared CB System token has account-admin rights and is what
    we use to invite people. Per-user OAuth tokens don't have invite
    rights unless that user is a BC account admin.
    """
    return (os.environ.get("BASECAMP_ACCESS_TOKEN") or "").strip()


def _bc_account_id() -> str:
    return (os.environ.get("BASECAMP_ACCOUNT_ID") or "3945211").strip()


def _bc_put(url: str, payload: dict, *, timeout: float = 20.0) -> dict:
    """PUT to BC with the admin token. Raises ProvisionError on any HTTP
    error; returns parsed JSON on success."""
    token = _bc_admin_token()
    if not token:
        raise ProvisionError("missing_admin_token",
                                                    "BASECAMP_ACCESS_TOKEN env not set")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Colaberry-BC-AI-provisioning/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if e.code == 402 or "seat" in body.lower():
            raise ProvisionError("seat_limit_reached",
                                                        f"BC plan is at its seat limit: {body}")
        if e.code == 403:
            raise ProvisionError("forbidden",
                                                        "Admin token can't grant access to this project. "
                                                        "Verify token is account-admin.")
        if e.code == 422:
            raise ProvisionError("validation_failed",
                                                        f"BC rejected the invite payload: {body}")
        raise ProvisionError(f"bc_http_{e.code}",
                                                    f"HTTP {e.code} {e.reason}: {body}") from e
    except urllib.error.URLError as e:
        raise ProvisionError("network_error", str(e))


def _bc_get(url: str, *, timeout: float = 15.0) -> dict:
    token = _bc_admin_token()
    if not token:
        raise ProvisionError("missing_admin_token",
                                                    "BASECAMP_ACCESS_TOKEN env not set")
    req = urllib.request.Request(
        url, method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Colaberry-BC-AI-provisioning/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise ProvisionError(f"bc_http_{e.code}",
                                                    f"HTTP {e.code}: {body}") from e


class ProvisionError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def provision_bc_ai_account(human_user,
                                                    *,
                                                    ai_email: str = "",
                                                    ai_display_name: str = "",
                                                    grant_extra_buckets: bool = True) -> ProvisionResult:
    """Invite a new "<Name> AI" Basecamp account on the human's behalf.

    1. Derives ai_email + display_name if not supplied.
    2. Picks an initial project to invite under (the human's personal
       BC project). BC's PUT-people endpoint requires a project context.
    3. Calls BC: PUT /projects/{personal_bc_project_id}/people/users.json
       with {create: [{name, email_address, title, company_name}]}.
    4. Captures the new BC user id from the response.
    5. Optionally grants access to bc_extra_buckets (loops over them
       with PUT + grant: [bc_user_id]).
    6. Persists bc_ai_user_email + bc_ai_user_id + bc_ai_provisioned_at
       on the human's tenancy record.

    On any BC error, returns ProvisionResult(ok=False, error_code=...,
    error_detail=...) so the admin UI can render a clean message.
    """
    ai_email = (ai_email or derive_ai_email(getattr(human_user, "email", "") or "")).strip().lower()
    ai_display_name = (ai_display_name or derive_ai_display_name(human_user)).strip()
    if not ai_email:
        return ProvisionResult(ok=False, error_code="no_human_email")

    project_id = int(getattr(human_user, "personal_bc_project_id", 0) or 0)
    if not project_id:
        return ProvisionResult(
            ok=False,
            error_code="no_personal_project",
            error_detail=(
                "Human user has no personal_bc_project_id. Provision their "
                "personal project first via personal_bc_provisioner."
            ),
        )

    account = _bc_account_id()
    invite_payload = {
        "create": [{
            "name": ai_display_name,
            "email_address": ai_email,
            "title": derive_ai_title(human_user),
            "company_name": "Colaberry",
        }],
    }
    invite_url = f"https://3.basecampapi.com/{account}/projects/{project_id}/people/users.json"
    try:
        response = _bc_put(invite_url, invite_payload)
    except ProvisionError as e:
        return ProvisionResult(
            ok=False,
            project_id=project_id,
            error_code=e.code,
            error_detail=e.detail,
        )

    # BC returns a `granted` array containing all current grantees (existing
    # + newly created). We want the entry whose email matches our request.
    bc_user_id = 0
    bc_user_email_actual = ""
    bc_user_name_actual = ""
    for person in (response.get("granted") or []):
        if (person.get("email_address") or "").strip().lower() == ai_email:
            bc_user_id = int(person.get("id") or 0)
            bc_user_email_actual = (person.get("email_address") or "").lower()
            bc_user_name_actual = person.get("name", "")
            break
    if not bc_user_id:
        # Fallback: response shape may differ. Probe people list.
        try:
            people = _bc_get(f"https://3.basecampapi.com/{account}/projects/{project_id}/people.json")
            for p in people or []:
                if (p.get("email_address") or "").strip().lower() == ai_email:
                    bc_user_id = int(p.get("id") or 0)
                    bc_user_email_actual = (p.get("email_address") or "").lower()
                    bc_user_name_actual = p.get("name", "")
                    break
        except Exception:
            pass

    if not bc_user_id:
        return ProvisionResult(
            ok=False,
            project_id=project_id,
            error_code="bc_invite_succeeded_but_user_id_unknown",
            error_detail=(
                "BC accepted the invite but we couldn't find the new user "
                "in the response. Check BC manually."
            ),
        )

    # Stamp tenancy.
    try:
        human_user.bc_ai_user_email = bc_user_email_actual or ai_email
        human_user.bc_ai_user_id = bc_user_id
        human_user.bc_ai_provisioned_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        if not getattr(human_user, "bc_ai_clone_name", "") or "":
            human_user.bc_ai_clone_name = ai_display_name
        tenancy.upsert_user(human_user)
    except Exception as e:
        logger.warning("provision: tenancy upsert failed for %s: %s",
                                  human_user.email, e)

    # Best-effort grant to extra buckets so the AI can also post in
    # collaborative projects the human is on.
    if grant_extra_buckets:
        for extra_id in (getattr(human_user, "bc_extra_buckets", None) or []):
            try:
                _bc_put(
                    f"https://3.basecampapi.com/{account}/projects/{int(extra_id)}/people/users.json",
                    {"grant": [bc_user_id]},
                )
            except ProvisionError:
                # Best-effort; if granting fails the AI just can't post
                # there until the admin grants manually.
                continue

    return ProvisionResult(
        ok=True,
        bc_user_id=bc_user_id,
        bc_user_email=bc_user_email_actual or ai_email,
        bc_user_name=bc_user_name_actual or ai_display_name,
        project_id=project_id,
        invite_status="created",
    )


# ── Status check ──────────────────────────────────────────────────────


def status_for_user(human_user) -> dict:
    """Compute the BC AI status for a human user.

    Returns a dict the admin UI can render directly:
      {
        "ai_email": str,
        "ai_user_id": int | None,
        "ai_display_name": str,
        "provisioned": bool,
        "provisioned_at": str | None,
        "vault_oauth_granted": bool,
        "vault_oauth_email": str,       # which BC identity is in vault
        "vault_oauth_is_ai": bool,      # ...and does it match an AI pattern
        "state": str,                   # one of: not_provisioned |
                                        #         invited | oauth_granted_human |
                                        #         oauth_granted_ai
        "next_action": str,             # what the admin (or user) should do
      }
    """
    out = {
        "ai_email": getattr(human_user, "bc_ai_user_email", "") or derive_ai_email(human_user.email),
        "ai_user_id": getattr(human_user, "bc_ai_user_id", None),
        "ai_display_name": (
            getattr(human_user, "bc_ai_clone_name", "")
            or derive_ai_display_name(human_user)
        ),
        "provisioned": False,
        "provisioned_at": getattr(human_user, "bc_ai_provisioned_at", None),
        "vault_oauth_granted": False,
        "vault_oauth_email": "",
        "vault_oauth_is_ai": False,
        "state": "not_provisioned",
        "next_action": "",
    }
    out["provisioned"] = bool(getattr(human_user, "bc_ai_user_id", None))

    # Check vault for the user's current BC OAuth grant (which BC
    # identity is wired up RIGHT NOW for posting on their behalf).
    try:
        from . import basecamp_oauth_token
        meta = basecamp_oauth_token.get_grant_metadata(human_user)
        if meta and not meta.get("legacy"):
            out["vault_oauth_granted"] = True
            out["vault_oauth_email"] = (meta.get("bc_user_email") or "").lower()
            out["vault_oauth_is_ai"] = is_ai_account_for_user(
                out["vault_oauth_email"], human_user,
            )
    except Exception:
        pass

    # Compute the composite state.
    if out["vault_oauth_granted"] and out["vault_oauth_is_ai"]:
        out["state"] = "oauth_granted_ai"
        out["next_action"] = "Nothing -- this user is fully set up."
    elif out["vault_oauth_granted"] and not out["vault_oauth_is_ai"]:
        out["state"] = "oauth_granted_human"
        if out["provisioned"]:
            out["next_action"] = (
                f"User has connected as {out['vault_oauth_email']} (human). "
                f"Ask them to sign into BC as {out['ai_email']} in Incognito "
                "and re-run /profile/connect-basecamp."
            )
        else:
            out["next_action"] = (
                "Provision the AI account first, then ask the user to "
                "reconnect from that BC identity."
            )
    elif out["provisioned"]:
        out["state"] = "invited"
        out["next_action"] = (
            f"AI account invited as {out['ai_email']}. Tell the user to "
            "accept the invite, then sign into BC as that identity in an "
            "Incognito window + re-run /profile/connect-basecamp."
        )
    else:
        out["state"] = "not_provisioned"
        out["next_action"] = (
            f"Click 'Provision in BC' to invite {out['ai_email']} as a new "
            f"BC user."
        )
    return out


def _email_looks_like_ai(email: str) -> bool:
    """Pattern-only check (no per-user context). Duplicate of
    app.routers.basecamp_connect.is_ai_account_email so this module
    has no router dependency. Prefer is_ai_account_for_user() when you
    have the human user record -- it also matches hardcoded overrides
    like Ali -> CB System."""
    if not email:
        return False
    e = email.strip().lower()
    if "@" not in e:
        return False
    local = e.split("@", 1)[0]
    return (
        local.endswith("-ai")
        or local.endswith("+ai")
        or local.endswith(".ai")
        or local == "ai"
    )


def is_ai_account_for_user(bc_email: str, human_user) -> bool:
    """Context-aware AI-account check. Returns True when bc_email
    looks like the human user's AI persona, accounting for both the
    suffix convention AND the HARDCODED_AI_OVERRIDES map.

    Example: vishnu@colaberry.com is AI when human_user is Ali (because
    CB System is Ali's hardcoded AI), but is NOT AI when human_user is
    Vishnu (it's his own human account).
    """
    if not bc_email:
        return False
    bce = bc_email.strip().lower()
    he = (getattr(human_user, "email", "") or "").strip().lower()
    if he in HARDCODED_AI_OVERRIDES:
        if bce == HARDCODED_AI_OVERRIDES[he]["ai_email"].lower():
            return True
    return _email_looks_like_ai(bce)
