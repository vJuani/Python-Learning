"""Web Push (VAPID) helpers. Phase 1: test notification only."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re

from modules.database.push_subscriptions_repository import (
    list_active_push_subscriptions,
    mark_push_subscription_failure,
    mark_push_subscription_success,
)

logger = logging.getLogger(__name__)

DEFAULT_VAPID_SUBJECT = "mailto:admin@jrhone.com"
TEST_TITLE = "JRH One"
TEST_BODY = "Las notificaciones ya están activadas."
TEST_URL = "/"
GONE_STATUSES = {404, 410}
VAPID_KEY_ERROR_MARKERS = (
    "could not deserialize",
    "header too long",
    "unable to load",
    "malformed",
)


class WebPushError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def vapid_public_key():
    return (os.environ.get("WEB_PUSH_VAPID_PUBLIC_KEY") or "").strip()


def _env_value(name):
    return (os.environ.get(name) or "").strip()


def _private_key_source_present():
    return bool(
        _env_value("WEB_PUSH_VAPID_PRIVATE_KEY_B64")
        or _env_value("WEB_PUSH_VAPID_PRIVATE_KEY")
    )


def _looks_like_pem(material):
    return b"-----BEGIN" in (material or b"")


def _resolve_private_key_bytes():
    raw_b64 = _env_value("WEB_PUSH_VAPID_PRIVATE_KEY_B64")
    if raw_b64:
        compact = "".join(raw_b64.split())
        try:
            return base64.b64decode(compact, validate=False)
        except (ValueError, binascii.Error):
            raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
    raw = (os.environ.get("WEB_PUSH_VAPID_PRIVATE_KEY") or "").strip()
    raw = raw.replace("\\n", "\n").replace("\r\n", "\n")
    if not raw:
        return b""
    return raw.encode("utf-8")


def _decode_application_server_key(value):
    compact = "".join(str(value or "").split())
    if not compact:
        return b""
    compact = compact.replace("+", "-").replace("/", "_")
    padded = compact + "=" * ((4 - len(compact) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, binascii.Error, UnicodeEncodeError):
        return None


def _derived_application_server_key(vapid):
    from cryptography.hazmat.primitives import serialization
    from py_vapid.utils import b64urlencode

    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64urlencode(raw)


def _validate_vapid_instance(vapid):
    from cryptography.hazmat.primitives.asymmetric import ec

    try:
        private_key = vapid.private_key
        curve = getattr(private_key, "curve", None)
    except Exception:
        logger.warning("web_push invalid vapid private key")
        raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
    if not isinstance(curve, ec.SECP256R1):
        logger.warning("web_push invalid vapid private key")
        raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
    expected = _decode_application_server_key(vapid_public_key())
    derived = _decode_application_server_key(_derived_application_server_key(vapid))
    if expected is None or derived is None or expected != derived:
        logger.warning("web_push vapid public key mismatch")
        raise WebPushError("pwa_push_err_vapid_key_mismatch", 503)


def load_vapid_private_key():
    from py_vapid import Vapid

    material = _resolve_private_key_bytes()
    if not material:
        raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
    try:
        if _looks_like_pem(material):
            vapid = Vapid.from_pem(material.strip())
        else:
            vapid = Vapid.from_string(material.decode("utf-8").strip())
    except WebPushError:
        raise
    except Exception:
        logger.warning("web_push invalid vapid private key")
        raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
    _validate_vapid_instance(vapid)
    return vapid


def vapid_subject():
    return (os.environ.get("WEB_PUSH_VAPID_SUBJECT") or DEFAULT_VAPID_SUBJECT).strip()


def vapid_configured():
    return bool(vapid_public_key() and _private_key_source_present())


def require_vapid():
    if not vapid_configured():
        raise WebPushError("pwa_push_err_vapid_missing", 503)
    return {
        "public_key": vapid_public_key(),
        "vapid": load_vapid_private_key(),
        "subject": vapid_subject(),
    }


def push_diagnostics():
    """Configuration health without exposing any key material."""
    from modules.notifications.dispatcher import configured_dispatcher
    from modules.visit_reminder_scheduler import inprocess_scheduler_state

    public_key = vapid_public_key()
    subject_raw = _env_value("WEB_PUSH_VAPID_SUBJECT")
    vapid_error = None
    if vapid_configured():
        try:
            load_vapid_private_key()
        except WebPushError as error:
            vapid_error = error.message_key
    else:
        vapid_error = "pwa_push_err_vapid_missing"
    scheduler = inprocess_scheduler_state()
    return {
        "vapid_public_key_present": bool(public_key),
        "vapid_private_key_present": _private_key_source_present(),
        "vapid_private_key_source": (
            "b64"
            if _env_value("WEB_PUSH_VAPID_PRIVATE_KEY_B64")
            else ("pem_or_raw" if _env_value("WEB_PUSH_VAPID_PRIVATE_KEY") else None)
        ),
        "vapid_subject_set": bool(subject_raw),
        "vapid_subject_scheme": (vapid_subject().split(":", 1)[0] or None),
        "vapid_valid": vapid_error is None,
        "vapid_error": vapid_error,
        "dispatcher": configured_dispatcher(),
        "inprocess_scheduler_alive": bool(scheduler.get("alive")),
        "inprocess_scheduler_started_at": scheduler.get("started_at"),
        "inprocess_scheduler_last_tick_at": scheduler.get("last_tick_at"),
        "inprocess_scheduler_last_error": bool(scheduler.get("last_error")),
    }


def safe_internal_url(raw):
    value = " ".join(str(raw or "").split()) or "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    if re.search(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        return "/"
    return value


def is_safe_internal_url(raw):
    value = " ".join(str(raw or "").split())
    if not value:
        return True
    return value == safe_internal_url(value)


def test_payload():
    return {
        "title": TEST_TITLE,
        "body": TEST_BODY,
        "url": TEST_URL,
        "tag": "jrh-one-test",
        "icon": "/static/icons/icon-192.png",
        "badge": "/static/icons/icon-192.png",
    }


def _status_from_exception(error):
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(error, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _is_vapid_deserialize_error(error):
    text = str(error or "").lower()
    return any(marker in text for marker in VAPID_KEY_ERROR_MARKERS)


def send_web_push(subscription, payload):
    from pywebpush import WebPushException, webpush

    keys = require_vapid()
    body = dict(payload or {})
    body["url"] = safe_internal_url(body.get("url"))
    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {
                    "p256dh": subscription["p256dh"],
                    "auth": subscription["auth"],
                },
            },
            data=json.dumps(body),
            vapid_private_key=keys["vapid"],
            vapid_claims={"sub": keys["subject"]},
        )
    except WebPushException as error:
        if _is_vapid_deserialize_error(error):
            logger.warning("web_push invalid vapid private key")
            raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
        status = _status_from_exception(error)
        logger.warning(
            "web_push failed subscription=%s status=%s",
            subscription.get("id"),
            status,
        )
        return {"ok": False, "status": status, "gone": status in GONE_STATUSES}
    except WebPushError:
        raise
    except Exception as error:
        if _is_vapid_deserialize_error(error):
            logger.warning("web_push invalid vapid private key")
            raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
        logger.exception("web_push unexpected error subscription=%s", subscription.get("id"))
        return {"ok": False, "status": None, "gone": False}
    return {"ok": True, "status": 201, "gone": False}


def _deliver_pushes(subscriptions, organization_id, payload):
    sent = 0
    failed = 0
    deactivated = 0
    for item in subscriptions:
        try:
            result = send_web_push(item, payload)
        except Exception:
            logger.warning(
                "web_push failed subscription=%s",
                item.get("id"),
                exc_info=True,
            )
            failed += 1
            continue
        if result["ok"]:
            mark_push_subscription_success(item["id"], organization_id)
            sent += 1
            continue
        mark_push_subscription_failure(
            item["id"],
            organization_id,
            deactivate=result["gone"],
        )
        if result["gone"]:
            deactivated += 1
        failed += 1
    return {
        "push_targets_count": len(subscriptions),
        "sent_count": sent,
        "failed_count": failed,
        "deactivated_count": deactivated,
        "sent": sent,
        "failed": failed,
        "deactivated": deactivated,
    }


def send_user_pushes(organization_id, user_id, payload):
    """Best-effort fan-out to every active device. Never raises."""
    subscriptions = list_active_push_subscriptions(organization_id, user_id)
    empty = {
        "push_targets_count": len(subscriptions),
        "sent_count": 0,
        "failed_count": 0,
        "deactivated_count": 0,
        "sent": 0,
        "failed": 0,
        "deactivated": 0,
    }
    if not subscriptions:
        return empty
    if not vapid_configured():
        logger.warning(
            "notification_dispatch vapid not configured organization_id=%s user_id=%s",
            organization_id,
            user_id,
        )
        empty["failed_count"] = len(subscriptions)
        empty["failed"] = len(subscriptions)
        return empty
    try:
        return _deliver_pushes(subscriptions, organization_id, payload)
    except Exception:
        logger.warning(
            "notification_dispatch push fanout failed organization_id=%s user_id=%s",
            organization_id,
            user_id,
            exc_info=True,
        )
        empty["failed_count"] = len(subscriptions)
        empty["failed"] = len(subscriptions)
        return empty


def send_test_push(organization_id, user_id):
    require_vapid()
    subscriptions = list_active_push_subscriptions(organization_id, user_id)
    if not subscriptions:
        raise WebPushError("pwa_push_err_no_subscription", 400)
    payload = test_payload()
    stats = _deliver_pushes(subscriptions, organization_id, payload)
    if stats["sent_count"] == 0 and stats["failed_count"]:
        raise WebPushError("pwa_push_err_send_failed", 502)
    return {
        "sent": stats["sent_count"],
        "failed": stats["failed_count"],
        "deactivated": stats["deactivated_count"],
        "payload": payload,
    }
