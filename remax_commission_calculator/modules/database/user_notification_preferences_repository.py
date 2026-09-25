"""Per-user push notification category preferences. Missing row = all ON."""

from __future__ import annotations

import re
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


DEFAULT_FOLLOW_UP_DIGEST_TIME = "09:00"
_DIGEST_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def normalize_follow_up_digest_time(value):
    match = _DIGEST_TIME_RE.match((value or "").strip())
    if match is None:
        return DEFAULT_FOLLOW_UP_DIGEST_TIME
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return DEFAULT_FOLLOW_UP_DIGEST_TIME
    return f"{hour:02d}:{minute:02d}"


def get_follow_up_notification_settings(organization_id, user_id):
    """Digest clock and whether urgent contacts also get their own push."""
    organization_id = require_organization_id(organization_id)
    settings = {
        "follow_up_digest_time": DEFAULT_FOLLOW_UP_DIGEST_TIME,
        "follow_up_individual_alerts": True,
    }
    if user_id is None:
        return settings
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT follow_up_digest_time, follow_up_individual_alerts
            FROM user_notification_preferences
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (organization_id, user_id),
        )
        row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return settings
    settings["follow_up_digest_time"] = normalize_follow_up_digest_time(row[0])
    settings["follow_up_individual_alerts"] = _as_bool(row[1], True)
    return settings


def save_follow_up_notification_settings(
    organization_id,
    user_id,
    *,
    digest_time,
    individual_alerts,
):
    organization_id = require_organization_id(organization_id)
    save_user_notification_preferences(organization_id, user_id)
    when = normalize_follow_up_digest_time(digest_time)
    flag = 1 if _as_bool(individual_alerts, True) else 0
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            UPDATE user_notification_preferences
            SET follow_up_digest_time = ?,
                follow_up_individual_alerts = ?,
                updated_at = ?
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (when, flag, _now_iso(), organization_id, user_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_follow_up_notification_settings(organization_id, user_id)
