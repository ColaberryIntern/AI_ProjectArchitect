"""Library inventory — surfaces every governed-asset category as a
uniform list, regardless of which Platform Core registry it comes from.

Returns plain dicts so templates don't need to know about backend types.
Every entry has: name, kind, version, owner, description, tags, source.

For asset types that don't yet have a backing store (prompts, MCP servers,
connectors, adapters), returns an empty list — the template shows a
'no items yet' card with a 'How to publish' hint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AssetCategory:
    """One row on the Library overview page."""

    key: str                # url slug — e.g. "skills"
    label: str              # display name — e.g. "Skills"
    emoji: str              # visual icon
    description: str        # one-liner
    source: str             # which Platform Core registry feeds it
    status: str             # "live" | "pending" — pending = no backing yet


CATEGORIES: list[AssetCategory] = [
    AssetCategory(
        "skills", "Skills", "🛠️",
        "Atomic, reusable units of work an agent or workflow can invoke.",
        "execution/skill_catalog.py", "live",
    ),
    AssetCategory(
        "agents", "Agents", "🤖",
        "Personas with autonomy policies — recommend, approve, low-risk-auto, full-auto.",
        "execution/ops_platform/agent_registry.py", "live",
    ),
    AssetCategory(
        "prompts", "Prompts", "💬",
        "Versioned prompt templates with evaluation hooks.",
        "skill_catalog (classified) + submissions", "live",
    ),
    AssetCategory(
        "mcp", "MCP Servers", "🔌",
        "Discovered MCP servers with capability surface + health.",
        "skill_catalog (classified) + submissions", "live",
    ),
    AssetCategory(
        "capabilities", "Capabilities", "🧩",
        "Plugin registry — every operation the platform exposes.",
        "execution/ops_platform/capability_registry.py", "live",
    ),
    AssetCategory(
        "templates", "Templates", "📋",
        "Project blueprints used by Architect to bootstrap new builds.",
        "config/blueprints", "live",
    ),
    AssetCategory(
        "policies", "Policies", "📜",
        "Rules evaluated by the policy engine at the enforcement boundary.",
        "execution/ops_platform/policy_engine.py", "live",
    ),
    AssetCategory(
        "workflows", "Workflows", "🎼",
        "Multi-step orchestrations + their step claims.",
        "execution/ops_platform/orchestration_engine.py", "live",
    ),
    AssetCategory(
        "projections", "Projections", "📊",
        "Event-sourced read models — rebuildable from history.",
        "execution/ops_platform/projection_engine.py", "live",
    ),
    AssetCategory(
        "recovery", "Recovery Playbooks", "🩹",
        "Recovery actions the coordinator can propose or apply.",
        "execution/ops_platform/recovery_coordinator.py", "live",
    ),
    AssetCategory(
        "chaos", "Chaos Drills", "🌪️",
        "Fault-injection scenarios for resilience testing.",
        "execution/ops_platform/chaos_engine.py", "live",
    ),
    AssetCategory(
        "governance", "Governance Rules", "🏛️",
        "Scorecards + controls evaluated platform-wide.",
        "execution/ops_platform/governance_scorecards.py", "live",
    ),
    AssetCategory(
        "evals", "Evaluation Datasets", "🧪",
        "Datasets used to score agent / prompt quality.",
        "execution/ops_platform/evaluation.py", "live",
    ),
    AssetCategory(
        "connectors", "Connectors", "🔗",
        "Integration definitions for external systems.",
        "(new — backing store coming)", "pending",
    ),
    AssetCategory(
        "adapters", "Tool Adapters", "🪛",
        "Glue between Platform Core and external tooling.",
        "(new — backing store coming)", "pending",
    ),
]

CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}


# ─── per-category loaders ──────────────────────────────────────────────────


def _safe(loader: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Never let a single backing-store hiccup take the whole Library down."""
    try:
        return loader() or []
    except Exception as exc:  # noqa: BLE001 — Library is read-only catalog
        return [{"name": f"(load error: {type(exc).__name__})", "kind": "error",
                       "version": "—", "owner": "—",
                       "description": str(exc)[:200], "tags": [], "source": "—"}]


def _normalize(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": s.get("name") or s.get("id") or "?",
        "kind": s.get("category") or s.get("type") or "asset",
        "version": s.get("version") or "1.0",
        "owner": s.get("owner") or "—",
        "description": (s.get("description") or "")[:200],
        "tags": s.get("tags") or [],
        "source": s.get("source_url") or s.get("source") or "",
        "_classification": s.get("_classification"),
    }


# ── Catalog loader (shared, classifies once) ─────────────────────────

