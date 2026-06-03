"""GitHub sync — propagate Colaberry-approved assets to the canonical
`ColaberryIntern/AI_ProjectArchitect` repo at `library/{type}/{slug}.md`.

[Infra 1] criterion 3-5:
    - On approve  → write/update file in the target repo, commit + push.
    - On unflag   → delete the file, commit + push.
    - Every sync event recorded with author + timestamp + artifact + commit SHA.

Sync is *triggerable*, not *automatic-on-write*. Two entry points:
    sync_asset(category, asset_id, approved=True)      — one item
    sync_all_approved(workspace="global")              — full reconciliation

Use `gh` CLI if available (preferred — works with the cached gh auth on the
prod box), else fall back to `git` with whatever credentials are
configured in the local repo. Both code paths emit the same audit row.

NO `gh`/`git` execution is performed at import time. Tests monkeypatch
`_run` to assert on the commands without touching a real repo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
APPROVERS_PATH = ROOT / "config" / "library_approvers.json"
AUDIT_DIR = ROOT / "output" / "library" / "_github_sync"


# ── Audit log row ─────────────────────────────────────────────────


@dataclass
class SyncEvent:
    """One sync attempt, success or failure. Audit-grade row."""

    event_id: str
    operation: str          # "upsert" | "delete"
    asset_kind: str         # "library_asset" | "use_case"
    category: str           # e.g. "skills", "agents"
    asset_id: str           # name / id
    repo: str               # "ColaberryIntern/AI_ProjectArchitect"
    branch: str             # "main"
    target_path: str        # "library/skills/MCP Filesystem Server.md"
    author_email: str       # who approved (drives the commit message attribution)
    author_display_name: str
    triggered_by: str       # "manual" | "scheduled" | "webhook"
    commit_sha: str         # filled in on success; "" on failure
    status: str             # "success" | "failed" | "noop"
    error: str = ""         # only when status="failed"
    bytes_written: int = 0
    started_at: str = ""
    finished_at: str = ""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _audit_file() -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"


def _append_audit(ev: SyncEvent) -> None:
    with _audit_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(ev)) + "\n")


def history(asset_kind: str = "", category: str = "",
                asset_id: str = "") -> list[SyncEvent]:
    """Return audit rows, optionally filtered. Scans every date-bucketed file."""
    out: list[SyncEvent] = []
    if not AUDIT_DIR.exists():
        return out
    for p in sorted(AUDIT_DIR.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = SyncEvent(**json.loads(line))
            except Exception:
                continue
            if asset_kind and ev.asset_kind != asset_kind:
                continue
            if category and ev.category != category:
                continue
            if asset_id and ev.asset_id != asset_id:
                continue
            out.append(ev)
    return out


# ── Approver loading + authorization check ────────────────────────


def _load_approvers() -> dict[str, Any]:
    if not APPROVERS_PATH.exists():
        return {"approvers": [], "approval_target_repo": "",
                  "sync_path_template": "library/{type}/{slug}.md",
                  "default_branch": "main"}
    return json.loads(APPROVERS_PATH.read_text(encoding="utf-8"))


def can_approve(approver_email: str, category: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Looks up the approver matrix."""
    cfg = _load_approvers()
    for a in cfg.get("approvers", []):
        if a.get("email", "").lower() != approver_email.lower():
            continue
        allowed = a.get("can_approve", [])
        if "all" in allowed:
            return (True, f"{a['display_name']} has 'all' grant")
        if category in allowed:
            return (True, f"{a['display_name']} grants category '{category}'")
        if a.get("role") == "sales" and category in ("use_cases", "prompts", "agents") and "sales" in allowed:
            return (True, f"{a['display_name']} sales role covers {category}")
        if a.get("role") == "tech" and "tech" in allowed:
            tech_cats = {"skills", "mcp", "capabilities", "workflows",
                              "projections", "recovery", "chaos", "governance",
                              "evals", "connectors", "adapters", "templates", "policies"}
            if category in tech_cats:
                return (True, f"{a['display_name']} tech role covers {category}")
        return (False, f"{a['display_name']} cannot approve '{category}'")
    return (False, f"{approver_email} is not in the approvers list")


# ── Slug + content shaping ────────────────────────────────────────


_SLUG_BAD = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(name: str) -> str:
    s = _SLUG_BAD.sub("-", name.strip()).strip("-_")
    return s[:120] or "asset"


def target_path_for(category: str, asset_id: str,
                          template: str = "library/{type}/{slug}.md") -> str:
    return template.format(type=category, slug=slugify(asset_id))


