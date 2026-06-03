"""[Provision 1] Per-user GitHub workspace auto-create.

Each provisioned user gets a personal repo at
    `ColaberryIntern/{username}-workspace`
scaffolded from `ColaberryIntern/workspace-template`.

Operations are idempotent — re-running on an existing user is a no-op.

Implementation: GitHub REST API via gh CLI (preferred — uses cached
auth on the prod box) with a fallback to direct REST call using the
`GITHUB_ADMIN_TOKEN` env var. Tokens for individual users are NOT
stored here — those go in the vault ([Provision 2]) via Admin 2.

The org-level admin token (used by THIS module) is intentionally an
env-var, not a per-user secret in the vault, since it's the
credential that BOOTSTRAPS the vault.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tenancy

LAYER = "platform_core"
PRODUCT = "library"

ROOT = Path(__file__).resolve().parents[3]

DEFAULT_ORG = "ColaberryIntern"
DEFAULT_TEMPLATE = "ColaberryIntern/workspace-template"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _audit_path() -> Path:
    p = tenancy._root() / "workspace_provision_audit.jsonl"
    return p


def _audit(actor_id: str, target_user_id: str, action: str,
              repo: str = "", error: str = "", details: dict | None = None) -> None:
    row = {
        "actor_id": actor_id, "target_user_id": target_user_id,
        "action": action, "repo": repo, "error": error,
        "details": details or {}, "at": _now(),
    }
    with _audit_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


# ── Username slug — email → repo-friendly handle ────────────────


_USERNAME_BAD = re.compile(r"[^a-z0-9-]+")


def username_slug(email: str) -> str:
    """Convert email to a repo-safe username. Defaults to local part."""
    if not email or "@" not in email:
        return "unknown"
    local = email.split("@", 1)[0].lower()
    slug = _USERNAME_BAD.sub("-", local).strip("-")
    return slug[:39] or "user"


def workspace_repo_for_user(email: str, org: str = DEFAULT_ORG) -> str:
    return f"{org}/{username_slug(email)}-workspace"


# ── gh / API helpers ────────────────────────────────────────────


def _gh_available() -> bool:
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True, timeout=10)
        return True
    except Exception:
        return False


def _admin_token() -> str | None:
    return os.environ.get("GITHUB_ADMIN_TOKEN") or os.environ.get("GH_TOKEN")


def _gh_api(method: str, path: str, payload: dict | None = None) -> dict:
    """Hit GitHub API via gh CLI (preferred) or direct urllib if no gh."""
    if _gh_available():
        cmd = ["gh", "api", "--method", method, path]
        if payload:
            cmd += ["--input", "-"]
        proc = subprocess.run(cmd,
                                       input=json.dumps(payload) if payload else None,
                                       capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"gh api {method} {path} failed: {proc.stderr.strip()[:300]}")
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    tok = _admin_token()
    if not tok:
        raise RuntimeError("GitHub admin token missing — set GITHUB_ADMIN_TOKEN")
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Colaberry workspace provisioner",
    })
    if payload:
        req.data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.request.HTTPError as e:
        raise RuntimeError(f"github api {method} {path} failed: HTTP {e.code} {e.reason}")


# ── Provision flow ──────────────────────────────────────────────


@dataclass
class ProvisionResult:
    ok: bool
    repo_url: str = ""
    repo_already_existed: bool = False
    invited_user: bool = False
    error: str = ""
    details: dict | None = None


def repo_exists(repo: str) -> bool:
    """`{org}/{name}` — returns True iff the repo exists + we can see it."""
    try:
        _gh_api("GET", f"/repos/{repo}")
        return True
    except Exception:
        return False


def provision_user_workspace(user: tenancy.User,
                                       admin_actor_id: str,
                                       template_repo: str = DEFAULT_TEMPLATE,
                                       org: str = DEFAULT_ORG,
                                       dry_run: bool = False) -> dict:
    """Create the user's workspace repo + add them as collaborator.

    Idempotent — if the repo already exists, just verifies + returns.
    """
    repo = workspace_repo_for_user(user.email, org=org)
    result = ProvisionResult(ok=False, repo_url=f"https://github.com/{repo}")

    try:
        if dry_run:
            _audit(admin_actor_id, user.user_id, "dry_run", repo=repo)
            result.ok = True
            return _result_dict(result)

        # 1. Does the repo already exist?
        if repo_exists(repo):
            result.repo_already_existed = True
            _audit(admin_actor_id, user.user_id, "skip_existing", repo=repo)
        else:
            # 2. Create from template
            org_part, name_part = repo.split("/", 1)
            template_owner, template_name = template_repo.split("/", 1)
            payload = {
                "owner": org_part,
                "name": name_part,
                "description": (f"{user.display_name}'s Colaberry workspace "
                                       "— scaffolded from workspace-template"),
                "private": True,
                "include_all_branches": False,
            }
            try:
                _gh_api("POST",
                              f"/repos/{template_owner}/{template_name}/generate",
                              payload=payload)
                _audit(admin_actor_id, user.user_id, "create_repo", repo=repo)
            except RuntimeError as e:
                # If template doesn't exist OR we lack permissions, try a bare repo
                if "404" in str(e) or "Not Found" in str(e):
                    _gh_api("POST", f"/orgs/{org_part}/repos", payload={
                        "name": name_part,
                        "description": (f"{user.display_name}'s Colaberry workspace"),
                        "private": True,
                        "auto_init": True,
                    })
                    _audit(admin_actor_id, user.user_id, "create_repo_bare",
                              repo=repo,
                              details={"reason": "template not found"})
                else:
                    raise

        # 3. Invite the user as collaborator (best-effort — needs gh username)
        gh_handle = username_slug(user.email)
        try:
            _gh_api("PUT", f"/repos/{repo}/collaborators/{gh_handle}",
                          payload={"permission": "write"})
            result.invited_user = True
            _audit(admin_actor_id, user.user_id, "invite_collaborator",
                      repo=repo,
                      details={"gh_handle": gh_handle, "permission": "write"})
        except Exception as e:
            _audit(admin_actor_id, user.user_id, "invite_failed",
                      repo=repo,
                      details={"gh_handle": gh_handle},
                      error=str(e)[:200])

        result.ok = True
        return _result_dict(result)

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        _audit(admin_actor_id, user.user_id, "provision_failed",
                  repo=repo, error=result.error)
        return _result_dict(result)


def _result_dict(r: ProvisionResult) -> dict:
    return {
        "ok": r.ok, "repo_url": r.repo_url,
        "repo_already_existed": r.repo_already_existed,
        "invited_user": r.invited_user, "error": r.error,
    }


def provision_history(user_id: str | None = None) -> list[dict]:
    p = _audit_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if user_id and row.get("target_user_id") != user_id:
            continue
        out.append(row)
    return out


# ── Workspace template scaffolding (the contents the template repo should hold) ──


def render_starter_user_profile_md(user: tenancy.User) -> str:
    return f"""# {user.display_name}'s Colaberry Workspace

