"""APScheduler wiring for use-case generation.

Two jobs:
    bootstrap_50  — runs once on app startup if the use-case count is
                       below the bootstrap threshold. Generates up to
                       LIBRARY_UC_BOOTSTRAP_COUNT (default 50) one-at-a-time.
                       Idempotent: skips if already past threshold.

    daily_two     — runs every day at 03:00 UTC; generates
                       LIBRARY_UC_DAILY_COUNT (default 2) new use cases.

Schedule + count are configurable via env vars so an operator can dial
them without code changes. APScheduler is optional — if not installed,
both functions log + return gracefully so the app still starts.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from . import use_cases
from .use_case_generator import generate_many

logger = logging.getLogger(__name__)

_scheduler: Any = None


# ── Config ──────────────────────────────────────────────────────────


def _bootstrap_count() -> int:
    return int(os.environ.get("LIBRARY_UC_BOOTSTRAP_COUNT", "50"))


def _daily_count() -> int:
    return int(os.environ.get("LIBRARY_UC_DAILY_COUNT", "2"))


def _daily_hour() -> int:
    return int(os.environ.get("LIBRARY_UC_DAILY_HOUR", "3"))


def _daily_minute() -> int:
    return int(os.environ.get("LIBRARY_UC_DAILY_MINUTE", "0"))


def _workspace() -> str:
    return os.environ.get("LIBRARY_UC_WORKSPACE", "global")


# ── Jobs ────────────────────────────────────────────────────────────


def run_bootstrap() -> dict[str, Any]:
    """Generate up to bootstrap_count use cases if we're below threshold.

    Safe to call repeatedly — only fills the gap up to the configured target.
    """
    ws = _workspace()
    target = _bootstrap_count()
    existing = use_cases.count(ws)
    needed = max(0, target - existing)
    if needed == 0:
        logger.info(f"[use-case bootstrap] {existing}/{target} already present; skipping.")
        return {"status": "skipped", "existing": existing, "target": target, "generated": 0}

    logger.info(f"[use-case bootstrap] generating {needed} new use cases…")
    generated = generate_many(ws, needed, creator="scheduler:bootstrap")
    logger.info(f"[use-case bootstrap] done — generated {len(generated)}.")
    return {"status": "generated", "existing": existing, "target": target,
              "generated": len(generated),
              "ids": [u.use_case_id for u in generated]}


def run_daily() -> dict[str, Any]:
    ws = _workspace()
    n = _daily_count()
    logger.info(f"[use-case daily] generating {n} use cases…")
    generated = generate_many(ws, n, creator="scheduler:daily")
    logger.info(f"[use-case daily] done — {len(generated)} new.")
    return {"status": "generated", "generated": len(generated),
              "ids": [u.use_case_id for u in generated]}


# ── Wiring (optional — APScheduler may not be installed) ────────────


def start_scheduler():
    """Start the use-case scheduler. No-op if APScheduler is absent."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.info("APScheduler not installed; use-case scheduler disabled.")
        return

    if _scheduler is not None:
        return  # already running

    _scheduler = BackgroundScheduler(daemon=True)
    # Daily at LIBRARY_UC_DAILY_HOUR:MINUTE UTC
    _scheduler.add_job(
        run_daily,
        trigger=CronTrigger(hour=_daily_hour(), minute=_daily_minute()),
        id="library_uc_daily",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(
        f"[use-case scheduler] started; daily at "
        f"{_daily_hour():02d}:{_daily_minute():02d} UTC, "
        f"generating {_daily_count()} use case(s)."
    )

    # Bootstrap in a background thread — NEVER block app startup. Each
    # generated case is an LLM call; with bootstrap_count=50 the first
    # cold-start would otherwise hang uvicorn for minutes.
    import threading

    def _bg_bootstrap():
        try:
            run_bootstrap()
        except Exception:
            logger.warning("[use-case bootstrap] failed", exc_info=True)

    threading.Thread(target=_bg_bootstrap, daemon=True,
                          name="library-uc-bootstrap").start()


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
