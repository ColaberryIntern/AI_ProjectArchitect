"""Daily scheduler for the productivity & AI-leverage report.

Fires every weekday morning (05:30 America/Chicago by default = 5:30 AM CST/CDT)
and runs runner.run(), which writes the HTML to disk and, when
PRODUCTIVITY_REPORT_DELIVERY=1, emails it to the recipients in
config/report_recipients.json.

Same lifecycle shape as execution/products/pilot/scheduler.py: started/stopped
from app/main.py lifespan, idempotent, failures non-fatal (the HTML on disk is
the source of truth). Delivery gating lives in delivery.py, not here.

Schedule is overridable via env for prod tuning:
    PRODUCTIVITY_REPORT_TZ     (default "America/Chicago" — Central, auto CST/CDT)
    PRODUCTIVITY_REPORT_HOUR   (default 5)
    PRODUCTIVITY_REPORT_MINUTE (default 30)
    PRODUCTIVITY_REPORT_DOW    (default "mon-fri")
"""
from __future__ import annotations

import logging
import os
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import runner

logger = logging.getLogger(__name__)

JOB_ID = "productivity_daily_report"

_scheduler: BackgroundScheduler | None = None


def _fire() -> None:
    """Job target: run the report, log the outcome."""
    result = runner.run()
    logger.info(
        "productivity_daily_report fired: status=%s operators=%d team_verdict=%s "
        "delivery=%s path=%s",
        result.status, result.operators, result.verdict,
        result.delivery_status, result.output_path,
    )


def start_scheduler() -> BackgroundScheduler | None:
    """Start the daily report cron scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = ZoneInfo(os.environ.get("PRODUCTIVITY_REPORT_TZ", "America/Chicago"))
    hour = int(os.environ.get("PRODUCTIVITY_REPORT_HOUR", "5"))
    minute = int(os.environ.get("PRODUCTIVITY_REPORT_MINUTE", "30"))
    dow = os.environ.get("PRODUCTIVITY_REPORT_DOW", "mon-fri")

    sched = BackgroundScheduler(timezone=tz)
    sched.add_job(
        _fire,
        CronTrigger(day_of_week=dow, hour=hour, minute=minute, timezone=tz),
        id=JOB_ID, replace_existing=True, max_instances=1,
    )
    sched.start()
    _scheduler = sched
    logger.info(
        "productivity report scheduler started — %s %02d:%02d %s, delivery_enabled=%s",
        dow, hour, minute, tz, os.environ.get("PRODUCTIVITY_REPORT_DELIVERY", "0") == "1",
    )
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            logger.warning("productivity report scheduler shutdown failed", exc_info=True)
        _scheduler = None
