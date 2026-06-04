"""Project management routes: list, create, dashboard."""

import re
import threading
import time
from collections import deque
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.dependencies import (
    PHASE_URLS,
    get_dashboard_stats,
    get_phase_info,
    get_project_state,
    list_projects,
)
from config.blueprints import get_all_blueprints, resolve_blueprint
from execution.state_manager import delete_project, initialize_state, save_state

router = APIRouter()


# ── DoS guard: per-IP rate limit on /projects/new ────────────────────
#
# The /projects/new endpoint is anonymous by design (public demo of the
# ideation flow). Prod output/ has historical probe artifacts (`pwn2`,
# `rce-test`, `var-www-html-shell`, `2e-2e-2f-2e-2e-2f-2e-2e-2f`, etc.)
# that confirmed someone was running automated SSTI/RCE/traversal probes.
# The slugifier blocked the traversal — _slugify() in state_manager.py
# strips every non-alphanumeric so '../../../etc/passwd' becomes
# 'etc-passwd' and lands harmlessly inside output/. But there's no
# throttle, so an attacker can still spam the endpoint and burn disk +
# LLM budget. This in-process token bucket caps it.
_RL_LOCK = threading.Lock()
_RL_PER_IP: dict[str, deque] = {}     # ip -> deque of recent create timestamps
_RL_PER_IP_LIMIT = 5                  # max creates per IP per RL_WINDOW
_RL_WINDOW_SECONDS = 60               # rolling window
_RL_GLOBAL: deque = deque()           # all timestamps (any IP)
_RL_GLOBAL_LIMIT = 100                # max creates org-wide per hour
_RL_GLOBAL_WINDOW = 3600

# Heuristic: reject obviously-malicious project names BEFORE we even
# slug. These patterns showed up in prod probe artifacts and have no
# legitimate use case for naming a project.
_PROBE_PATTERNS = (
    # Probe keywords (no \b so 'pwn2' / 'rce8080' / 'qg-test' all match)
    re.compile(r"(rce|ssti|ssrf|pwn|pwned|shell|eval[-_]?test|qg[-_]?test)", re.I),
    re.compile(r"\.\./|%2[fF]|%5[cC]"),                          # path traversal
    re.compile(r"\{\{.+\}\}|\{%.+%\}"),                          # template injection
    re.compile(r"[<>;`|$]"),                                     # shell metacharacters
    # Python introspection probes (came through in the prod artifacts:
    # `lipsum-globals-os-popen-id-read`, `request-application-globals`)
    re.compile(r"(__globals__|__builtins__|popen|__class__|__mro__)", re.I),
)
_NAME_MAX_LEN = 200


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For (nginx proxies us)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_create_rate_limit(ip: str) -> tuple[bool, str]:
    """Returns (allowed, reason_if_denied). Thread-safe."""
    now = time.time()
    with _RL_LOCK:
        # Trim + check global bucket
        while _RL_GLOBAL and _RL_GLOBAL[0] < now - _RL_GLOBAL_WINDOW:
            _RL_GLOBAL.popleft()
        if len(_RL_GLOBAL) >= _RL_GLOBAL_LIMIT:
            return False, "global rate limit (100/hour)"
        # Trim + check per-IP bucket
        q = _RL_PER_IP.setdefault(ip, deque())
        while q and q[0] < now - _RL_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= _RL_PER_IP_LIMIT:
            return False, f"per-IP rate limit ({_RL_PER_IP_LIMIT}/{_RL_WINDOW_SECONDS}s)"
        # Record the new create
        q.append(now)
        _RL_GLOBAL.append(now)
        return True, ""


def _looks_like_probe(name: str) -> bool:
    """Heuristic: does this look like an automated security probe?"""
    return any(p.search(name) for p in _PROBE_PATTERNS)


@router.get("/")
async def index(request: Request):
    """Dashboard: system stats, project list, skill browser."""
    projects = list_projects()
    blueprints = get_all_blueprints()
    stats = get_dashboard_stats()
    return request.app.state.templates.TemplateResponse(
        request, "index.html", {
            "projects": projects,
            "blueprints": blueprints,
            "stats": stats,
        },
    )


