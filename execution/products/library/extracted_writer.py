"""Render + write Phase 4 extracted artifacts.

Companion to skill_extractor.py. This module is intentionally narrow:

  1. Load a Jinja2 template by `output_type` from app/templates/extracted/
  2. Render an ExtractedSource through it
  3. Write the rendered content to local disk under output/library/extracted/
  4. Optionally PUT the content to a GitHub branch (skill-extracted/<slug>)
     in the library repo, returning {branch, file_path, raw_url}

Both onboarding (Phase 4 admin.user_new hook) and My Day Extract (Phase 6
surface) call this through skill_extractor.extract(); the rendering is
guaranteed identical because both go through the same render() call.

Storage record persistence to a per-extract index file lives here so the
two call paths don't need to reimplement it.

Stdlib + Jinja2 only. Jinja2 is already a runtime dependency for the
FastAPI templates.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape, StrictUndefined

# Resolve to the repo root (4 parents up: library/ -> products/ -> execution/ -> root)
ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = ROOT / "app" / "templates" / "extracted"
OUTPUT_DIR = ROOT / "output" / "library" / "extracted"
RECORDS_PATH = OUTPUT_DIR / "_records.json"

# The library repo where extracted artifacts are pushed as branches.
# Defaults to the central project repo; can be overridden per call.
DEFAULT_LIBRARY_REPO = os.environ.get(
    "GITHUB_LIBRARY_REPO", "ColaberryIntern/AI_ProjectArchitect"
)
BRANCH_PREFIX = "skill-extracted/"

# Map extracted output_type -> live library category key. Lets us upsert
# the extracted artifact into store.AssetMetadata so it shows up at
# /library/<category>/<slug> immediately, not just on an unmerged branch.
# Anything not mapped here falls back to "skills" -- a safe default since
# the legacy templates were skill-shaped.
OUTPUT_TYPE_TO_CATEGORY: dict[str, str] = {
    "skill": "skills",
    "agent": "agents",
    "prompt": "prompts",
    "mcp": "mcp",
    "capability": "capabilities",
    "template": "templates",
    "directive": "policies",      # closest existing category
    "policy": "policies",
    "scorecard": "governance",
    "eval": "evals",
    "report": "templates",        # no first-class "report" category yet
    "connector": "connectors",
    "adapter": "adapters",
    "cron": "workflows",          # crons orchestrate steps
    "workflow": "workflows",
}


@dataclass
class ExtractedArtifact:
    """Persistent record of one extract() call.

    Both Phase 4 (onboarding) and Phase 6 (My Day Extract) write this; Phase 6's
    store.py can reuse this dataclass via `from execution.products.library.extracted_writer
    import ExtractedArtifact` rather than redefining it.
    """
    slug: str
    output_type: str
    source_kind: str
    source_bc_id: str
    branch: str = ""
    file_path: str = ""       # path within the library repo (e.g. "library/skills/foo.md")
    raw_url: str = ""         # GitHub raw URL on the branch
    local_path: str = ""      # path on disk under output/library/extracted/
    created_at: str = ""
    created_by: str = ""      # user_id of who triggered the extract
    use_count: int = 0
    workspace_path: str = ""  # path within the user's personal workspace repo (if synced)
    workspace_url: str = ""   # GitHub raw URL of the file inside the workspace repo


# ── Template rendering ────────────────────────────────────────────────


def _env() -> Environment:
    """Build a Jinja env scoped to app/templates/extracted/.

    Uses StrictUndefined so a typo in a template surfaces as a loud error at
    render time instead of silently producing an empty `{{ src.foo }}`.
    """
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(default=False),  # markdown output, not HTML
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def available_output_types() -> list[str]:
    """Return list of available output_types (one per .j2 in the template dir)."""
    if not TEMPLATE_DIR.exists():
        return []
    return sorted(
        p.stem.split(".")[0]
        for p in TEMPLATE_DIR.glob("*.j2")
    )


def render(src, output_type: str, slug: str, created_at: str = "") -> str:
    """Render the source through the template for output_type.

    `src` is an ExtractedSource (defined in skill_extractor.py). Imported lazily
    to avoid a circular import (skill_extractor imports this module too).
    """
    template_path = TEMPLATE_DIR / f"{output_type}.md.j2"
    if not template_path.exists():
        raise FileNotFoundError(
            f"No template for output_type={output_type!r} at {template_path}. "
            f"Available: {available_output_types()}"
        )
    env = _env()
    tmpl = env.get_template(f"{output_type}.md.j2")
    return tmpl.render(
        src=src,
        slug=slug,
        created_at=created_at or _now_iso(),
    )


# ── Local disk write ──────────────────────────────────────────────────


def write_to_disk(content: str, output_type: str, slug: str) -> Path:
    """Write rendered content to output/library/extracted/{output_type}/{slug}.md.

    Idempotent: overwrites the existing file. Returns the absolute path.
    """
    out_dir = OUTPUT_DIR / output_type
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


# ── GitHub branch + file commit ───────────────────────────────────────


def _gh_token() -> str:
    """Pull the admin token; raise if missing so the caller can degrade gracefully."""
    tok = os.environ.get("GITHUB_ADMIN_TOKEN") or os.environ.get("GH_TOKEN", "")
    if not tok:
        raise RuntimeError("GITHUB_ADMIN_TOKEN not set; cannot push extracted artifact")
    return tok


def _gh_request(method: str, path: str, payload: Optional[dict] = None) -> dict:
    """Thin GitHub API caller. Raises on non-2xx; returns parsed JSON or {}."""
    url = f"https://api.github.com{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, method=method, data=body,
        headers={
            "Authorization": f"Bearer {_gh_token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Colaberry extracted_writer",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"GH {method} {path} -> HTTP {e.code} {e.reason}: {msg}") from e


def _ensure_branch(repo: str, branch: str) -> str:
    """Ensure `branch` exists in `repo`. Creates it off main if not. Returns the head sha."""
    try:
        existing = _gh_request("GET", f"/repos/{repo}/git/refs/heads/{branch}")
        return existing["object"]["sha"]
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise
    # Doesn't exist; create off the default branch.
    repo_info = _gh_request("GET", f"/repos/{repo}")
    default_branch = repo_info.get("default_branch", "main")
    main_ref = _gh_request("GET", f"/repos/{repo}/git/refs/heads/{default_branch}")
    base_sha = main_ref["object"]["sha"]
    _gh_request("POST", f"/repos/{repo}/git/refs", payload={
        "ref": f"refs/heads/{branch}",
        "sha": base_sha,
    })
    return base_sha


def _put_file_on_branch(repo: str, branch: str, path: str,
                                          content: str, message: str) -> dict:
    """PUT a file to `branch` in `repo`. Idempotent via sha when file exists."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    sha = None
    try:
        existing = _gh_request("GET", f"/repos/{repo}/contents/{path}?ref={branch}")
        if isinstance(existing, dict):
            sha = existing.get("sha")
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise
    payload: dict = {"message": message, "content": encoded, "branch": branch}
    if sha:
        payload["sha"] = sha
    return _gh_request("PUT", f"/repos/{repo}/contents/{path}", payload=payload)


