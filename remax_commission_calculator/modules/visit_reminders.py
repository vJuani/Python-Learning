"""Visit reminder scan. Idempotent; safe to run from Cron or in-process.

Production Gunicorn (1 worker) starts ``visit_reminder_scheduler``.
``python dispatch_visit_reminders.py`` remains valid as an optional Cron.
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
from modules.notifications.catalog import PREF_AGENDA_REMINDERS
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
REMINDER_TOLERANCE_MINUTES = 5
DEFAULT_VISIT_REMINDER_MINUTES = 30
MAX_REMINDER_MINUTES = 120
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
PREF_KEY = PREF_AGENDA_REMINDERS


def normalize_visit_task_type(value):
    raw = str(value or "").strip().lower()
    if raw in VISIT_TYPE_ALIASES:
        return VISIT_TYPE
    return raw


def is_visit_task_type(value):
    return normalize_visit_task_type(value) == VISIT_TYPE


def resolved_reminder_minutes(task):
    raw = None if task is None else task.get("reminder_minutes")
    if raw is None or raw == "":
        if is_visit_task_type(None if task is None else task.get("task_type")):
            return DEFAULT_VISIT_REMINDER_MINUTES
        return None
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = None
    if minutes is None or minutes <= 0:
        if is_visit_task_type(None if task is None else task.get("task_type")):
            return DEFAULT_VISIT_REMINDER_MINUTES
        return None
    return minutes


def visit_reminder_event_key(task_id, due_at, reminder_minutes=None):
    minutes = (
        DEFAULT_VISIT_REMINDER_MINUTES
        if reminder_minutes is None
        else int(reminder_minutes)
    )
    stamp = str(due_at or "").replace(" ", "T")
    return f"agenda_event_{int(task_id)}_reminder_{minutes}m_{stamp}"


def visit_reminder_legacy_event_key(task_id, due_at):
    stamp = str(due_at or "").replace(" ", "T")
    return f"agenda_visit_{int(task_id)}_30m_{stamp}"


def visit_reminder_event_key_prefix(task_id):
    return f"agenda_event_{int(task_id)}_reminder_"


def aware_utc(value):
    """Normalize a datetime or stored ISO timestamp to aware UTC."""
    if value is None:
        return None
    if hasattr(value, "tzinfo"):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC, microsecond=0)
        return value.astimezone(UTC).replace(microsecond=0)
    return parse_utc_iso(value)


def reminder_window(now=None, reminder_minutes=None):
    """
    Inclusive tolerant window around the configured reminder.

    Default visit reminder is 30 minutes (25–35). Clock is UTC.
    Agenda ``due_at`` is naive UTC ISO; UI times are converted from
    the organization timezone before insert.
    """
    instant = aware_utc(now) or now_utc()
    target = (
        DEFAULT_VISIT_REMINDER_MINUTES
        if reminder_minutes is None
        else int(reminder_minutes)
    )
    window_start = instant + timedelta(minutes=target - REMINDER_TOLERANCE_MINUTES)
    window_end = instant + timedelta(minutes=target + REMINDER_TOLERANCE_MINUTES)
    return instant, window_start, window_end


def minutes_until(due_at, now):
    due = aware_utc(due_at)
    instant = aware_utc(now)
    if due is None or instant is None:
        return None
    return (due - instant).total_seconds() / 60.0


def in_reminder_window(due_at, now, reminder_minutes=None):
    delta = minutes_until(due_at, now)
    if delta is None:
        return False
    target = (
        DEFAULT_VISIT_REMINDER_MINUTES
        if reminder_minutes is None
        else int(reminder_minutes)
    )
    return (target - REMINDER_TOLERANCE_MINUTES) <= delta <= (
        target + REMINDER_TOLERANCE_MINUTES
    )


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
    """SQL prefilter (padded), then per-event reminder window."""
    query_from = to_utc_iso(instant - timedelta(minutes=QUERY_PAD_MINUTES))
    query_to = to_utc_iso(
        instant
        + timedelta(
            minutes=MAX_REMINDER_MINUTES + REMINDER_TOLERANCE_MINUTES + QUERY_PAD_MINUTES
        )
    )
    rows = list_agent_tasks(
        organization_id,
        statuses=(STATUS_PENDING,),
        due_from=query_from,
        due_to=query_to,
        order="asc",
        limit=200,
    )
    matches = []
    for task in rows:
        reminder = resolved_reminder_minutes(task)
        if reminder is None:
            continue
        if in_reminder_window(task.get("due_at"), instant, reminder):
            matches.append(task)
    return matches


def _list_nearby_tasks(organization_id, instant):
    """Pending tasks around now, including near-misses outside the 25–35 window."""
    query_from = to_utc_iso(instant - timedelta(minutes=30))
    query_to = to_utc_iso(instant + timedelta(minutes=120))
    return list_agent_tasks(
        organization_id,
        statuses=(STATUS_PENDING,),
        due_from=query_from,
        due_to=query_to,
        order="asc",
        limit=200,
    )


def _push_target_count(organization_id, user_id):
    if user_id is None:
        return 0
    from modules.database.push_subscriptions_repository import (
        list_active_push_subscriptions,
    )

    return len(list_active_push_subscriptions(organization_id, user_id))


def classify_visit_for_reminder(task, organization_id, instant):
    """Explain whether this task is a reminder candidate and why not."""
    task_id = task.get("id")
    status = task.get("status") or STATUS_PENDING
    task_type = task.get("task_type")
    due_at = task.get("due_at")
    minutes = minutes_until(due_at, instant)
    user = resolve_visit_recipient(task, organization_id)
    user_id = None if user is None else user.get("id")
    preference_enabled = (
        is_push_category_enabled(organization_id, user_id, PREF_KEY)
        if user_id is not None
        else False
    )
    reminder = resolved_reminder_minutes(task)
    event_key = (
        visit_reminder_event_key(task_id, due_at, reminder) if task_id and reminder else None
    )
    already = None
    if event_key:
        already = find_notification_by_event_key(organization_id, event_key)
        if already is None and reminder == DEFAULT_VISIT_REMINDER_MINUTES and task_id:
            already = find_notification_by_event_key(
                organization_id,
                visit_reminder_legacy_event_key(task_id, due_at),
            )
    already_sent = already is not None
    targets = _push_target_count(organization_id, user_id)

    skip_reason = None
    candidate = True
    if reminder is None:
        candidate = False
        skip_reason = "no_reminder_configured"
    elif status in SKIP_STATUSES or status not in ACTIVE_STATUSES:
        candidate = False
        skip_reason = f"status_{status}"
    elif minutes is None:
        candidate = False
        skip_reason = "invalid_due_at"
    elif not in_reminder_window(due_at, instant, reminder):
        low = reminder - REMINDER_TOLERANCE_MINUTES
        high = reminder + REMINDER_TOLERANCE_MINUTES
        candidate = False
        skip_reason = (
            f"outside_window minutes_until={round(minutes, 1)} "
            f"need={low}..{high}"
        )
    elif task_id is None:
        candidate = False
        skip_reason = "missing_task_id"
    elif user_id is None:
        skip_reason = "no_recipient"
    elif already_sent:
        skip_reason = "already_sent"
    elif not preference_enabled:
        skip_reason = "preference_off"
    elif targets == 0:
        skip_reason = "no_push_subscription"

    return {
        "event_id": task_id,
        "event_type": task_type,
        "event_status": status,
        "starts_at": due_at,
        "minutes_until": None if minutes is None else round(minutes, 1),
        "minutes_until_start": None if minutes is None else round(minutes, 1),
        "reminder_minutes": reminder,
        "assigned_agent_id": task.get("agent_id"),
        "assigned_user_id": user_id,
        "resolved_user_id": user_id,
        "push_visit_reminders": preference_enabled,
        "preference": preference_enabled,
        "dedupe": event_key,
        "dedupe_key": event_key,
        "already_sent": already_sent,
        "candidate": candidate,
        "skip_reason": skip_reason,
        "dispatcher_reason": skip_reason,
        "push_targets": targets,
        "push_targets_count": targets,
        "notification_created": False,
        "push_sent_count": 0,
        "push_failed_count": 0,
        "created": False,
        "dispatched": False,
        "sent": 0,
        "failed": 0,
        "sent_count": 0,
        "failed_count": 0,
        "user_id": user_id,
        "organization_id": organization_id,
        "status": status,
        "type": task_type,
        "preference_enabled": preference_enabled,
        "skipped_reason": skip_reason,
    }


def _pick_probe_agent(organization_id, current_user=None):
    """Prefer the current agent; else an org agent whose user has an active push."""
    if current_user and current_user.get("agent_id"):
        user = get_user_by_agent_id(current_user["agent_id"], organization_id)
        if user is not None:
            return current_user["agent_id"], user["id"]

    from modules.database.users_repository import get_users

    for user in get_users(organization_id):
        if not user.get("is_active") or user.get("agent_id") is None:
            continue
        if _push_target_count(organization_id, user["id"]) > 0:
            return user["agent_id"], user["id"]
    for user in get_users(organization_id):
        if user.get("is_active") and user.get("agent_id") is not None:
            return user["agent_id"], user["id"]
    return None, None


def ensure_probe_visit(organization_id, *, current_user=None, now=None):
    """Reuse an in-window pending visit or create one ~30 minutes ahead."""
    instant, _, _ = reminder_window(now)
    existing = _list_window_candidates(organization_id, instant)
    if existing:
        return existing[0], False
    agent_id, _user_id = _pick_probe_agent(organization_id, current_user)
    if agent_id is None:
        raise RuntimeError("no_agent_for_probe")
    from modules.database.agent_tasks_repository import create_agent_task

    due_at = to_utc_iso(instant + timedelta(minutes=DEFAULT_VISIT_REMINDER_MINUTES))
    task = create_agent_task(
        organization_id,
        agent_id,
        title="QA reminder visita",
        task_type=VISIT_TYPE,
        due_at=due_at,
        reminder_minutes=DEFAULT_VISIT_REMINDER_MINUTES,
        created_by_user_id=None if not current_user else current_user.get("id"),
    )
    return task, True


def scheduler_status(organization_id, *, now=None):
    from modules.database.visit_reminder_runs_repository import (
        get_last_visit_reminder_run,
    )
    from modules.visit_reminder_scheduler import inprocess_scheduler_state

    instant = aware_utc(now) or now_utc()
    last_cron = get_last_visit_reminder_run(organization_id, source="cron")
    last_qa = get_last_visit_reminder_run(organization_id, source="qa")
    last_run = None if last_cron is None else last_cron.get("ran_at")
    next_run = None
    running = False
    if last_run:
        parsed = parse_utc_iso(last_run)
        if parsed is not None:
            age_minutes = (instant - parsed).total_seconds() / 60.0
            running = age_minutes <= CRON_INTERVAL_MINUTES * 3
            next_run = to_utc_iso(
                parsed + timedelta(minutes=CRON_INTERVAL_MINUTES)
            )
    inprocess = inprocess_scheduler_state()
    alive = bool(inprocess.get("alive"))
    if alive:
        running = True
        next_run = inprocess.get("next_run") or next_run
    return {
        "automatic_running": running,
        "inprocess_alive": alive,
        "inprocess_started_at": inprocess.get("started_at"),
        "inprocess_last_tick_at": inprocess.get("last_tick_at"),
        "inprocess_last_error": inprocess.get("last_error"),
        "last_cron_run": last_run,
        "next_cron_run": next_run,
        "last_qa_run": None if last_qa is None else last_qa.get("ran_at"),
        "interval": CRON_INTERVAL,
    }


def _record_run(organization_id, source, result):
    from modules.database.visit_reminder_runs_repository import (
        record_visit_reminder_run,
    )

    record_visit_reminder_run(
        organization_id,
        source=source,
        ran_at=result.get("now"),
        candidate_count=result.get("candidates_found") or 0,
        dispatched=result.get("notifications_created") or 0,
    )


def scan_visit_reminders(
    organization_id,
    *,
    now=None,
    send=True,
    source="cron",
    include_near_misses=False,
):
    """
    Scan one organization for visits in the 30-minute reminder window.

    ``send=False`` is a dry-run: no notification row, no push.
    """
    instant, window_start, window_end = reminder_window(now)
    timezone_name = _org_timezone_name(organization_id)
    tz = organization_timezone(organization_id)
    language = _org_language(organization_id)
    now_local = ""
    local_now = instant.astimezone(tz)
    now_local = local_now.strftime("%Y-%m-%d %H:%M:%S")

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

    rows = []
    notifications_created = 0
    push_sent = 0
    push_failed = 0

    for task in visits:
        record = _process_candidate(
            organization_id,
            task,
            instant=instant,
            tz=tz,
            language=language,
            send=send,
        )
        rows.append(record)
        if record.get("notification_created") or record.get("created"):
            notifications_created += 1
        push_sent += int(record.get("push_sent_count") or record.get("sent_count") or 0)
        push_failed += int(
            record.get("push_failed_count") or record.get("failed_count") or 0
        )
        if record.get("dispatched"):
            logger.info(
                "agenda_reminder_dispatched event_id=%s user_id=%s "
                "sent_count=%s failed_count=%s",
                record.get("event_id"),
                record.get("resolved_user_id") or record.get("user_id"),
                record.get("push_sent_count") or 0,
                record.get("push_failed_count") or 0,
            )

    if include_near_misses:
        seen = {item.get("event_id") for item in rows}
        for task in _list_nearby_tasks(organization_id, instant):
            if task.get("id") in seen:
                continue
            rows.append(classify_visit_for_reminder(task, organization_id, instant))

    omitted = [
        {
            "event_id": item.get("event_id"),
            "skip_reason": item.get("skip_reason"),
            "candidate": item.get("candidate"),
            "notification_created": item.get("notification_created"),
            "push_sent_count": item.get("push_sent_count") or 0,
        }
        for item in rows
        if item.get("skip_reason")
    ]

    result = {
        "organization_id": organization_id,
        "now": to_utc_iso(instant),
        "now_local": now_local,
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
        "push_failed": push_failed,
        "skipped": len(omitted),
        "omitted": omitted,
        "candidates": rows,
        "scheduler": scheduler_status(organization_id, now=instant),
    }
    try:
        _record_run(organization_id, source, result)
    except Exception:
        logger.warning(
            "visit_reminder_run_record_failed organization_id=%s source=%s",
            organization_id,
            source,
            exc_info=True,
        )
    return result


def probe_visit_reminders(organization_id, *, current_user=None, now=None):
    """Admin QA: ensure a +30m visit exists in this org, then scan once."""
    created_task = None
    created_new = False
    try:
        created_task, created_new = ensure_probe_visit(
            organization_id,
            current_user=current_user,
            now=now,
        )
    except RuntimeError as error:
        result = scan_visit_reminders(
            organization_id,
            now=now,
            send=True,
            source="qa",
            include_near_misses=True,
        )
        result["probe_error"] = str(error)
        result["created_visit"] = False
        return result

    result = scan_visit_reminders(
        organization_id,
        now=now,
        send=True,
        source="qa",
        include_near_misses=True,
    )
    result["created_visit"] = created_new
    result["probe_task_id"] = None if not created_task else created_task.get("id")
    return result


def _process_candidate(organization_id, task, *, instant, tz, language, send):
    record = classify_visit_for_reminder(task, organization_id, instant)
    task_id = record["event_id"]
    user_id = record["resolved_user_id"]
    due_at = record["starts_at"]
    event_key = record["dedupe_key"]

    logger.info(
        "agenda_reminder_candidate event_id=%s user_id=%s organization_id=%s "
        "starts_at=%s status=%s type=%s dedupe_key=%s preference_enabled=%s "
        "already_sent=%s candidate=%s skip_reason=%s",
        task_id,
        user_id,
        organization_id,
        due_at,
        record["event_status"],
        record["event_type"],
        event_key,
        int(bool(record["push_visit_reminders"])),
        int(record["already_sent"]),
        int(record["candidate"]),
        record["skip_reason"] or "",
    )

    send_blocked = record["skip_reason"] in {
        "no_recipient",
        "already_sent",
        "missing_task_id",
    } or not record["candidate"]
    if send_blocked or not send:
        if not send and record["skip_reason"] is None:
            record["skip_reason"] = "dry_run"
            record["skipped_reason"] = "dry_run"
        record["dispatcher_reason"] = record["skip_reason"]
        return record

    address = _visit_address(task)
    time_label = format_local_time(due_at, tz) or "—"
    reminder = record.get("reminder_minutes") or DEFAULT_VISIT_REMINDER_MINUTES
    kind = KIND if is_visit_task_type(record.get("event_type")) else "agenda_reminder"
    title_key = (
        "push_visit_reminder_title" if kind == KIND else "push_agenda_reminder_title"
    )
    title = translate(title_key, language, minutes=int(reminder))
    body = translate(
        "push_visit_reminder_body",
        language,
        address=address,
        time=time_label,
    )
    url = f"/agenda/{int(task_id)}/edit"
    try:
        from modules.notifications.events import emit_event

        result = emit_event(
            "agenda.reminder" if kind == KIND else "agenda.meeting_reminder",
            {
                "organization_id": organization_id,
                "user_id": user_id,
                "type": kind,
                "title": title,
                "body": body,
                "url": url,
                "event_key": event_key,
                "entity_type": "agent_task",
                "entity_id": task_id,
                "minutes_until": record.get("minutes_until_start"),
                "metadata": {
                    "task_id": task_id,
                    "address": address,
                    "due_at": due_at,
                    "reminder_minutes": reminder,
                },
            },
        )
    except Exception:
        logger.warning(
            "visit_reminder_failed organization_id=%s task_id=%s",
            organization_id,
            task_id,
            exc_info=True,
        )
        record["skip_reason"] = "send_failed"
        record["skipped_reason"] = "send_failed"
        record["dispatcher_reason"] = "send_failed"
        return record

    push = result.get("push") or {}
    created = bool(result.get("created"))
    sent_count = int(push.get("sent_count") or 0)
    failed_count = int(push.get("failed_count") or 0)
    targets = int(push.get("push_targets_count") or record["push_targets_count"] or 0)
    record.update(
        {
            "notification_created": created,
            "created": created,
            "dispatched": created,
            "push_sent_count": sent_count,
            "push_failed_count": failed_count,
            "push_targets": targets,
            "push_targets_count": targets,
            "sent": sent_count,
            "failed": failed_count,
            "sent_count": sent_count,
            "failed_count": failed_count,
            "already_sent": (not created) or record["already_sent"],
        }
    )
    if not created:
        record["skip_reason"] = "already_sent"
    elif not record["push_visit_reminders"]:
        record["skip_reason"] = "preference_off"
    elif sent_count == 0 and failed_count > 0:
        record["skip_reason"] = f"push_failed failed_count={failed_count}"
    elif sent_count == 0 and targets == 0:
        record["skip_reason"] = "no_push_subscription"
    elif sent_count == 0:
        record["skip_reason"] = "push_sent_count_0"
    else:
        record["skip_reason"] = None
    record["skipped_reason"] = record["skip_reason"]
    record["dispatcher_reason"] = record["skip_reason"]
    return record


def dispatch_due_visit_reminders(organization_id, *, now=None):
    """
    Notify assigned agents of pending visits due in ~30 minutes.

    Window is +25 to +35 minutes from ``now`` (UTC). Idempotent via
    ``event_key`` that includes the current ``due_at``.
    """
    return scan_visit_reminders(organization_id, now=now, send=True, source="cron")


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

    deleted = 0
    for kind in (KIND, "agenda_reminder"):
        deleted += delete_notifications_for_entity(
            organization_id,
            kind=kind,
            entity_type="agent_task",
            entity_id=task_id,
        )
    return deleted