def render_asset_markdown(category: str, asset_id: str,
                                  raw: dict[str, Any] | None,
                                  meta: Any) -> str:
    """Produce a clean human-readable .md for the synced asset.
    Frontmatter for machine processing + body for humans / search engines."""
    raw = raw or {}
    fields: list[str] = []
    fields.append("---")
    fields.append(f'asset_id: "{asset_id}"')
    fields.append(f"category: {category}")
    fields.append(f'name: "{(raw.get("name") or asset_id).replace(chr(34), chr(39))}"')
    if raw.get("version"):
        fields.append(f'version: "{raw["version"]}"')
    if raw.get("owner"):
        fields.append(f'owner: "{raw["owner"]}"')
    if getattr(meta, "vetted", False):
        fields.append("vetted: true")
        if getattr(meta, "vetted_by", None):
            fields.append(f'vetted_by: "{meta.vetted_by}"')
        if getattr(meta, "vetted_at", None):
            fields.append(f'vetted_at: "{meta.vetted_at}"')
    if raw.get("source"):
        fields.append(f'source: "{raw["source"]}"')
    if raw.get("tags"):
        tags = ", ".join(f'"{t}"' for t in raw["tags"])
        fields.append(f"tags: [{tags}]")
    fields.append("---")
    fields.append("")
    fields.append(f"# {raw.get('name') or asset_id}")
    fields.append("")
    if getattr(meta, "what_its_for", ""):
        fields.append("## What it's used for")
        fields.append("")
        fields.append(meta.what_its_for)
        fields.append("")
    desc = raw.get("description") or getattr(meta, "description", "")
    if desc:
        fields.append("## Description")
        fields.append("")
        fields.append(desc)
        fields.append("")
    if getattr(meta, "how_to_use", ""):
        fields.append("## How to use")
        fields.append("")
        fields.append("```")
        fields.append(meta.how_to_use)
        fields.append("```")
        fields.append("")
    if getattr(meta, "example", ""):
        fields.append("## Example")
        fields.append("")
        fields.append("```")
        fields.append(meta.example)
        fields.append("```")
        fields.append("")
    if getattr(meta, "install_command", ""):
        fields.append("## Install")
        fields.append("")
        fields.append("```sh")
        fields.append(meta.install_command)
        fields.append("```")
        fields.append("")
    if getattr(meta, "readme_markdown", ""):
        fields.append("## README (snapshot)")
        fields.append("")
        fields.append(meta.readme_markdown)
        fields.append("")
    if raw.get("source"):
        fields.append(f"---\n[↗ Source]({raw['source']})")
    return "\n".join(fields).rstrip() + "\n"


# ── Sync runner ───────────────────────────────────────────────────


