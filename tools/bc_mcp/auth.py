"""Basecamp token fetcher with disk cache.

Single source of truth: CCPP SQL Server (Basecamp_AuthInfo table) on the prod
backend container. We SSH to the prod host, exec a tiny Node script inside the
running container (which has CCPP creds in its env), and read the latest
active row.

Token rotation is 14 days, human-mediated via the Basecamp OAuth UI. There is
no auto-refresh anywhere in the upstream codebase — humans rotate, CCPP gets a
new row, this fetcher just reads the freshest one.

Cache strategy:
- Disk cache at ~/.cache/bc_mcp/token.json
- Refresh if older than CACHE_TTL_SECONDS
- Force refresh on 401 (handled by api.py, not here)
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

SSH_HOST = os.environ.get("BC_MCP_SSH_HOST", "root@95.216.199.47")
CONTAINER = os.environ.get("BC_MCP_CONTAINER", "accelerator-backend")
CACHE_PATH = Path.home() / ".cache" / "bc_mcp" / "token.json"
CACHE_TTL_SECONDS = 12 * 60 * 60  # 12h — safely under 14-day rotation

# Note: this JS uses only single quotes so it can be wrapped in bash double
# quotes when passed via `ssh user@host "docker exec ... node -e '...'"`.
_NODE_SCRIPT = (
    "const sql=require('mssql');"
    "(async()=>{"
    "try{"
    "await sql.connect({"
    "server:process.env.MSSQL_HOST,"
    "port:parseInt(process.env.MSSQL_PORT||'1433',10),"
    "user:process.env.MSSQL_USER,"
    "password:process.env.MSSQL_PASS,"
    "database:'CCPP',"
    "options:{encrypt:true,trustServerCertificate:true}"
    "});"
    "const r=await sql.query('SELECT TOP 1 AccessToken,ExpiryDate FROM Basecamp_AuthInfo WHERE IsActive=1 ORDER BY BasecampAuthInfoID DESC');"
    "await sql.close();"
    "const row=r.recordset[0];"
    "let t=row.AccessToken;"
    "if(t.startsWith('Bearer '))t=t.slice(7);"
    "process.stdout.write(JSON.stringify({token:t,expiry:row.ExpiryDate}));"
    "}catch(e){process.stderr.write('ERR: '+e.message);process.exit(2);}"
    "})();"
)


class TokenFetchError(RuntimeError):
    pass


def _fetch_from_ccpp() -> dict:
    """SSH → docker exec → node script. Returns {token, expiry, fetched_at}.

    Quoting layers:
      ssh arg-3 is a single string sent verbatim to the remote shell.
      Remote shell sees:  docker exec <container> node -e "<js>"
      The JS is single-quoted-only so it can sit inside the bash double quotes.
    """
    remote = f'docker exec {CONTAINER} node -e "{_NODE_SCRIPT}"'
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        SSH_HOST,
        remote,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as e:
        raise TokenFetchError(f"SSH/docker timeout: {e}") from e
    if r.returncode != 0:
        raise TokenFetchError(
            f"Token fetch failed (rc={r.returncode}): {r.stderr.strip() or r.stdout.strip()}"
        )
    try:
        payload = json.loads(r.stdout.strip())
    except json.JSONDecodeError as e:
        raise TokenFetchError(f"Token fetch returned non-JSON: {r.stdout[:200]}") from e
    payload["fetched_at"] = int(time.time())
    return payload


def _read_cache() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(payload: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(CACHE_PATH, 0o600)
    except OSError:
        pass  # Windows: chmod is a no-op


def get_token(force_refresh: bool = False) -> str:
    """Return a valid Basecamp Bearer token. Refresh from CCPP if stale or forced."""
    cached = None if force_refresh else _read_cache()
    if cached and (time.time() - cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
        return cached["token"]
    fresh = _fetch_from_ccpp()
    _write_cache(fresh)
    return fresh["token"]


def token_info() -> dict:
    """Return cached token metadata (NEVER the token itself) for diagnostics."""
    cached = _read_cache()
    if not cached:
        return {"cached": False}
    age = int(time.time() - cached.get("fetched_at", 0))
    return {
        "cached": True,
        "age_seconds": age,
        "fresh": age < CACHE_TTL_SECONDS,
        "expiry": cached.get("expiry"),
        "token_length": len(cached.get("token", "")),
    }
