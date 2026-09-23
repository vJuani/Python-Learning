"""PWA install settings and Web Push API (phase 1)."""

from __future__ import annotations

import re
from functools import wraps

import logging

from flask import jsonify, redirect, render_template, request, session, url_for

from modules.auth import get_current_user, is_admin, is_guest_session, login_required
from modules.database.push_subscriptions_repository import (
    deactivate_all_push_subscriptions,
    deactivate_push_subscription,
    get_push_subscription_by_endpoint,
    list_active_push_subscriptions,
    upsert_push_subscription,
)
from modules.database.user_notification_preferences_repository import (
    get_user_notification_preferences,
    save_user_notification_preferences,
)
from modules.notifications.catalog import PREF_GROUPS, PREF_KEYS, PREF_LABEL_KEYS
from modules.web_push import (
    WebPushError,
    push_diagnostics,
    require_vapid,
    send_test_push,
)


ENDPOINT_RE = re.compile(r"^https://", re.I)
KEY_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{10,}$")
PUSH_DEVICE_SESSION_KEY = "push_device_endpoint"

logger = logging.getLogger(__name__)


def _owned_subscription(organization_id, user_id, endpoint):
    row = get_push_subscription_by_endpoint(endpoint) if endpoint else None
    if row is None:
        return None
    if int(row["organization_id"]) != int(organization_id):
        return None
    if int(row["user_id"]) != int(user_id):
        return None
    return row


def deactivate_current_device_push(organization_id, user_id):
    """
    Logout hook: stop pushes to the browser that owns this session.

    Only the endpoint bound to the session is touched, so the user's
    other devices keep receiving. Never raises: logout must not fail.
    """
    endpoint = session.pop(PUSH_DEVICE_SESSION_KEY, None)
    if not endpoint or organization_id is None or user_id is None:
        return False
    try:
        return deactivate_push_subscription(organization_id, user_id, endpoint)
    except Exception:
        logger.warning(
            "push_logout_deactivate_failed organization_id=%s user_id=%s",
            organization_id,
            user_id,
            exc_info=True,
        )
        return False


def _json_error(message_key, status_code):
    return jsonify({"ok": False, "error": message_key}), status_code


def _require_json_user():
    user = get_current_user()
    if user is None or is_guest_session():
        return None, _json_error("login_required", 401)
    return user, None


def json_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user, error = _require_json_user()
        if error:
            return error
        return view(*args, **kwargs)

    return wrapped


def _clean(value):
    return " ".join(str(value or "").split())


def _device_label(user_agent):
    ua = _clean(user_agent)
    if not ua:
        return None
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    if "Android" in ua:
        return "Android"
    if "Windows" in ua:
        return "Windows"
    if "Mac OS" in ua or "Macintosh" in ua:
        return "Mac"
    return ua[:40]


def _subscription_from_payload(payload):
    payload = payload or {}
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    endpoint = _clean(payload.get("endpoint"))
    p256dh = _clean(keys.get("p256dh") or payload.get("p256dh"))
    auth = _clean(keys.get("auth") or payload.get("auth"))
    if not endpoint or not ENDPOINT_RE.match(endpoint) or " " in endpoint:
        raise WebPushError("pwa_push_err_invalid_subscription", 400)
    if not KEY_RE.match(p256dh) or not KEY_RE.match(auth):
        raise WebPushError("pwa_push_err_invalid_subscription", 400)
    return {
        "endpoint": endpoint,
        "p256dh": p256dh,
        "auth": auth,
        "user_agent": _clean(payload.get("user_agent"))[:400] or None,
        "device_label": _device_label(payload.get("user_agent")),
    }


