"""Persistence for Web Push subscriptions. Scoped by organization and user."""

from __future__ import annotations

from datetime import datetime, timedelta

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


def list_push_subscriptions(organization_id, user_id):
    """Every device of the user in this organization, active first."""
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            _select_sql()
            + """
            WHERE organization_id = ?
                AND user_id = ?
            ORDER BY is_active DESC, COALESCE(last_success_at, updated_at, created_at) DESC, id DESC
            """,
            (organization_id, user_id),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def deactivate_push_subscription_by_id(organization_id, user_id, subscription_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE push_subscriptions
            SET is_active = 0, updated_at = ?
            WHERE id = ?
                AND organization_id = ?
                AND user_id = ?
                AND is_active = 1
            """,
            (_now_iso(), int(subscription_id), organization_id, user_id),
        )
        changed = cursor.rowcount or 0
        connection.commit()
        return changed > 0
    finally:
        connection.close()


def replace_push_subscription_endpoint(old_endpoint, *, endpoint, p256dh, auth):
    """
    Move a rotated browser subscription onto its existing row.

    Keeps ``organization_id``/``user_id``/label of the old row so a
    ``pushsubscriptionchange`` never creates a second row for one device.
    If the new endpoint is already stored for the same user and office, the
    old row is dropped and the existing one keeps the old active flag. A new
    endpoint owned by someone else is never taken over. Returns the resulting
    row, or None when the old endpoint is unknown or the new one belongs to
    another owner.
    """
    old = get_push_subscription_by_endpoint(old_endpoint)
    if old is None:
        return None
    existing = get_push_subscription_by_endpoint(endpoint)
    if existing is not None and existing["id"] != old["id"] and (
        existing["organization_id"] != old["organization_id"]
        or existing["user_id"] != old["user_id"]
    ):
        return None
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        if existing is not None and existing["id"] != old["id"]:
            cursor.execute("DELETE FROM push_subscriptions WHERE id = ?", (old["id"],))
            cursor.execute(
                """
                UPDATE push_subscriptions
                SET organization_id = ?, user_id = ?, p256dh = ?, auth = ?,
                    is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    old["organization_id"],
                    old["user_id"],
                    p256dh,
                    auth,
                    1 if old["is_active"] else 0,
                    now,
                    existing["id"],
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE push_subscriptions
                SET endpoint = ?, p256dh = ?, auth = ?, updated_at = ?
                WHERE id = ?
                """,
                (endpoint, p256dh, auth, now, old["id"]),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_push_subscription_by_endpoint(endpoint)


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
                    device_label = COALESCE(NULLIF(device_label, ''), ?),
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


PURGE_INACTIVE_AFTER_DAYS = 30


def purge_inactive_push_subscriptions(*, now=None, older_than_days=PURGE_INACTIVE_AFTER_DAYS):
    """
    Hard-delete subscriptions inactive for longer than the retention window.

    Age is measured from ``last_failure_at`` (404/410 from the push
    service). Rows deactivated without a failure, e.g. on logout, fall
    back to ``updated_at`` so they are purged too. Active rows are never
    touched. Cross-organization by design: this is a maintenance job.
    """
    instant = now or datetime.utcnow()
    cutoff = (
        instant.replace(tzinfo=None, microsecond=0)
        - timedelta(days=int(older_than_days))
    ).isoformat()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            DELETE FROM push_subscriptions
            WHERE is_active = 0
                AND COALESCE(
                    NULLIF(last_failure_at, ''),
                    NULLIF(updated_at, ''),
                    created_at
                ) < ?
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount or 0
        connection.commit()
        return max(deleted, 0)
    except Exception:
        connection.rollback()
        raise
    finally:
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
