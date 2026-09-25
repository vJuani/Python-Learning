"""Notification types, categories, priorities, routes, and preference keys."""

from __future__ import annotations


PRIORITY_INFO = "info"
PRIORITY_IMPORTANT = "important"
PRIORITY_URGENT = "urgent"
PRIORITIES = (PRIORITY_INFO, PRIORITY_IMPORTANT, PRIORITY_URGENT)

PREF_AGENDA_REMINDERS = "push_visit_reminders"
PREF_AGENDA_CHANGES = "push_agenda_changes"
PREF_AGENDA_ASSIGNMENTS = "push_agenda_assignments"
PREF_OPERATIONS = "push_operations"
PREF_BILLING = "push_invoice_ready"
PREF_PROPERTIES = "push_property_matches"
PREF_CRM = "push_crm"
PREF_TASKS = "push_task_overdue"
PREF_TREASURY = "push_treasury"
PREF_OFFICE = "push_office_announcements"
PREF_SYSTEM = "push_system"

PREF_KEYS = (
    PREF_AGENDA_REMINDERS,
    PREF_AGENDA_CHANGES,
    PREF_AGENDA_ASSIGNMENTS,
    PREF_OPERATIONS,
    PREF_BILLING,
    PREF_PROPERTIES,
    PREF_CRM,
    PREF_TASKS,
    PREF_TREASURY,
    PREF_OFFICE,
    PREF_SYSTEM,
)

PREF_GROUPS = (
    {
        "id": "agenda",
        "title_key": "settings_push_group_agenda",
        "pref_keys": (
            PREF_AGENDA_REMINDERS,
            PREF_AGENDA_CHANGES,
            PREF_AGENDA_ASSIGNMENTS,
        ),
    },
    {
        "id": "operations",
        "title_key": "settings_push_group_operations",
        "pref_keys": (PREF_OPERATIONS,),
    },
    {
        "id": "billing",
        "title_key": "settings_push_group_billing",
        "pref_keys": (PREF_BILLING,),
    },
    {
        "id": "properties",
        "title_key": "settings_push_group_properties",
        "pref_keys": (PREF_PROPERTIES,),
    },
    {
        "id": "crm",
        "title_key": "settings_push_group_crm",
        "pref_keys": (PREF_CRM,),
    },
    {
        "id": "tasks",
        "title_key": "settings_push_group_tasks",
        "pref_keys": (PREF_TASKS,),
    },
    {
        "id": "treasury",
        "title_key": "settings_push_group_treasury",
        "pref_keys": (PREF_TREASURY,),
    },
    {
        "id": "office",
        "title_key": "settings_push_group_office",
        "pref_keys": (PREF_OFFICE,),
    },
    {
        "id": "system",
        "title_key": "settings_push_group_system",
        "pref_keys": (PREF_SYSTEM,),
    },
)

PREF_LABEL_KEYS = {
    PREF_AGENDA_REMINDERS: "settings_push_visit_reminders",
    PREF_AGENDA_CHANGES: "settings_push_agenda_changes",
    PREF_AGENDA_ASSIGNMENTS: "settings_push_agenda_assignments",
    PREF_OPERATIONS: "settings_push_operations",
    PREF_BILLING: "settings_push_invoice_ready",
    PREF_PROPERTIES: "settings_push_property_matches",
    PREF_CRM: "settings_push_crm",
    PREF_TASKS: "settings_push_task_overdue",
    PREF_TREASURY: "settings_push_treasury",
    PREF_OFFICE: "settings_push_office_announcements",
    PREF_SYSTEM: "settings_push_system",
}


def _type(
    category,
    pref_key,
    *,
    priority=PRIORITY_INFO,
    icon="🔔",
    url="/notifications",
    ui_type=None,
    entity_type="notification",
):
    return {
        "category": category,
        "pref_key": pref_key,
        "priority": priority,
        "icon": icon,
        "url": url,
        "ui_type": ui_type or category,
        "entity_type": entity_type,
    }


