"""One-shot notification jobs. Safe for Railway Cron or a dedicated worker."""

from __future__ import annotations

import logging

from modules.organization_time import now_utc, to_utc_iso


logger = logging.getLogger(__name__)


def run_notification_jobs(*, now=None, source="cron"):
    """Scan every active org for due agenda reminders and overdue tasks."""
    instant = now or now_utc()
    from modules.task_overdue import dispatch_overdue_tasks_all
    from modules.visit_reminders import dispatch_due_visit_reminders_all

    agenda = dispatch_due_visit_reminders_all(now=instant)
    overdue = dispatch_overdue_tasks_all(now=instant)
    purged = _purge_push_subscriptions(instant)
    summary = {
        "now": to_utc_iso(instant),
        "source": source,
        "agenda_orgs": len(agenda),
        "agenda_created": sum(item.get("notifications_created") or 0 for item in agenda),
        "agenda_candidates": sum(item.get("candidates_found") or 0 for item in agenda),
        "overdue_orgs": len(overdue),
        "overdue_created": sum(item.get("dispatched") or 0 for item in overdue),
        "agenda": agenda,
        "overdue": overdue,
        "push_subscriptions_purged": purged,
    }
    logger.info(
        "notification_jobs_ran source=%s agenda_created=%s overdue_created=%s "
        "push_subscriptions_purged=%s",
        source,
        summary["agenda_created"],
        summary["overdue_created"],
        purged,
    )
    return summary


def _purge_push_subscriptions(instant):
    from modules.database.push_subscriptions_repository import (
        purge_inactive_push_subscriptions,
    )

    try:
        return purge_inactive_push_subscriptions(now=instant)
    except Exception:
        logger.warning("push_subscription_purge_failed", exc_info=True)
        return 0
