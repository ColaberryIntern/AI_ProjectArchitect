"""Pure Basecamp URL derivation helpers.

A BC todo's app URL looks like
    https://3.basecamp.com/<acct>/buckets/<proj>/todos/<id>
and its parent records share the same `.../buckets/<proj>/` prefix:
    list     -> https://3.basecamp.com/<acct>/buckets/<proj>/todolists/<list_id>
    project  -> https://3.basecamp.com/<acct>/buckets/<proj>

These helpers derive the list/project URL from any todo's app URL by swapping
the trailing `/todos/<id>` segment. They return "" (never raise) when the
sample URL doesn't match the expected shape, so callers can treat "" as
"no URL available" and degrade gracefully. Shared by the rollup (Heat map
deep-links) and the per-todo prompt renderers (CONTEXT block links).
"""
from __future__ import annotations

_MARKER = "/todos/"


def list_url(sample_app_url: str, list_id: int) -> str:
    """Derive the todolist URL from any todo's app URL in that list."""
    if not sample_app_url:
        return ""
    idx = sample_app_url.find(_MARKER)
    if idx == -1:
        return ""
    return f"{sample_app_url[:idx]}/todolists/{list_id}"


def project_url(sample_app_url: str) -> str:
    """Derive the project (bucket) URL from any todo's app URL."""
    if not sample_app_url:
        return ""
    idx = sample_app_url.find(_MARKER)
    if idx == -1:
        return ""
    return sample_app_url[:idx]