TYPES = {
    "visit_reminder": _type(
        "agenda",
        PREF_AGENDA_REMINDERS,
        priority=PRIORITY_IMPORTANT,
        icon="📅",
        url="/agenda/{entity_id}/edit",
        ui_type="agenda_visit",
        entity_type="agent_task",
    ),
    "agenda_reminder": _type(
        "agenda",
        PREF_AGENDA_REMINDERS,
        priority=PRIORITY_IMPORTANT,
        icon="📅",
        url="/agenda/{entity_id}/edit",
        ui_type="agenda_event",
        entity_type="agent_task",
    ),
    "agenda_changed": _type(
        "agenda",
        PREF_AGENDA_CHANGES,
        priority=PRIORITY_IMPORTANT,
        icon="📅",
        url="/agenda/{entity_id}/edit",
        ui_type="agenda_changed",
        entity_type="agent_task",
    ),
    "agenda_cancelled": _type(
        "agenda",
        PREF_AGENDA_CHANGES,
        priority=PRIORITY_IMPORTANT,
        icon="📅",
        url="/agenda/{entity_id}/edit",
        ui_type="agenda_cancelled",
        entity_type="agent_task",
    ),
    "agenda_assigned": _type(
        "agenda",
        PREF_AGENDA_ASSIGNMENTS,
        priority=PRIORITY_IMPORTANT,
        icon="📅",
        url="/agenda/{entity_id}/edit",
        ui_type="agenda_assigned",
        entity_type="agent_task",
    ),
    "task_assigned": _type(
        "tasks",
        PREF_TASKS,
        icon="✅",
        url="/agenda/{entity_id}/edit",
        ui_type="task_assigned",
        entity_type="agent_task",
    ),
    "task_overdue": _type(
        "tasks",
        PREF_TASKS,
        priority=PRIORITY_IMPORTANT,
        icon="⏰",
        url="/agenda/{entity_id}/edit",
        ui_type="task_overdue",
        entity_type="agent_task",
    ),
    "task_completed": _type(
        "tasks",
        PREF_TASKS,
        icon="✅",
        url="/agenda/{entity_id}/edit",
        ui_type="task_completed",
        entity_type="agent_task",
    ),
    "operation_assigned": _type(
        "operations",
        PREF_OPERATIONS,
        icon="📁",
        url="/operations/{entity_id}",
        ui_type="operation",
        entity_type="operation",
    ),
    "operation_updated": _type(
        "operations",
        PREF_OPERATIONS,
        icon="📁",
        url="/operations/{entity_id}",
        ui_type="operation",
        entity_type="operation",
    ),
    "operation_approved": _type(
        "operations",
        PREF_OPERATIONS,
        priority=PRIORITY_IMPORTANT,
        icon="📁",
        url="/operations/{entity_id}",
        ui_type="operation",
        entity_type="operation",
    ),
    "operation_rejected": _type(
        "operations",
        PREF_OPERATIONS,
        priority=PRIORITY_IMPORTANT,
        icon="📁",
        url="/operations/{entity_id}",
        ui_type="operation",
        entity_type="operation",
    ),
    "operation_side_ready_to_invoice": _type(
        "billing",
        PREF_BILLING,
        priority=PRIORITY_IMPORTANT,
        icon="🧾",
        url="/billing?tab=pending",
        ui_type="invoice_ready",
        entity_type="operation",
    ),
    "operation_invoice_amount_ready": _type(
        "billing",
        PREF_BILLING,
        priority=PRIORITY_IMPORTANT,
        icon="🧾",
        url="/billing?tab=pending",
        ui_type="invoice_ready",
        entity_type="operation",
    ),
    "invoice_created": _type(
        "billing",
        PREF_BILLING,
        icon="🧾",
        url="/billing/{entity_id}",
        ui_type="invoice",
        entity_type="invoice",
    ),
    "invoice_error": _type(
        "billing",
        PREF_BILLING,
        priority=PRIORITY_IMPORTANT,
        icon="🧾",
        url="/billing",
        ui_type="invoice_error",
        entity_type="invoice",
    ),
    "property_approved": _type(
        "properties",
        PREF_PROPERTIES,
        priority=PRIORITY_IMPORTANT,
        icon="🏠",
        url="/properties/{entity_id}",
        ui_type="property",
        entity_type="property",
    ),
    "property_rejected": _type(
        "properties",
        PREF_PROPERTIES,
        priority=PRIORITY_IMPORTANT,
        icon="🏠",
        url="/properties/{entity_id}",
        ui_type="property",
        entity_type="property",
    ),
    "property_change_approved": _type(
        "properties",
        PREF_PROPERTIES,
        icon="🏠",
        url="/properties/{entity_id}",
        ui_type="property",
        entity_type="property_change",
    ),
    "property_change_rejected": _type(
        "properties",
        PREF_PROPERTIES,
        icon="🏠",
        url="/properties/{entity_id}",
        ui_type="property",
        entity_type="property_change",
    ),
    "property_assigned": _type(
        "properties",
        PREF_PROPERTIES,
        icon="🏠",
        url="/properties/{entity_id}",
        ui_type="property",
        entity_type="property",
    ),
    "property_paused": _type(
        "properties",
        PREF_PROPERTIES,
        icon="🏠",
        url="/properties/{entity_id}",
        ui_type="property",
        entity_type="property",
    ),
    "property_match": _type(
        "properties",
        PREF_PROPERTIES,
        icon="🏠",
        url="/contacts/{entity_id}/property-matches",
        ui_type="property_match",
        entity_type="contact",
    ),
    "crm_daily_follow_up": _type(
        "crm",
        PREF_CRM,
        priority=PRIORITY_IMPORTANT,
        icon="👤",
        url="/contacts/follow-ups",
        ui_type="crm",
        entity_type="follow_up_digest",
    ),
    "crm_follow_up_due": _type(
        "crm",
        PREF_CRM,
        priority=PRIORITY_IMPORTANT,
        icon="👤",
        url="/contacts/{entity_id}",
        ui_type="crm",
        entity_type="contact",
    ),
    "contact_assigned": _type(
        "crm",
        PREF_CRM,
        icon="👤",
        url="/contacts/{entity_id}",
        ui_type="crm",
        entity_type="contact",
    ),
    "agent_payment_confirmed": _type(
        "treasury",
        PREF_TREASURY,
        icon="💳",
        url="/wallet",
        ui_type="treasury",
        entity_type="agent_account_movement",
    ),
    "commission_credited": _type(
        "treasury",
        PREF_TREASURY,
        icon="💳",
        url="/wallet",
        ui_type="treasury",
        entity_type="agent_account_movement",
    ),
    "recurring_charge_generated": _type(
        "treasury",
        PREF_TREASURY,
        icon="💳",
        url="/wallet",
        ui_type="treasury",
        entity_type="agent_account_movement",
    ),
    "treasury_needs_review": _type(
        "treasury",
        PREF_TREASURY,
        priority=PRIORITY_IMPORTANT,
        icon="💳",
        url="/cash",
        ui_type="treasury",
        entity_type="treasury_movement",
    ),
    "treasury_receipt_failed": _type(
        "treasury",
        PREF_TREASURY,
        priority=PRIORITY_IMPORTANT,
        icon="💳",
        url="/cash",
        ui_type="treasury",
        entity_type="treasury_movement",
    ),
    "arca_credential_attention": _type(
        "system",
        PREF_SYSTEM,
        priority=PRIORITY_URGENT,
        icon="⚠️",
        url="/settings/arca",
        ui_type="system",
        entity_type="arca",
    ),
    "office_announcement": _type(
        "office",
        PREF_OFFICE,
        priority=PRIORITY_IMPORTANT,
        icon="📢",
        url="/notifications",
        ui_type="office_announcement",
        entity_type="office",
    ),
    "integration_disconnected": _type(
        "system",
        PREF_SYSTEM,
        priority=PRIORITY_URGENT,
        icon="🔌",
        url="/settings/integrations",
        ui_type="system",
        entity_type="integration",
    ),
    "sync_failed": _type(
        "system",
        PREF_SYSTEM,
        priority=PRIORITY_IMPORTANT,
        icon="🔌",
        url="/settings/integrations",
        ui_type="system",
        entity_type="integration",
    ),
    "import_finished": _type(
        "system",
        PREF_SYSTEM,
        icon="📥",
        url="/notifications",
        ui_type="system",
        entity_type="import",
    ),
    "import_failed": _type(
        "system",
        PREF_SYSTEM,
        priority=PRIORITY_IMPORTANT,
        icon="📥",
        url="/notifications",
        ui_type="system",
        entity_type="import",
    ),
}

