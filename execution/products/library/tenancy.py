"""Multi-tenant data model — [Auth 1].

JSON-file backed (consistent with the rest of the Library product).
Tables:
    companies        — tenant root (one row per customer + Colaberry itself)
    users            — FK company_id
    item_approvals   — per-(item, company) approval record;
                          one item can be approved by many companies independently
    access_scopes    — per-user-per-tool grants (consumed by [Provision 2])

Files:
    output/library/_tenants/companies.json        — JSON-array
    output/library/_tenants/users.json            — JSON-array
    output/library/_tenants/item_approvals.json   — dict keyed by
                                                              "{kind}|{cat}|{id}|{company_id}"
    output/library/_tenants/access_scopes.jsonl   — append-only event log
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

LAYER = "platform_core"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
TENANT_ROOT = ROOT / "output" / "library" / "_tenants"

# Canonical roles + visibility tiers
ROLES = ("admin", "contributor", "consumer")
VISIBILITIES = ("same-company-only", "shared-public", "shared-with-allowlist")
APPROVAL_STATUSES = ("approved", "rejected", "pending", "withdrawn", "deprecated")
ITEM_KINDS = ("library_asset", "use_case")

DEFAULT_VISIBILITY = "same-company-only"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}-{str(uuid.uuid4()).replace('-', '')[:10]}"


def _root() -> Path:
    TENANT_ROOT.mkdir(parents=True, exist_ok=True)
    return TENANT_ROOT


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class Company:
    company_id: str            # slug e.g. "colaberry", "demo-tenant"
    display_name: str
    plan: str = "free"          # free | team | enterprise
    default_visibility: str = DEFAULT_VISIBILITY
    primary_admin_user_id: str | None = None
    created_at: str = ""
    is_active: bool = True
    notes: str = ""


@dataclass
class User:
    user_id: str
    email: str
    company_id: str
    display_name: str
    roles: list[str] = field(default_factory=lambda: ["consumer"])
    google_subject: str | None = None   # OAuth `sub` (Auth 2)
    workspace_repo: str | None = None   # Provision 1
    created_at: str = ""
    last_login_at: str | None = None
    is_active: bool = True


@dataclass
class ItemApproval:
    """One company's approval of one item. Multiple per item across companies."""

    item_kind: str             # "library_asset" | "use_case"
    item_id: str               # asset_id or use_case_id
    category: str              # library category, or "use_cases"
    company_id: str            # who approved it
    approved_by_user_id: str   # which user in that company
    approved_at: str
    status: str                # one of APPROVAL_STATUSES
    visibility: str            # one of VISIBILITIES
    notes: str = ""
    shared_with: list[str] = field(default_factory=list)   # for visibility="shared-with-allowlist"


@dataclass
class AccessScope:
    """Per-user-per-tool grant. Append-only event-log style for audit."""

    scope_id: str
    user_id: str
    tool: str                  # e.g. "gmail", "calendar", "github", "basecamp", "ccpp"
    grant_type: str            # "granted" | "revoked"
    granted_by_user_id: str
    granted_at: str
    notes: str = ""


# ── File I/O helpers ────────────────────────────────────────────────


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _drop_unknown(cls, data: dict) -> dict:
    import dataclasses
    fields = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in fields}


# ── Companies ───────────────────────────────────────────────────────


def _companies_path() -> Path:
    return _root() / "companies.json"


def list_companies(active_only: bool = True) -> list[Company]:
    rows = _read_json(_companies_path(), [])
    out = []
    for r in rows:
        try:
            c = Company(**_drop_unknown(Company, r))
            if active_only and not c.is_active:
                continue
            out.append(c)
        except Exception:
            pass
    return out


def get_company(id_or_slug: str) -> Company | None:
    for c in list_companies(active_only=False):
        if c.company_id == id_or_slug:
            return c
    return None


def upsert_company(company: Company) -> Company:
    if not company.created_at:
        company.created_at = _now()
    rows = [c for c in list_companies(active_only=False) if c.company_id != company.company_id]
    rows.append(company)
    _write_json(_companies_path(), [asdict(c) for c in rows])
    return company


def deactivate_company(company_id: str) -> Company | None:
    c = get_company(company_id)
    if not c:
        return None
    c.is_active = False
    upsert_company(c)
    return c


# ── Users ───────────────────────────────────────────────────────────


