"""In-process notification loop for the single Gunicorn worker.

This is the production dispatcher (``NOTIFICATION_DISPATCHER=inprocess``,
the default). It only starts when that is the configured strategy, so a
worker/cron/HTTP tick can never run alongside it.

Safe with ``--workers 1``. Overlapping ticks during a deploy are still
idempotent via ``event_key``. Disable with ``VISIT_REMINDER_SCHEDULER=0``
or by selecting another dispatcher.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import timedelta

from modules.organization_time import now_utc, to_utc_iso


logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 5 * 60
FIRST_DELAY_SECONDS = 15

_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD = None
_STATE = {
    "alive": False,
    "started_at": None,
    "next_run": None,
    "last_tick_at": None,
    "last_error": None,
}


def _env_enabled():
    from modules.notifications.dispatcher import (
        DISPATCHER_INPROCESS,
        dispatcher_allows,
    )

    raw = (os.environ.get("VISIT_REMINDER_SCHEDULER") or "1").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return dispatcher_allows(DISPATCHER_INPROCESS)


def inprocess_scheduler_state():
    return dict(_STATE)


def _set_next_run(seconds):
    _STATE["next_run"] = to_utc_iso(now_utc() + timedelta(seconds=seconds))


def _tick():
    from modules.notifications.jobs import run_notification_jobs

    run_notification_jobs(source="inprocess")


def _loop(interval_seconds, first_delay_seconds):
    _STATE["alive"] = True
    _STATE["started_at"] = to_utc_iso(now_utc())
    _STATE["last_error"] = None
    logger.info(
        "agenda_reminder_inprocess_started interval_seconds=%s "
        "first_delay_seconds=%s process_id=%s",
        interval_seconds,
        first_delay_seconds,
        os.getpid(),
    )
    _set_next_run(first_delay_seconds)
    if _STOP.wait(first_delay_seconds):
        _STATE["alive"] = False
        return
    while not _STOP.is_set():
        _STATE["last_tick_at"] = to_utc_iso(now_utc())
        try:
            _tick()
            _STATE["last_error"] = None
        except Exception as error:
            _STATE["last_error"] = str(error)
            logger.warning("agenda_reminder_inprocess_tick_failed", exc_info=True)
        _set_next_run(interval_seconds)
        if _STOP.wait(interval_seconds):
            break
    _STATE["alive"] = False
    logger.info("agenda_reminder_inprocess_stopped process_id=%s", os.getpid())


def start_visit_reminder_scheduler(
    *,
    enabled=None,
    interval_seconds=INTERVAL_SECONDS,
    first_delay_seconds=FIRST_DELAY_SECONDS,
):
    """Start the daemon thread once per process. Returns True if started."""
    if enabled is None:
        enabled = _env_enabled()
    if not enabled:
        logger.info("agenda_reminder_inprocess_disabled")
        return False
    with _LOCK:
        global _THREAD
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_loop,
            args=(int(interval_seconds), int(first_delay_seconds)),
            name="visit-reminders",
            daemon=True,
        )
        _THREAD.start()
        _STATE["alive"] = True
        return True


def stop_visit_reminder_scheduler(timeout=2):
    """Test helper. Production never needs this."""
    _STOP.set()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    _STATE["alive"] = False
    _STATE["next_run"] = None
