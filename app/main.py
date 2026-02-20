"""FastAPI application for AI Project Architect & Build Companion."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.routers import (
    auto_build,
    chapter_build,
    chat,
    feature_discovery,
    final_assembly,
    generate,
    idea_intake,
    outline_approval,
    outline_generation,
    projects,
    quality_gates,
)

APP_DIR = Path(__file__).parent

app = FastAPI(title="AI Project Architect & Build Companion")

# Templates and static files
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

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
app.include_router(generate.router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle ValueError from execution scripts as user-friendly redirects."""
    from urllib.parse import quote

    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=f"{referer}?error={quote(str(exc))}", status_code=303)