EVENT_TO_TYPE = {
    "agenda.reminder": "visit_reminder",
    "agenda.meeting_reminder": "agenda_reminder",
    "agenda.assigned": "agenda_assigned",
    "agenda.changed": "agenda_changed",
    "agenda.cancelled": "agenda_cancelled",
    "task.assigned": "task_assigned",
    "task.overdue": "task_overdue",
    "task.completed": "task_completed",
    "operation.created": "operation_assigned",
    "operation.updated": "operation_updated",
    "operation.approved": "operation_approved",
    "operation.rejected": "operation_rejected",
    "operation.invoice_ready": "operation_side_ready_to_invoice",
    "billing.invoice_created": "invoice_created",
    "billing.invoice_error": "invoice_error",
    "property.assigned": "property_assigned",
    "property.approved": "property_approved",
    "property.rejected": "property_rejected",
    "property.paused": "property_paused",
    "property.change_approved": "property_change_approved",
    "property.change_rejected": "property_change_rejected",
    "property.match": "property_match",
    "crm.daily_follow_up": "crm_daily_follow_up",
    "crm.follow_up_due": "crm_follow_up_due",
    "crm.assigned": "contact_assigned",
    "treasury.payment_confirmed": "agent_payment_confirmed",
    "treasury.commission_credited": "commission_credited",
    "treasury.recurring_charge": "recurring_charge_generated",
    "treasury.needs_review": "treasury_needs_review",
    "treasury.receipt_failed": "treasury_receipt_failed",
    "arca.credential_attention": "arca_credential_attention",
    "office.announcement": "office_announcement",
    "system.google_disconnected": "integration_disconnected",
    "system.sync_failed": "sync_failed",
    "system.import_finished": "import_finished",
    "system.import_failed": "import_failed",
}


