"""[Infra 2] PR-based GitHub sync — promotes [Infra 1]'s direct-commit sync
to a reviewable, CI-gated, branch-and-PR workflow.

Why a separate module:
    Infra 1 wrote files straight to `main`. That's fine for single-tenant
    bootstrap but unsafe at scale (no review, no rollback, no CI gate).
    Infra 2 makes every sync a PR — the canonical repo's `main` is only
    touched after CI passes (and optionally a human review).

Public API:
    open_sync_pr(category, asset_id, approver_email, ...) -> PRResult
    reconcile_via_prs(workspace="global") -> list[PRResult]

The renderer + auth + audit log are reused from [Infra 1].
Branch naming: `sync/{category}/{asset_slug}-{timestamp}`.
PR title:      `[Library sync] {category}/{asset_id}`.

NO subprocess calls happen at import time — tests monkeypatch `_run`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import github_sync  # reuse renderer + auth + audit row shape

LAYER = "product"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]
AUDIT_DIR = ROOT / "output" / "library" / "_github_pr_sync"


@dataclass
class PRResult:
    pr_id: str
    operation: str            # "upsert" | "delete"
    category: str
    asset_id: str
    branch: str
    pr_number: int | None
    pr_url: str
    status: str               # "opened" | "merged" | "auto_merged" | "noop" | "failed"
    error: str = ""
    auto_merged: bool = False
    started_at: str = ""
    finished_at: str = ""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _audit_file() -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl"


def _append_audit(r: PRResult) -> None:
    with _audit_file().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(r)) + "\n")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", (s or "").strip()).strip("-").lower()
    return s or "asset"


def _branch_name(category: str, asset_id: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"sync/{_slug(category)}/{_slug(asset_id)}-{ts}"


def _run(cmd: list[str], cwd: str | None = None, env: dict | None = None,
              timeout: int = 90) -> tuple[int, str, str]:
    """Wrapper kept narrow so tests can monkeypatch."""
    proc = subprocess.run(cmd, cwd=cwd, env=env,
                                       capture_output=True, text=True,
                                       timeout=timeout, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def _gh_available() -> bool:
    code, _, _ = _run(["gh", "--version"])
    return code == 0


def _target_repo() -> str:
    return (github_sync._load_approvers().get("approval_target_repo")
                  or "ColaberryIntern/AI_ProjectArchitect")


def _sync_path(category: str, asset_id: str) -> str:
    tmpl = (github_sync._load_approvers().get("sync_path_template")
                   or "library/{type}/{slug}.md")
    return tmpl.format(type=category, slug=_slug(asset_id))


# ── Auto-merge policy ────────────────────────────────────────────


def _auto_merge_enabled() -> bool:
    """Is auto-merge configured for the target repo?

    Reads from config/library_approvers.json:
        "pr_auto_merge": true | false (default false — safer)
    """
    return bool(github_sync._load_approvers().get("pr_auto_merge", False))


def _smoke_test_script_path() -> str:
    """Where the CI gate's smoke test lives in the repo."""
    return "scripts/library_sync_smoke.py"


# ── Open a sync PR ───────────────────────────────────────────────


