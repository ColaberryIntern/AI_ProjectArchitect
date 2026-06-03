"""Smoke test: fetch token via the CCPP chain + ping BC for the caller identity.

Run from repo root:
    python -m tools.bc_mcp.smoke
"""
from __future__ import annotations

import json

from . import api, auth


def main() -> int:
    print("Token cache state:")
    print(json.dumps(auth.token_info(), indent=2, default=str))
    print()

    try:
        tok = auth.get_token()
    except Exception as e:  # noqa: BLE001
        print(f"TOKEN FETCH FAILED: {e}")
        return 2
    print(f"Got token (length {len(tok)}, prefix {tok[:8]}...)")
    print()

    try:
        me = api.get("/my/profile.json")
    except Exception as e:  # noqa: BLE001
        print(f"BC PING FAILED: {e}")
        return 3

    if isinstance(me, dict):
        print(f"BC identity: {me.get('name')} ({me.get('email_address')})")
        print(f"  id: {me.get('id')}")
    else:
        print(f"BC returned: {str(me)[:200]}")

    print()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