def _users_path() -> Path:
    return _root() / "users.json"


def list_users(company_id: str | None = None, active_only: bool = True) -> list[User]:
    rows = _read_json(_users_path(), [])
    out = []
    for r in rows:
        try:
            u = User(**_drop_unknown(User, r))
            if active_only and not u.is_active:
                continue
            if company_id and u.company_id != company_id:
                continue
            out.append(u)
        except Exception:
            pass
    return out


def get_user(id_or_email: str) -> User | None:
    for u in list_users(active_only=False):
        if u.user_id == id_or_email or u.email.lower() == id_or_email.lower():
            return u
    return None


def upsert_user(user: User) -> User:
    if not user.created_at:
        user.created_at = _now()
    rows = [u for u in list_users(active_only=False) if u.user_id != user.user_id]
    rows.append(user)
    _write_json(_users_path(), [asdict(u) for u in rows])
    return user


def has_role(user_id: str, role: str) -> bool:
    u = get_user(user_id)
    return bool(u and role in u.roles)


def record_login(user_id: str) -> User | None:
    u = get_user(user_id)
    if not u:
        return None
    u.last_login_at = _now()
    upsert_user(u)
    return u


# ── Item approvals — per-(item, company) ──────────────────────────


def _approvals_path() -> Path:
    return _root() / "item_approvals.json"


def _approval_key(kind: str, category: str, item_id: str, company_id: str) -> str:
    return f"{kind}|{category}|{item_id}|{company_id}"


def _load_approvals() -> dict[str, dict]:
    return _read_json(_approvals_path(), {})


def _save_approvals(data: dict[str, dict]) -> None:
    _write_json(_approvals_path(), data)


def record_approval(item_kind: str, item_id: str, category: str,
                          company_id: str, approved_by_user_id: str,
                          status: str = "approved",
                          visibility: str = DEFAULT_VISIBILITY,
                          notes: str = "",
                          shared_with: list[str] | None = None) -> ItemApproval:
    """Record/replace one company's approval of one item."""
    assert item_kind in ITEM_KINDS, f"bad kind {item_kind}"
    assert status in APPROVAL_STATUSES, f"bad status {status}"
    assert visibility in VISIBILITIES, f"bad visibility {visibility}"

    ev = ItemApproval(
        item_kind=item_kind, item_id=item_id, category=category,
        company_id=company_id, approved_by_user_id=approved_by_user_id,
        approved_at=_now(), status=status, visibility=visibility,
        notes=notes, shared_with=shared_with or [],
    )
    data = _load_approvals()
    data[_approval_key(item_kind, category, item_id, company_id)] = asdict(ev)
    _save_approvals(data)
    return ev


def get_approval(item_kind: str, item_id: str, category: str,
                       company_id: str) -> ItemApproval | None:
    data = _load_approvals()
    row = data.get(_approval_key(item_kind, category, item_id, company_id))
    if not row:
        return None
    try:
        return ItemApproval(**_drop_unknown(ItemApproval, row))
    except Exception:
        return None


def list_approvals(item_kind: str | None = None,
                          item_id: str | None = None,
                          category: str | None = None,
                          company_id: str | None = None,
                          status: str | None = None) -> list[ItemApproval]:
    data = _load_approvals()
    out: list[ItemApproval] = []
    for row in data.values():
        try:
            a = ItemApproval(**_drop_unknown(ItemApproval, row))
        except Exception:
            continue
        if item_kind and a.item_kind != item_kind: continue
        if item_id and a.item_id != item_id: continue
        if category and a.category != category: continue
        if company_id and a.company_id != company_id: continue
        if status and a.status != status: continue
        out.append(a)
    return out


def revoke_approval(item_kind: str, item_id: str, category: str,
                          company_id: str, revoked_by_user_id: str,
                          notes: str = "") -> ItemApproval | None:
    existing = get_approval(item_kind, item_id, category, company_id)
    if not existing:
        return None
    return record_approval(
        item_kind=item_kind, item_id=item_id, category=category,
        company_id=company_id,
        approved_by_user_id=revoked_by_user_id,
        status="withdrawn",
        visibility=existing.visibility,
        notes=notes or "revoked",
    )


# ── Visibility check — used by inventory filter ──────────────────


