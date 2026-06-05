"""FastAPI application for AI Project Architect & Build Companion."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

from app.advisory.routes import router as advisory_router
from app.routers import (
    admin,
    auth,
    auto_build,
    chapter_build,
    chat,
    demo,
    feature_discovery,
    final_assembly,
    generate,
    idea_intake,
    library,
    mcp_server,
    my_day,
    ops_platform,
    outline_approval,
    outline_generation,
    projects,
    quality_gates,
)

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan: start/stop background services."""
    stops: list = []
    try:
        from execution.skill_scanner_scheduler import start_scheduler, stop_scheduler
        start_scheduler()
        stops.append(stop_scheduler)
    except ImportError:
        logger.info("APScheduler not installed — skill scanner disabled")
    except Exception:
        logger.warning("Failed to start skill scanner scheduler", exc_info=True)

    try:
        from execution.products.library.use_case_scheduler import (
            start_scheduler as uc_start, stop_scheduler as uc_stop,
        )
        uc_start()
        stops.append(uc_stop)
    except Exception:
        logger.warning("Failed to start use-case scheduler", exc_info=True)

    try:
        from execution.products.ops.scheduler import (
            start_scheduler as ops_start, stop_scheduler as ops_stop,
        )
        ops_start()
        stops.append(ops_stop)
        # Print + log so we can verify in container logs that the
        # scheduler actually started (the prior logger.info call was
        # being filtered out by uvicorn's default log config).
        print("[lifespan] ops sync scheduler started", flush=True)
    except Exception as e:
        print(f"[lifespan] FAILED to start ops sync scheduler: {e}", flush=True)
        logger.warning("Failed to start ops sync scheduler", exc_info=True)

    try:
        from execution.products.pilot.scheduler import (
            start_scheduler as pilot_start, stop_scheduler as pilot_stop,
        )
        pilot_start()
        stops.append(pilot_stop)
        print("[lifespan] pilot dash scheduler started", flush=True)
    except Exception as e:
        print(f"[lifespan] FAILED to start pilot dash scheduler: {e}", flush=True)
        logger.warning("Failed to start pilot dash scheduler", exc_info=True)

    yield

    for stop in stops:
        try: stop()
        except Exception: pass


app = FastAPI(title="AI Project Architect & Build Companion", lifespan=lifespan)

# [Auth 2] Identity gate. No-op when SSO env vars are absent; gates
# /library/ once Ali registers the OAuth app and populates .env.prod
# (see docs/specs/auth-02-google-sso.md activation steps).
from app.middleware import auth_gate_middleware
app.middleware("http")(auth_gate_middleware)


# [Phase 8] MCP first-login gate. Once a Google-SSO'd user is identified,
# if they've never minted an MCP token, force a redirect to /profile/mcp-setup
# so they install MCP before navigating the rest of the app. Skips itself
# on the setup page, the token routes, auth, static assets, and the MCP RPC
# endpoint (which has its own bearer-token auth, not Google SSO).
async def mcp_first_login_gate(request, call_next):
    from execution.products.library import auth_google as _ag
    path = request.url.path
    skip_prefixes = (
        "/profile/mcp-setup", "/profile/mcp-token", "/profile/mcp-revoke",
        "/profile/mcp-status.json",
        "/auth/", "/static/", "/advisory/static/",
        "/mcp", "/api/", "/favicon",
        "/openapi.json", "/docs", "/redoc",
    )
    if any(path.startswith(p) for p in skip_prefixes):
        return await call_next(request)
    cookie = request.cookies.get(_ag.SESSION_COOKIE_NAME)
    user = _ag.current_user_from_cookie(cookie) if cookie else None
    if user is not None and not user.mcp_token_issued_at:
        # Only redirect for HTML page loads. JSON/form posts get a 401 so
        # client-side handlers can decide what to do.
        accept = (request.headers.get("accept") or "").lower()
        if "text/html" in accept or accept == "" or "*/*" in accept:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(
                "/profile/mcp-setup?reason=first-login",
                status_code=303,
            )
    return await call_next(request)


app.middleware("http")(mcp_first_login_gate)

# Templates and static files
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.mount("/advisory/static", StaticFiles(directory=str(APP_DIR / "advisory" / "static")), name="advisory_static")


def _days_from_today(due_on):
    """Jinja filter: int days from today's UTC date to `due_on`.

    Negative = overdue. Zero = due today. Positive = future. None for
    invalid / empty input. Used everywhere /my-day/ renders an OVERDUE
    badge — the cached score_breakdown.due_days is computed at scorer
    run time and can lag the current date by minutes (between syncs)
    or days (if the scorer cron didn't fire). This filter is always
    fresh.
    """
    if not due_on:
        return None
    from datetime import datetime, timezone
    try:
        d = datetime.strptime(due_on, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (d - datetime.now(timezone.utc).date()).days


templates.env.filters["days_from_today"] = _days_from_today

# Store templates on app state so routers can access them
app.state.templates = templates

# Include routers
app.include_router(projects.router)
app.include_router(idea_intake.router, prefix="/projects/{slug}")
app.include_router(feature_discovery.router, prefix="/projects/{slug}")
app.include_router(outline_generation.router, prefix="/projects/{slug}")
app.include_router(outline_approval.router, prefix="/projects/{slug}")
app.include_router(auto_build.router, prefix="/projects/{slug}")
app.include_router(chapter_build.router, prefix="/projects/{slug}")
app.include_router(quality_gates.router, prefix="/projects/{slug}")
app.include_router(final_assembly.router, prefix="/projects/{slug}")
app.include_router(chat.router, prefix="/projects/{slug}")
app.include_router(demo.router, prefix="/projects/{slug}")
app.include_router(generate.router)
app.include_router(advisory_router)
app.include_router(ops_platform.router)
app.include_router(library.router)
app.include_router(my_day.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(mcp_server.router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle ValueError from execution scripts as user-friendly redirects."""
    from urllib.parse import quote

    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=f"{referer}?error={quote(str(exc))}", status_code=303)