def write_and_commit(src, output_type: str, slug: str,
                                    *,
                                    repo: str = "",
                                    library_path_prefix: str = "library",
                                    created_by: str = "",
                                    commit_message: str = "",
                                    workspace_repo: str = "",
                                    owning_company_id: str = "community") -> ExtractedArtifact:
    """Render `src` -> write to disk -> push to a skill-extracted/<slug> branch.

    Returns an ExtractedArtifact with the branch + file_path + raw_url for the
    caller to surface (Phase 6 returns this directly in the route response;
    Phase 4 admin hook persists it as part of the audit log).

    Per Phase 6 anti-scope: never auto-merges the branch. Ali (or any tenant
    admin) reviews + merges the branch into main manually.

    If `workspace_repo` is supplied (e.g. "ColaberryIntern/ali-workspace"),
    ALSO write the rendered file to that repo's main branch at
    `.claude/extracted/<output_type>/<slug>.md` so the user's local Claude
    Code session sees the extracted artifact the next time they pull. Best-
    effort: a workspace-push failure is recorded in the audit but doesn't
    fail the library commit (the library is the source of truth).
    """
    now = _now_iso()
    content = render(src, output_type, slug, created_at=now)
    local_path = write_to_disk(content, output_type, slug)

    repo = repo or DEFAULT_LIBRARY_REPO
    branch = f"{BRANCH_PREFIX}{slug}"
    repo_file_path = f"{library_path_prefix}/extracted/{output_type}/{slug}.md"

    _ensure_branch(repo, branch)
    msg = commit_message or (
        f"Extract {output_type} {slug!r} from {src.source_kind} {src.source_id}"
    )
    _put_file_on_branch(repo, branch, repo_file_path, content, msg)
    raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{repo_file_path}"

    # Optional: push the same content into the user's personal workspace
    # at .claude/extracted/<type>/<slug>.md so their local claude session
    # picks it up. Lands on the default branch (main) since the user's
    # workspace is private + per-user; no review queue applies there.
    workspace_path = ""
    workspace_url = ""
    if workspace_repo:
        ws_file_path = f".claude/extracted/{output_type}/{slug}.md"
        ws_msg = f"Add extracted {output_type} {slug!r} (from {src.source_kind} {src.source_id})"
        try:
            ws_info = _gh_request("GET", f"/repos/{workspace_repo}")
            ws_default = ws_info.get("default_branch", "main")
            _put_file_on_branch(workspace_repo, ws_default, ws_file_path, content, ws_msg)
            workspace_path = ws_file_path
            workspace_url = (
                f"https://raw.githubusercontent.com/{workspace_repo}/{ws_default}/{ws_file_path}"
            )
        except Exception:
            # Don't fail the library commit if the workspace push fails;
            # caller can re-run later via the same extract route (idempotent).
            workspace_path = ""
            workspace_url = ""

    artifact = ExtractedArtifact(
        slug=slug,
        output_type=output_type,
        source_kind=src.source_kind,
        source_bc_id=str(src.source_id),
        branch=branch,
        file_path=repo_file_path,
        raw_url=raw_url,
        local_path=str(local_path),
        created_at=now,
        created_by=created_by,
        workspace_path=workspace_path,
        workspace_url=workspace_url,
    )
    _record_artifact(artifact)
    _register_as_library_asset(artifact, src, content, output_type,
                                                 owning_company_id=owning_company_id,
                                                 created_by=created_by)
    return artifact


