"""Periodic overdue-task dispatch. One-shot; safe for Railway Cron."""

from __future__ import annotations

import logging

from modules.database.agent_tasks_repository import (
    STATUS_PENDING,
    list_agent_tasks,
)
from modules.database.organizations_repository import get_organizations
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.notifications_service import send_user_notification
from modules.organization_time import now_utc, to_utc_iso


logger = logging.getLogger(__name__)


def overdue_task_event_key(task_id, due_at):
    stamp = str(due_at or "").replace(" ", "T")
    return f"task_{int(task_id)}_overdue_{stamp}"


def _org_language(organization_id):
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )

    settings = get_organization_settings(organization_id) or {}
    return settings.get("default_language") or "es"


def _task_label(task):
    title = (task.get("title") or "").strip()
    if title:
        return title
    contact = (task.get("contact_name") or "").strip()
    if contact:
        return contact
    return "—"


def dispatch_overdue_tasks(organization_id, *, now=None):
    """
    Notify assigned agents of pending tasks whose due_at is in the past.

    Idempotent via event_key that includes the current due_at, so a
    rescheduled task can notify again if it becomes overdue later.
    """
    instant = now or now_utc()
    due_to = to_utc_iso(instant)
    tasks = list_agent_tasks(
        organization_id,
        statuses=(STATUS_PENDING,),
        due_to=due_to,
        order="asc",
        limit=200,
    )
    language = _org_language(organization_id)
    dispatched = 0
    skipped = 0

    for task in tasks:
        task_id = task.get("id")
        agent_id = task.get("agent_id")
        if task_id is None or agent_id is None:
            skipped += 1
            continue
        if (task.get("status") or STATUS_PENDING) != STATUS_PENDING:
            skipped += 1
            continue
        if not task.get("due_at"):
            skipped += 1
            continue
        if (task.get("task_type") or "") == "visit":
            skipped += 1
            continue

        user = get_user_by_agent_id(agent_id, organization_id)
        if user is None:
            skipped += 1
            continue

        label = _task_label(task)
        title = translate("push_task_overdue_title", language)
        body = translate("push_task_overdue_body", language, title=label)
        url = f"/agenda/{int(task_id)}/edit"
        event_key = overdue_task_event_key(task_id, task.get("due_at"))
        try:
            result = send_user_notification(
                user["id"],
                organization_id,
                "task_overdue",
                title,
                body,
                url,
                metadata={
                    "task_id": task_id,
                    "due_at": task.get("due_at"),
                    "task_title": label,
                },
                event_key=event_key,
                entity_type="agent_task",
                entity_id=task_id,
            )
        except Exception:
            logger.warning(
                "task_overdue_failed organization_id=%s task_id=%s",
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
        "candidates": len(tasks),
        "dispatched": dispatched,
        "skipped": skipped,
        "due_to": due_to,
    }


def dispatch_overdue_tasks_all(*, now=None):
    results = []
    for organization in get_organizations():
        if not organization.get("is_active", True):
            continue
        try:
            results.append(dispatch_overdue_tasks(organization["id"], now=now))
        except Exception:
            logger.warning(
                "task_overdue_org_failed organization_id=%s",
                organization.get("id"),
                exc_info=True,
            )
    return results
