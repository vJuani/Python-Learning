"""Single source of truth for which process dispatches scheduled notifications.

Exactly one strategy may run at a time. Every entry point (Gunicorn thread,
``notification_worker.py``, ``dispatch_visit_reminders.py`` and the HTTP tick)
asks ``dispatcher_allows`` before scanning, so enabling a second one by
accident is a no-op instead of a race.

``NOTIFICATION_DISPATCHER``:
    inprocess  (default) daemon thread inside the single Gunicorn worker
    worker     ``python notification_worker.py [--loop]`` in its own service
    cron       ``python dispatch_visit_reminders.py`` / ``notification_worker.py``
               launched by Railway Cron
    http       ``POST /internal/jobs/notifications/tick`` from an external cron
    off        nothing dispatches automatically
"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)

DISPATCHER_INPROCESS = "inprocess"
DISPATCHER_WORKER = "worker"
DISPATCHER_CRON = "cron"
DISPATCHER_HTTP = "http"
DISPATCHER_OFF = "off"

VALID_DISPATCHERS = (
    DISPATCHER_INPROCESS,
    DISPATCHER_WORKER,
    DISPATCHER_CRON,
    DISPATCHER_HTTP,
    DISPATCHER_OFF,
)
DEFAULT_DISPATCHER = DISPATCHER_INPROCESS


def configured_dispatcher():
    raw = (os.environ.get("NOTIFICATION_DISPATCHER") or "").strip().lower()
    if not raw:
        return DEFAULT_DISPATCHER
    if raw not in VALID_DISPATCHERS:
        logger.warning(
            "notification_dispatcher_invalid value=%s fallback=%s",
            raw,
            DISPATCHER_OFF,
        )
        return DISPATCHER_OFF
    return raw


def dispatcher_allows(strategy):
    """True only when ``strategy`` is the configured dispatcher."""
    active = configured_dispatcher()
    allowed = active == strategy
    if not allowed:
        logger.info(
            "notification_dispatcher_skipped strategy=%s active=%s",
            strategy,
            active,
        )
    return allowed
