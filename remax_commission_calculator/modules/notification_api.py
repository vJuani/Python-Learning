"""JSON API for the notification center and the live bell.

Every query is scoped to the session's user and organization; ids from
the client are only ever matched together with both, so another user's
or another office's notifications are unreachable.
"""

from __future__ import annotations

from flask import jsonify, request

from modules.auth import get_current_user
from modules.database.notifications_repository import (
    MAX_PAGE_LIMIT,
    count_unread_notifications,
    get_notification,
    list_notifications_page,
    mark_all_notifications_read,
    mark_notification_read,
)
from modules.notifications.catalog import CATEGORY_ORDER, kinds_for_category
from modules.pwa_routes import json_login_required

DEFAULT_PAGE_LIMIT = 20


def _as_int(value, default=None, minimum=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and number < minimum:
        return default
    return number


def _as_flag(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def register_notification_api(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    get_current_language = helpers["get_current_language"]

    def _scope():
        user = get_current_user()
        return user, require_user_organization()

    @app.get("/api/notifications/unread-count")
    @json_login_required
    def api_notifications_unread_count():
        user, organization_id = _scope()
        return jsonify(
            {
                "ok": True,
                "unread_count": count_unread_notifications(user["id"], organization_id),
            }
        )

    @app.get("/api/notifications")
    @json_login_required
    def api_notifications_list():
        from modules.notification_center import notification_to_json
        from modules.organization_time import organization_timezone

        user, organization_id = _scope()
        limit = _as_int(request.args.get("limit"), DEFAULT_PAGE_LIMIT, minimum=1)
        limit = min(limit, MAX_PAGE_LIMIT)
        page = _as_int(request.args.get("page"), 1, minimum=1)
        before_id = _as_int(request.args.get("before"), None, minimum=1)
        unread_only = _as_flag(request.args.get("unread_only"))
        category = (request.args.get("category") or "").strip().lower()
        if category and category not in CATEGORY_ORDER:
            return jsonify({"ok": False, "error": "invalid_category"}), 400

        rows, has_more = list_notifications_page(
            user["id"],
            organization_id,
            limit=limit,
            offset=(page - 1) * limit,
            before_id=before_id,
            unread_only=unread_only,
            kinds=kinds_for_category(category),
        )
        language = get_current_language()
        tz = organization_timezone(organization_id)
        items = [notification_to_json(row, language, tz) for row in rows]
        return jsonify(
            {
                "ok": True,
                "items": items,
                "page": page,
                "limit": limit,
                "has_more": has_more,
                "next_before": items[-1]["id"] if items and has_more else None,
                "unread_count": count_unread_notifications(user["id"], organization_id),
            }
        )

    @app.post("/api/notifications/<int:notification_id>/read")
    @json_login_required
    def api_notifications_mark_read(notification_id):
        user, organization_id = _scope()
        if get_notification(notification_id, user["id"], organization_id) is None:
            return jsonify({"ok": False, "error": "not_found"}), 404
        mark_notification_read(notification_id, user["id"], organization_id)
        return jsonify(
            {
                "ok": True,
                "id": notification_id,
                "unread_count": count_unread_notifications(user["id"], organization_id),
            }
        )

    @app.post("/api/notifications/read-all")
    @json_login_required
    def api_notifications_mark_all_read():
        user, organization_id = _scope()
        updated = mark_all_notifications_read(user["id"], organization_id)
        return jsonify(
            {
                "ok": True,
                "updated": updated,
                "unread_count": count_unread_notifications(user["id"], organization_id),
            }
        )
