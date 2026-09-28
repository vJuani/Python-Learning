"""Signed-token password reset (no extra DB table)."""

from __future__ import annotations

import logging

from flask import current_app, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from modules.auth import hash_password
from modules.branding import get_app_base_url
from modules.database.users_repository import (
    get_user_by_id,
    get_user_by_username,
    update_user_password,
)
from modules.email_delivery import (
    EmailDeliveryError,
    send_password_reset_email,
)
from modules.passwords import validate_password_policy


logger = logging.getLogger(__name__)

RESET_SALT = "jrh-one-password-reset"
RESET_PURPOSE = "password_reset"
RESET_MAX_AGE_SECONDS = 60 * 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt=RESET_SALT,
    )


def generate_reset_token(user_id: int, organization_id: int) -> str:
    return _serializer().dumps({
        "purpose": RESET_PURPOSE,
        "user_id": int(user_id),
        "organization_id": int(organization_id),
    })


def verify_reset_token(token: str) -> dict | None:
    """Return user_id and organization_id, or None when the token is unusable."""
    if not (token or "").strip():
        return None

    try:
        payload = _serializer().loads(
            token.strip(),
            max_age=RESET_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None

    if not isinstance(payload, dict) or payload.get("purpose") != RESET_PURPOSE:
        return None

    try:
        return {
            "user_id": int(payload["user_id"]),
            "organization_id": int(payload["organization_id"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _is_resettable(user) -> bool:
    if not user or not user.get("is_active"):
        return False
    return user.get("account_status") in (None, "active")


def active_users_for_identifier(identifier: str) -> list:
    """Every active account for this username or email. Never picks one."""
    identifier = (identifier or "").strip()
    if not identifier:
        return []
    result = get_user_by_username(identifier)
    if result is None:
        return []
    rows = result if isinstance(result, list) else [result]
    return [user for user in rows if _is_resettable(user)]


def build_reset_url(token: str) -> str:
    return f"{get_app_base_url().rstrip('/')}{url_for('reset_password', token=token)}"


def request_password_reset(identifier: str, language: str | None = None) -> None:
    """
    Always succeeds from the caller's perspective (no user enumeration).

    One active account receives one link. Several accounts that share the
    email receive one message with a separate link per organization. The
    public response does not say which case happened.
    """
    users = active_users_for_identifier(identifier)
    if not users:
        return

    email = (users[0].get("email") or users[0].get("username") or "").strip()
    if not email or "@" not in email:
        return

    choices = []
    for user in users:
        token = generate_reset_token(user["id"], user["organization_id"])
        label = (user.get("organization_name") or "").strip()
        choices.append({
            "label": label,
            "url": build_reset_url(token),
        })

    try:
        send_password_reset_email(
            email,
            choices[0]["url"],
            language=language or "es",
            choices=choices if len(choices) > 1 else None,
        )
    except EmailDeliveryError:
        logger.exception(
            "password_reset_request_failed user_count=%s",
            len(users),
        )


def complete_password_reset(
    token: str,
    password: str,
    confirm_password: str,
) -> str | None:
    """Return i18n error key or None on success."""
    identity = verify_reset_token(token)

    if identity is None:
        return "reset_password_invalid"

    user = get_user_by_id(identity["user_id"])

    if not _is_resettable(user):
        return "reset_password_invalid"

    if int(user["organization_id"]) != identity["organization_id"]:
        return "reset_password_invalid"

    if int(user["id"]) != identity["user_id"]:
        return "reset_password_invalid"

    policy_error = validate_password_policy(password, confirm_password)

    if policy_error:
        return policy_error

    update_user_password(identity["user_id"], hash_password(password))
    return None
