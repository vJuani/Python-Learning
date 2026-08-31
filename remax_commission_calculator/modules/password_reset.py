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
RESET_MAX_AGE_SECONDS = 60 * 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt=RESET_SALT,
    )


def generate_reset_token(user_id: int) -> str:
    return _serializer().dumps({"user_id": int(user_id)})


def verify_reset_token(token: str) -> int | None:
    if not (token or "").strip():
        return None

    try:
        payload = _serializer().loads(
            token.strip(),
            max_age=RESET_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None

    user_id = payload.get("user_id") if isinstance(payload, dict) else None

    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _resolve_user_by_identifier(identifier: str):
    identifier = (identifier or "").strip()

    if not identifier:
        return None

    result = get_user_by_username(identifier)

    if result is None:
        return None

    if isinstance(result, list):
        for user in result:
            if user.get("is_active") and user.get("account_status") == "active":
                return user
        return result[0] if result else None

    return result


def build_reset_url(token: str) -> str:
    return f"{get_app_base_url().rstrip('/')}{url_for('reset_password', token=token)}"


def request_password_reset(identifier: str, language: str | None = None) -> None:
    """
  Always succeeds from the caller's perspective (no user enumeration).
  Sends email only when a matching active user exists.
    """
    user = _resolve_user_by_identifier(identifier)

    if user is None or not user.get("is_active"):
        return

    if user.get("account_status") not in (None, "active"):
        return

    email = (user.get("email") or user.get("username") or "").strip()

    if not email or "@" not in email:
        return

    token = generate_reset_token(user["id"])
    reset_url = build_reset_url(token)
    lang = language or "es"

    try:
        send_password_reset_email(email, reset_url, language=lang)
    except EmailDeliveryError:
        logger.exception(
            "password_reset_request_failed user_id=%s",
            user.get("id"),
        )


def complete_password_reset(
    token: str,
    password: str,
    confirm_password: str,
) -> str | None:
    """Return i18n error key or None on success."""
    user_id = verify_reset_token(token)

    if user_id is None:
        return "reset_password_invalid"

    user = get_user_by_id(user_id)

    if user is None or not user.get("is_active"):
        return "reset_password_invalid"

    policy_error = validate_password_policy(password, confirm_password)

    if policy_error:
        return policy_error

    update_user_password(user_id, hash_password(password))
    return None
