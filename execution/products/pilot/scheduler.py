"""Calendar-driven scheduler for the Karun + Kes weekly dashboards.

Cron schedule (per `directives/pilot-weekly-cadence.md`):
    - Karun 1:1 = Monday 09:00 ET  → fire dash 30 min before = 08:30 ET
    - Kes 1:1   = Monday 09:30 ET  → fire dash 30 min before = 09:00 ET

The actual rendering happens in `dash_runner.run(dri)` — this module
owns scheduling + lifecycle only. Delivery (Gmail push to Ali + DRI
inboxes) is gated by PILOT_DASH_DELIVERY env; default OFF until the
recipient list + Gmail token are confirmed live.

Started/stopped from app/main.py lifespan, same pattern as
execution/products/ops/scheduler.py.

Per BC tickets Karun 3 (9953889285) + Kes 3 (9953889413) — the
"4-hour MVP from the Alden plan email."
"""
from __future__ import annotations

import logging
import os
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import dash_runner

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
KARUN_JOB_ID = "pilot_dash_karun"
KES_JOB_ID = "pilot_dash_kes"
# Delivery via Gmail is opt-in; without explicit enable, the dashboard
# writes to disk only (safe default for dev + prod-before-recipient-
# confirmation). When enabled, scheduler.py will POST the HTML to the
# Gmail MCP server with the recipient list from config.
DELIVERY_ENABLED = os.environ.get("PILOT_DASH_DELIVERY", "0") == "1"

_scheduler: BackgroundScheduler | None = None


def _fire_karun() -> None:
    """Job target: render the Karun dashboard, log the result."""
    result = dash_runner.run("karun")
    logger.info(
        "pilot_dash_karun fired: status=%s placeholder=%s critic_failures=%d path=%s",
        result.status, result.placeholder,
        len(result.critic_failures), result.output_path,
    )
    if DELIVERY_ENABLED and result.status == "ok":
        _deliver(result)


def _fire_kes() -> None:
    """Job target: render the Kes dashboard, log the result."""
    result = dash_runner.run("kes")
    logger.info(
        "pilot_dash_kes fired: status=%s placeholder=%s critic_failures=%d path=%s",
        result.status, result.placeholder,
        len(result.critic_failures), result.output_path,
    )
    if DELIVERY_ENABLED and result.status == "ok":
        _deliver(result)


def _deliver(result: dash_runner.DashResult) -> None:
    """Stub for Gmail delivery — wires up once recipient list is confirmed.

    Per the cadence directive, recipients are:
        - ali@colaberry.com
        - karun@colaberry.com (or kes@colaberry.com)

    Until Ali confirms the recipient addresses + the Gmail MCP token is
    stored in the vault under 'gmail_pilot_dash', this no-ops + logs.
    """
    logger.info("pilot_dash delivery placeholder: would email result for %s "
                  "(set PILOT_DASH_DELIVERY=1 + vault gmail_pilot_dash to enable)",
                  result.dri)


def start_scheduler() -> BackgroundScheduler | None:
    """Start the pilot dash cron scheduler. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    sched = BackgroundScheduler(timezone=ET)
    sched.add_job(
        _fire_karun,
        CronTrigger(day_of_week="mon", hour=8, minute=30, timezone=ET),
        id=KARUN_JOB_ID, replace_existing=True, max_instances=1,
    )
    sched.add_job(
        _fire_kes,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=ET),
        id=KES_JOB_ID, replace_existing=True, max_instances=1,
    )
    sched.start()
    _scheduler = sched
    logger.info(
        "pilot dash scheduler started — Karun Mon 08:30 ET, Kes Mon 09:00 ET, "
        "delivery_enabled=%s", DELIVERY_ENABLED,
    )
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            logger.warning("pilot dash scheduler shutdown failed", exc_info=True)
        _scheduler = None
