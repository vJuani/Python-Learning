"""Display helpers for the in-app notification center."""

from __future__ import annotations

from datetime import timedelta

from modules.i18n import translate
from modules.notifications.catalog import get_type, resolve_url
from modules.organization_time import (
    format_local_datetime,
    now_utc,
    organization_timezone,
    parse_utc_iso,
)
from modules.web_push import is_safe_internal_url, safe_internal_url


KIND_TO_UI = {
    "visit_reminder": "agenda_visit",
    "agenda_reminder": "agenda_event",
    "operation_side_ready_to_invoice": "invoice_ready",
    "property_match": "property_match",
    "task_overdue": "task_overdue",
    "office_announcement": "office_announcement",
}

UI_ICONS = {
    "agenda_visit": "📅",
    "agenda_event": "📅",
    "agenda_changed": "📅",
    "agenda_cancelled": "📅",
    "agenda_assigned": "📅",
    "invoice_ready": "🧾",
    "invoice": "🧾",
    "invoice_error": "🧾",
    "property_match": "🏠",
    "property": "🏠",
    "task_overdue": "⏰",
    "task_assigned": "✅",
    "task_completed": "✅",
    "office_announcement": "📢",
    "operation": "📁",
    "crm": "👤",
    "treasury": "💳",
    "system": "🔌",
}

DEFAULT_ICON = "🔔"
DAY_ORDER = ("today", "yesterday", "older")


def notification_ui_type(kind):
    spec = get_type(kind)
    return KIND_TO_UI.get(kind or "", spec.get("ui_type") or kind or "notification")


def fallback_url(notification):
    kind = notification.get("kind")
    entity_id = notification.get("entity_id")
    ui_type = notification_ui_type(kind)
    if ui_type in (
        "agenda_visit",
        "agenda_event",
        "agenda_changed",
        "agenda_cancelled",
        "agenda_assigned",
        "task_overdue",
        "task_assigned",
        "task_completed",
    ) and entity_id:
        return f"/agenda/{int(entity_id)}/edit"
    if ui_type in ("invoice_ready", "invoice", "invoice_error"):
        return "/billing?tab=pending"
    if ui_type == "property_match" and entity_id:
        return f"/contacts/{int(entity_id)}/property-matches"
    if ui_type in ("property",) and entity_id:
        return f"/properties/{int(entity_id)}"
    if ui_type == "operation" and entity_id:
        return f"/operations/{int(entity_id)}"
    return "/notifications"


PROPERTY_CHANGE_KINDS = ("property_change_approved", "property_change_rejected")


def _route_exists(url):
    """True when ``url`` matches a GET route of the running app."""
    try:
        from flask import current_app
        from werkzeug.exceptions import MethodNotAllowed, NotFound
        from werkzeug.routing import RequestRedirect

        adapter = current_app.url_map.bind("localhost")
    except (ImportError, RuntimeError):
        return True
    path = url.split("?", 1)[0].split("#", 1)[0] or "/"
    try:
        adapter.match(path, method="GET")
    except RequestRedirect:
        return True
    except (NotFound, MethodNotAllowed):
        return False
    return True


def _property_change_url(notification):
    """Change requests carry their own id; the useful place is the listing."""
    from modules.database.property_change_requests_repository import (
        get_property_change_request,
    )

    request_id = notification.get("entity_id")
    organization_id = notification.get("organization_id")
    if not request_id or not organization_id:
        return None
    try:
        change = get_property_change_request(int(request_id), organization_id)
    except Exception:
        return None
    property_id = (change or {}).get("property_id")
    return f"/properties/{int(property_id)}" if property_id else None


def notification_open_url(notification):
    """Destination when the user taps a notification. Never a dead route."""
    if notification.get("kind") in PROPERTY_CHANGE_KINDS:
        return _property_change_url(notification) or "/notifications"
    payload = notification.get("payload") or {}
    raw = payload.get("url")
    if raw and is_safe_internal_url(raw):
        cleaned = safe_internal_url(raw)
        if cleaned != "/" and _route_exists(cleaned):
            return cleaned
    candidate = resolve_url(
        notification.get("kind"),
        entity_id=notification.get("entity_id") or None,
    )
    if candidate and candidate != "/notifications" and _route_exists(candidate):
        return candidate
    fallback = fallback_url(notification)
    return fallback if _route_exists(fallback) else "/notifications"


def notification_to_json(notification, language, tz):
    """API shape of one decorated notification. Only the owner ever sees it."""
    item = decorate_notification(notification, language, tz)
    payload = item.get("payload") or {}
    return {
        "id": item["id"],
        "kind": item.get("kind"),
        "category": get_type(item.get("kind")).get("category"),
        "ui_type": item.get("ui_type"),
        "icon": item.get("icon"),
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "reason": payload.get("reason") or "",
        "priority": item.get("priority") or "info",
        "is_read": bool(item.get("is_read")),
        "created_at": item.get("created_at"),
        "when_label": item.get("when_label") or "",
        "time_label": item.get("time_label") or "",
        "day_bucket": item.get("day_bucket"),
        "open_url": f"/notifications/{int(item['id'])}/open",
    }


def _day_bucket(created_at, tz):
    parsed = parse_utc_iso(created_at)
    if parsed is None:
        return "older"
    local = parsed.astimezone(tz).date()
    today = now_utc().astimezone(tz).date()
    if local == today:
        return "today"
    if local == today - timedelta(days=1):
        return "yesterday"
    return "older"


def decorate_notification(notification, language, tz):
    payload = notification.get("payload") or {}
    kind = notification.get("kind")
    spec = get_type(kind)
    ui_type = notification_ui_type(kind)
    title = (
        payload.get("title")
        or translate(f"notification_{kind}", language)
    )
    body = payload.get("body") or payload.get("address") or ""
    priority = notification.get("priority") or payload.get("priority") or spec["priority"]
    day_bucket = _day_bucket(notification.get("created_at"), tz)
    return {
        **notification,
        "ui_type": ui_type,
        "category": spec.get("category"),
        "icon": spec.get("icon") or UI_ICONS.get(ui_type, DEFAULT_ICON),
        "title": title,
        "body": body,
        "priority": priority,
        "day_bucket": day_bucket,
        "open_url": f"/notifications/{int(notification['id'])}/open"
        if notification.get("id")
        else "/notifications",
        "when_label": format_local_datetime(notification.get("created_at"), tz)
        or (notification.get("created_at") or ""),
        "time_label": _time_label(notification.get("created_at"), tz, day_bucket),
    }


def _time_label(created_at, tz, day_bucket):
    parsed = parse_utc_iso(created_at)
    if parsed is None:
        return created_at or ""
    local = parsed.astimezone(tz)
    if day_bucket in ("today", "yesterday"):
        return local.strftime("%H:%M")
    return local.strftime("%d/%m %H:%M")


def group_notifications(items, language):
    """Group by today / yesterday / older. Individual events stay intact."""
    buckets = {key: [] for key in DAY_ORDER}
    for item in items:
        buckets.get(item.get("day_bucket") or "older", buckets["older"]).append(item)

    decorated = []
    for key in DAY_ORDER:
        bucket_items = buckets[key]
        if not bucket_items:
            continue
        decorated.append(
            {
                "ui_type": key,
                "count": len(bucket_items),
                "heading": translate(f"notifications_group_{key}", language),
                "items": bucket_items,
            }
        )
    return decorated


def decorate_notification_feed(notifications, organization_id, language):
    tz = organization_timezone(organization_id)
    items = [
        decorate_notification(item, language, tz) for item in (notifications or [])
    ]
    return group_notifications(items, language)
