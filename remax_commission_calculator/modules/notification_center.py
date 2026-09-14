"""Display helpers for the in-app notification center."""

from __future__ import annotations

from modules.i18n import translate
from modules.organization_time import format_local_datetime, organization_timezone
from modules.web_push import is_safe_internal_url, safe_internal_url


KIND_TO_UI = {
    "visit_reminder": "agenda_visit",
    "operation_side_ready_to_invoice": "invoice_ready",
    "property_match": "property_match",
    "task_overdue": "task_overdue",
    "office_announcement": "office_announcement",
}

UI_ICONS = {
    "agenda_visit": "📅",
    "invoice_ready": "🧾",
    "property_match": "🏠",
    "task_overdue": "⏰",
    "office_announcement": "📢",
}

DEFAULT_ICON = "🔔"


def notification_ui_type(kind):
    return KIND_TO_UI.get(kind or "", kind or "notification")


def fallback_url(notification):
    kind = notification.get("kind")
    entity_id = notification.get("entity_id")
    ui_type = notification_ui_type(kind)
    if ui_type in ("agenda_visit", "task_overdue") and entity_id:
        return f"/agenda/{int(entity_id)}/edit"
    if ui_type == "invoice_ready":
        return "/billing?tab=pending"
    if ui_type == "property_match" and entity_id:
        return f"/contacts/{int(entity_id)}/property-matches"
    return "/notifications"


def notification_open_url(notification):
    payload = notification.get("payload") or {}
    raw = payload.get("url")
    if raw and is_safe_internal_url(raw):
        cleaned = safe_internal_url(raw)
        if cleaned != "/":
            return cleaned
    return fallback_url(notification)


def decorate_notification(notification, language, tz):
    payload = notification.get("payload") or {}
    ui_type = notification_ui_type(notification.get("kind"))
    title = (
        payload.get("title")
        or translate(f"notification_{notification.get('kind')}", language)
    )
    body = payload.get("body") or payload.get("address") or ""
    return {
        **notification,
        "ui_type": ui_type,
        "icon": UI_ICONS.get(ui_type, DEFAULT_ICON),
        "title": title,
        "body": body,
        "open_url": notification_open_url(notification),
        "when_label": format_local_datetime(notification.get("created_at"), tz)
        or (notification.get("created_at") or ""),
    }


def group_notifications(items, language):
    """Visual grouping only. Individual events stay intact."""
    groups = []
    for item in items:
        ui_type = item.get("ui_type")
        if groups and groups[-1]["ui_type"] == ui_type:
            groups[-1]["items"].append(item)
            continue
        groups.append({"ui_type": ui_type, "items": [item]})

    decorated = []
    for group in groups:
        count = len(group["items"])
        ui_type = group["ui_type"]
        group_key = f"notifications_group_{ui_type}"
        heading = translate(group_key, language, count=count)
        if heading == group_key:
            heading = None
        decorated.append(
            {
                "ui_type": ui_type,
                "count": count,
                "heading": heading if count > 1 else None,
                "items": group["items"],
            }
        )
    return decorated


def decorate_notification_feed(notifications, organization_id, language):
    tz = organization_timezone(organization_id)
    items = [
        decorate_notification(item, language, tz) for item in (notifications or [])
    ]
    return group_notifications(items, language)
