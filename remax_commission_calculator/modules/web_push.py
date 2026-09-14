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
PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PEM_END_RE = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
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


def _is_pem_private_key(pem):
    begin = PEM_BEGIN_RE.search(pem or "")
    end = PEM_END_RE.search(pem or "")
    return begin is not None and end is not None and begin.start() < end.start()


def _decode_private_key_b64(raw_b64):
    compact = "".join(raw_b64.split())
    try:
        decoded = base64.b64decode(compact, validate=False)
        return decoded.decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)


def _normalize_private_key_pem(pem):
    return (pem or "").replace("\\n", "\n").replace("\r\n", "\n").strip()


def vapid_private_key():
    raw_b64 = _env_value("WEB_PUSH_VAPID_PRIVATE_KEY_B64")
    if raw_b64:
        pem = _decode_private_key_b64(raw_b64)
    else:
        pem = os.environ.get("WEB_PUSH_VAPID_PRIVATE_KEY") or ""
    pem = _normalize_private_key_pem(pem)
    if not pem:
        return ""
    if not _is_pem_private_key(pem):
        raise WebPushError("pwa_push_err_invalid_vapid_private_key", 503)
    return pem


def vapid_subject():
    return (os.environ.get("WEB_PUSH_VAPID_SUBJECT") or DEFAULT_VAPID_SUBJECT).strip()


def vapid_configured():
    return bool(vapid_public_key() and _private_key_source_present())


def require_vapid():
    if not vapid_configured():
        raise WebPushError("pwa_push_err_vapid_missing", 503)
    return {
        "public_key": vapid_public_key(),
        "private_key": vapid_private_key(),
        "subject": vapid_subject(),
    }


def safe_internal_url(raw):
    value = " ".join(str(raw or "").split()) or "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    if re.search(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        return "/"
    return value


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
            vapid_private_key=keys["private_key"],
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


def send_test_push(organization_id, user_id):
    require_vapid()
    subscriptions = list_active_push_subscriptions(organization_id, user_id)
    if not subscriptions:
        raise WebPushError("pwa_push_err_no_subscription", 400)
    payload = test_payload()
    sent = 0
    failed = 0
    deactivated = 0
    for item in subscriptions:
        result = send_web_push(item, payload)
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
    if sent == 0 and failed:
        raise WebPushError("pwa_push_err_send_failed", 502)
    return {
        "sent": sent,
        "failed": failed,
        "deactivated": deactivated,
        "payload": payload,
    }
