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
    except Exception:
        logger.warning("Failed to start ops sync scheduler", exc_info=True)

    yield

    for stop in stops:
        try: stop()
        except Exception: pass


app = FastAPI(title="AI Project Architect & Build Companion", lifespan=lifespan)

# Templates and static files
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.mount("/advisory/static", StaticFiles(directory=str(APP_DIR / "advisory" / "static")), name="advisory_static")

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


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle ValueError from execution scripts as user-friendly redirects."""
    from urllib.parse import quote

    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=f"{referer}?error={quote(str(exc))}", status_code=303)