Welcome! This is your personal repository inside Colaberry's
multi-tenant AI platform.

- **Email**: {user.email}
- **Company**: {user.company_id}
- **Roles**: {", ".join(user.roles)}
- **Provisioned**: {user.created_at}

## What lives here

- `.claude/skills/` — your custom skills, vetted by Colaberry can sync from the central [Library](https://advisor.colaberry.ai/library/)
- `.mcp.json` — your wired MCP servers (auto-generated from the
  [admin tools-access matrix](https://advisor.colaberry.ai/admin/users/{user.user_id}/scopes))
- `USER_PROFILE.md` — this file; edit freely

## Links

- [Open the Library](https://advisor.colaberry.ai/library/) — browse vetted skills,
  agents, prompts, MCP servers, capabilities, templates, policies, and more
- [Your admin profile](https://advisor.colaberry.ai/admin/users/{user.user_id})
"""


def render_starter_mcp_json(user: tenancy.User, scopes: set[str]) -> str:
    """Render a .mcp.json from the user's granted tool scopes.

    NOTE: this generator omits the actual credentials — those live in
    the vault and are injected by the runtime, not committed to the repo.
    """
    servers = {}
    if "github" in scopes:
        servers["github"] = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${{ vault.github }}"},
        }
    if "gmail" in scopes:
        servers["gmail"] = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-gmail"],
            "env": {"GMAIL_REFRESH_TOKEN": "${{ vault.gmail }}"},
        }
    if "calendar" in scopes:
        servers["calendar"] = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-gcal"],
            "env": {"GOOGLE_OAUTH_REFRESH": "${{ vault.calendar }}"},
        }
    if "slack" in scopes:
        servers["slack"] = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-slack"],
            "env": {"SLACK_BOT_TOKEN": "${{ vault.slack }}"},
        }
    return json.dumps({"mcpServers": servers}, indent=2)
