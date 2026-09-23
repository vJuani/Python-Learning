"""Out-of-process notification dispatcher. Do not run this loop inside Gunicorn.

Only runs when it is the configured ``NOTIFICATION_DISPATCHER``:

Railway Cron (``NOTIFICATION_DISPATCHER=cron``):
    python notification_worker.py

Dedicated worker service (``NOTIFICATION_DISPATCHER=worker``):
    python notification_worker.py --loop

See ``modules/notifications/dispatcher.py`` for the other strategies.
"""

from __future__ import annotations

import argparse
import logging
import time

from modules.config import load_dotenv_file
from modules.database import create_tables
from modules.notifications.dispatcher import (
    DISPATCHER_CRON,
    DISPATCHER_WORKER,
    configured_dispatcher,
    dispatcher_allows,
)
from modules.notifications.jobs import run_notification_jobs


logger = logging.getLogger(__name__)
DEFAULT_INTERVAL_SECONDS = 5 * 60


def build_parser():
    parser = argparse.ArgumentParser(
        description="Dispatch JRH notifications (agenda reminders and overdue tasks).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep scanning every 5 minutes. Use only in a dedicated Railway worker.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Loop interval in seconds (default 300).",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    load_dotenv_file()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    strategy = DISPATCHER_WORKER if args.loop else DISPATCHER_CRON
    if not dispatcher_allows(strategy):
        print(
            f"notification_worker skipped: NOTIFICATION_DISPATCHER="
            f"{configured_dispatcher()} (this entry point is '{strategy}')"
        )
        return 0
    create_tables()
    if not args.loop:
        summary = run_notification_jobs(source="worker")
        print(
            "agenda_created={agenda_created} overdue_created={overdue_created}".format(
                **summary
            )
        )
        return 0

    logger.info("notification_worker_loop_started interval=%s", args.interval)
    while True:
        try:
            run_notification_jobs(source="worker")
        except Exception:
            logger.warning("notification_worker_tick_failed", exc_info=True)
        time.sleep(max(15, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
