"""Worker entrypoint — long-running queue drainer.

Registers with worker_coordination, heartbeats every 10s, drains the queue
in a loop. Single-host multi-process safe via distributed_lock.

Usage:
    python -m execution.ops_platform.worker_main
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from execution.ops_platform import worker_coordination, workflow_runner

logging.basicConfig(level=logging.INFO,
                      format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ops_worker")


_STOP = False


def _handle_signal(signum, frame):
    global _STOP
    _STOP = True
    logger.info("worker received signal %s — draining", signum)


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    worker = worker_coordination.register(role="general", queues=["default"])
    logger.info("worker registered: %s", worker.worker_id)
    last_heartbeat = time.time()

    while not _STOP:
        try:
            workflow_runner.drain_queue_once(worker_id=worker.worker_id)
        except Exception:
            logger.warning("worker loop iteration failed", exc_info=True)
        if time.time() - last_heartbeat >= 10:
            worker_coordination.heartbeat(worker.worker_id)
            last_heartbeat = time.time()
        time.sleep(0.5)

    worker_coordination.drain(worker.worker_id)
    worker_coordination.stop(worker.worker_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
