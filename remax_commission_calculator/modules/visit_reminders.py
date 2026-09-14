"""Periodic visit reminder dispatch. One-shot; safe for Railway Cron.

This module is NOT started by Gunicorn. Production must invoke
``python dispatch_visit_reminders.py`` on a Cron schedule (every 5 min).
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from modules.database.agent_tasks_repository import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    list_agent_tasks,
)
from modules.database.notifications_repository import find_notification_by_event_key
from modules.database.organizations_repository import get_organizations
from modules.database.user_notification_preferences_repository import (
    is_push_category_enabled,
)
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.notifications_service import send_user_notification
from modules.organization_time import (
    UTC,
    format_local_time,
    now_utc,
    organization_timezone,
    parse_utc_iso,
    to_utc_iso,
)


logger = logging.getLogger(__name__)

WINDOW_START_MINUTES = 25
WINDOW_END_MINUTES = 35
QUERY_PAD_MINUTES = 5
CRON_INTERVAL_MINUTES = 5
CRON_INTERVAL = "*/5 * * * *"
VISIT_TYPE = "visit"
VISIT_TYPE_ALIASES = frozenset(
    {
        "visit",
        "property_visit",
        "showing",
        "visita",
        "visita_propiedad",
    }
)
ACTIVE_STATUSES = frozenset({STATUS_PENDING})
SKIP_STATUSES = frozenset({STATUS_COMPLETED, STATUS_CANCELLED})
KIND = "visit_reminder"
PREF_KEY = "push_visit_reminders"


def normalize_visit_task_type(value):
    raw = str(value or "").strip().lower()
    if raw in VISIT_TYPE_ALIASES:
        return VISIT_TYPE
    return raw


def is_visit_task_type(value):
    return normalize_visit_task_type(value) == VISIT_TYPE


def visit_reminder_event_key(task_id, due_at):
    stamp = str(due_at or "").replace(" ", "T")
    return f"agenda_visit_{int(task_id)}_30m_{stamp}"


def visit_reminder_event_key_prefix(task_id):
    return f"agenda_visit_{int(task_id)}_30m"


def aware_utc(value):
    """Normalize a datetime or stored ISO timestamp to aware UTC."""
    if value is None:
        return None
    if hasattr(value, "tzinfo"):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC, microsecond=0)
        return value.astimezone(UTC).replace(microsecond=0)
    return parse_utc_iso(value)


def reminder_window(now=None):
    """
    Inclusive 25–35 minute window ahead of ``now``.

    Clock is UTC. Agenda ``due_at`` is naive UTC ISO; UI times are
    converted from the organization timezone before insert.
    """
    instant = aware_utc(now) or now_utc()
    window_start = instant + timedelta(minutes=WINDOW_START_MINUTES)
    window_end = instant + timedelta(minutes=WINDOW_END_MINUTES)
    return instant, window_start, window_end


def minutes_until(due_at, now):
    due = aware_utc(due_at)
    instant = aware_utc(now)
    if due is None or instant is None:
        return None
    return (due - instant).total_seconds() / 60.0


def in_reminder_window(due_at, now):
    delta = minutes_until(due_at, now)
    if delta is None:
        return False
    return WINDOW_START_MINUTES <= delta <= WINDOW_END_MINUTES


def resolve_visit_recipient(task, organization_id):
    """Map agenda ``agent_id`` to the Agent login user. Never treat ids as equal."""
    agent_id = task.get("agent_id")
    if agent_id is None:
        return None
    return get_user_by_agent_id(agent_id, organization_id)


def log_scheduler_started(*, timezone_name, now=None):
    instant = aware_utc(now) or now_utc()
    next_run = to_utc_iso(instant + timedelta(minutes=CRON_INTERVAL_MINUTES))
    logger.info(
        "agenda_reminder_scheduler_started timezone=%s interval=%s "
        "process_id=%s next_run=%s",
        timezone_name,
        CRON_INTERVAL,
        os.getpid(),
        next_run,
    )
    return {
        "timezone": timezone_name,
        "interval": CRON_INTERVAL,
        "process_id": os.getpid(),
        "next_run": next_run,
    }


def _org_language(organization_id):
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )

    settings = get_organization_settings(organization_id) or {}
    return settings.get("default_language") or "es"


def _org_timezone_name(organization_id):
    from modules.database.organization_settings_repository import (
        DEFAULT_TIMEZONE,
        get_organization_settings,
    )

    settings = get_organization_settings(organization_id) or {}
    return settings.get("timezone") or DEFAULT_TIMEZONE


def _visit_address(task):
    return (
        (task.get("property_address") or "").strip()
        or (task.get("formatted_address") or "").strip()
        or (task.get("title") or "").strip()
        or (task.get("contact_name") or "").strip()
        or "—"
    )


def _list_window_candidates(organization_id, instant):
    """SQL prefilter (padded), then exact 25–35 minute datetime filter."""
    query_from = to_utc_iso(
        instant + timedelta(minutes=WINDOW_START_MINUTES - QUERY_PAD_MINUTES)
    )
    query_to = to_utc_iso(
        instant + timedelta(minutes=WINDOW_END_MINUTES + QUERY_PAD_MINUTES)
    )
    rows = list_agent_tasks(
        organization_id,
        statuses=(STATUS_PENDING,),
        due_from=query_from,
        due_to=query_to,
        order="asc",
        limit=200,
    )
    return [
        task
        for task in rows
        if is_visit_task_type(task.get("task_type"))
        and in_reminder_window(task.get("due_at"), instant)
    ]


def scan_visit_reminders(organization_id, *, now=None, send=True):
    """
    Scan one organization for visits in the 30-minute reminder window.

    ``send=False`` is a dry-run: no notification row, no push.
    """
    instant, window_start, window_end = reminder_window(now)
    timezone_name = _org_timezone_name(organization_id)
    tz = organization_timezone(organization_id)
    language = _org_language(organization_id)
    visits = _list_window_candidates(organization_id, instant)

    logger.info(
        "agenda_reminder_scan now=%s timezone=%s window_start=%s "
        "window_end=%s candidate_count=%s organization_id=%s",
        to_utc_iso(instant),
        timezone_name,
        to_utc_iso(window_start),
        to_utc_iso(window_end),
        len(visits),
        organization_id,
    )

    candidates = []
    notifications_created = 0
    push_sent = 0
    skipped = 0

    for task in visits:
        record = _process_candidate(
            organization_id,
            task,
            instant=instant,
            tz=tz,
            language=language,
            send=send,
        )
        candidates.append(record)
        if record.get("created"):
            notifications_created += 1
        if record.get("skipped_reason"):
            skipped += 1
        push_sent += int(record.get("sent_count") or 0)
        if record.get("dispatched"):
            logger.info(
                "agenda_reminder_dispatched event_id=%s user_id=%s "
                "sent_count=%s failed_count=%s",
                record.get("event_id"),
                record.get("user_id"),
                record.get("sent_count") or 0,
                record.get("failed_count") or 0,
            )

    return {
        "organization_id": organization_id,
        "now": to_utc_iso(instant),
        "timezone": timezone_name,
        "window_start": to_utc_iso(window_start),
        "window_end": to_utc_iso(window_end),
        "due_from": to_utc_iso(window_start),
        "due_to": to_utc_iso(window_end),
        "candidate_count": len(visits),
        "candidates_found": len(visits),
        "notifications_created": notifications_created,
        "dispatched": notifications_created,
        "push_sent": push_sent,
        "skipped": skipped,
        "candidates": candidates,
    }


def _process_candidate(organization_id, task, *, instant, tz, language, send):
    task_id = task.get("id")
    status = task.get("status") or STATUS_PENDING
    task_type = task.get("task_type")
    due_at = task.get("due_at")
    event_key = visit_reminder_event_key(task_id, due_at) if task_id else None
    already = (
        find_notification_by_event_key(organization_id, event_key)
        if event_key
        else None
    )
    already_sent = already is not None
    user = resolve_visit_recipient(task, organization_id)
    user_id = None if user is None else user.get("id")
    preference_enabled = (
        is_push_category_enabled(organization_id, user_id, PREF_KEY)
        if user_id is not None
        else False
    )
    skipped_reason = None
    if task_id is None:
        skipped_reason = "missing_task_id"
    elif user_id is None:
        skipped_reason = "no_recipient"
    elif status in SKIP_STATUSES or status not in ACTIVE_STATUSES:
        skipped_reason = f"status_{status}"
    elif already_sent:
        skipped_reason = "already_sent"

    logger.info(
        "agenda_reminder_candidate event_id=%s user_id=%s organization_id=%s "
        "starts_at=%s status=%s type=%s dedupe_key=%s preference_enabled=%s "
        "already_sent=%s",
        task_id,
        user_id,
        organization_id,
        due_at,
        status,
        task_type,
        event_key,
        int(bool(preference_enabled)),
        int(already_sent),
    )

    empty_push = {"sent_count": 0, "failed_count": 0, "push_targets_count": 0}
    record = {
        "event_id": task_id,
        "user_id": user_id,
        "organization_id": organization_id,
        "starts_at": due_at,
        "status": status,
        "type": task_type,
        "dedupe_key": event_key,
        "preference_enabled": preference_enabled,
        "already_sent": already_sent,
        "skipped_reason": skipped_reason,
        "created": False,
        "dispatched": False,
        "sent_count": 0,
        "failed_count": 0,
    }
    if skipped_reason or not send:
        if not send and skipped_reason is None:
            record["skipped_reason"] = "dry_run"
        return record

    address = _visit_address(task)
    time_label = format_local_time(due_at, tz) or "—"
    title = translate("push_visit_reminder_title", language)
    body = translate(
        "push_visit_reminder_body",
        language,
        address=address,
        time=time_label,
    )
    url = f"/agenda/{int(task_id)}/edit"
    try:
        result = send_user_notification(
            user_id,
            organization_id,
            KIND,
            title,
            body,
            url,
            metadata={
                "task_id": task_id,
                "address": address,
                "due_at": due_at,
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
        record["skipped_reason"] = "send_failed"
        return record

    push = result.get("push") or empty_push
    created = bool(result.get("created"))
    record.update(
        {
            "created": created,
            "dispatched": created,
            "sent_count": int(push.get("sent_count") or 0),
            "failed_count": int(push.get("failed_count") or 0),
            "already_sent": (not created) or already_sent,
            "skipped_reason": None if created else "already_sent",
            "preference_enabled": preference_enabled,
        }
    )
    if created and not preference_enabled:
        record["skipped_reason"] = "preference_off"
    return record


def dispatch_due_visit_reminders(organization_id, *, now=None):
    """
    Notify assigned agents of pending visits due in ~30 minutes.

    Window is +25 to +35 minutes from ``now`` (UTC). Idempotent via
    ``event_key`` that includes the current ``due_at``.
    """
    return scan_visit_reminders(organization_id, now=now, send=True)


def dispatch_due_visit_reminders_all(*, now=None):
    from modules.database.organization_settings_repository import DEFAULT_TIMEZONE

    log_scheduler_started(timezone_name=DEFAULT_TIMEZONE, now=now)
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


def clear_visit_reminder_dedupe(organization_id, task_id):
    """QA helper: drop in-app visit reminders for one task so it can fire again."""
    from modules.database.notifications_repository import (
        delete_notifications_for_entity,
    )

    return delete_notifications_for_entity(
        organization_id,
        kind=KIND,
        entity_type="agent_task",
        entity_id=task_id,
    )
