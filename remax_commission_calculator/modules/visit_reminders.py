"""Periodic visit reminder dispatch. One-shot; safe for Railway Cron."""

from __future__ import annotations

import logging
from datetime import timedelta

from modules.database.agent_tasks_repository import (
    STATUS_PENDING,
    list_agent_tasks,
)
from modules.database.organizations_repository import get_organizations
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.notifications_service import send_user_notification
from modules.organization_time import (
    format_local_time,
    now_utc,
    organization_timezone,
    to_utc_iso,
)


logger = logging.getLogger(__name__)

WINDOW_START_MINUTES = 25
WINDOW_END_MINUTES = 35
VISIT_TYPE = "visit"


def visit_reminder_event_key(task_id, due_at):
    stamp = str(due_at or "").replace(" ", "T")
    return f"agenda_visit_{int(task_id)}_30m_{stamp}"


def _org_language(organization_id):
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )

    settings = get_organization_settings(organization_id) or {}
    return settings.get("default_language") or "es"


def _visit_address(task):
    return (
        (task.get("property_address") or "").strip()
        or (task.get("formatted_address") or "").strip()
        or (task.get("title") or "").strip()
        or (task.get("contact_name") or "").strip()
        or "—"
    )


def dispatch_due_visit_reminders(organization_id, *, now=None):
    """
    Notify assigned agents of pending visits due in ~30 minutes.

    Window is +25 to +35 minutes from ``now`` (UTC). Idempotent via
    ``event_key`` that includes the current ``due_at``.
    """
    instant = now or now_utc()
    due_from = to_utc_iso(instant + timedelta(minutes=WINDOW_START_MINUTES))
    due_to = to_utc_iso(
        instant + timedelta(minutes=WINDOW_END_MINUTES, seconds=1)
    )
    visits = list_agent_tasks(
        organization_id,
        statuses=(STATUS_PENDING,),
        task_type=VISIT_TYPE,
        due_from=due_from,
        due_to=due_to,
        limit=200,
    )
    language = _org_language(organization_id)
    tz = organization_timezone(organization_id)
    dispatched = 0
    skipped = 0

    for task in visits:
        task_id = task.get("id")
        agent_id = task.get("agent_id")
        if task_id is None or agent_id is None:
            skipped += 1
            continue
        if (task.get("status") or STATUS_PENDING) != STATUS_PENDING:
            skipped += 1
            continue

        user = get_user_by_agent_id(agent_id, organization_id)
        if user is None:
            skipped += 1
            continue

        address = _visit_address(task)
        time_label = format_local_time(task.get("due_at"), tz) or "—"
        title = translate("push_visit_reminder_title", language)
        body = translate(
            "push_visit_reminder_body",
            language,
            address=address,
            time=time_label,
        )
        url = f"/agenda/{int(task_id)}/edit"
        event_key = visit_reminder_event_key(task_id, task.get("due_at"))
        try:
            result = send_user_notification(
                user["id"],
                organization_id,
                "visit_reminder",
                title,
                body,
                url,
                metadata={
                    "task_id": task_id,
                    "address": address,
                    "due_at": task.get("due_at"),
                },
                event_key=event_key,
                entity_type="agent_task",
                entity_id=task_id,
            )
        except Exception:
            logger.warning(
                "visit_reminder_failed organization_id=%s task_id=%s",
                organization_id,
                task_id,
                exc_info=True,
            )
            skipped += 1
            continue

        if result.get("created"):
            dispatched += 1
        else:
            skipped += 1

    return {
        "organization_id": organization_id,
        "candidates": len(visits),
        "dispatched": dispatched,
        "skipped": skipped,
        "due_from": due_from,
        "due_to": due_to,
    }


def dispatch_due_visit_reminders_all(*, now=None):
    results = []
    for organization in get_organizations():
        if not organization.get("is_active", True):
            continue
        try:
            results.append(
                dispatch_due_visit_reminders(organization["id"], now=now)
            )
        except Exception:
            logger.warning(
                "visit_reminder_org_failed organization_id=%s",
                organization.get("id"),
                exc_info=True,
            )
    return results