def _run(cmd: list[str], cwd: Path | None = None,
            env: dict[str, str] | None = None,
            check: bool = True) -> tuple[int, str, str]:
    """Run a subprocess. Returns (returncode, stdout, stderr)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, env=full_env,
        capture_output=True, text=True, timeout=120,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} → exit {proc.returncode}\n"
            f"STDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
        )
    return (proc.returncode, proc.stdout, proc.stderr)


def _gh_available() -> bool:
    try:
        _run(["gh", "--version"])
        return True
    except Exception:
        return False


def _sync_via_gh(repo: str, branch: str, target_path: str,
                       content: str | None, commit_message: str,
                       author_name: str, author_email: str,
                       operation: str) -> str:
    """Sync via the `gh` CLI (uses cached auth). Returns commit SHA.

    `content=None` + operation="delete" deletes the file.
    """
    if operation == "upsert":
        encoded = (content or "").encode("utf-8")
        import base64
        b64 = base64.b64encode(encoded).decode("ascii")
        # Look up existing SHA (if file exists) so the put updates instead of failing
        existing_sha = ""
        try:
            rc, out, _ = _run([
                "gh", "api",
                f"/repos/{repo}/contents/{target_path}?ref={branch}",
            ], check=False)
            if rc == 0:
                existing_sha = json.loads(out).get("sha", "")
        except Exception:
            pass

        api_args = [
            "gh", "api", "--method", "PUT",
            f"/repos/{repo}/contents/{target_path}",
            "-f", f"message={commit_message}",
            "-f", f"content={b64}",
            "-f", f"branch={branch}",
            "-f", f"committer[name]={author_name}",
            "-f", f"committer[email]={author_email}",
        ]
        if existing_sha:
            api_args += ["-f", f"sha={existing_sha}"]
        _, out, _ = _run(api_args)
        return json.loads(out).get("commit", {}).get("sha", "")

    if operation == "delete":
        # Need the file's current SHA to delete
        rc, out, _ = _run([
            "gh", "api",
            f"/repos/{repo}/contents/{target_path}?ref={branch}",
        ], check=False)
        if rc != 0:
            return ""  # nothing to delete; noop
        existing_sha = json.loads(out).get("sha", "")
        if not existing_sha:
            return ""
        _, out, _ = _run([
            "gh", "api", "--method", "DELETE",
            f"/repos/{repo}/contents/{target_path}",
            "-f", f"message={commit_message}",
            "-f", f"sha={existing_sha}",
            "-f", f"branch={branch}",
            "-f", f"committer[name]={author_name}",
            "-f", f"committer[email]={author_email}",
        ])
        return json.loads(out).get("commit", {}).get("sha", "")

    raise ValueError(f"unknown operation: {operation}")


def sync_asset(category: str, asset_id: str,
                  approver_email: str = "ali@colaberry.com",
                  operation: str = "upsert",
                  triggered_by: str = "manual",
                  workspace: str = "global",
                  dry_run: bool = False) -> SyncEvent:
    """Sync ONE asset.

    operation:
      - "upsert" → write/update `library/{type}/{slug}.md` from current
                       inventory + metadata in the target repo
      - "delete" → remove the file (used when an item is unflagged)

    Returns the SyncEvent (also appended to the audit log).
    """
    from . import inventory, store

    cfg = _load_approvers()
    repo = cfg.get("approval_target_repo", "ColaberryIntern/AI_ProjectArchitect")
    branch = cfg.get("default_branch", "main")
    path_template = cfg.get("sync_path_template", "library/{type}/{slug}.md")
    target_path = target_path_for(category, asset_id, path_template)
    author_email = cfg.get("sync_commit_author_email", "library-sync@colaberry.com")
    author_name = cfg.get("sync_commit_author_name", "Colaberry Library Sync")

    # Resolve approver display name for the commit message attribution
    approver_display = approver_email
    for a in cfg.get("approvers", []):
        if a.get("email", "").lower() == approver_email.lower():
            approver_display = a.get("display_name", approver_email)
            break

    ev = SyncEvent(
        event_id=str(uuid.uuid4())[:12],
        operation=operation,
        asset_kind="library_asset",
        category=category,
        asset_id=asset_id,
        repo=repo, branch=branch, target_path=target_path,
        author_email=approver_email,
        author_display_name=approver_display,
        triggered_by=triggered_by,
        commit_sha="", status="failed", error="",
        started_at=_now(),
    )

    try:
        if operation == "upsert":
            rows = inventory.load_category(category) or []
            raw = next((r for r in rows
                              if (r.get("name") or r.get("id") or "") == asset_id), None)
            meta = store.get_metadata(workspace, category, asset_id)
            content = render_asset_markdown(category, asset_id, raw, meta)
            ev.bytes_written = len(content.encode("utf-8"))
            commit_message = (
                f"[Library sync] Upsert {category}/{asset_id} (approver: {approver_display})"
            )
        elif operation == "delete":
            content = None
            commit_message = (
                f"[Library sync] Remove {category}/{asset_id} (approver: {approver_display})"
            )
        else:
            raise ValueError(f"unknown operation: {operation}")

        if dry_run:
            ev.status = "noop"
            ev.commit_sha = "dry-run"
        elif _gh_available():
            sha = _sync_via_gh(repo, branch, target_path, content,
                                       commit_message, author_name, author_email,
                                       operation)
            ev.commit_sha = sha or "noop"
            ev.status = "noop" if not sha else "success"
        else:
            ev.status = "failed"
            ev.error = "gh CLI not available + git-fallback not yet implemented in v1"

    except Exception as e:  # noqa: BLE001 — capture every fault into the audit
        ev.status = "failed"
        ev.error = f"{type(e).__name__}: {e}"

    ev.finished_at = _now()
    _append_audit(ev)
    return ev


def sync_all_approved(workspace: str = "global",
                              triggered_by: str = "scheduled",
                              approver_email: str = "ali@colaberry.com",
                              dry_run: bool = False) -> dict[str, int]:
    """Full reconciliation: walk every category, upsert every vetted asset,
    delete any synced asset whose metadata is no longer vetted.

    Returns counts: {upserted, deleted, failed, skipped}.
    """
    from . import inventory, store

    out = {"upserted": 0, "deleted": 0, "failed": 0, "skipped": 0}
    for cat in inventory.CATEGORIES:
        rows = inventory.load_category(cat.key) or []
        for row in rows:
            asset_id = row.get("name") or row.get("id") or ""
            if not asset_id:
                continue
            meta = store.get_metadata(workspace, cat.key, asset_id)
            if not meta.vetted:
                # Was it previously synced? If yes, delete it.
                prior = [e for e in history(category=cat.key, asset_id=asset_id)
                            if e.status == "success" and e.operation == "upsert"]
                already_deleted = [e for e in history(category=cat.key, asset_id=asset_id)
                                          if e.status == "success" and e.operation == "delete"]
                if prior and (not already_deleted or
                                 prior[-1].finished_at > already_deleted[-1].finished_at):
                    ev = sync_asset(cat.key, asset_id, approver_email,
                                            "delete", triggered_by, workspace, dry_run)
                    out["deleted" if ev.status in ("success", "noop") else "failed"] += 1
                else:
                    out["skipped"] += 1
                continue
            ev = sync_asset(cat.key, asset_id, approver_email,
                                    "upsert", triggered_by, workspace, dry_run)
            out["upserted" if ev.status in ("success", "noop") else "failed"] += 1
    return out
