"""Persistence for Web Push subscriptions. Scoped by organization and user."""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _as_bool(value):
    return bool(value) and str(value) not in {"0", "false", "False"}


def _row_to_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "user_id": row[2],
        "endpoint": row[3],
        "p256dh": row[4],
        "auth": row[5],
        "user_agent": row[6] or None,
        "device_label": row[7] or None,
        "is_active": _as_bool(row[8]),
        "created_at": row[9] or None,
        "updated_at": row[10] or None,
        "last_success_at": row[11] or None,
        "last_failure_at": row[12] or None,
    }


def _select_sql():
    return """
        SELECT
            id,
            organization_id,
            user_id,
            endpoint,
            p256dh,
            auth,
            user_agent,
            device_label,
            is_active,
            created_at,
            updated_at,
            last_success_at,
            last_failure_at
        FROM push_subscriptions
    """


def get_push_subscription_by_endpoint(endpoint):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        _select_sql() + " WHERE endpoint = ?",
        (endpoint,),
    )
    row = _row_to_dict(cursor.fetchone())
    connection.close()
    return row


def list_active_push_subscriptions(organization_id, user_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        _select_sql()
        + """
        WHERE organization_id = ?
            AND user_id = ?
            AND is_active = 1
        """,
        (organization_id, user_id),
    )
    rows = [_row_to_dict(row) for row in cursor.fetchall()]
    connection.close()
    return rows


def upsert_push_subscription(
    organization_id,
    user_id,
    *,
    endpoint,
    p256dh,
    auth,
    user_agent=None,
    device_label=None,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    current = get_push_subscription_by_endpoint(endpoint)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        if current:
            cursor.execute(
                """
                UPDATE push_subscriptions
                SET
                    organization_id = ?,
                    user_id = ?,
                    p256dh = ?,
                    auth = ?,
                    user_agent = ?,
                    device_label = ?,
                    is_active = 1,
                    updated_at = ?
                WHERE endpoint = ?
                """,
                (
                    organization_id,
                    user_id,
                    p256dh,
                    auth,
                    user_agent,
                    device_label,
                    now,
                    endpoint,
                ),
            )
            connection.commit()
            return get_push_subscription_by_endpoint(endpoint)
        row_id = execute_insert(
            cursor,
            """
            INSERT INTO push_subscriptions (
                organization_id,
                user_id,
                endpoint,
                p256dh,
                auth,
                user_agent,
                device_label,
                is_active,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                organization_id,
                user_id,
                endpoint,
                p256dh,
                auth,
                user_agent,
                device_label,
                now,
                now,
            ),
        )
        connection.commit()
        return get_push_subscription_by_endpoint(endpoint) or {"id": row_id}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def deactivate_push_subscription(organization_id, user_id, endpoint):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        UPDATE push_subscriptions
        SET is_active = 0, updated_at = ?
        WHERE organization_id = ?
            AND user_id = ?
            AND endpoint = ?
        """,
        (now, organization_id, user_id, endpoint),
    )
    changed = cursor.rowcount
    connection.commit()
    connection.close()
    return changed > 0


def deactivate_all_push_subscriptions(organization_id, user_id):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        UPDATE push_subscriptions
        SET is_active = 0, updated_at = ?
        WHERE organization_id = ?
            AND user_id = ?
            AND is_active = 1
        """,
        (now, organization_id, user_id),
    )
    changed = cursor.rowcount
    connection.commit()
    connection.close()
    return max(changed, 0)


def mark_push_subscription_success(subscription_id, organization_id):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        UPDATE push_subscriptions
        SET last_success_at = ?, updated_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (now, now, subscription_id, organization_id),
    )
    connection.commit()
    connection.close()


def mark_push_subscription_failure(subscription_id, organization_id, *, deactivate=False):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        UPDATE push_subscriptions
        SET
            last_failure_at = ?,
            updated_at = ?,
            is_active = CASE WHEN ? = 1 THEN 0 ELSE is_active END
        WHERE id = ?
            AND organization_id = ?
        """,
        (now, now, 1 if deactivate else 0, subscription_id, organization_id),
    )
    connection.commit()
    connection.close()