_AUTO_SENTINEL = "[AUTO-EXTRACTED: review and fill in]"


def _extract_section(body: str, *headings) -> str:
    """Return the body of a '## <heading>' section from a markdown
    string, stopping at the next ## or end. Case-insensitive across
    every heading variant supplied. Returns '' on no match.
    """
    import re
    for h in headings:
        pat = re.compile(
            rf"^##\s+{re.escape(h)}\s*$([\s\S]*?)(?=^##\s|\Z)",
            re.MULTILINE | re.IGNORECASE,
        )
        m = pat.search(body or "")
        if m:
            text = m.group(1).strip()
            if text:
                return text
    return ""


def _derive_category_required_fields(category: str, src, body: str) -> dict:
    """For each category whose schema requires a domain-specific field
    not in the AssetMetadata base (e.g. system_prompt for agents,
    prompt_body for prompts, install_command for mcp), derive the field
    from the rendered body. Falls back to a sentinel string so the
    schema validator is satisfied and Ali can filter for unfilled
    auto-extracted assets via the 'auto-extracted' tag.

    Returns a kwargs dict to merge into AssetMetadata(...).
    """
    out: dict = {}
    src_body = (getattr(src, "body", "") or "").strip()

    if category == "agents":
        out["role"] = (
            _extract_section(body, "Role", "Persona", "Agent role")
            or _AUTO_SENTINEL
        )
        out["system_prompt"] = (
            _extract_section(body, "System prompt", "System Prompt",
                                              "Instructions", "Behavior")
            or src_body  # the source body is at least a starting prompt
            or _AUTO_SENTINEL
        )
    elif category == "prompts":
        out["prompt_body"] = (
            _extract_section(body, "Prompt body", "Prompt", "Template")
            or src_body
            or _AUTO_SENTINEL
        )
    elif category == "mcp":
        out["install_command"] = (
            _extract_section(body, "Install command", "Install", "Setup")
            or _AUTO_SENTINEL
        )
    elif category == "templates":
        out["blueprint_path"] = (
            _extract_section(body, "Blueprint path", "Blueprint",
                                              "Scaffolding path", "Path")
            or _AUTO_SENTINEL
        )
    elif category == "policies":
        out["rule_text"] = (
            _extract_section(body, "Rule text", "Rule", "Policy",
                                              "What it enforces")
            or src_body
            or _AUTO_SENTINEL
        )
    elif category == "governance":
        out["rule_text"] = (
            _extract_section(body, "Rule text", "Rule", "Scorecard rule",
                                              "What it scores", "Controls")
            or src_body
            or _AUTO_SENTINEL
        )
    elif category == "evals":
        out["dataset_url"] = (
            _extract_section(body, "Dataset URL", "Dataset", "Data")
            or _AUTO_SENTINEL
        )
    elif category == "workflows":
        out["steps"] = (
            _extract_section(body, "Steps", "Ordered steps",
                                              "Sub-steps", "Procedure")
            or src_body
            or _AUTO_SENTINEL
        )
        out["invocation_pattern"] = (
            _extract_section(body, "Invocation pattern", "How it's invoked",
                                              "Trigger", "When it runs")
            or _AUTO_SENTINEL
        )
    elif category == "recovery":
        out["trigger_condition"] = (
            _extract_section(body, "Trigger condition", "Trigger",
                                              "When this fires")
            or _AUTO_SENTINEL
        )
        out["mitigation_action"] = (
            _extract_section(body, "Mitigation action", "Mitigation",
                                              "What to do", "Response")
            or src_body
            or _AUTO_SENTINEL
        )
    elif category == "chaos":
        out["fault_scenario"] = (
            _extract_section(body, "Fault scenario", "Scenario",
                                              "What this simulates")
            or src_body
            or _AUTO_SENTINEL
        )
    elif category == "projections":
        out["event_source"] = (
            _extract_section(body, "Event source", "Source", "Events")
            or _AUTO_SENTINEL
        )

    return out