def register_pwa_routes(app, helpers):
    require_user_organization = helpers["require_user_organization"]
    flash_i18n = helpers["flash_i18n"]

    @app.after_request
    def _pwa_static_headers(response):
        path = request.path or ""
        if path.endswith("/service-worker.js"):
            response.headers["Service-Worker-Allowed"] = "/"
            response.headers["Cache-Control"] = "no-cache"
        if path.endswith("/static/js/pwa.js"):
            response.headers["Cache-Control"] = "no-cache"
        if path.endswith(".webmanifest") or path.endswith("manifest.webmanifest"):
            response.headers["Content-Type"] = "application/manifest+json"
        return response

    @app.route("/settings/notifications", methods=["GET", "POST"])
    @login_required
    def settings_notifications():
        user = get_current_user()
        if user is None or is_guest_session():
            return ("Forbidden", 403)
        organization_id = require_user_organization()
        if request.method == "POST":
            save_user_notification_preferences(
                organization_id,
                user["id"],
                **{key: request.form.get(key) == "1" for key in PREF_KEYS},
            )
            flash_i18n("settings_push_prefs_saved", "success")
            return redirect(url_for("settings_notifications"))
        active = list_active_push_subscriptions(organization_id, user["id"])
        prefs = get_user_notification_preferences(organization_id, user["id"])
        return render_template(
            "settings/notifications.html",
            push_has_active=bool(active),
            push_prefs=prefs,
            push_pref_groups=PREF_GROUPS,
            push_pref_labels=PREF_LABEL_KEYS,
        )

    @app.get("/api/push/public-key")
    @json_login_required
    def api_push_public_key():
        try:
            keys = require_vapid()
        except WebPushError as error:
            return _json_error(error.message_key, error.status_code)
        return jsonify({"ok": True, "public_key": keys["public_key"]})

    @app.post("/api/push/subscribe")
    @json_login_required
    def api_push_subscribe():
        user = get_current_user()
        organization_id = require_user_organization()
        try:
            subscription = _subscription_from_payload(request.get_json(silent=True) or {})
        except WebPushError as error:
            return _json_error(error.message_key, error.status_code)
        row = upsert_push_subscription(
            organization_id,
            user["id"],
            endpoint=subscription["endpoint"],
            p256dh=subscription["p256dh"],
            auth=subscription["auth"],
            user_agent=subscription["user_agent"],
            device_label=subscription["device_label"],
        )
        session[PUSH_DEVICE_SESSION_KEY] = subscription["endpoint"]
        return jsonify(
            {
                "ok": True,
                "id": row.get("id"),
                "user_id": user["id"],
                "organization_id": organization_id,
            }
        )

    @app.post("/api/push/unsubscribe")
    @json_login_required
    def api_push_unsubscribe():
        user = get_current_user()
        organization_id = require_user_organization()
        payload = request.get_json(silent=True) or {}
        endpoint = _clean(payload.get("endpoint"))
        if not endpoint:
            return jsonify({"ok": True, "deactivated": False})
        changed = deactivate_push_subscription(organization_id, user["id"], endpoint)
        if session.get(PUSH_DEVICE_SESSION_KEY) == endpoint:
            session.pop(PUSH_DEVICE_SESSION_KEY, None)
        return jsonify({"ok": True, "deactivated": changed})

    @app.get("/api/push/status")
    @json_login_required
    def api_push_status():
        user = get_current_user()
        organization_id = require_user_organization()
        active = list_active_push_subscriptions(organization_id, user["id"])
        return jsonify({"ok": True, "has_active": bool(active), "count": len(active)})

    @app.post("/api/push/device")
    @json_login_required
    def api_push_device():
        """Bind this browser's subscription to the session, read-only otherwise."""
        user = get_current_user()
        organization_id = require_user_organization()
        payload = request.get_json(silent=True) or {}
        endpoint = _clean(payload.get("endpoint"))
        row = _owned_subscription(organization_id, user["id"], endpoint)
        if row is None:
            return jsonify({"ok": True, "known": False, "device_active": False})
        session[PUSH_DEVICE_SESSION_KEY] = endpoint
        return jsonify(
            {"ok": True, "known": True, "device_active": bool(row["is_active"])}
        )

    @app.post("/api/push/reset")
    @json_login_required
    def api_push_reset():
        user = get_current_user()
        organization_id = require_user_organization()
        deactivated = deactivate_all_push_subscriptions(organization_id, user["id"])
        return jsonify({"ok": True, "deactivated": deactivated})

    @app.get("/api/push/diagnostics")
    @json_login_required
    def api_push_diagnostics():
        """Admin-only production check. Reports booleans, never key material."""
        user = get_current_user()
        if not is_admin(user):
            return _json_error("forbidden", 403)
        return jsonify({"ok": True, **push_diagnostics()})

    @app.post("/api/push/test")
    @json_login_required
    def api_push_test():
        user = get_current_user()
        organization_id = require_user_organization()
        try:
            result = send_test_push(organization_id, user["id"])
        except WebPushError as error:
            return _json_error(error.message_key, error.status_code)
        return jsonify({"ok": True, **result})