def get_type(type_name):
    return TYPES.get(type_name) or _type("system", PREF_SYSTEM)


CATEGORY_ORDER = (
    "agenda",
    "tasks",
    "operations",
    "billing",
    "properties",
    "crm",
    "treasury",
    "office",
    "system",
)


def kinds_for_category(category):
    """Notification kinds of one category, or None for "all"."""
    if not category:
        return None
    return tuple(
        name for name, spec in TYPES.items() if spec["category"] == category
    )


def pref_key_for_type(type_name):
    return get_type(type_name)["pref_key"]


def resolve_priority(type_name, minutes_until=None, priority=None):
    if priority in PRIORITIES:
        return priority
    if minutes_until is not None:
        try:
            delta = float(minutes_until)
        except (TypeError, ValueError):
            delta = None
        if delta is not None:
            if delta <= 5:
                return PRIORITY_URGENT
            if delta <= 30:
                return PRIORITY_IMPORTANT
    return get_type(type_name)["priority"]


def resolve_url(type_name, *, entity_id=None, url=None):
    if url:
        return url
    template = get_type(type_name)["url"]
    if "{entity_id}" in template:
        if entity_id is None:
            return "/notifications"
        return template.format(entity_id=int(entity_id))
    return template


def event_type_name(event_name, fallback=None):
    return EVENT_TO_TYPE.get(event_name) or fallback or "office_announcement"


PUSH_PREF_BY_TYPE = {
    type_name: spec["pref_key"] for type_name, spec in TYPES.items()
}