def _register_as_library_asset(artifact: "ExtractedArtifact",
                                                          src, content: str, output_type: str,
                                                          *,
                                                          owning_company_id: str,
                                                          created_by: str) -> None:
    """Promote a fresh extract into a live /library/<category>/<slug> asset.

    Without this, the extracted artifact lives on an unmerged GitHub branch
    (skill-extracted/<slug>) and never appears in /library/ -- the user sees
    a "Previous extracts" entry but can't browse or invoke it via MCP.

    Best-effort: failures here are logged-and-swallowed so the extract HTTP
    response still succeeds. The artifact remains on disk + GitHub branch
    + records.json regardless; the worst case is the user has to manually
    re-trigger after fixing whatever broke.

    Auto-approve gating: when LIBRARY_AUTO_APPROVE_ON_SUBMIT=1, also write
    a tenancy.ItemApproval row so the owning company's My Day view sees
    the asset immediately without a separate review step.
    """
    try:
        from . import store
    except Exception:
        return
    category = OUTPUT_TYPE_TO_CATEGORY.get(output_type, "skills")
    body_text = content or ""
    description = (src.body or "").strip()[:280] if hasattr(src, "body") else ""
    name = (getattr(src, "title", "") or artifact.slug).strip()
    # Derive the per-category schema fields from the source so the
    # AssetMetadata isn't half-empty when /library/<cat>/<id> renders.
    # The BC body usually describes the work; we surface that as
    # how_to_use. what_its_for gets the first sentence of the body
    # (or the title as fallback). example references the source for
    # provenance. Auto-extracted assets are tagged so Ali can filter
    # /library/<cat>?tag=auto-extracted to find ones that need polish.
    src_body = (getattr(src, "body", "") or "").strip()
    first_sentence = ""
    if src_body:
        # Cheap "first sentence": split on '. ', take the first 240 chars.
        for sep in (". ", ".\n", "\n\n"):
            if sep in src_body[:400]:
                first_sentence = src_body.split(sep, 1)[0].strip() + "."
                break
        if not first_sentence:
            first_sentence = src_body[:240].strip()
    what_its_for = first_sentence or (name or artifact.slug)
    how_to_use = src_body or ""
    example = (
        f"See the source ticket for the original context: "
        f"{artifact.raw_url or artifact.source_kind + ':' + artifact.source_bc_id}"
    )
    tags = ["auto-extracted", artifact.source_kind or "extracted",
                  f"output:{output_type}"]
    # Derive category-specific required fields (system_prompt for
    # agents, prompt_body for prompts, install_command for mcp, etc.).
    # Returns kwargs; empty for categories whose required fields are
    # already in the common base (name, description, how_to_use, example).
    category_kwargs = _derive_category_required_fields(category, src, body_text)
    # Anything we filled with the sentinel gets an extra tag so Ali can
    # filter /library/<cat>?tag=needs-polish to find unfinished ones.
    if any(v == _AUTO_SENTINEL for v in category_kwargs.values()):
        tags = list(tags) + ["needs-polish"]
    # Only set kwargs that AssetMetadata actually accepts (defensive against
    # category fields not yet present in the dataclass).
    valid_field_names = {f.name for f in store.AssetMetadata.__dataclass_fields__.values()}
    category_kwargs = {k: v for k, v in category_kwargs.items()
                                    if k in valid_field_names}
    try:
        meta = store.AssetMetadata(
            asset_id=artifact.slug,
            category=category,
            workspace="global",
            name=name,
            description=description or f"Extracted {output_type} from {artifact.source_kind} {artifact.source_bc_id}",
            how_to_use=how_to_use,
            example=example,
            what_its_for=what_its_for,
            readme_markdown=body_text,
            source=artifact.raw_url or "extracted",
            source_url=artifact.raw_url or "",
            submitted_by=created_by or "extract-flow",
            submitted_at=artifact.created_at,
            owner=created_by or "extract-flow",
            tags=tags,
            owning_company_id=owning_company_id or "community",
            enrichment_state="enriched",
            enriched_at=artifact.created_at,
            enriched_by="extract-flow",
            **category_kwargs,
        )
        # Auto-approve when the rollout flag is set: mark vetted so the
        # asset detail page shows the green badge + visibility opens.
        auto_approve = (os.environ.get("LIBRARY_AUTO_APPROVE_ON_SUBMIT", "") or "").strip() in ("1", "true", "yes", "on")
        if auto_approve:
            meta.vetted = True
            meta.vetted_by = created_by or "extract-flow"
            meta.vetted_at = artifact.created_at
            meta.vetted_status = "vetted"
            meta.vetted_notes = "auto-approved per LIBRARY_AUTO_APPROVE_ON_SUBMIT rollout policy"
        store.save_metadata(meta)

        if auto_approve:
            try:
                from . import tenancy
                tenancy.record_approval(
                    item_kind="library_asset",
                    item_id=artifact.slug,
                    category=category,
                    company_id=owning_company_id or "community",
                    approved_by_user_id=created_by or "extract-flow",
                    status="approved",
                    notes="auto-approved per LIBRARY_AUTO_APPROVE_ON_SUBMIT rollout policy (extract flow)",
                )
            except Exception:
                # Tenancy is advisory; failure shouldn't block.
                pass
    except Exception:
        # Don't crash the extract route on metadata-write failures.
        pass


# ── Records index (so we can list extracted artifacts) ────────────────


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _record_artifact(artifact: ExtractedArtifact) -> None:
    """Append/update artifact in the records index.

    The index is keyed by (output_type, slug). Re-extracting the same slug
    updates the existing record rather than appending a duplicate.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    if RECORDS_PATH.exists():
        try:
            records = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                records = []
        except Exception:
            records = []

    key = (artifact.output_type, artifact.slug)
    updated = False
    for i, r in enumerate(records):
        if (r.get("output_type"), r.get("slug")) == key:
            records[i] = asdict(artifact)
            updated = True
            break
    if not updated:
        records.append(asdict(artifact))

    RECORDS_PATH.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def list_records(output_type: Optional[str] = None,
                            created_by: Optional[str] = None) -> list[dict]:
    """Read the records index, optionally filtered."""
    if not RECORDS_PATH.exists():
        return []
    try:
        records = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(records, list):
        return []
    if output_type:
        records = [r for r in records if r.get("output_type") == output_type]
    if created_by:
        records = [r for r in records if r.get("created_by") == created_by]
    return records
