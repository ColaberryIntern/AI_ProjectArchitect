"""[Workflow 3b] Install a library asset into the user's workspace repo
via PR. Phase 2 of Workflow 3b.

Public API:
    open_install_pr(user, category, asset_id, *, mcp_scope, subscribe,
                              triggered_by, dry_run) -> InstallResult

Compared to github_pr_sync.open_sync_pr (the canonical-repo PR-creator
that powers Infra 2):
  - target_repo is workspaces.workspace_repo_for_user(user.email),
    NOT the canonical AI_ProjectArchitect repo
  - branch naming: install/{category}/{slug}-{timestamp}
  - the PR body lists asset name, source company, dependencies bundled
    or skipped, subscribe-to-updates state
  - file content rendered per category (skill / agent / prompt / use
    case / capability / external mcp) at the type-appropriate path
  - dependency walker: top-level forward edges follow the new
    list[dict] shape; unresolved "?"-category entries are skipped and
    surfaced as a TODO in the PR body
  - audit row appended to output/library/_install_audit/{date}.jsonl
  - subscribe-to-updates: if subscribe=True we record an ItemSubscription
    keyed on (user_id, item_kind, item_id) so the future upgrade notifier
    knows to open a follow-up PR when the source asset is approved-bumped

Live-in-MCP assets (per gap 3 decision): the Install button is suppressed
at the UI layer. This module trusts that and refuses to write a token-
bearing .mcp.json entry; if called for a live-in-MCP asset it returns
status="refused" + reason.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import store, subscriptions, tenancy, use_cases, workspaces

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
AUDIT_DIR = ROOT / "output" / "library" / "_install_audit"

# Six item kinds we install. The category key from the catalog (plural)
# is normalized to the singular kind here for filesystem-friendly paths.
_KIND_FROM_CATEGORY = {
    "skills": "skill",
    "agents": "agent",
    "prompts": "prompt",
    "mcp": "mcp",
    "use_case": "use_case",
    "use_cases": "use_case",
    "capabilities": "capability",
}


@dataclass
class FileChange:
    """One file to add or modify inside the install PR."""
    path: str
    content: str
    operation: str = "upsert"   # "upsert" only for now (no installs delete)
    note: str = ""              # human-readable origin (parent vs dep)


@dataclass
class InstallResult:
    install_id: str
    user_email: str
    category: str
    asset_id: str
    kind: str
    target_repo: str
    branch: str
    pr_number: int | None
    pr_url: str
    files_written: list[str] = field(default_factory=list)
    deps_bundled: list[dict] = field(default_factory=list)   # the {category, asset_id} edges that landed
    deps_skipped: list[dict] = field(default_factory=list)   # unresolved "?"-category entries
    subscribed: bool = False
    status: str = ""            # opened | refused | failed
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


# ── Utilities ───────────────────────────────────────────────────────


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (s or "").strip()).strip("-").lower()
    return s or "asset"


def _branch_name(kind: str, asset_id: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"install/{_slug(kind)}/{_slug(asset_id)}-{ts}"


def _audit_file() -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"


def _append_audit(r: InstallResult) -> None:
    with _audit_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(r)) + "\n")


def _kind_for(category: str) -> str:
    return _KIND_FROM_CATEGORY.get(category, "asset")


# ── Per-kind path + content rendering ──────────────────────────────


def _front_matter(meta: store.AssetMetadata, kind: str) -> str:
    """A small YAML-ish frontmatter so the rendered file carries traceability
    back to the library entry. Not a full YAML lib dependency, just enough
    for humans + the upgrade notifier to read."""
    return (
        "---\n"
        f"installed_from: colaberry-library\n"
        f"item_kind: {kind}\n"
        f"item_id: {meta.asset_id}\n"
        f"source_company_id: {meta.owning_company_id or 'community'}\n"
        f"installed_at: {_now()}\n"
        "---\n\n"
    )


def _md_body(meta: store.AssetMetadata) -> str:
    """The shared markdown body used for skill / agent / prompt / use case
    installs. Heavy on the asset's own fields so the local file matches what
    the user saw in the library detail view at install time."""
    lines: list[str] = []
    if meta.name:
        lines.append(f"# {meta.name}")
        lines.append("")
    if meta.description:
        lines.append(meta.description)
        lines.append("")
    if meta.how_to_use:
        lines.append("## How to use")
        lines.append(meta.how_to_use)
        lines.append("")
    if meta.example:
        lines.append("## Example")
        lines.append(meta.example)
        lines.append("")
    if meta.readme_markdown:
        lines.append("## Details")
        lines.append(meta.readme_markdown)
        lines.append("")
    if meta.install_steps:
        lines.append("## Install steps")
        for s in meta.install_steps:
            lines.append(f"- {s}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _render_skill(meta: store.AssetMetadata) -> list[FileChange]:
    slug = _slug(meta.asset_id)
    body = _front_matter(meta, "skill") + _md_body(meta)
    return [FileChange(path=f".claude/skills/{slug}/SKILL.md", content=body)]


def _render_agent(meta: store.AssetMetadata) -> list[FileChange]:
    slug = _slug(meta.asset_id)
    body = _front_matter(meta, "agent")
    body += f"# {meta.name or meta.asset_id}\n\n"
    if meta.role:
        body += f"**Role:** {meta.role}\n\n"
    if meta.autonomy_level:
        body += f"**Autonomy:** {meta.autonomy_level}\n\n"
    if meta.system_prompt:
        body += "## System prompt\n\n"
        body += meta.system_prompt + "\n\n"
    if meta.allowed_tools:
        body += "## Allowed tools\n\n"
        for t in meta.allowed_tools:
            body += f"- {t}\n"
        body += "\n"
    if meta.guardrails:
        body += "## Guardrails\n\n" + meta.guardrails + "\n\n"
    if meta.description:
        body += "## Description\n\n" + meta.description + "\n"
    return [FileChange(path=f".claude/agents/{slug}.md", content=body)]


def _render_prompt(meta: store.AssetMetadata) -> list[FileChange]:
    slug = _slug(meta.asset_id)
    body = _front_matter(meta, "prompt")
    body += f"# {meta.name or meta.asset_id}\n\n"
    if meta.description:
        body += meta.description + "\n\n"
    body += "## Prompt\n\n"
    body += "```\n" + (meta.prompt_body or "") + "\n```\n\n"
    if meta.expected_output:
        body += "## Expected output\n\n" + meta.expected_output + "\n\n"
    if meta.model_hint:
        body += f"_Model hint: {meta.model_hint}_\n"
    return [FileChange(path=f".claude/prompts/{slug}.md", content=body)]


def _render_use_case(uc) -> list[FileChange]:
    """Use cases come from use_cases.UseCase not store.AssetMetadata. The
    install puts a reference doc into docs/use-cases/{slug}.md so the
    user has the walkthrough in their workspace as a guide. Not executable
    on its own; the assets it references install separately if subscribed."""
    slug = _slug(uc.use_case_id)
    body = (
        "---\n"
        "installed_from: colaberry-library\n"
        "item_kind: use_case\n"
        f"item_id: {uc.use_case_id}\n"
        f"source_company_id: {uc.owning_company_id or 'community'}\n"
        f"installed_at: {_now()}\n"
        "---\n\n"
        f"# {uc.title}\n\n"
        f"{uc.summary or ''}\n\n"
    )
    if uc.persona:
        body += f"**Persona:** {uc.persona}\n\n"
    if uc.problem:
        body += "## Problem\n\n" + uc.problem + "\n\n"
    if uc.solution:
        body += "## Solution\n\n" + uc.solution + "\n\n"
    if uc.walkthrough:
        body += "## Walkthrough\n\n"
        for i, step in enumerate(uc.walkthrough, 1):
            body += f"{i}. {step}\n"
        body += "\n"
    if uc.tools_used:
        body += "## Tools referenced\n\n"
        for t in uc.tools_used:
            cat = t.get("category", "")
            aid = t.get("asset_id", "")
            role = t.get("role", "")
            body += f"- `{cat}` / `{aid}`" + (f": {role}" if role else "") + "\n"
        body += "\n"
    if uc.outcome_metric:
        body += f"**Expected outcome:** {uc.outcome_metric}\n"
    return [FileChange(path=f"docs/use-cases/{slug}.md", content=body)]


def _render_mcp_external(meta: store.AssetMetadata, repo: str) -> list[FileChange]:
    """For an EXTERNAL mcp server (not live-in-Colaberry-MCP) we add an
    entry to the repo's .mcp.json. We fetch the current file, merge in
    the new server entry, and write back. If the file is absent, create.

    Live-in-Colaberry-MCP assets must be rejected before reaching this
    function (see open_install_pr's guard) -- writing a bearer-bound
    advisor.colaberry.ai entry to a committed file is a security
    disaster.
    """
    slug = _slug(meta.asset_id)
    # Build the new server entry from category-specific fields. We do not
    # write the entire config_template raw -- we wrap it with the canonical
    # Claude Code mcpServers shape.
    new_entry: dict = {}
    if meta.config_template:
        # Try to parse as JSON; if it parses to a dict that already looks
        # like an mcpServers value, use it directly. Else, treat it as a
        # command string.
        try:
            parsed = json.loads(meta.config_template)
            if isinstance(parsed, dict):
                new_entry = parsed
        except Exception:
            new_entry = {"command": meta.config_template.strip()}
    if not new_entry and meta.install_command:
        new_entry = {"command": meta.install_command.strip()}
    if meta.env_vars:
        # env_vars is a multi-line string of "KEY=value" lines; convert to a
        # placeholder dict so the user can paste real values
        env_dict: dict = {}
        for line in (meta.env_vars or "").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env_dict[k.strip()] = (v.strip() or "REPLACE_ME")
        if env_dict:
            new_entry["env"] = env_dict
    if not new_entry:
        new_entry = {"command": "# REPLACE_ME -- no config_template or install_command on the library entry"}

    # Fetch current .mcp.json if any.
    existing: dict = {"mcpServers": {}}
    try:
        existing_raw = workspaces._gh_api("GET", f"/repos/{repo}/contents/.mcp.json")
        if isinstance(existing_raw, dict) and existing_raw.get("content"):
            import base64
            decoded = base64.b64decode(existing_raw["content"]).decode("utf-8", errors="replace")
            parsed_existing = json.loads(decoded)
            if isinstance(parsed_existing, dict):
                existing = parsed_existing
                existing.setdefault("mcpServers", {})
    except Exception:
        # .mcp.json doesn't exist or is malformed; start fresh
        pass

    existing["mcpServers"][slug] = new_entry
    body = json.dumps(existing, indent=2) + "\n"
    return [FileChange(path=".mcp.json", content=body,
                                  note="merged-into-existing-mcp-json")]


def _render_capability(meta: store.AssetMetadata) -> list[FileChange]:
    """A capability bundles a description plus a manifest of sub-items the
    install walker should pick up. The capability itself lands as a doc
    file in docs/capabilities/{slug}.md; the actual sub-items install via
    the dependency walker (each entry in meta.dependencies)."""
    slug = _slug(meta.asset_id)
    body = _front_matter(meta, "capability") + _md_body(meta)
    return [FileChange(path=f"docs/capabilities/{slug}.md", content=body)]


def _render_for_kind(category: str, asset_id: str,
                                          workspace: str = "global",
                                          repo: str = ""
                                          ) -> tuple[list[FileChange], object]:
    """Resolve the asset, dispatch to the per-kind renderer. Returns
    (changes, asset_record) where asset_record is the AssetMetadata or
    UseCase used so the caller can pass it on (e.g. for dep walking)."""
    if category in ("use_case", "use_cases"):
        uc = use_cases.get(workspace, asset_id)
        if uc is None:
            raise ValueError(f"use case {asset_id!r} not found in workspace {workspace!r}")
        return _render_use_case(uc), uc

    meta = store.get_metadata(workspace, category, asset_id)

    if category == "skills":
        return _render_skill(meta), meta
    if category == "agents":
        return _render_agent(meta), meta
    if category == "prompts":
        return _render_prompt(meta), meta
    if category == "mcp":
        return _render_mcp_external(meta, repo), meta
    if category == "capabilities":
        return _render_capability(meta), meta

    # Unknown category: fall back to a generic markdown file so the
    # install doesn't drop the asset entirely.
    fallback = (
        "---\n"
        "installed_from: colaberry-library\n"
        f"item_kind: {category}\n"
        f"item_id: {meta.asset_id}\n"
        f"source_company_id: {meta.owning_company_id or 'community'}\n"
        f"installed_at: {_now()}\n"
        "---\n\n"
        f"# {meta.name or meta.asset_id}\n\n{meta.description or ''}\n"
    )
    return ([FileChange(path=f"docs/{category}/{_slug(meta.asset_id)}.md",
                                    content=fallback)], meta)


# ── Dependency walker ──────────────────────────────────────────────


def _walk_dependencies(meta_or_uc, workspace: str, repo: str,
                                       seen: set
                                       ) -> tuple[list[FileChange], list[dict], list[dict]]:
    """Walk the dependencies list[dict] one level deep, rendering each
    resolved edge. Returns (changes, bundled_edges, skipped_edges).

    Skipped: entries with category=="?" (legacy opaque strings) or whose
    asset_id cannot be resolved. Surfaced in the PR body as TODOs.

    One level only for Phase 2: deeper graphs cycle-detect quickly but
    are out of scope; revisit if real usage produces multi-level deps."""
    deps = []
    if hasattr(meta_or_uc, "dependencies"):
        deps = meta_or_uc.dependencies or []
    elif hasattr(meta_or_uc, "tools_used"):
        # use case -- tools_used is the structured edge list
        deps = [{"category": t.get("category", "?"),
                       "asset_id": t.get("asset_id", ""),
                       "optional": False}
                      for t in (meta_or_uc.tools_used or [])]

    changes: list[FileChange] = []
    bundled: list[dict] = []
    skipped: list[dict] = []

    for d in deps:
        cat = (d.get("category") or "?").strip()
        aid = (d.get("asset_id") or "").strip()
        if not aid:
            continue
        if cat == "?":
            skipped.append({"category": "?", "asset_id": aid, "reason": "unresolved category"})
            continue
        key = f"{cat}:{aid}"
        if key in seen:
            continue
        seen.add(key)
        try:
            dep_changes, _ = _render_for_kind(cat, aid, workspace=workspace, repo=repo)
        except Exception as e:
            skipped.append({"category": cat, "asset_id": aid,
                                  "reason": f"render failed: {type(e).__name__}: {e}"})
            continue
        for c in dep_changes:
            c.note = f"bundled-dep:{cat}/{aid}"
        changes.extend(dep_changes)
        bundled.append({"category": cat, "asset_id": aid})

    return changes, bundled, skipped


# ── PR creation ────────────────────────────────────────────────────
#
# Uses workspaces._gh_api which prefers gh CLI when available and falls
# back to direct urllib + GITHUB_ADMIN_TOKEN otherwise. The prod
# container does not have gh installed, so the urllib path is what
# actually runs. Keeping the call shape identical (same payload dicts)
# means if we install gh later, no code change needed.


def _open_pr(repo: str, branch: str, changes: list[FileChange],
                       pr_title: str, pr_body: str) -> tuple[str, int | None]:
    """Create branch, PUT each file, open PR. Returns (pr_url, pr_number).
    Raises on any failure."""
    import base64

    # 1. Latest main SHA from the user's workspace_repo
    ref = workspaces._gh_api("GET", f"/repos/{repo}/git/ref/heads/main")
    base_sha = ((ref or {}).get("object") or {}).get("sha", "")
    if not base_sha:
        raise RuntimeError(f"could not resolve main SHA on {repo}")

    # 2. Branch from main
    workspaces._gh_api("POST", f"/repos/{repo}/git/refs", payload={
        "ref": f"refs/heads/{branch}",
        "sha": base_sha,
    })

    # 3. PUT each file on the branch.
    for ch in changes:
        b64 = base64.b64encode(ch.content.encode("utf-8")).decode("ascii")
        # Look up existing sha on the branch if the file already exists.
        # Required for PUT-as-update; harmless for PUT-as-create.
        existing_sha = ""
        try:
            existing = workspaces._gh_api(
                "GET", f"/repos/{repo}/contents/{ch.path}?ref={branch}",
            )
            if isinstance(existing, dict):
                existing_sha = str(existing.get("sha") or "")
        except Exception:
            existing_sha = ""
        payload = {
            "branch": branch,
            "message": f"Install: {ch.path}",
            "content": b64,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        workspaces._gh_api(
            "PUT", f"/repos/{repo}/contents/{ch.path}", payload=payload,
        )

    # 4. Open PR
    pr = workspaces._gh_api("POST", f"/repos/{repo}/pulls", payload={
        "title": pr_title,
        "body": pr_body,
        "head": branch,
        "base": "main",
    })
    pr_url = str((pr or {}).get("html_url") or "")
    pr_number = pr.get("number") if isinstance(pr, dict) else None
    return (pr_url, pr_number)


# ── Public entrypoint ──────────────────────────────────────────────


def open_install_pr(user: tenancy.User, category: str, asset_id: str,
                                  *,
                                  workspace: str = "global",
                                  subscribe: bool = False,
                                  triggered_by: str = "manual",
                                  dry_run: bool = False) -> InstallResult:
    """Open a PR in the user's workspace_repo installing this asset and
    its direct dependencies.

    Live-in-Colaberry-MCP assets are refused with status="refused" per
    gap-3 decision: the Install button is suppressed in the UI for those,
    and this function refuses defensively in case it is called anyway.
    """
    install_id = f"inst-{uuid.uuid4().hex[:10]}"
    started = _now()
    kind = _kind_for(category)
    target_repo = workspaces.workspace_repo_for_user(user.email) if user else ""
    branch = _branch_name(kind, asset_id)

    result = InstallResult(
        install_id=install_id, user_email=getattr(user, "email", "") or "",
        category=category, asset_id=asset_id, kind=kind,
        target_repo=target_repo, branch=branch, pr_number=None, pr_url="",
        started_at=started,
    )

    # Defensive guard: refuse to write a token-bearing .mcp.json entry
    # for live-in-Colaberry-MCP assets. The UI suppresses the button, but
    # the API endpoint must refuse too in case a client bypasses the UI.
    if category == "mcp":
        from app.routers.library import is_live_in_colaberry_mcp
        meta = store.get_metadata(workspace, category, asset_id)
        if is_live_in_colaberry_mcp(
            name=meta.name or asset_id, asset_id=asset_id,
            tags=meta.tags, source=meta.source_url or meta.source,
        ):
            result.status = "refused"
            result.error = (
                "Asset is live in your Colaberry MCP server. Install "
                "is disabled to avoid committing a bearer token to git. "
                "Use the per-user bearer at /profile/mcp-setup for "
                "external clients."
            )
            result.finished_at = _now()
            _append_audit(result)
            return result

    if not target_repo:
        result.status = "failed"
        result.error = "user has no workspace_repo configured"
        result.finished_at = _now()
        _append_audit(result)
        return result

    # Render the parent file(s) + walk one level of dependencies.
    try:
        parent_changes, asset_record = _render_for_kind(
            category, asset_id, workspace=workspace, repo=target_repo,
        )
    except Exception as e:
        result.status = "failed"
        result.error = f"render failed: {type(e).__name__}: {e}"
        result.finished_at = _now()
        _append_audit(result)
        return result

    seen: set = {f"{category}:{asset_id}"}
    dep_changes, bundled, skipped = _walk_dependencies(
        asset_record, workspace=workspace, repo=target_repo, seen=seen,
    )
    result.deps_bundled = bundled
    result.deps_skipped = skipped

    all_changes = parent_changes + dep_changes
    result.files_written = [c.path for c in all_changes]

    if dry_run:
        result.status = "dry_run"
        result.pr_url = "(dry_run)"
        result.finished_at = _now()
        _append_audit(result)
        return result

    asset_name = ""
    if hasattr(asset_record, "name"):
        asset_name = asset_record.name or asset_id
    elif hasattr(asset_record, "title"):
        asset_name = asset_record.title or asset_id
    source_company_id = getattr(asset_record, "owning_company_id", "community")

    pr_title = f"Install [{kind}] {asset_name} from {source_company_id} library"
    pr_body_lines: list[str] = [
        f"Installing **{asset_name}** (`{category}` / `{asset_id}`) from the {source_company_id} library.",
        "",
        f"- Triggered by: `{triggered_by}`",
        f"- Item kind: `{kind}`",
        f"- Source company: `{source_company_id}`",
        f"- Subscribe to updates: `{subscribe}`",
        "",
        "## Files written",
    ]
    for c in all_changes:
        suffix = f" _(via {c.note})_" if c.note else ""
        pr_body_lines.append(f"- `{c.path}`{suffix}")

    if bundled:
        pr_body_lines.append("")
        pr_body_lines.append("## Dependencies bundled into this PR")
        for d in bundled:
            pr_body_lines.append(f"- `{d['category']}` / `{d['asset_id']}`")

    if skipped:
        pr_body_lines.append("")
        pr_body_lines.append("## Dependencies skipped (TODO: needs curation)")
        for s in skipped:
            pr_body_lines.append(
                f"- `{s['category']}` / `{s['asset_id']}` -- "
                f"{s.get('reason', 'unknown')}"
            )

    pr_body_lines.append("")
    pr_body_lines.append("---")
    pr_body_lines.append(
        f"_Opened by colaberry-library install on behalf of "
        f"{result.user_email}. install_id: `{install_id}`._"
    )
    pr_body = "\n".join(pr_body_lines)

    try:
        pr_url, pr_number = _open_pr(target_repo, branch, all_changes,
                                                            pr_title, pr_body)
        result.pr_url = pr_url
        result.pr_number = pr_number
        result.status = "opened"
    except Exception as e:
        result.status = "failed"
        result.error = str(e)[:300]
        result.finished_at = _now()
        _append_audit(result)
        return result

    # Record the subscription if requested. We do this AFTER the PR
    # succeeds so a failed install doesn't leave an orphan subscription.
    if subscribe and getattr(user, "user_id", ""):
        try:
            subscriptions.subscribe(
                user_id=user.user_id,
                item_kind=kind,
                item_id=asset_id,
                target_repo=target_repo,
            )
            result.subscribed = True
        except Exception as e:
            # Subscription failure shouldn't roll back the install. Log
            # and continue.
            result.error = (result.error + f" subscribe_failed:{e}").strip()

    result.finished_at = _now()
    _append_audit(result)
    return result
