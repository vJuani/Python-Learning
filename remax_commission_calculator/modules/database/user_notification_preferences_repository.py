"""Per-user push notification category preferences. Missing row = all ON."""

from __future__ import annotations

from datetime import datetime

from modules.notifications.catalog import PREF_KEYS

from .connection import get_connection
from .tenant import require_organization_id


DEFAULT_PREFERENCES = {key: True for key in PREF_KEYS}


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip() not in ("0", "false", "False", "")


def _row_to_prefs(row, columns):
    prefs = dict(DEFAULT_PREFERENCES)
    if row is None:
        return prefs
    for index, column_name in enumerate(columns):
        if index < len(row) and column_name in prefs:
            prefs[column_name] = bool(row[index])
    return prefs


def get_user_notification_preferences(organization_id, user_id):
    organization_id = require_organization_id(organization_id)
    if user_id is None:
        return dict(DEFAULT_PREFERENCES)

    select_sql = ", ".join(PREF_KEYS)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"""
            SELECT {select_sql}
            FROM user_notification_preferences
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (organization_id, user_id),
        )
        return _row_to_prefs(cursor.fetchone(), PREF_KEYS)
    finally:
        connection.close()


def is_push_category_enabled(organization_id, user_id, pref_key):
    if pref_key not in PREF_KEYS:
        return True
    prefs = get_user_notification_preferences(organization_id, user_id)
    return bool(prefs.get(pref_key, True))


def save_user_notification_preferences(organization_id, user_id, **kwargs):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    current = get_user_notification_preferences(organization_id, user_id)
    values = []
    for key in PREF_KEYS:
        if key in kwargs:
            current[key] = _as_bool(kwargs[key], True)
        values.append(1 if current[key] else 0)
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
        columns_sql = ", ".join(PREF_KEYS)
        placeholders = ", ".join("?" for _ in PREF_KEYS)
        assignments = ", ".join(f"{key} = ?" for key in PREF_KEYS)
        if existing is None:
            cursor.execute(
                f"""
                INSERT INTO user_notification_preferences (
                    organization_id,
                    user_id,
                    {columns_sql},
                    updated_at
                )
                VALUES (?, ?, {placeholders}, ?)
                """,
                (organization_id, user_id, *values, now),
            )
        else:
            cursor.execute(
                f"""
                UPDATE user_notification_preferences
                SET
                    {assignments},
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
