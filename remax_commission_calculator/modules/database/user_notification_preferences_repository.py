"""Per-user push notification category preferences. Missing row = all ON."""

from __future__ import annotations

from datetime import datetime

from .connection import get_connection
from .tenant import require_organization_id


PREF_KEYS = (
    "push_visit_reminders",
    "push_invoice_ready",
    "push_property_matches",
)

DEFAULT_PREFERENCES = {key: True for key in PREF_KEYS}


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip() not in ("0", "false", "False", "")


def _row_to_prefs(row):
    if row is None:
        return dict(DEFAULT_PREFERENCES)
    return {
        "push_visit_reminders": bool(row[0]),
        "push_invoice_ready": bool(row[1]),
        "push_property_matches": bool(row[2]),
    }


def get_user_notification_preferences(organization_id, user_id):
    organization_id = require_organization_id(organization_id)
    if user_id is None:
        return dict(DEFAULT_PREFERENCES)

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                push_visit_reminders,
                push_invoice_ready,
                push_property_matches
            FROM user_notification_preferences
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (organization_id, user_id),
        )
        return _row_to_prefs(cursor.fetchone())
    finally:
        connection.close()


def is_push_category_enabled(organization_id, user_id, pref_key):
    if pref_key not in PREF_KEYS:
        return True
    prefs = get_user_notification_preferences(organization_id, user_id)
    return bool(prefs.get(pref_key, True))


def save_user_notification_preferences(
    organization_id,
    user_id,
    *,
    push_visit_reminders=True,
    push_invoice_ready=True,
    push_property_matches=True,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    values = (
        1 if _as_bool(push_visit_reminders) else 0,
        1 if _as_bool(push_invoice_ready) else 0,
        1 if _as_bool(push_property_matches) else 0,
    )
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT id
            FROM user_notification_preferences
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (organization_id, user_id),
        )
        existing = cursor.fetchone()
        if existing is None:
            cursor.execute(
                """
                INSERT INTO user_notification_preferences (
                    organization_id,
                    user_id,
                    push_visit_reminders,
                    push_invoice_ready,
                    push_property_matches,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (organization_id, user_id, *values, now),
            )
        else:
            cursor.execute(
                """
                UPDATE user_notification_preferences
                SET
                    push_visit_reminders = ?,
                    push_invoice_ready = ?,
                    push_property_matches = ?,
                    updated_at = ?
                WHERE organization_id = ?
                    AND user_id = ?
                """,
                (*values, now, organization_id, user_id),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_user_notification_preferences(organization_id, user_id)
