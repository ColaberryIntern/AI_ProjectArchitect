"""[Deploy 1] Preflight check for the multi-tenant cut-over.

Verifies the prod server's environment is configured correctly BEFORE
the new containers start. Exits non-zero with a clear list of missing /
mis-configured variables.

Run on the prod box:
    python scripts/deploy_preflight.py

Run remotely from deploy.sh:
    ssh root@... "cd /opt/ai-project-architect && python scripts/deploy_preflight.py"

Categories of check:
    1. Required env vars (existence + non-empty)
    2. Soft-warning env vars (works without, but feature degrades)
    3. Filesystem invariants (data dir exists, vault key is binary-safe, etc.)
    4. Tenant seed (companies.json + users.json reachable)

Exit codes:
    0 — pass, OK to roll the new container
    1 — hard failure, do NOT deploy
    2 — soft warnings only (deploy may proceed, but degradations expected)
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TENANT_ROOT = REPO_ROOT / "output" / "library" / "_tenants"


def _load_env_prod() -> int:
    """Load REPO_ROOT/.env.prod into os.environ if not already set.

    The preflight is invoked from the host (not inside the container),
    so docker-compose's env-file plumbing hasn't happened yet. Without
    this, every check fails because os.environ is the host's bare env.
    Process env wins over .env.prod for testability.
    """
    env_file = REPO_ROOT / ".env.prod"
    if not env_file.exists():
        return 0
    loaded = 0
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        # Strip a single matching pair of surrounding quotes; preserve everything else.
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v
            loaded += 1
    return loaded

# Hard required (deploy fails)
HARD_REQUIRED = [
    ("OPENAI_API_KEY", "Project Architect generation pipeline"),
]

# Soft required for the new multi-tenant features
SOFT_REQUIRED = [
    ("GOOGLE_OAUTH_CLIENT_ID",
      "[Auth 2] Google SSO — without it, library runs in open mode"),
    ("GOOGLE_OAUTH_CLIENT_SECRET",
      "[Auth 2] Google SSO — paired with CLIENT_ID"),
    ("GOOGLE_OAUTH_REDIRECT_URI",
      "[Auth 2] Google SSO — must match Cloud Console registration"),
    ("LIBRARY_SESSION_SECRET",
      "[Auth 2] JWT signing key — 32+ char random; sessions break without it"),
    ("LIBRARY_VAULT_MASTER_KEY",
      "[Provision 2] AES-GCM master key — vault refuses to encrypt without it"),
    ("GITHUB_ADMIN_TOKEN",
      "[Provision 1] + [Infra 1] + [Infra 2] — workspace + sync calls audit as noop without it"),
    ("GITHUB_LIBRARY_REPO",
      "[Infra 1] target repo for direct-commit sync (e.g. ColaberryIntern/library-published)"),
]


def _check_env_present(name: str) -> tuple[bool, str]:
    val = os.environ.get(name, "")
    if not val:
        return False, f"{name} is unset"
    if name == "LIBRARY_SESSION_SECRET" and len(val) < 32:
        return False, f"{name} is < 32 chars (got {len(val)})"
    if name == "LIBRARY_VAULT_MASTER_KEY":
        # Should be base64 or raw 32 bytes — sanity check length
        if len(val) < 32:
            return False, f"{name} is < 32 chars; AES-GCM-256 needs 32 raw bytes (or 44 base64)"
    return True, "OK"


def _check_tenant_seed() -> tuple[bool, str]:
    companies = TENANT_ROOT / "companies.json"
    users = TENANT_ROOT / "users.json"
    if not companies.exists():
        return False, f"missing {companies} — run tenancy.seed_initial_companies_and_users()"
    if not users.exists():
        return False, f"missing {users} — run tenancy.seed_initial_companies_and_users()"
    try:
        cos = json.loads(companies.read_text(encoding="utf-8"))
        us = json.loads(users.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"seed file parse error: {e}"
    if not isinstance(cos, list) or not cos:
        return False, f"{companies} is empty or malformed"
    if not isinstance(us, list) or not us:
        return False, f"{users} is empty or malformed"
    co_ids = {c.get("company_id") for c in cos}
    if "colaberry" not in co_ids:
        return False, "colaberry tenant not seeded; backfill skipped"
    return True, f"{len(cos)} companies, {len(us)} users seeded"


def _check_data_dir_writeable() -> tuple[bool, str]:
    target = REPO_ROOT / "output" / "library"
    if not target.exists():
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return False, f"cannot create {target}: {e}"
    probe = target / ".preflight_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        return False, f"{target} not writeable: {e}"
    return True, str(target)


def main() -> int:
    hard_failures: list[str] = []
    soft_warnings: list[str] = []
    passes: list[str] = []

    print("=" * 60)
    print("[Deploy 1] Multi-tenant cut-over preflight")
    print("=" * 60)

    loaded = _load_env_prod()
    if loaded:
        print(f"\nLoaded {loaded} vars from .env.prod (process env still wins)")

    print("\n[1/4] Hard-required environment")
    for name, why in HARD_REQUIRED:
        ok, msg = _check_env_present(name)
        marker = "OK  " if ok else "FAIL"
        line = f"  [{marker}] {name:32s} — {why}: {msg}"
        print(line)
        (passes if ok else hard_failures).append(line.strip())

    print("\n[2/4] Soft-required environment (degraded mode if missing)")
    for name, why in SOFT_REQUIRED:
        ok, msg = _check_env_present(name)
        marker = "OK  " if ok else "WARN"
        line = f"  [{marker}] {name:32s} — {why}: {msg}"
        print(line)
        (passes if ok else soft_warnings).append(line.strip())

    print("\n[3/4] Filesystem invariants")
    ok, msg = _check_data_dir_writeable()
    print(f"  [{'OK  ' if ok else 'FAIL'}] data dir writeable: {msg}")
    if not ok:
        hard_failures.append(f"data dir not writeable: {msg}")

    print("\n[4/4] Tenant seed")
    ok, msg = _check_tenant_seed()
    print(f"  [{'OK  ' if ok else 'WARN'}] {msg}")
    if not ok:
        # Soft: a first-time install legitimately won't have seed yet
        soft_warnings.append(f"tenant seed missing: {msg}")

    print("\n" + "=" * 60)
    print(f"Result: {len(passes)} passed, "
              f"{len(soft_warnings)} warnings, "
              f"{len(hard_failures)} hard failures")
    print("=" * 60)

    if hard_failures:
        print("\nDO NOT DEPLOY. Resolve the failures above first.\n")
        for f in hard_failures:
            print(f"  - {f}")
        return 1
    if soft_warnings:
        print("\nDeploy may proceed; features WILL be degraded:")
        for w in soft_warnings:
            print(f"  - {w}")
        return 2
    print("\nAll checks green. Safe to deploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
