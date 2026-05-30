"""Scheduler entrypoint — runs the background ticker with leader election.

Single-active per host; multiple replicas yield one leader and warm
standbys (via distributed_lock leadership lease).

Usage:
    python -m execution.ops_platform.scheduler_main
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from execution.ops_platform import scheduler

logging.basicConfig(level=logging.INFO,
                      format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ops_scheduler")


_STOP = False


def _handle_signal(signum, frame):
    global _STOP
    _STOP = True


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    scheduler.start_background_ticker(interval_seconds=10)
    logger.info("scheduler ticker started")
    while not _STOP:
        time.sleep(1)
    scheduler.stop_background_ticker()
    return 0


if __name__ == "__main__":
    sys.exit(main())
