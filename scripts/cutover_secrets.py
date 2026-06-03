"""Mint the cut-over secrets that Ali appends to /opt/ai-project-architect/.env.prod.

Writes to the OS temp directory (NOT the repo, and NOT OneDrive — the repo
sits inside OneDrive which would cloud-sync anything written here). Default
path: %TEMP%/cutover_secrets.local on Windows, $TMPDIR/cutover_secrets.local
on Unix.

Contains the two secrets that CAN be machine-generated:

    LIBRARY_VAULT_MASTER_KEY   — 32 random bytes, base64
    LIBRARY_SESSION_SECRET     — token_urlsafe(64)

The other 5 env vars (GOOGLE_OAUTH_*, GITHUB_ADMIN_TOKEN, GITHUB_LIBRARY_REPO)
require human action — see the printed checklist.

Usage:
    python scripts/cutover_secrets.py             # mint to %TEMP%, also print
    python scripts/cutover_secrets.py --print-only       # print to stdout, no file
    python scripts/cutover_secrets.py --out-path PATH    # custom path
    python scripts/cutover_secrets.py --force-overwrite  # re-mint

WARNING: LIBRARY_VAULT_MASTER_KEY must NEVER be rotated without re-encrypting
every stored secret. Once you append it to .env.prod and the vault stores
anything, that key is the only thing that can ever decrypt it. Save it to a
password manager BEFORE deleting the local file.
"""
from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
import tempfile
from pathlib import Path

DEFAULT_OUTPUT_PATH = Path(tempfile.gettempdir()) / "cutover_secrets.local"


def mint() -> dict[str, str]:
    return {
        "LIBRARY_VAULT_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "LIBRARY_SESSION_SECRET": secrets.token_urlsafe(64),
    }


def _onedrive_warn(path: Path) -> None:
    """Refuse to write under OneDrive (would cloud-sync secrets)."""
    parts_lower = [p.lower() for p in path.resolve().parts]
    if any("onedrive" in p for p in parts_lower):
        print(
            f"ERROR: {path} resolves under OneDrive -- refusing to write secrets there.\n"
            f"Use --out-path to point to a non-synced location "
            f"(e.g. {DEFAULT_OUTPUT_PATH}), or --print-only.",
            file=sys.stderr,
        )
        sys.exit(2)


def write_secrets(s: dict[str, str], out_path: Path, *, force: bool) -> None:
    _onedrive_warn(out_path)
    if out_path.exists() and not force:
        print(
            f"  {out_path} already exists — refusing to overwrite. "
            f"Pass --force-overwrite to re-mint."
        )
        sys.exit(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}={v}\n" for k, v in s.items())
    out_path.write_text(body, encoding="utf-8")
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass  # Windows: chmod is a no-op
    print(f"  Wrote {out_path} ({len(body)} bytes, 0600)")


CHECKLIST = """
=== Manual steps that CANNOT be scripted ===

A. Google OAuth app (capture CLIENT_ID + CLIENT_SECRET)
   1. https://console.cloud.google.com/ -> APIs & Services -> OAuth consent screen
   2. App type: Web application; user type: Internal (Colaberry workspace)
   3. Authorized redirect URI: https://advisor.colaberry.ai/auth/callback
   4. Capture the Client ID and Client Secret

B. GitHub PAT (capture as GITHUB_ADMIN_TOKEN)
   1. https://github.com/settings/tokens -> Generate new (classic)
   2. Scopes: repo, admin:org
   3. Capture the token (one-time view)

C. Workspace-template repo
   gh repo create ColaberryIntern/workspace-template --private --add-readme

D. Backup existing prod data BEFORE deploy
   ssh root@95.216.199.47 "cd /opt/ai-project-architect && \\
       tar -czf output_backup_$(date +%Y%m%d_%H%M%S).tar.gz output/"

E. Append to /opt/ai-project-architect/.env.prod on the prod box:
     # From the cutover_secrets.local file (default: %TEMP%/cutover_secrets.local)
     LIBRARY_VAULT_MASTER_KEY=...
     LIBRARY_SESSION_SECRET=...
     # From step A
     GOOGLE_OAUTH_CLIENT_ID=...
     GOOGLE_OAUTH_CLIENT_SECRET=...
     GOOGLE_OAUTH_REDIRECT_URI=https://advisor.colaberry.ai/auth/callback
     # From step B
     GITHUB_ADMIN_TOKEN=...
     GITHUB_LIBRARY_REPO=ColaberryIntern/AI_ProjectArchitect

F. Securely delete the local file (default location: %TEMP%/cutover_secrets.local)
   On Windows:  del /F /Q "%TEMP%\cutover_secrets.local"
   On Unix:     shred -u "$TMPDIR/cutover_secrets.local"  (or  rm -P  on macOS)

G. Cut-over
   ssh root@95.216.199.47 "cd /opt/ai-project-architect && ./deploy.sh"

H. Backfill (Data 1) -- after deploy.sh reports success
   ssh root@95.216.199.47 "cd /opt/ai-project-architect && \\
       docker compose exec app python -c 'from execution.products.library.tenancy_backfill import run; print(run())'"
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-path", type=Path, default=DEFAULT_OUTPUT_PATH,
                    help=f"Where to write the secrets file (default: {DEFAULT_OUTPUT_PATH})")
    ap.add_argument("--print-only", action="store_true",
                    help="Print secrets to stdout, do not write a file")
    ap.add_argument("--force-overwrite", action="store_true",
                    help="Overwrite the output file if it exists")
    args = ap.parse_args()

    print("Minting cut-over secrets...")
    minted = mint()

    if args.print_only:
        print()
        print("=== Secrets (stdout only, no file) ===")
        for k, v in minted.items():
            print(f"{k}={v}")
        print()
        print("Clear your terminal scrollback after copying!")
    else:
        write_secrets(minted, args.out_path, force=args.force_overwrite)
        print()
        print("=== Minted secrets (preview only — full values in the file above) ===")
        for k, v in minted.items():
            print(f"  {k}={v[:8]}...{v[-4:]}  (length {len(v)})")

    print(CHECKLIST)
    print(
        "Reminder: LIBRARY_VAULT_MASTER_KEY rotation is destructive. Once the\n"
        "vault stores anything, this key is the ONLY way to decrypt it. Save\n"
        "a copy to a password manager BEFORE deleting the local file."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
