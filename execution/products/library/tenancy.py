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
APPROVAL_STATUSES = (
    # Legacy (pre-Workflow 1)
    "approved", "rejected", "pending", "withdrawn", "deprecated",
    # [Workflow 1] state-machine values
    "draft",              # author wrote it but hasn't submitted to queue yet
    "submitted",          # in their company's queue, not yet claimed
    "under_review",       # a reviewer has claimed it
    "changes_requested",  # reviewer asked for revisions; back to author
)
ITEM_KINDS = ("library_asset", "use_case")

# [Workflow 1] valid state transitions — guards in submit/claim/decide
SUBMISSION_TRANSITIONS = {
    "draft":              {"submitted"},
    "submitted":          {"under_review", "withdrawn"},
    "under_review":       {"approved", "rejected", "changes_requested"},
    "changes_requested":  {"draft", "submitted", "withdrawn"},
    "rejected":           {"draft"},                  # author can revise
    "approved":           {"withdrawn", "deprecated"},
    "withdrawn":          {"draft"},
    "deprecated":         set(),
    "pending":            {"submitted", "under_review"},  # legacy compat
}

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
    # [Workflow 2] cross-company share policy
    allow_cross_company_shares: bool = False    # can admins flip items to shared-public?
    allow_inbound_follows: bool = True           # can outsiders follow this co's authors?


@dataclass
class User:
    user_id: str
    email: str
    company_id: str
    display_name: str
    roles: list[str] = field(default_factory=lambda: ["consumer"])
    google_subject: str | None = None   # OAuth `sub` (Auth 2)
    workspace_repo: str | None = None   # Provision 1
    bc_user_id: int | None = None       # Basecamp human identity (My Day sync)
    bc_ai_clone_name: str | None = None # Display name for the AI clone (e.g. "Ali Clone")
    bc_extra_buckets: list[int] = field(default_factory=list)  # Extra BC project ids to sync
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


# ════════════════════════════════════════════════════════════════════
# [Workflow 1] Per-company publish workflow + moderation queue
# ════════════════════════════════════════════════════════════════════
#
# State machine (per (item, company)):
#   draft → submitted → under_review → approved | rejected | changes_requested
#   approved → withdrawn → draft (resubmit cycle)
#   rejected / changes_requested → draft
#
# The state lives on ItemApproval.status. The same join key
# (item_kind, item_id, category, company_id) is used as in [Auth 1] —
# this is intentional: one record per (item, company), and updates
# mutate that single row + append to a history log.


def _transitions_path() -> Path:
    """Append-only history of every state change. Audit trail."""
    return _root() / "approval_transitions.jsonl"


def _log_transition(item_kind: str, item_id: str, category: str,
                              company_id: str, from_status: str | None,
                              to_status: str, actor_id: str,
                              notes: str = "") -> None:
    rec = {
        "at": _now(),
        "item_kind": item_kind, "item_id": item_id,
        "category": category, "company_id": company_id,
        "from_status": from_status, "to_status": to_status,
        "actor_id": actor_id, "notes": notes,
    }
    with _transitions_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def list_transitions(item_kind: str | None = None,
                              item_id: str | None = None,
                              category: str | None = None,
                              company_id: str | None = None) -> list[dict]:
    path = _transitions_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line: continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if item_kind and rec.get("item_kind") != item_kind: continue
        if item_id and rec.get("item_id") != item_id: continue
        if category and rec.get("category") != category: continue
        if company_id and rec.get("company_id") != company_id: continue
        out.append(rec)
    return out