def open_sync_pr(category: str, asset_id: str, approver_email: str,
                          operation: str = "upsert",
                          triggered_by: str = "manual",
                          dry_run: bool = False) -> PRResult:
    """Create a branch, write/delete the artifact, open a PR.

    Returns PRResult with PR URL on success. Audit-logged either way.
    """
    pr_id = f"prs-{uuid.uuid4().hex[:10]}"
    started = _now()
    branch = _branch_name(category, asset_id)
    target_path = _sync_path(category, asset_id)

    # Permission check inherited from Infra 1
    can, reason = github_sync.can_approve(approver_email, category)
    if not can:
        result = PRResult(
            pr_id=pr_id, operation=operation, category=category,
            asset_id=asset_id, branch=branch, pr_number=None, pr_url="",
            status="failed", error=f"not authorised: {reason}",
            started_at=started, finished_at=_now(),
        )
        _append_audit(result)
        return result

    if dry_run:
        result = PRResult(
            pr_id=pr_id, operation=operation, category=category,
            asset_id=asset_id, branch=branch, pr_number=None,
            pr_url="(dry_run)", status="noop",
            started_at=started, finished_at=_now(),
        )
        _append_audit(result)
        return result

    if not _gh_available():
        result = PRResult(
            pr_id=pr_id, operation=operation, category=category,
            asset_id=asset_id, branch=branch, pr_number=None, pr_url="",
            status="failed", error="gh CLI not available on host",
            started_at=started, finished_at=_now(),
        )
        _append_audit(result)
        return result

    # Render the markdown body
    body_md = ""
    if operation == "upsert":
        try:
            body_md = github_sync.render_asset_markdown(category, asset_id)
        except Exception as e:
            result = PRResult(
                pr_id=pr_id, operation=operation, category=category,
                asset_id=asset_id, branch=branch, pr_number=None, pr_url="",
                status="failed", error=f"render failed: {e}",
                started_at=started, finished_at=_now(),
            )
            _append_audit(result)
            return result

    # Hand off to gh for the actual PR creation. Single subprocess call so
    # tests can monkeypatch _run cleanly.
    pr_url = ""
    pr_number = None
    try:
        pr_url, pr_number = _open_pr_with_gh(
            repo=_target_repo(), branch=branch,
            target_path=target_path, body_md=body_md,
            operation=operation, category=category, asset_id=asset_id,
            approver_email=approver_email, triggered_by=triggered_by,
        )
    except Exception as e:
        result = PRResult(
            pr_id=pr_id, operation=operation, category=category,
            asset_id=asset_id, branch=branch, pr_number=None, pr_url="",
            status="failed", error=str(e),
            started_at=started, finished_at=_now(),
        )
        _append_audit(result)
        return result

    status = "opened"
    auto_merged = False
    if _auto_merge_enabled() and pr_number:
        # Best-effort auto-merge — gh will only flip the bit; actual merge
        # waits for required checks
        code, _, err = _run([
            "gh", "pr", "merge", str(pr_number),
            "--repo", _target_repo(), "--auto", "--squash",
        ])
        if code == 0:
            status = "auto_merged"
            auto_merged = True

    result = PRResult(
        pr_id=pr_id, operation=operation, category=category,
        asset_id=asset_id, branch=branch, pr_number=pr_number, pr_url=pr_url,
        status=status, auto_merged=auto_merged,
        started_at=started, finished_at=_now(),
    )
    _append_audit(result)
    return result


