"""
Agenda and follow-up service for agents (Phase 4B).

Design notes:

* Tasks are the first persisted agenda entity; everything else in the
  app stays untouched and is only referenced (agent, property,
  operation).
* ``related_entity_type``/``related_entity_id`` are generic so future
  leads or contacts link here without a new migration.
* All functions take plain arguments and return plain data, so a future
  assistant ("remind me to call Juan tomorrow at 10") can call this
  service directly without going through HTTP.
* Cross-organization and cross-agent relations are rejected here, not
  in the templates.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from modules.visit_outcome import normalize_visit_outcome, outcome_is_present
from modules.database.agent_tasks_repository import (
    PRIORITIES,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    TASK_TYPES,
    count_agent_tasks,
    create_agent_task,
    get_agent_task,
    list_agent_tasks,
    list_visits_missing_outcome,
    save_task_outcome,
    set_agent_task_status,
    set_attendance_status,
    update_agent_task_fields,
)
from modules.database.agents_repository import get_agent_record
from modules.database.operations_repository import get_operation_record
from modules.database.properties_repository import get_property_record
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.organization_time import (
    format_local_date_iso,
    format_local_datetime,
    format_local_time,
    local_date_bounds_utc,
    local_datetime_to_utc_iso,
    now_utc,
    organization_timezone,
    to_local,
    to_utc_iso,
)


logger = logging.getLogger(__name__)

SECTION_OVERDUE = "overdue"
SECTION_TODAY = "today"
SECTION_TOMORROW = "tomorrow"
SECTION_THIS_WEEK = "this_week"
SECTION_LATER = "later"

AGENDA_SECTIONS = (
    SECTION_OVERDUE,
    SECTION_TODAY,
    SECTION_TOMORROW,
    SECTION_THIS_WEEK,
    SECTION_LATER,
)

FILTER_TODAY = "today"
FILTER_UPCOMING = "upcoming"
FILTER_OVERDUE = "overdue"
FILTER_COMPLETED = "completed"

AGENDA_FILTERS = (
    FILTER_TODAY,
    FILTER_UPCOMING,
    FILTER_OVERDUE,
    FILTER_COMPLETED,
)

# A task is announced as "starting soon" this many minutes ahead. The
# reminder is computed at render time, so there is no scheduler.
SOON_THRESHOLD_MINUTES = 60

MAX_TITLE_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 2000
MAX_CONTACT_LENGTH = 120
DURATION_CHOICES = (5, 15, 30, 45, 60, 90, 120)
REMINDER_CHOICES = (15, 30, 60)
ATTENDANCE_PENDING = "pending_confirmation"
ATTENDANCE_CONFIRMED = "confirmed"


class AgentTaskError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def emit_task_event(event_type, task, *, actor_user_id=None, **extra):
    """
    Lightweight hook point for future automations.

    Intentionally not an event bus: it records the event so later
    phases (assistant, reminders) can subscribe without reworking the
    call sites.
    """
    logger.info(
        "agent_task_event type=%s task=%s org=%s agent=%s actor=%s",
        event_type,
        task.get("id") if task else None,
        task.get("organization_id") if task else None,
        task.get("agent_id") if task else None,
        actor_user_id,
    )

    try:
        from modules.google_calendar import sync_task_event

        sync_task_event(
            event_type,
            task,
            actor_user_id=actor_user_id,
        )
    except Exception:
        logger.exception(
            "google_calendar_hook_failed type=%s task=%s",
            event_type,
            task.get("id") if task else None,
        )

    return {
        "event_type": event_type,
        "task_id": task.get("id") if task else None,
        "organization_id": (
            task.get("organization_id") if task else None
        ),
        "agent_id": task.get("agent_id") if task else None,
        "actor_user_id": actor_user_id,
        **extra,
    }


def _clean_text(value, *, max_length):
    text = (value or "").strip()

    return text[:max_length]


def _optional_int(value, *, allowed, error_key):
    if value in (None, "", "none"):
        return None

    try:
        number = int(value)
    except (TypeError, ValueError):
        raise AgentTaskError(error_key) from None

    if number not in allowed:
        raise AgentTaskError(error_key)

    return number


def _resolve_agent(organization_id, agent_id):
    if agent_id is None:
        raise AgentTaskError("agent_task_err_agent_required")

    agent = get_agent_record(agent_id, organization_id)

    if agent is None:
        raise AgentTaskError("agent_task_err_agent_not_found")

    return agent


def _resolve_property(organization_id, property_id):
    """Return the property id, rejecting other organizations."""
    if property_id in (None, ""):
        return None

    try:
        property_id = int(property_id)
    except (TypeError, ValueError):
        raise AgentTaskError(
            "agent_task_err_property_not_found"
        ) from None

    record = get_property_record(property_id, organization_id)

    if record is None:
        raise AgentTaskError("agent_task_err_property_not_found")

    return record["id"]


def _resolve_operation(organization_id, operation_id):
    """
    Return the operation database id, rejecting other organizations.

    Operation dicts expose ``id`` as the display reference
    (``COM-000005``), so the numeric key is ``db_id``.
    """
    if operation_id in (None, ""):
        return None

    try:
        operation_id = int(operation_id)
    except (TypeError, ValueError):
        raise AgentTaskError(
            "agent_task_err_operation_not_found"
        ) from None

    record = get_operation_record(operation_id, organization_id)

    if record is None:
        raise AgentTaskError("agent_task_err_operation_not_found")

    return record["db_id"]


def _resolve_contact(organization_id, contact_id, *, agent_id):
    if contact_id in (None, ""):
        return None, None

    from modules.contacts import ContactError, load_contact

    try:
        contact_id = int(contact_id)
    except (TypeError, ValueError):
        raise AgentTaskError("contacts_err_not_found") from None

    try:
        contact = load_contact(
            organization_id,
            contact_id,
            agent_id=agent_id,
        )
    except ContactError as error:
        raise AgentTaskError(error.message_key) from error

    return contact["id"], contact.get("name") or None


def validate_task_payload(organization_id, agent_id, payload):
    """
    Validate a task form payload and resolve its relations.

    ``due_date``/``due_time`` are local to the organization timezone
    and are converted to a single UTC instant.
    """
    organization_id = require_organization_id(organization_id)
    agent = _resolve_agent(organization_id, agent_id)

    title = _clean_text(
        payload.get("title"),
        max_length=MAX_TITLE_LENGTH,
    )

    if not title:
        raise AgentTaskError("agent_task_err_title_required")

    contact_name = _clean_text(
        payload.get("contact_name"),
        max_length=MAX_CONTACT_LENGTH,
    )
    duration_minutes = _optional_int(
        payload.get("duration_minutes"),
        allowed=DURATION_CHOICES,
        error_key="agent_task_err_invalid_duration",
    )
    reminder_minutes = _optional_int(
        payload.get("reminder_minutes"),
        allowed=REMINDER_CHOICES,
        error_key="agent_task_err_invalid_reminder",
    )

    task_type = (payload.get("task_type") or "").strip()

    if task_type not in TASK_TYPES:
        raise AgentTaskError("agent_task_err_invalid_type")

    priority = (payload.get("priority") or PRIORITY_NORMAL).strip()

    if priority not in PRIORITIES:
        raise AgentTaskError("agent_task_err_invalid_priority")

    tz = organization_timezone(organization_id)

    try:
        due_at = local_datetime_to_utc_iso(
            payload.get("due_date"),
            payload.get("due_time"),
            tz,
        )
    except ValueError:
        raise AgentTaskError("agent_task_err_invalid_due_at") from None

    property_id = _resolve_property(
        organization_id,
        payload.get("property_id"),
    )
    operation_id = _resolve_operation(
        organization_id,
        payload.get("operation_id"),
    )
    contact_id, linked_name = _resolve_contact(
        organization_id,
        payload.get("contact_id"),
        agent_id=agent["id"],
    )
    if linked_name:
        contact_name = linked_name

    return {
        "agent_id": agent["id"],
        "title": title,
        "description": _clean_text(
            payload.get("description"),
            max_length=MAX_DESCRIPTION_LENGTH,
        ),
        "task_type": task_type,
        "priority": priority,
        "due_at": due_at,
        "property_id": property_id,
        "operation_id": operation_id,
        "related_entity_type": (
            payload.get("related_entity_type") or None
        ),
        "related_entity_id": payload.get("related_entity_id") or None,
        "contact_name": contact_name or None,
        "contact_id": contact_id,
        "duration_minutes": duration_minutes,
        "reminder_minutes": reminder_minutes,
        "attendance_status": (
            payload.get("attendance_status") or None
        ),
    }


def create_task(
    organization_id,
    agent_id,
    payload,
    *,
    created_by_user_id=None,
):
    validated = validate_task_payload(
        organization_id,
        agent_id,
        payload,
    )
    task = create_agent_task(
        organization_id,
        validated["agent_id"],
        title=validated["title"],
        task_type=validated["task_type"],
        due_at=validated["due_at"],
        priority=validated["priority"],
        description=validated["description"] or None,
        property_id=validated["property_id"],
        operation_id=validated["operation_id"],
        related_entity_type=validated["related_entity_type"],
        related_entity_id=validated["related_entity_id"],
        contact_name=validated["contact_name"],
        contact_id=validated["contact_id"],
        duration_minutes=validated["duration_minutes"],
        reminder_minutes=validated["reminder_minutes"],
        attendance_status=validated["attendance_status"],
        created_by_user_id=created_by_user_id,
    )
    emit_task_event(
        "task_created",
        task,
        actor_user_id=created_by_user_id,
    )

    return task


def load_editable_task(organization_id, task_id, *, agent_id=None):
    """Load a task, enforcing organization and agent ownership."""
    organization_id = require_organization_id(organization_id)
    task = get_agent_task(task_id, organization_id)

    if task is None:
        raise AgentTaskError("agent_task_err_not_found")

    if agent_id is not None and task["agent_id"] != agent_id:
        raise AgentTaskError("agent_task_err_forbidden")

    return task


def update_task(
    organization_id,
    task_id,
    payload,
    *,
    agent_id=None,
    actor_user_id=None,
):
    task = load_editable_task(
        organization_id,
        task_id,
        agent_id=agent_id,
    )

    if task["status"] != STATUS_PENDING:
        raise AgentTaskError("agent_task_err_not_pending")

    validated = validate_task_payload(
        organization_id,
        task["agent_id"],
        payload,
    )
    updated = update_agent_task_fields(
        task_id,
        organization_id,
        title=validated["title"],
        description=validated["description"],
        task_type=validated["task_type"],
        due_at=validated["due_at"],
        priority=validated["priority"],
        property_id=validated["property_id"],
        operation_id=validated["operation_id"],
        contact_name=validated["contact_name"] or "",
        duration_minutes=validated["duration_minutes"],
        reminder_minutes=validated["reminder_minutes"],
        attendance_status=validated["attendance_status"],
    )

    if updated is None:
        raise AgentTaskError("agent_task_err_not_pending")

    emit_task_event(
        "task_updated",
        updated,
        actor_user_id=actor_user_id,
        previous_due_at=task["due_at"],
    )

    return updated


def reschedule_task(
    organization_id,
    task_id,
    *,
    due_date,
    due_time,
    agent_id=None,
    actor_user_id=None,
):
    """Move a pending task in time without creating a duplicate."""
    task = load_editable_task(
        organization_id,
        task_id,
        agent_id=agent_id,
    )

    if task["status"] != STATUS_PENDING:
        raise AgentTaskError("agent_task_err_not_pending")

    tz = organization_timezone(organization_id)

    try:
        due_at = local_datetime_to_utc_iso(due_date, due_time, tz)
    except ValueError:
        raise AgentTaskError("agent_task_err_invalid_due_at") from None

    updated = update_agent_task_fields(
        task_id,
        organization_id,
        due_at=due_at,
    )

    if updated is None:
        raise AgentTaskError("agent_task_err_not_pending")

    emit_task_event(
        "task_rescheduled",
        updated,
        actor_user_id=actor_user_id,
        previous_due_at=task["due_at"],
    )

    return updated


def complete_task(
    organization_id,
    task_id,
    *,
    agent_id=None,
    actor_user_id=None,
):
    load_editable_task(organization_id, task_id, agent_id=agent_id)
    task = set_agent_task_status(
        task_id,
        organization_id,
        status=STATUS_COMPLETED,
        actor_user_id=actor_user_id,
    )

    if task is None:
        raise AgentTaskError("agent_task_err_not_pending")

    emit_task_event(
        "task_completed",
        task,
        actor_user_id=actor_user_id,
    )
    if task.get("contact_id"):
        from modules.contacts import touch_contact_interaction

        touch_contact_interaction(organization_id, task["contact_id"])

    return task


def cancel_task(
    organization_id,
    task_id,
    *,
    agent_id=None,
    actor_user_id=None,
):
    load_editable_task(organization_id, task_id, agent_id=agent_id)
    task = set_agent_task_status(
        task_id,
        organization_id,
        status=STATUS_CANCELLED,
        actor_user_id=actor_user_id,
    )

    if task is None:
        raise AgentTaskError("agent_task_err_not_pending")

    emit_task_event(
        "task_cancelled",
        task,
        actor_user_id=actor_user_id,
    )

    return task


def _section_for(local_due, today, now_local):
    if local_due is None:
        return SECTION_LATER

    due_date = local_due.date()

    if local_due < now_local:
        return SECTION_OVERDUE
    if due_date == today:
        return SECTION_TODAY
    if due_date == today + timedelta(days=1):
        return SECTION_TOMORROW
    if due_date <= today + timedelta(days=7):
        return SECTION_THIS_WEEK

    return SECTION_LATER


def _relative_overdue_label(local_due, now_local, language):
    delta = now_local - local_due
    minutes = int(delta.total_seconds() // 60)

    if minutes < 60:
        return translate(
            "agent_task_overdue_minutes",
            language,
            minutes=max(minutes, 1),
        )

    hours = minutes // 60

    if hours < 24:
        return translate(
            "agent_task_overdue_hours",
            language,
            hours=hours,
        )

    return translate(
        "agent_task_overdue_days",
        language,
        days=hours // 24,
    )


def _url_quote(value):
    from urllib.parse import quote

    return quote(value or "", safe="")


def _whatsapp_text(task, language):
    contact = (task.get("contact_name") or "").strip()
    address = (task.get("property_address") or "").strip()

    if not contact and not address:
        return ""

    if language == "en":
        if contact and address:
            return f"Hi {contact}, writing about {address}."
        if contact:
            return f"Hi {contact}."
        return f"Hi, writing about {address}."

    if contact and address:
        return f"Hola {contact}, te escribo por {address}."
    if contact:
        return f"Hola {contact}."
    return f"Hola, te escribo por {address}."


def _parse_outcome(raw):
    normalized = normalize_visit_outcome(raw)
    return normalized or None


def greeting_for_user(user, *, now_local, language="es"):
    hour = now_local.hour

    if hour < 12:
        hello_key = "agenda_hello_morning"
    elif hour < 19:
        hello_key = "agenda_hello_afternoon"
    else:
        hello_key = "agenda_hello_evening"

    name = (
        (user or {}).get("first_name")
        or ((user or {}).get("agent_name") or "").split(" ")[0]
        or (user or {}).get("username")
        or ""
    )

    return translate(hello_key, language, name=name)


def save_visit_outcome(
    organization_id,
    task_id,
    outcome,
    *,
    agent_id=None,
):
    task = load_editable_task(
        organization_id,
        task_id,
        agent_id=agent_id,
    )
    payload = json.dumps(normalize_visit_outcome(outcome), ensure_ascii=False)
    updated = save_task_outcome(task_id, organization_id, payload)

    if updated is None:
        raise AgentTaskError("agent_task_err_not_found")

    emit_task_event("task_outcome_saved", updated)
    if updated.get("contact_id"):
        from modules.contacts import touch_contact_interaction

        touch_contact_interaction(organization_id, updated["contact_id"])

    return updated


def confirm_attendance(
    organization_id,
    task_id,
    *,
    agent_id=None,
):
    load_editable_task(organization_id, task_id, agent_id=agent_id)
    updated = set_attendance_status(
        task_id,
        organization_id,
        ATTENDANCE_CONFIRMED,
    )

    if updated is None:
        raise AgentTaskError("agent_task_err_not_pending")

    return updated


def decorate_task(task, *, tz, now, language="es"):
    """Add rendering data (local time, overdue flag, labels) to a task."""
    local_due = to_local(task["due_at"], tz)
    now_local = now.astimezone(tz)
    today = now_local.date()
    is_pending = task["status"] == STATUS_PENDING
    is_overdue = bool(
        is_pending and local_due is not None and local_due < now_local
    )
    minutes_until = None

    if local_due is not None:
        minutes_until = int(
            (local_due - now_local).total_seconds() // 60
        )

    starts_soon = bool(
        is_pending
        and minutes_until is not None
        and 0 <= minutes_until <= SOON_THRESHOLD_MINUTES
    )
    relation_label = (
        task.get("property_address")
        or task.get("operation_reference")
        or ""
    )
    maps_url = (
        "https://www.google.com/maps/search/?api=1&query="
        + _url_quote(task.get("property_address") or "")
        if task.get("property_address")
        else None
    )
    whatsapp_text = _whatsapp_text(task, language)
    outcome = _parse_outcome(task.get("outcome_json"))

    return {
        **task,
        "due_time_label": format_local_time(task["due_at"], tz),
        "due_datetime_label": format_local_datetime(task["due_at"], tz),
        "due_date_value": format_local_date_iso(task["due_at"], tz),
        "due_time_value": format_local_time(task["due_at"], tz),
        "type_label": translate(
            f"agent_task_type_{task['task_type']}",
            language,
        ),
        "status_label": translate(
            f"agent_task_status_{task['status']}",
            language,
        ),
        "relation_label": relation_label,
        "contact_name": task.get("contact_name") or "",
        "needs_attendance": (
            is_pending
            and task.get("attendance_status") == ATTENDANCE_PENDING
        ),
        "maps_url": maps_url,
        "whatsapp_url": (
            "https://wa.me/?text=" + _url_quote(whatsapp_text)
            if whatsapp_text
            else None
        ),
        "outcome": outcome,
        "has_outcome": outcome_is_present(outcome),
        "is_overdue": is_overdue,
        "is_high_priority": task["priority"] == PRIORITY_HIGH,
        "starts_soon": starts_soon,
        "minutes_until": minutes_until,
        "soon_label": (
            translate(
                "agent_task_starts_in_minutes",
                language,
                minutes=max(minutes_until or 0, 1),
            )
            if starts_soon
            else None
        ),
        "overdue_label": (
            _relative_overdue_label(local_due, now_local, language)
            if is_overdue
            else None
        ),
        "section": _section_for(local_due, today, now_local),
        "section_date_label": (
            local_due.strftime("%A %d") if local_due else ""
        ),
    }


def build_agenda_view(
    organization_id,
    *,
    agent_id=None,
    agenda_filter=None,
    search=None,
    task_type=None,
    due_date=None,
    language="es",
    now=None,
    limit=100,
):
    """
    Build the agenda screen for one agent or a whole organization.

    Everything comes from one task query plus the joins in the
    repository, so the view cost does not grow with the number of
    properties, operations or agents involved.
    """
    organization_id = require_organization_id(organization_id)
    tz = organization_timezone(organization_id)
    now = now or now_utc()
    now_local = now.astimezone(tz)
    today = now_local.date()

    agenda_filter = (
        agenda_filter
        if agenda_filter in AGENDA_FILTERS
        else FILTER_UPCOMING
    )
    statuses = (STATUS_PENDING,)
    due_from = None
    due_to = None
    order = "asc"

    if agenda_filter == FILTER_COMPLETED:
        statuses = (STATUS_COMPLETED, STATUS_CANCELLED)
        order = "desc"
    elif agenda_filter == FILTER_TODAY:
        due_from, due_to = local_date_bounds_utc(today, tz)
    elif agenda_filter == FILTER_OVERDUE:
        due_to = to_utc_iso(now)

    if due_date:
        try:
            selected = date.fromisoformat(str(due_date))
        except ValueError:
            selected = None

        if selected is not None:
            due_from, due_to = local_date_bounds_utc(selected, tz)

    tasks = [
        decorate_task(task, tz=tz, now=now, language=language)
        for task in list_agent_tasks(
            organization_id,
            agent_id=agent_id,
            statuses=statuses,
            due_from=due_from,
            due_to=due_to,
            search=search,
            task_type=task_type,
            order=order,
            limit=limit,
        )
    ]

    sections = []

    if agenda_filter == FILTER_COMPLETED:
        sections.append(
            {
                "key": "completed",
                "label": translate(
                    "agent_task_section_completed",
                    language,
                ),
                "tasks": tasks,
            }
        )
    else:
        grouped = {key: [] for key in AGENDA_SECTIONS}

        for task in tasks:
            grouped[task["section"]].append(task)

        for key in AGENDA_SECTIONS:
            if grouped[key]:
                sections.append(
                    {
                        "key": key,
                        "label": translate(
                            f"agent_task_section_{key}",
                            language,
                        ),
                        "tasks": grouped[key],
                    }
                )

    return {
        "filter": agenda_filter,
        "search": (search or "").strip(),
        "task_type": task_type or "",
        "due_date": due_date or "",
        "sections": sections,
        "total": len(tasks),
        "today_count": sum(
            1 for task in tasks if task["section"] == SECTION_TODAY
        ),
        "overdue_count": sum(
            1 for task in tasks if task["is_overdue"]
        ),
        "today_value": today.isoformat(),
        "today_heading": translate(
            "agenda_today_heading",
            language,
            weekday=_weekday_label(today, language),
            day=today.day,
            month=_month_label(today, language),
        ),
        "timezone_name": str(tz),
        "now_local": now_local,
        **_missing_followup_fields(
            organization_id,
            agent_id=agent_id,
            language=language,
            now=now,
        ),
    }


def merge_external_tasks(
    agenda,
    extra_tasks,
    *,
    tz,
    now=None,
    language="es",
    due_date=None,
):
    """
    Fold read-only overlay tasks (Google events) into an agenda view.

    Sections and counters are rebuilt so a day that only has Google
    events still appears on the timeline.
    """
    if not extra_tasks:
        return agenda

    now = now or now_utc()
    extras = [
        decorate_task(task, tz=tz, now=now, language=language)
        for task in extra_tasks
    ]

    if due_date:
        extras = [
            task
            for task in extras
            if task.get("due_date_value") == str(due_date)
        ]

    agenda_filter = agenda.get("filter")

    if agenda_filter == FILTER_TODAY:
        extras = [
            task
            for task in extras
            if task.get("section") == SECTION_TODAY
        ]
    elif agenda_filter == FILTER_OVERDUE:
        extras = [
            task for task in extras if task.get("is_overdue")
        ]

    existing = [
        task
        for section in agenda.get("sections") or []
        for task in section.get("tasks") or []
    ]
    tasks = existing + extras
    reverse = agenda_filter == FILTER_COMPLETED
    tasks.sort(
        key=lambda task: task.get("due_at") or "",
        reverse=reverse,
    )

    sections = []

    if agenda_filter == FILTER_COMPLETED:
        sections.append(
            {
                "key": "completed",
                "label": translate(
                    "agent_task_section_completed",
                    language,
                ),
                "tasks": tasks,
            }
        )
    else:
        grouped = {key: [] for key in AGENDA_SECTIONS}

        for task in tasks:
            grouped[task["section"]].append(task)

        for key in AGENDA_SECTIONS:
            if grouped[key]:
                sections.append(
                    {
                        "key": key,
                        "label": translate(
                            f"agent_task_section_{key}",
                            language,
                        ),
                        "tasks": grouped[key],
                    }
                )

    agenda["sections"] = sections
    agenda["total"] = len(tasks)
    agenda["today_count"] = sum(
        1 for task in tasks if task["section"] == SECTION_TODAY
    )
    agenda["overdue_count"] = sum(
        1 for task in tasks if task["is_overdue"]
    )

    return agenda


def build_agenda_summary(
    organization_id,
    agent_id,
    *,
    language="es",
    now=None,
    limit=3,
):
    """
    Compact dashboard block: how many today plus the next few tasks.

    Deliberately does not load the full agenda.
    """
    organization_id = require_organization_id(organization_id)
    tz = organization_timezone(organization_id)
    now = now or now_utc()
    today = now.astimezone(tz).date()
    day_start, day_end = local_date_bounds_utc(today, tz)

    today_count = count_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=(STATUS_PENDING,),
        due_from=day_start,
        due_to=day_end,
    )
    upcoming = [
        decorate_task(task, tz=tz, now=now, language=language)
        for task in list_agent_tasks(
            organization_id,
            agent_id=agent_id,
            statuses=(STATUS_PENDING,),
            due_from=day_start,
            limit=limit,
        )
    ]
    overdue_count = count_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=(STATUS_PENDING,),
        due_to=to_utc_iso(now),
    )

    return {
        "today_count": today_count,
        "overdue_count": overdue_count,
        "tasks": upcoming,
        "today_value": today.isoformat(),
    }


def list_overdue_tasks(
    organization_id,
    *,
    agent_id=None,
    language="es",
    now=None,
    limit=5,
):
    """Overdue pending tasks, used by the Pending Center (Phase 4A)."""
    organization_id = require_organization_id(organization_id)
    tz = organization_timezone(organization_id)
    now = now or now_utc()

    return [
        decorate_task(task, tz=tz, now=now, language=language)
        for task in list_agent_tasks(
            organization_id,
            agent_id=agent_id,
            statuses=(STATUS_PENDING,),
            due_to=to_utc_iso(now),
            limit=limit,
        )
    ]


def count_overdue_tasks(organization_id, *, agent_id=None, now=None):
    organization_id = require_organization_id(organization_id)

    return count_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=(STATUS_PENDING,),
        due_to=to_utc_iso(now or now_utc()),
    )


def list_tasks_for_property(
    organization_id,
    property_id,
    *,
    agent_id=None,
    language="es",
    now=None,
    limit=10,
):
    return _related_tasks(
        organization_id,
        property_id=property_id,
        agent_id=agent_id,
        language=language,
        now=now,
        limit=limit,
    )


def list_tasks_for_operation(
    organization_id,
    operation_id,
    *,
    agent_id=None,
    language="es",
    now=None,
    limit=10,
):
    return _related_tasks(
        organization_id,
        operation_id=operation_id,
        agent_id=agent_id,
        language=language,
        now=now,
        limit=limit,
    )


def _related_tasks(
    organization_id,
    *,
    property_id=None,
    operation_id=None,
    agent_id=None,
    language="es",
    now=None,
    limit=10,
):
    organization_id = require_organization_id(organization_id)
    tz = organization_timezone(organization_id)
    now = now or now_utc()

    return [
        decorate_task(task, tz=tz, now=now, language=language)
        for task in list_agent_tasks(
            organization_id,
            agent_id=agent_id,
            statuses=(STATUS_PENDING,),
            property_id=property_id,
            operation_id=operation_id,
            limit=limit,
        )
    ]


def default_form_values(organization_id, *, now=None, **overrides):
    """Prefill values for the create form: today, next round hour."""
    tz = organization_timezone(organization_id)
    now_local = (now or now_utc()).astimezone(tz)
    suggested = (now_local + timedelta(hours=1)).replace(
        minute=0,
        second=0,
    )

    values = {
        "title": "",
        "task_type": "call",
        "priority": PRIORITY_NORMAL,
        "due_date": suggested.date().isoformat(),
        "due_time": suggested.strftime("%H:%M"),
        "property_id": "",
        "operation_id": "",
        "contact_name": "",
        "duration_minutes": 60,
        "reminder_minutes": 15,
        "description": "",
        "ai_suggestion": "",
    }
    values.update(
        {
            key: value
            for key, value in overrides.items()
            if value is not None
        }
    )

    return values


def _weekday_label(value, language):
    names = {
        "es": (
            "lunes", "martes", "miércoles", "jueves",
            "viernes", "sábado", "domingo",
        ),
        "en": (
            "Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday",
        ),
    }

    return names.get(language, names["es"])[value.weekday()]


def _month_label(value, language):
    names = {
        "es": (
            "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre",
            "diciembre",
        ),
        "en": (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November",
            "December",
        ),
    }

    return names.get(language, names["es"])[value.month - 1]


def _missing_followup_fields(organization_id, *, agent_id, language, now):
    if agent_id is None:
        return {
            "missing_followup_count": 0,
            "missing_followup_id": None,
        }

    missing = list_visits_missing_outcome(
        organization_id,
        agent_id=agent_id,
        limit=5,
    )
    first = missing[0] if missing else None

    return {
        "missing_followup_count": len(missing),
        "missing_followup_id": first["id"] if first else None,
        "missing_followup": (
            decorate_task(
                first,
                tz=organization_timezone(organization_id),
                now=now,
                language=language,
            )
            if first
            else None
        ),
    }


__all__ = [
    "AGENDA_FILTERS",
    "AGENDA_SECTIONS",
    "AgentTaskError",
    "DURATION_CHOICES",
    "PRIORITIES",
    "PRIORITY_HIGH",
    "PRIORITY_NORMAL",
    "REMINDER_CHOICES",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_PENDING",
    "TASK_TYPES",
    "build_agenda_summary",
    "build_agenda_view",
    "cancel_task",
    "complete_task",
    "confirm_attendance",
    "count_overdue_tasks",
    "create_task",
    "decorate_task",
    "default_form_values",
    "emit_task_event",
    "greeting_for_user",
    "list_overdue_tasks",
    "list_tasks_for_operation",
    "list_tasks_for_property",
    "load_editable_task",
    "merge_external_tasks",
    "reschedule_task",
    "save_visit_outcome",
    "update_task",
]