def can_transition(from_status: str | None, to_status: str) -> bool:
    """Is from→to a legal move? `from_status=None` means brand-new submission."""
    if from_status is None:
        return to_status in ("draft", "submitted")
    allowed = SUBMISSION_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def submit_for_review(item_kind: str, item_id: str, category: str,
                                company_id: str, author_user_id: str,
                                notes: str = "") -> "ItemApproval":
    """Author submits an item to their company's moderation queue.
    Idempotent: re-submitting a draft moves it to submitted; re-submitting
    a submitted item is a no-op."""
    existing = get_approval(item_kind, item_id, category, company_id)
    from_status = existing.status if existing else None
    if from_status == "submitted":
        return existing
    if not can_transition(from_status, "submitted"):
        raise ValueError(f"Cannot submit from {from_status}: "
                                 f"legal transitions = "
                                 f"{SUBMISSION_TRANSITIONS.get(from_status or 'None', set())}")
    ev = record_approval(
        item_kind=item_kind, item_id=item_id, category=category,
        company_id=company_id, approved_by_user_id=author_user_id,
        status="submitted",
        visibility=existing.visibility if existing else DEFAULT_VISIBILITY,
        notes=notes,
    )
    _log_transition(item_kind, item_id, category, company_id,
                            from_status, "submitted", author_user_id, notes)
    return ev


def claim_for_review(item_kind: str, item_id: str, category: str,
                              company_id: str, reviewer_user_id: str) -> "ItemApproval":
    """Reviewer claims a submitted item — moves submitted → under_review."""
    existing = get_approval(item_kind, item_id, category, company_id)
    if not existing:
        raise ValueError(f"No submission for {item_id} in {company_id}")
    if not can_transition(existing.status, "under_review"):
        raise ValueError(f"Cannot claim from {existing.status}")
    ev = record_approval(
        item_kind=item_kind, item_id=item_id, category=category,
        company_id=company_id, approved_by_user_id=reviewer_user_id,
        status="under_review", visibility=existing.visibility,
        notes=f"claimed by {reviewer_user_id}",
    )
    _log_transition(item_kind, item_id, category, company_id,
                            existing.status, "under_review", reviewer_user_id)
    return ev


def decide_review(item_kind: str, item_id: str, category: str,
                          company_id: str, reviewer_user_id: str,
                          decision: str,             # "approved" | "rejected" | "changes_requested"
                          notes: str = "",
                          visibility: str | None = None) -> "ItemApproval":
    """Reviewer decides — moves under_review → {approved|rejected|changes_requested}.
    `visibility` only used when decision='approved'; otherwise inherits existing."""
    assert decision in ("approved", "rejected", "changes_requested"), \
        f"bad decision {decision}"
    existing = get_approval(item_kind, item_id, category, company_id)
    if not existing:
        raise ValueError(f"No submission for {item_id} in {company_id}")
    if not can_transition(existing.status, decision):
        raise ValueError(f"Cannot {decision} from {existing.status}")
    ev = record_approval(
        item_kind=item_kind, item_id=item_id, category=category,
        company_id=company_id, approved_by_user_id=reviewer_user_id,
        status=decision,
        visibility=visibility or existing.visibility,
        notes=notes,
    )
    _log_transition(item_kind, item_id, category, company_id,
                            existing.status, decision, reviewer_user_id, notes)
    return ev


def queue_for_company(company_id: str,
                                status_filter: tuple[str, ...] = ("submitted", "under_review")
                                ) -> list["ItemApproval"]:
    """Moderation queue: items submitted within this company awaiting decision.
    Sorted by approved_at (which is the submitted_at for submitted items)."""
    all_approvals = list_approvals(company_id=company_id)
    queue = [a for a in all_approvals if a.status in status_filter]
    queue.sort(key=lambda a: a.approved_at)
    return queue


def queue_counts(company_id: str) -> dict[str, int]:
    """Bell counter helper — counts per state in the queue."""
    queue = list_approvals(company_id=company_id)
    out = {"submitted": 0, "under_review": 0, "changes_requested": 0}
    for a in queue:
        if a.status in out:
            out[a.status] += 1
    return out


def can_review(user: "User", category: str | None = None) -> bool:
    """Reviewer authorization check for [Workflow 1].
    v1 rule: any user with role='admin' can review for their company.
    Future: hook into config/library_approvers.json for category-aware
    sub-approver delegation."""
    if not user or not user.is_active:
        return False
    return "admin" in (user.roles or [])


# ════════════════════════════════════════════════════════════════════
# [Workflow 2] Cross-company visibility + follow-author
# ════════════════════════════════════════════════════════════════════
#
# Visibility rules ALREADY live in companies_with_access (see Auth 1).
# What Workflow 2 adds:
#   - Per-company opt-in for cross-company shares (Company.allow_cross_company_shares)
#   - "Follow this author" affordance with notification fan-out
#   - Bulk "upgrade approved item to shared-public" admin action