def companies_with_access(item_kind: str, item_id: str, category: str,
                                viewer_company_id: str) -> bool:
    """Can the viewer's company see this item, given the approvals on it?

    Returns True if any of:
      - viewer_company == owning company (item is theirs)  -- caller handles
      - viewer_company has its own approval row for this item
      - someone has approved it with visibility=shared-public
      - someone has approved it with visibility=shared-with-allowlist
        and viewer_company is in that allowlist
    """
    if not viewer_company_id:
        return False
    for a in list_approvals(item_kind=item_kind, item_id=item_id, category=category,
                                       status="approved"):
        if a.company_id == viewer_company_id:
            return True
        if a.visibility == "shared-public":
            return True
        if a.visibility == "shared-with-allowlist" and viewer_company_id in (a.shared_with or []):
            return True
    return False


# ── Access scopes — append-only event log ────────────────────────


def _scopes_path() -> Path:
    return _root() / "access_scopes.jsonl"


def grant_scope(user_id: str, tool: str, granted_by_user_id: str,
                  notes: str = "") -> AccessScope:
    s = AccessScope(
        scope_id=_new_id("scope"), user_id=user_id, tool=tool,
        grant_type="granted", granted_by_user_id=granted_by_user_id,
        granted_at=_now(), notes=notes,
    )
    with _scopes_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(s)) + "\n")
    return s


def revoke_scope(user_id: str, tool: str, revoked_by_user_id: str,
                   notes: str = "") -> AccessScope:
    s = AccessScope(
        scope_id=_new_id("scope"), user_id=user_id, tool=tool,
        grant_type="revoked", granted_by_user_id=revoked_by_user_id,
        granted_at=_now(), notes=notes,
    )
    with _scopes_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(s)) + "\n")
    return s


def current_scopes(user_id: str) -> set[str]:
    """Compute the current set of granted tools for a user (most-recent wins)."""
    path = _scopes_path()
    if not path.exists():
        return set()
    state: dict[str, str] = {}   # tool → "granted" | "revoked"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line: continue
        try:
            s = AccessScope(**_drop_unknown(AccessScope, json.loads(line)))
        except Exception:
            continue
        if s.user_id != user_id: continue
        state[s.tool] = s.grant_type
    return {tool for tool, gt in state.items() if gt == "granted"}


# ── Seed (called by backfill + tests) ────────────────────────────


def seed_initial_companies_and_users() -> dict[str, int]:
    """Idempotent — only adds rows that don't exist yet."""
    counts = {"companies": 0, "users": 0}

    if not get_company("colaberry"):
        upsert_company(Company(
            company_id="colaberry",
            display_name="Colaberry Inc",
            plan="enterprise",
            default_visibility="same-company-only",
            notes="Tenant root for Colaberry's own content + the global Library curators.",
        ))
        counts["companies"] += 1

    if not get_company("demo-tenant"):
        upsert_company(Company(
            company_id="demo-tenant",
            display_name="Demo Tenant",
            plan="free",
            default_visibility="same-company-only",
            notes="Dev/test company. Used to verify multi-tenant isolation.",
        ))
        counts["companies"] += 1

    # Seed Colaberry curators per config/library_approvers.json
    approver_seed = [
        ("ali@colaberry.com", "Ali Muwwakkil", ["admin", "contributor", "consumer"]),
        ("ram@colaberry.com", "Ram", ["admin", "contributor", "consumer"]),
        ("karun@colaberry.com", "Karun", ["admin", "contributor", "consumer"]),
        ("kes@colaberry.com", "Kes", ["admin", "contributor", "consumer"]),
    ]
    for email, name, roles in approver_seed:
        if not get_user(email):
            upsert_user(User(
                user_id=_new_id("usr"),
                email=email, display_name=name,
                company_id="colaberry", roles=roles,
            ))
            counts["users"] += 1

    # And one demo user on demo-tenant
    if not get_user("demo@demo-tenant.local"):
        upsert_user(User(
            user_id=_new_id("usr"),
            email="demo@demo-tenant.local",
            display_name="Demo Admin",
            company_id="demo-tenant",
            roles=["admin", "contributor", "consumer"],
        ))
        counts["users"] += 1

    # Set primary_admin on colaberry to Ali (after Ali is seeded)
    colaberry = get_company("colaberry")
    ali = get_user("ali@colaberry.com")
    if colaberry and ali and not colaberry.primary_admin_user_id:
        colaberry.primary_admin_user_id = ali.user_id
        upsert_company(colaberry)

    return counts