@router.get("/api/dashboard/stats")
async def dashboard_stats_api():
    """JSON endpoint for dashboard statistics."""
    return JSONResponse(content=get_dashboard_stats())


@router.post("/projects/new")
async def create_project(
    request: Request,
    project_name: str = Form(...),
    blueprint: str = Form("standard"),
):
    """Create a new project and redirect to idea intake.

    Hardening (post-audit 2026-06-04):
      - Per-IP + global rate limit (drops automated probes)
      - Length cap on project_name (200 chars)
      - Probe-pattern rejection (rce/ssti/ssrf/pwn/template/shell)
      - Slug must be non-empty after sanitization (rejects all-symbol input)
      - Strip control characters before storing
    """
    project_name = (project_name or "").strip()

    # Reject probe-shaped input early — saves disk + LLM tokens
    if not project_name:
        raise HTTPException(400, "project_name is required")
    if len(project_name) > _NAME_MAX_LEN:
        raise HTTPException(
            400, f"project_name too long (max {_NAME_MAX_LEN} chars)",
        )
    if _looks_like_probe(project_name):
        raise HTTPException(400, "project_name rejected")

    # Strip control chars + non-printable garbage
    project_name = "".join(c for c in project_name if c.isprintable())

    # Sanity-check the slug BEFORE creating dirs. _slugify strips
    # everything but [a-z0-9-], so all-symbol input slugs to empty.
    from execution.state_manager import _slugify
    if not _slugify(project_name):
        raise HTTPException(400, "project_name has no valid characters")

    # Rate limit
    ip = _client_ip(request)
    allowed, reason = _check_create_rate_limit(ip)
    if not allowed:
        raise HTTPException(429, f"Too many project creations — {reason}")

    resolved_blueprint = resolve_blueprint(blueprint)
    state = initialize_state(project_name, blueprint=resolved_blueprint)
    slug = state["project"]["slug"]
    return RedirectResponse(
        url=f"/projects/{slug}/idea-intake", status_code=303
    )


@router.post("/projects/delete-all")
async def delete_all_projects_route(request: Request):
    """Delete all projects and redirect to the project list."""
    projects = list_projects()
    errors = []
    deleted_count = 0
    for p in projects:
        try:
            result = delete_project(p["slug"])
            if result:
                deleted_count += 1
        except (OSError, ValueError) as e:
            errors.append(f"{p['name']}: {e}")

    if errors:
        error_msg = quote(
            f"Deleted {deleted_count} projects. "
            f"Failed: {'; '.join(errors)}"
        )
        return RedirectResponse(url=f"/?error={error_msg}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@router.post("/projects/{slug}/delete")
async def delete_project_route(request: Request, slug: str):
    """Delete a project and redirect to the project list."""
    try:
        deleted = delete_project(slug)
    except OSError as e:
        return RedirectResponse(
            url=f"/?error={quote(str(e))}", status_code=303
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return RedirectResponse(url="/", status_code=303)


@router.get("/projects/{slug}/guided-ideation")
async def guided_ideation_gone(request: Request, slug: str):
    """Guided Ideation was removed. Redirect to the project's current phase."""
    return RedirectResponse(url=f"/projects/{slug}", status_code=302)


@router.get("/projects/{slug}")
async def project_dashboard(request: Request, slug: str):
    """Redirect to the current phase page for this project."""
    state = get_project_state(slug)
    phase = state["current_phase"]
    if phase not in PHASE_URLS:
        # Auto-migrate: deprecated phase → nearest valid phase
        if state.get("idea", {}).get("original_raw"):
            phase = "feature_discovery"
        else:
            phase = "idea_intake"
        state["current_phase"] = phase
        save_state(state, slug)
    url_segment = PHASE_URLS[phase]
    return RedirectResponse(
        url=f"/projects/{slug}/{url_segment}", status_code=302
    )