def can_publish_cross_company(approving_company: "Company | None") -> bool:
    """Can this company stamp an item with visibility=shared-public or
    shared-with-allowlist? Off by default for new tenants (safer)."""
    if not approving_company:
        return False
    return bool(getattr(approving_company, "allow_cross_company_shares", False))


def can_follow_author(viewer: "User", provenance: dict) -> bool:
    """Can the viewer follow this author?

    Rules:
      - Same-company always allowed (your colleagues).
      - Cross-company allowed only when the AUTHOR's company has
        allow_inbound_follows=True.
    """
    if not viewer:
        return False
    author_co = provenance.get("author_company")
    if not author_co:
        return False
    if author_co == viewer.company_id:
        return True
    co = get_company(author_co)
    if not co:
        return False
    return bool(getattr(co, "allow_inbound_follows", True))


# ── Follows — append-only event log ──────────────────────────────


@dataclass
class FollowEvent:
    event_id: str
    follower_user_id: str
    target_email: str
    action: str        # "follow" | "unfollow"
    at: str
    notes: str = ""


def _follows_path() -> Path:
    return _root() / "follows.jsonl"


def follow_author(follower_user_id: str, target_email: str,
                          notes: str = "") -> FollowEvent:
    ev = FollowEvent(
        event_id=_new_id("flw"),
        follower_user_id=follower_user_id,
        target_email=target_email.lower().strip(),
        action="follow", at=_now(), notes=notes,
    )
    with _follows_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(ev)) + "\n")
    return ev


def unfollow_author(follower_user_id: str, target_email: str,
                              notes: str = "") -> FollowEvent:
    ev = FollowEvent(
        event_id=_new_id("flw"),
        follower_user_id=follower_user_id,
        target_email=target_email.lower().strip(),
        action="unfollow", at=_now(), notes=notes,
    )
    with _follows_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(ev)) + "\n")
    return ev


def _read_follow_events() -> list[FollowEvent]:
    path = _follows_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        try:
            out.append(FollowEvent(**_drop_unknown(FollowEvent, json.loads(line))))
        except Exception:
            continue
    return out


def is_following(follower_user_id: str, target_email: str) -> bool:
    """Most-recent action wins (collapse follow/unfollow toggles)."""
    target = target_email.lower().strip()
    last = None
    for ev in _read_follow_events():
        if ev.follower_user_id == follower_user_id and ev.target_email == target:
            last = ev.action
    return last == "follow"


def followers_of(target_email: str) -> list[str]:
    """Return user_ids currently following this author."""
    target = target_email.lower().strip()
    state: dict[str, str] = {}   # follower_user_id → "follow" | "unfollow"
    for ev in _read_follow_events():
        if ev.target_email == target:
            state[ev.follower_user_id] = ev.action
    return [uid for uid, a in state.items() if a == "follow"]


def upgrade_item_visibility(item_kind: str, item_id: str, category: str,
                                          company_id: str, admin_user_id: str,
                                          new_visibility: str,
                                          shared_with: list[str] | None = None,
                                          notes: str = "") -> "ItemApproval":
    """[Workflow 2] Bulk action: upgrade an already-approved item's visibility.
    Guarded by can_publish_cross_company."""
    co = get_company(company_id)
    if new_visibility in ("shared-public", "shared-with-allowlist"):
        if not can_publish_cross_company(co):
            raise PermissionError(
                f"{company_id} does not have cross-company shares enabled. "
                f"Toggle allow_cross_company_shares=True in admin first."
            )
    existing = get_approval(item_kind, item_id, category, company_id)
    if not existing:
        raise ValueError(f"No approval record for {item_id} @ {company_id}")
    if existing.status != "approved":
        raise ValueError(f"Item is {existing.status}, must be approved to share")
    return record_approval(
        item_kind=item_kind, item_id=item_id, category=category,
        company_id=company_id, approved_by_user_id=admin_user_id,
        status="approved", visibility=new_visibility,
        shared_with=shared_with or existing.shared_with,
        notes=notes or f"visibility upgraded to {new_visibility}",
    )