_CATALOG_CACHE: dict[str, list[dict[str, Any]]] | None = None


def _load_classified_catalog() -> dict[str, list[dict[str, Any]]]:
    """Load the legacy 'skill_catalog' registry, then route every item
    through the classifier so it lands in the right Library bucket.

    Cached at module level so the 500-item classify cost is paid once
    per process. Call `reset_catalog_cache()` after a registry refresh.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    from .classifier import classify_many

    try:
        from execution.skill_catalog import load_registry
        raw = load_registry() or []
        if isinstance(raw, dict):
            raw = raw.get("skills") or raw.get("assets") or []
    except Exception:
        raw = []

    _CATALOG_CACHE = classify_many(list(raw))
    return _CATALOG_CACHE


def reset_catalog_cache() -> None:
    """Drop the classified-catalog cache (e.g. after registry refresh)."""
    global _CATALOG_CACHE
    _CATALOG_CACHE = None


def filter_for_company(rows: list[dict[str, Any]], category: str,
                              viewer_company_id: str | None) -> list[dict[str, Any]]:
    """[Auth 1] tenant filter — narrow `rows` to what `viewer_company_id`
    is allowed to see.

    Returns all rows when `viewer_company_id` is None (legacy/admin view).

    Inclusion rules per row:
      - viewer's company owns the asset (owning_company_id == viewer)
      - viewer's company has its own approval row for this asset
      - someone has approved with visibility=shared-public
      - someone has approved with visibility=shared-with-allowlist
        and the viewer's company is in the allowlist
    """
    if not viewer_company_id:
        return rows
    try:
        from . import tenancy, store
    except Exception:
        return rows   # tenancy module unavailable; degrade open

    out = []
    for row in rows:
        asset_id = row.get("name") or row.get("id") or ""
        if not asset_id:
            continue
        meta = store.get_metadata("global", category, asset_id)
        # Treat empty/missing owning_company_id as "community" -- the
        # default for legacy catalog rows. Otherwise empty-string rows
        # leak into the org-wide "all" scope only, and a freshly
        # imported skill silently belongs to nobody.
        owning = (getattr(meta, "owning_company_id", "") or "").strip() or "community"
        if owning == viewer_company_id:
            out.append(row)
            continue
        if tenancy.companies_with_access("library_asset", asset_id,
                                                    category, viewer_company_id):
            out.append(row)
    return out


def list_skills() -> list[dict[str, Any]]:
    return [_normalize(s) for s in _load_classified_catalog().get("skills", [])]


def list_mcp() -> list[dict[str, Any]]:
    return [_normalize(s) for s in _load_classified_catalog().get("mcp", [])]


def list_prompts() -> list[dict[str, Any]]:
    return [_normalize(s) for s in _load_classified_catalog().get("prompts", [])]


def list_agents() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # Native agent_registry agents
    try:
        from execution.ops_platform import agent_registry
        for n in agent_registry.list_agents() or []:
            try:
                a = agent_registry.get_agent(n)
                if isinstance(a, dict):
                    out.append({
                        "name": a.get("name") or n,
                        "kind": a.get("kind") or "agent",
                        "version": a.get("version") or "1.0",
                        "owner": a.get("owner") or "—",
                        "description": (a.get("description") or a.get("persona") or "")[:200],
                        "tags": a.get("tags") or [a.get("autonomy_policy", "recommend_only")],
                        "source": "agent_registry",
                    })
            except Exception:
                continue
    except Exception:
        pass
    # Plus anything in the classified catalog routed to agents
    out += [_normalize(s) for s in _load_classified_catalog().get("agents", [])]
    return out


def list_capabilities() -> list[dict[str, Any]]:
    from execution.ops_platform import capability_registry, plugin_loader
    out = []
    try:
        plugin_root = ROOT / "plugins"
        if plugin_root.exists():
            for p in plugin_root.rglob("manifest.json"):
                try:
                    m = json.loads(p.read_text(encoding="utf-8"))
                    out.append({
                        "name": m.get("name") or m.get("id") or p.parent.name,
                        "kind": m.get("kind") or "capability",
                        "version": m.get("version") or "1.0",
                        "owner": m.get("owner") or "—",
                        "description": (m.get("description") or "")[:200],
                        "tags": m.get("tags") or [],
                        "source": str(p.relative_to(ROOT)),
                    })
                except Exception:
                    pass
    except Exception:
        pass
    return out


def list_templates() -> list[dict[str, Any]]:
    try:
        from config.blueprints import get_all_blueprints
        out = []
        for bp in get_all_blueprints() or []:
            if isinstance(bp, dict):
                out.append({
                    "name": bp.get("name") or bp.get("id") or "?",
                    "kind": "blueprint",
                    "version": bp.get("version") or "1.0",
                    "owner": "—",
                    "description": (bp.get("description") or "")[:200],
                    "tags": bp.get("tags") or [],
                    "source": "config/blueprints",
                })
        return out
    except Exception:
        return []


def list_policies() -> list[dict[str, Any]]:
    """Policies are typically declared at runtime; surface the policy engine
    module + active rules if introspection is available."""
    out = []
    pdir = ROOT / "config" / "policies"
    if pdir.exists():
        for p in sorted(pdir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "name": d.get("name") or p.stem,
                    "kind": d.get("kind") or "policy",
                    "version": d.get("version") or "1.0",
                    "owner": d.get("owner") or "platform",
                    "description": (d.get("description") or "")[:200],
                    "tags": d.get("tags") or [],
                    "source": str(p.relative_to(ROOT)),
                })
            except Exception:
                pass
    return out


def list_workflows() -> list[dict[str, Any]]:
    from execution.ops_platform import orchestration_engine as oe
    out = []
    try:
        odir = oe._ORCH_DIR
        if odir and Path(odir).exists():
            for p in sorted(Path(odir).glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    out.append({
                        "name": d.get("name") or d.get("orchestration_id") or p.stem,
                        "kind": "orchestration",
                        "version": str(d.get("version") or "1"),
                        "owner": d.get("owner") or "—",
                        "description": (d.get("description") or "")[:200],
                        "tags": [d.get("status", "")] if d.get("status") else [],
                        "source": "orchestration_engine",
                    })
                except Exception:
                    pass
    except Exception:
        pass
    return out


def list_projections() -> list[dict[str, Any]]:
    from execution.ops_platform import projection_engine as pe
    try:
        pe.register_default_projections()
    except Exception:
        pass
    out = []
    for name in sorted(pe._REGISTRY):
        out.append({
            "name": name,
            "kind": "projection",
            "version": "1.0",
            "owner": "platform",
            "description": f"Event-sourced read model: {name}.",
            "tags": ["rebuildable"],
            "source": "projection_engine",
        })
    return out


def list_recovery() -> list[dict[str, Any]]:
    """Recovery playbooks = the 5 detectors + auto-actions in recovery_coordinator."""
    return [
        {"name": "outbox_backlog_drain", "kind": "playbook", "version": "1.0",
           "owner": "platform", "description": "Detects + drains outbox backlog when above threshold.",
           "tags": ["auto-executable"], "source": "recovery_coordinator"},
        {"name": "expired_claims_release", "kind": "playbook", "version": "1.0",
           "owner": "platform", "description": "Detects + releases stale orchestration claims.",
           "tags": ["auto-executable"], "source": "recovery_coordinator"},
        {"name": "redis_disconnect_reconnect", "kind": "playbook", "version": "1.0",
           "owner": "platform", "description": "Detects Redis disconnect + reconnects with jittered backoff.",
           "tags": ["auto-executable"], "source": "recovery_coordinator"},
        {"name": "dlq_pending_operator_review", "kind": "playbook", "version": "1.0",
           "owner": "platform", "description": "Surfaces DLQ items requiring operator decision.",
           "tags": ["operator-required"], "source": "recovery_coordinator"},
        {"name": "projection_drift_rebuild", "kind": "playbook", "version": "1.0",
           "owner": "platform", "description": "Detects projection drift + proposes rebuild.",
           "tags": ["low-risk"], "source": "recovery_coordinator"},
    ]


def list_chaos() -> list[dict[str, Any]]:
    from execution.ops_platform import chaos_engine as ce
    try:
        cdir = ce._CHAOS_DIR
        out = []
        if cdir and Path(cdir).exists():
            for p in sorted(Path(cdir).glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    out.append({
                        "name": d.get("name") or p.stem,
                        "kind": "chaos_drill",
                        "version": "1.0",
                        "owner": d.get("owner") or "—",
                        "description": (d.get("description") or "")[:200],
                        "tags": d.get("tags") or [],
                        "source": "chaos_engine",
                    })
                except Exception:
                    pass
        return out
    except Exception:
        return []


def list_governance() -> list[dict[str, Any]]:
    return [
        {"name": "rbac-quarterly-review", "kind": "scorecard", "version": "1.0",
           "owner": "platform", "description": "Quarterly access review.",
           "tags": ["compliance"], "source": "governance_scorecards"},
        {"name": "audit-chain-verify", "kind": "scorecard", "version": "1.0",
           "owner": "platform", "description": "Verifies HMAC chain integrity on audit log.",
           "tags": ["compliance", "automated"], "source": "signed_audit"},
        {"name": "autonomy-policy-coverage", "kind": "scorecard", "version": "1.0",
           "owner": "platform", "description": "Every registered agent has a declared autonomy policy.",
           "tags": ["governance"], "source": "agent_runtime"},
    ]


def list_evals() -> list[dict[str, Any]]:
    """Eval datasets — surface what evaluation.py knows about."""
    try:
        from execution.ops_platform import evaluation
        out = []
        # Try a few likely interfaces
        for attr in ("list_datasets", "list_evals", "datasets"):
            fn = getattr(evaluation, attr, None)
            if callable(fn):
                rows = fn() or []
                for r in rows:
                    if isinstance(r, dict):
                        out.append({
                            "name": r.get("name") or r.get("id") or "?",
                            "kind": "dataset",
                            "version": r.get("version") or "1.0",
                            "owner": r.get("owner") or "—",
                            "description": (r.get("description") or "")[:200],
                            "tags": r.get("tags") or [],
                            "source": "evaluation",
                        })
                break
        return out
    except Exception:
        return []


# ─── dispatch ──────────────────────────────────────────────────────────────

LOADERS: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "skills":       lambda: _safe(list_skills),
    "agents":       lambda: _safe(list_agents),
    "prompts":      lambda: _safe(list_prompts),
    "mcp":          lambda: _safe(list_mcp),
    "capabilities": lambda: _safe(list_capabilities),
    "templates":    lambda: _safe(list_templates),
    "policies":     lambda: _safe(list_policies),
    "workflows":    lambda: _safe(list_workflows),
    "projections":  lambda: _safe(list_projections),
    "recovery":     lambda: _safe(list_recovery),
    "chaos":        lambda: _safe(list_chaos),
    "governance":   lambda: _safe(list_governance),
    "evals":        lambda: _safe(list_evals),
    "connectors":   lambda: [],   # backing store TBD
    "adapters":     lambda: [],   # backing store TBD
}


def get_category(key: str) -> AssetCategory | None:
    return CATEGORY_BY_KEY.get(key)


def _load_submitted_assets(key: str) -> list[dict[str, Any]]:
    # Accepted submissions (via colaberry_propose_asset → store.review_submission)
    # land as AssetMetadata JSON files under output/library/<workspace>/<category>/.
    # The category-specific LOADER above only reads from the legacy registries
    # (skill_catalog, plugin manifests, etc.), so without this merge the
    # auto-approved propose_asset writes are invisible to /library/<category>
    # and to colaberry_list_assets. Walk every workspace dir so single-tenant
    # and per-workspace deploys both work.
    lib_root = ROOT / "output" / "library"
    if not lib_root.exists():
        return []
    out: list[dict[str, Any]] = []
    for ws_dir in lib_root.iterdir():
        if not ws_dir.is_dir() or ws_dir.name.startswith("_"):
            continue
        cat_dir = ws_dir / key
        if not cat_dir.exists() or not cat_dir.is_dir():
            continue
        for p in cat_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            row = _normalize(d)
            # filter_for_company needs the ownership row + the asset_id for
            # /library URL building. Preserve both verbatim.
            row["id"] = d.get("asset_id") or row.get("name") or p.stem
            row["owning_company_id"] = d.get("owning_company_id") or "community"
            row["vetted"] = bool(d.get("vetted"))
            out.append(row)
    return out


def load_category(key: str) -> list[dict[str, Any]]:
    fn = LOADERS.get(key)
    base = fn() if fn else []
    submitted = _load_submitted_assets(key)
    if not submitted:
        return base
    # Dedupe submitted rows against the legacy registry by name so a row
    # that exists in both places shows up once. Name is the only stable
    # cross-store key — registry rows have no asset_id, submission rows do.
    seen = {(r.get("name") or "").strip().lower() for r in base if r.get("name")}
    merged = list(base)
    for r in submitted:
        n = (r.get("name") or "").strip().lower()
        if n and n in seen:
            continue
        merged.append(r)
        if n:
            seen.add(n)
    return merged


def inventory_counts(viewer_company_id: str | None = None) -> dict[str, int]:
    """One count per category. If viewer_company_id is set, count only the
    items that tenant can see (per filter_for_company) — so the left-nav
    numbers match what's actually rendered on the category pages.
    """
    out: dict[str, int] = {}
    for c in CATEGORIES:
        rows = load_category(c.key)
        if viewer_company_id:
            rows = filter_for_company(rows, c.key, viewer_company_id)
        out[c.key] = len(rows)
    return out
