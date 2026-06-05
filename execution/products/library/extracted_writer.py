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
                                    workspace_repo: str = "") -> ExtractedArtifact:
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
    return artifact


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