def _open_pr_with_gh(repo: str, branch: str, target_path: str, body_md: str,
                              operation: str, category: str, asset_id: str,
                              approver_email: str, triggered_by: str
                              ) -> tuple[str, int | None]:
    """Compose the gh commands to create a branch + file + PR.

    Returns (pr_url, pr_number). Raises on any non-zero exit.

    We use `gh api` to mutate the remote ref + content rather than cloning
    the whole repo locally — keeps the sync cheap.
    """
    # 1. Get the latest main commit SHA
    code, out, err = _run([
        "gh", "api", f"/repos/{repo}/git/ref/heads/main",
        "--jq", ".object.sha",
    ])
    if code != 0:
        raise RuntimeError(f"resolve main SHA failed: {err}")
    base_sha = out.strip()

    # 2. Create a branch from main
    code, _, err = _run([
        "gh", "api", "--method", "POST",
        f"/repos/{repo}/git/refs",
        "-f", f"ref=refs/heads/{branch}",
        "-f", f"sha={base_sha}",
    ])
    if code != 0:
        raise RuntimeError(f"create branch failed: {err}")

    # 3. Put or delete the file on the branch
    if operation == "upsert":
        import base64
        b64 = base64.b64encode(body_md.encode("utf-8")).decode("ascii")
        # Check if file already exists on branch to capture its sha (required for PUT update)
        existing_sha = ""
        code, out, _ = _run([
            "gh", "api", f"/repos/{repo}/contents/{target_path}?ref={branch}",
            "--jq", ".sha",
        ])
        if code == 0:
            existing_sha = out.strip()
        put_cmd = [
            "gh", "api", "--method", "PUT",
            f"/repos/{repo}/contents/{target_path}",
            "-f", f"branch={branch}",
            "-f", f"message=Library sync: {operation} {category}/{asset_id}",
            "-f", f"content={b64}",
            "-f", f"committer[name]={github_sync._load_approvers().get('sync_commit_author_name','Colaberry Library Sync')}",
            "-f", f"committer[email]={github_sync._load_approvers().get('sync_commit_author_email','library-sync@colaberry.com')}",
        ]
        if existing_sha:
            put_cmd += ["-f", f"sha={existing_sha}"]
        code, _, err = _run(put_cmd)
        if code != 0:
            raise RuntimeError(f"PUT contents failed: {err}")
    elif operation == "delete":
        code, out, _ = _run([
            "gh", "api", f"/repos/{repo}/contents/{target_path}?ref={branch}",
            "--jq", ".sha",
        ])
        if code != 0:
            # Already gone; close the branch and bail
            return ("(noop — already deleted)", None)
        existing_sha = out.strip()
        code, _, err = _run([
            "gh", "api", "--method", "DELETE",
            f"/repos/{repo}/contents/{target_path}",
            "-f", f"branch={branch}",
            "-f", f"message=Library sync: delete {category}/{asset_id}",
            "-f", f"sha={existing_sha}",
        ])
        if code != 0:
            raise RuntimeError(f"DELETE contents failed: {err}")
    else:
        raise ValueError(f"unknown operation {operation}")

    # 4. Open the PR
    pr_title = f"[Library sync] {category}/{asset_id}"
    pr_body = (
        f"Automated library sync via [Infra 2].\n\n"
        f"- **Operation:** `{operation}`\n"
        f"- **Asset:** `{category}/{asset_id}`\n"
        f"- **Approver:** {approver_email}\n"
        f"- **Triggered by:** {triggered_by}\n"
        f"- **CI gate:** runs `{_smoke_test_script_path()} {target_path}`\n\n"
        f"_Merge once CI is green. Auto-merge is "
        f"{'ENABLED' if _auto_merge_enabled() else 'DISABLED'}._"
    )
    code, out, err = _run([
        "gh", "pr", "create",
        "--repo", repo, "--head", branch, "--base", "main",
        "--title", pr_title, "--body", pr_body,
    ])
    if code != 0:
        raise RuntimeError(f"open PR failed: {err}")
    pr_url = out.strip()
    pr_number = _parse_pr_number(pr_url)
    return (pr_url, pr_number)


def _parse_pr_number(url: str) -> int | None:
    m = re.search(r"/pull/(\d+)", url or "")
    return int(m.group(1)) if m else None


# ── Reconciler — PR per change vs the canonical state ────────────


def reconcile_via_prs(workspace: str = "global",
                                  approver_email: str = "library-sync@colaberry.com",
                                  dry_run: bool = True) -> list[PRResult]:
    """Open one PR per missing-or-out-of-date asset. Default dry_run=True
    so a manual operator can preview what would change."""
    results: list[PRResult] = []
    # Reuse Infra 1's per-asset reconcile helper if present
    try:
        plans = github_sync.diff_plan(workspace=workspace)
    except AttributeError:
        plans = []
    for plan in plans:
        result = open_sync_pr(
            category=plan["category"], asset_id=plan["asset_id"],
            approver_email=approver_email, operation=plan["operation"],
            triggered_by="scheduled", dry_run=dry_run,
        )
        results.append(result)
    return results


# ── Hook: trigger PR on approval (called by Workflow 1) ─────────


def maybe_trigger_pr_for_approval(item_kind: str, item_id: str,
                                                       category: str, company_id: str,
                                                       approver_email: str) -> PRResult | None:
    """Workflow 1's decide_review can call this. Best-effort, audit-logged.
    Only fires for the Colaberry tenant in v1 — per-tenant sync repos are
    a future ticket (each customer would own their own published repo)."""
    if company_id != "colaberry":
        # Other tenants don't have a target repo configured yet
        return None
    if item_kind != "library_asset":
        return None
    if os.environ.get("LIBRARY_PR_SYNC_DISABLED") == "1":
        return None
    try:
        return open_sync_pr(
            category=category, asset_id=item_id,
            approver_email=approver_email, operation="upsert",
            triggered_by="approval",
        )
    except Exception:
        return None
