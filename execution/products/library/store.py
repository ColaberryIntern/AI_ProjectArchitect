"""Library persistence — per-asset metadata, ratings, comments, submissions.

JSON files under output/library/ keyed by (workspace, category, asset_id).
Light enough to seed by hand; structured enough to migrate to a real
datastore later.

Workspace scoping:
    Every operation takes a `workspace` argument. Pass "global" for
    org-wide assets. Per-workspace assets live in their own subtree.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
LIB_ROOT = ROOT / "output" / "library"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ws_dir(workspace: str, category: str) -> Path:
    p = LIB_ROOT / workspace / category
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Asset metadata (vetting, ownership, descriptions) ───────────────


@dataclass
class AssetMetadata:
    asset_id: str
    category: str
    workspace: str = "global"
    name: str = ""
    description: str = ""
    how_to_use: str = ""
    example: str = ""
    owner: str = ""
    version: str = "1.0"
    tags: list[str] = field(default_factory=list)
    source: str = ""
    # Vetting
    vetted: bool = False
    vetted_by: str | None = None
    vetted_at: str | None = None
    vetted_status: str = "unreviewed"  # unreviewed | pending | vetted | rejected
    vetted_notes: str = ""
    submitted_by: str | None = None
    submitted_at: str | None = None
    # Aggregates (denormalized for list views)
    rating_avg: float = 0.0
    rating_count: int = 0
    comment_count: int = 0
    # ── Enrichment (populated by enrichment_job) ─────────────────
    enrichment_state: str = "unenriched"  # unenriched | enriching | enriched | failed
    enriched_at: str | None = None
    enriched_by: str | None = None
    enrichment_error: str | None = None
    readme_markdown: str = ""            # Rendered as the main "About" body
    install_steps: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)   # additional code examples
    code_samples: list[dict] = field(default_factory=list)  # [{path, language, content}]
    license: str = ""
    languages: list[str] = field(default_factory=list)
    # [Workflow 3b] dependencies is a list of typed cross-asset edges so
    # the install flow can walk them. Each entry: {category, asset_id,
    # optional}. Legacy list[str] entries (opaque strings) are normalized
    # to {category: "?", asset_id: s, optional: False} on load+save so
    # the install walker can identify which entries still need curation.
    dependencies: list[dict] = field(default_factory=list)
    repo_stats: dict = field(default_factory=dict)      # {stars, forks, last_commit, default_branch}
    snapshot_path: str = ""              # relative path under output/library/_snapshots/
    source_url: str = ""                 # canonical fetch URL (may differ from source label)
    # ── Plain-English explanation + actionable links ───────────────
    what_its_for: str = ""               # 1-3 sentence "why use this" — different from README
    install_command: str = ""            # e.g. "npm install @modelcontextprotocol/server-filesystem"
    install_url: str = ""                # e.g. npm/PyPI package page
    docs_url: str = ""                   # external docs link
    homepage_url: str = ""               # project homepage if different from source
    # ── Multi-tenant ownership ([Auth 1]) ─────────────────────────
    # Which company owns this asset record. Defaults to "community" so
    # the legacy/global catalog isn't auto-attributed to any one paying
    # tenant. Companies see their own assets by default; community
    # assets are visible only when the user opts in to ?scope=all
    # (or a future ?scope=community). Cross-company visibility is
    # governed by tenancy.ItemApproval rows + visibility tiers.
    owning_company_id: str = "community"
    # ── Per-category schema fields (category_schemas.SCHEMAS) ─────
    # Added so the extracted-asset pipeline + submit form can populate
    # the category-specific required fields without resorting to a dict
    # blob. Empty defaults are fine; only the categories that need a
    # given field will set it. The submit-form schema (Piece 1 of the
    # asset-flow-unify work) is the authoritative requiredness check.
    role: str = ""                        # agents
    system_prompt: str = ""               # agents
    autonomy_level: str = ""              # agents
    allowed_tools: list[str] = field(default_factory=list)  # agents
    guardrails: str = ""                  # agents
    prompt_body: str = ""                 # prompts
    expected_output: str = ""             # prompts
    model_hint: str = ""                  # prompts
    config_template: str = ""             # mcp
    env_vars: str = ""                    # mcp
    blueprint_path: str = ""              # templates
    scaffolding_config: str = ""          # templates
    rule_text: str = ""                   # policies / governance
    enforcement_point: str = ""           # policies / governance
    steps: str = ""                       # workflows  (multi-line string, one step per line)
    invocation_pattern: str = ""          # workflows
    success_criteria: str = ""            # workflows
    trigger_condition: str = ""           # recovery
    mitigation_action: str = ""           # recovery
    fault_scenario: str = ""              # chaos
    event_source: str = ""                # projections
    rebuild_strategy: str = ""            # projections
    dataset_url: str = ""                 # evals
    scoring_method: str = ""              # evals


def meta_path(workspace: str, category: str, asset_id: str) -> Path:
    return _ws_dir(workspace, category) / f"{asset_id}.meta.json"


_KNOWN_FIELDS: set[str] | None = None


def _known_fields() -> set[str]:
    global _KNOWN_FIELDS
    if _KNOWN_FIELDS is None:
        import dataclasses
        _KNOWN_FIELDS = {f.name for f in dataclasses.fields(AssetMetadata)}
    return _KNOWN_FIELDS


def _normalize_dependencies(deps: Any) -> list[dict]:
    """[Workflow 3b] Normalize an asset's dependencies into the typed
    list[dict] form. Accepts the legacy list[str] shape (opaque package
    or asset names) and wraps each entry as {category: "?", asset_id: s,
    optional: False}. The "?" category is a sentinel: the install walker
    can detect it, skip the unresolvable entry, and surface it as a TODO
    in the PR body so a curator can map it later.
    """
    if not deps:
        return []
    out: list[dict] = []
    for d in deps:
        if isinstance(d, dict):
            # Already typed. Backfill missing keys with defaults.
            out.append({
                "category": str(d.get("category") or "?"),
                "asset_id": str(d.get("asset_id") or ""),
                "optional": bool(d.get("optional", False)),
            })
        elif isinstance(d, str):
            # Legacy opaque string. Wrap with "?" category sentinel.
            out.append({"category": "?", "asset_id": d, "optional": False})
        # silently drop anything else (e.g. None, ints) -- malformed data
    return [e for e in out if e["asset_id"]]


def get_metadata(workspace: str, category: str, asset_id: str) -> AssetMetadata:
    p = meta_path(workspace, category, asset_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Drop unknown keys (e.g. removed fields); fill missing with defaults
            cleaned = {k: v for k, v in data.items() if k in _known_fields()}
            if "dependencies" in cleaned:
                cleaned["dependencies"] = _normalize_dependencies(cleaned["dependencies"])
            return AssetMetadata(**cleaned)
        except Exception:
            pass
    return AssetMetadata(asset_id=asset_id, category=category, workspace=workspace)


def save_metadata(meta: AssetMetadata) -> None:
    p = meta_path(meta.workspace, meta.category, meta.asset_id)
    # asset_id may contain path separators (e.g. "n8n Cron/Schedule Trigger");
    # ensure the nested directory exists before write.
    p.parent.mkdir(parents=True, exist_ok=True)
    # Normalize on write so any in-memory legacy string entries
    # (e.g. populated by older code paths) get persisted in typed form.
    meta.dependencies = _normalize_dependencies(meta.dependencies)
    p.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")


def upsert_metadata(workspace: str, category: str, asset_id: str,
                          **fields: Any) -> AssetMetadata:
    m = get_metadata(workspace, category, asset_id)
    for k, v in fields.items():
        setattr(m, k, v)
    save_metadata(m)
    return m


# ── Vetting ─────────────────────────────────────────────────────────


def mark_vetted(workspace: str, category: str, asset_id: str, vetted_by: str,
                  notes: str = "") -> AssetMetadata:
    m = get_metadata(workspace, category, asset_id)
    m.vetted = True
    m.vetted_by = vetted_by
    m.vetted_at = _now()
    m.vetted_status = "vetted"
    m.vetted_notes = notes
    save_metadata(m)
    return m


def reject(workspace: str, category: str, asset_id: str, vetted_by: str,
             notes: str = "") -> AssetMetadata:
    m = get_metadata(workspace, category, asset_id)
    m.vetted = False
    m.vetted_by = vetted_by
    m.vetted_at = _now()
    m.vetted_status = "rejected"
    m.vetted_notes = notes
    save_metadata(m)
    return m


# ── Ratings ─────────────────────────────────────────────────────────


@dataclass
class Rating:
    rating_id: str
    asset_id: str
    category: str
    workspace: str
    rater: str
    stars: int
    note: str = ""
    created_at: str = ""


def _ratings_file(workspace: str, category: str, asset_id: str) -> Path:
    return _ws_dir(workspace, category) / f"{asset_id}.ratings.jsonl"


def list_ratings(workspace: str, category: str, asset_id: str) -> list[Rating]:
    p = _ratings_file(workspace, category, asset_id)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(Rating(**json.loads(line)))
        except Exception:
            pass
    return rows


def add_rating(workspace: str, category: str, asset_id: str,
                rater: str, stars: int, note: str = "") -> Rating:
    stars = max(1, min(5, int(stars)))
    r = Rating(
        rating_id=str(uuid.uuid4())[:8],
        asset_id=asset_id, category=category, workspace=workspace,
        rater=rater, stars=stars, note=note, created_at=_now(),
    )
    p = _ratings_file(workspace, category, asset_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(r)) + "\n")
    # update aggregate
    ratings = list_ratings(workspace, category, asset_id)
    avg = sum(x.stars for x in ratings) / len(ratings)
    upsert_metadata(workspace, category, asset_id,
                          rating_avg=round(avg, 2), rating_count=len(ratings))
    return r


# ── Comments ────────────────────────────────────────────────────────


@dataclass
class Comment:
    comment_id: str
    asset_id: str
    category: str
    workspace: str
    author: str
    body: str
    created_at: str = ""


def _comments_file(workspace: str, category: str, asset_id: str) -> Path:
    return _ws_dir(workspace, category) / f"{asset_id}.comments.jsonl"


def list_comments(workspace: str, category: str, asset_id: str) -> list[Comment]:
    p = _comments_file(workspace, category, asset_id)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(Comment(**json.loads(line)))
        except Exception:
            pass
    return rows


def add_comment(workspace: str, category: str, asset_id: str,
                  author: str, body: str) -> Comment:
    c = Comment(
        comment_id=str(uuid.uuid4())[:8],
        asset_id=asset_id, category=category, workspace=workspace,
        author=author, body=body.strip(), created_at=_now(),
    )
    p = _comments_file(workspace, category, asset_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(c)) + "\n")
    upsert_metadata(workspace, category, asset_id,
                          comment_count=len(list_comments(workspace, category, asset_id)))
    return c


# ── Submissions (Add to Library) ────────────────────────────────────


@dataclass
class Submission:
    submission_id: str
    workspace: str
    category: str
    submitted_by: str
    name: str
    description: str
    how_to_use: str = ""
    example: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""
    status: str = "pending"  # pending | accepted | rejected
    review_notes: str = ""
    submitted_at: str = ""
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    asset_id: str | None = None  # set when accepted (links to created asset)
    # Category-specific extras (steps, prompt_body, system_prompt, etc.).
    # Keys here come from category_schemas.SCHEMAS[category]. Anything that
    # maps 1:1 to AssetMetadata fields is promoted at acceptance time;
    # anything that doesn't is preserved verbatim for future use.
    payload: dict = field(default_factory=dict)
    # Submitter's company at submit time, captured so the asset can default
    # to the right owning_company_id when accepted. Empty / "anonymous" for
    # not-signed-in submissions.
    owning_company_id: str = ""


def _sub_dir(workspace: str) -> Path:
    p = LIB_ROOT / workspace / "_submissions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def submit(workspace: str, category: str, submitted_by: str, name: str,
             description: str, how_to_use: str = "", example: str = "",
             tags: list[str] | None = None, source: str = "",
             payload: dict | None = None,
             owning_company_id: str = "") -> Submission:
    s = Submission(
        submission_id=str(uuid.uuid4())[:8],
        workspace=workspace, category=category, submitted_by=submitted_by,
        name=name.strip(), description=description.strip(),
        how_to_use=how_to_use.strip(), example=example.strip(),
        tags=tags or [], source=source, submitted_at=_now(),
        payload=payload or {},
        owning_company_id=owning_company_id or "",
    )
    p = _sub_dir(workspace) / f"{s.submission_id}.json"
    p.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    return s


def list_submissions(workspace: str | None = None,
                          status: str | None = None) -> list[Submission]:
    """List submissions across all workspaces (when workspace is None) or one."""
    results: list[Submission] = []
    if workspace:
        bases = [LIB_ROOT / workspace / "_submissions"]
    else:
        if not LIB_ROOT.exists():
            return []
        bases = [d / "_submissions" for d in LIB_ROOT.iterdir() if d.is_dir()]
    for base in bases:
        if not base.exists():
            continue
        for p in base.glob("*.json"):
            try:
                s = Submission(**json.loads(p.read_text(encoding="utf-8")))
                if status is None or s.status == status:
                    results.append(s)
            except Exception:
                pass
    results.sort(key=lambda x: x.submitted_at, reverse=True)
    return results


def review_submission(workspace: str, submission_id: str, decision: str,
                            reviewer: str, notes: str = "") -> Submission | None:
    """Accept or reject a submission. On accept, create the asset metadata."""
    p = _sub_dir(workspace) / f"{submission_id}.json"
    if not p.exists():
        return None
    s = Submission(**json.loads(p.read_text(encoding="utf-8")))
    s.status = decision  # "accepted" or "rejected"
    s.reviewed_at = _now()
    s.reviewed_by = reviewer
    s.review_notes = notes
    if decision == "accepted":
        asset_id = f"sub-{s.submission_id}"
        s.asset_id = asset_id
        # Promote category-specific extras from payload into AssetMetadata
        # for any key that maps 1:1 to a field. Anything else stays in the
        # Submission's payload (preserved for audit / future schema growth).
        valid_keys = {f.name for f in AssetMetadata.__dataclass_fields__.values()}
        meta_extras = {k: v for k, v in (s.payload or {}).items()
                                  if k in valid_keys and k not in {
                                      "asset_id", "category", "workspace", "name",
                                      "description", "owner", "tags", "source",
                                      "vetted", "vetted_by", "vetted_at",
                                      "vetted_status", "submitted_by", "submitted_at",
                                      "owning_company_id",
                                  }}
        meta = AssetMetadata(
            asset_id=asset_id, category=s.category, workspace=s.workspace,
            name=s.name, description=s.description,
            how_to_use=s.how_to_use, example=s.example,
            owner=s.submitted_by, tags=s.tags, source=s.source or "user-submitted",
            vetted=True, vetted_by=reviewer, vetted_at=_now(),
            vetted_status="vetted",
            submitted_by=s.submitted_by, submitted_at=s.submitted_at,
            owning_company_id=s.owning_company_id or "community",
            **meta_extras,
        )
        save_metadata(meta)
    p.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    return s


# ── Workspaces (cheap registry) ─────────────────────────────────────


def list_workspaces() -> list[str]:
    """List directories that contain at least one asset.

    Always includes 'global' as the default workspace.
    """
    out = ["global"]
    if LIB_ROOT.exists():
        for d in LIB_ROOT.iterdir():
            if d.is_dir() and d.name not in out and not d.name.startswith("_"):
                out.append(d.name)
    return out


def ensure_workspace(workspace: str) -> None:
    (LIB_ROOT / workspace).mkdir(parents=True, exist_ok=True)
