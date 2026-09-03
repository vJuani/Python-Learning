import json
from datetime import datetime

from .connection import (
    IntegrityError,
    execute_insert,
    get_connection,
)
from .tenant import require_organization_id


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def create_notification(
    organization_id,
    user_id,
    kind,
    entity_type,
    entity_id,
    payload=None,
    actor_user_id=None,
    event_key=None
):
    """
    Insert an informational notification event.

    When ``event_key`` is given the write is idempotent: a repeated
    logical event returns the existing notification id instead of
    creating a duplicate.
    """
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        if event_key:
            existing = _find_id_by_event_key(
                cursor,
                organization_id,
                event_key
            )

            if existing is not None:
                return existing

        try:
            notification_id = execute_insert(
                cursor,
                """
                INSERT INTO notifications (
                    organization_id,
                    user_id,
                    kind,
                    entity_type,
                    entity_id,
                    payload_json,
                    is_read,
                    actor_user_id,
                    created_at,
                    event_key
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    organization_id,
                    user_id,
                    kind,
                    entity_type,
                    entity_id,
                    json.dumps(payload or {}),
                    actor_user_id,
                    _now_iso(),
                    event_key
                )
            )
            connection.commit()
            return notification_id
        except IntegrityError:
            connection.rollback()

            if not event_key:
                raise

            existing = _find_id_by_event_key(
                cursor,
                organization_id,
                event_key
            )

            if existing is None:
                raise

            return existing
    finally:
        connection.close()


def _find_id_by_event_key(cursor, organization_id, event_key):
    cursor.execute(
        """
        SELECT id
        FROM notifications
        WHERE organization_id = ?
            AND event_key = ?
        """,
        (
            organization_id,
            event_key
        )
    )
    row = cursor.fetchone()

    return row[0] if row is not None else None


def list_notifications(user_id, organization_id, limit=50):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            organization_id,
            user_id,
            kind,
            entity_type,
            entity_id,
            payload_json,
            is_read,
            actor_user_id,
            created_at,
            read_at
        FROM notifications
        WHERE user_id = ?
            AND organization_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            user_id,
            organization_id,
            limit
        )
    )

    rows = cursor.fetchall()
    connection.close()

    notifications = []

    for row in rows:
        payload = {}

        if row[6]:
            try:
                payload = json.loads(row[6])
            except json.JSONDecodeError:
                payload = {}

        notifications.append({
            "id": row[0],
            "organization_id": row[1],
            "user_id": row[2],
            "kind": row[3],
            "entity_type": row[4],
            "entity_id": row[5],
            "payload": payload,
            "is_read": bool(row[7]),
            "actor_user_id": row[8],
            "created_at": row[9],
            "read_at": row[10] if len(row) > 10 else None
        })

    return notifications


def count_unread_notifications(user_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM notifications
        WHERE user_id = ?
            AND organization_id = ?
            AND is_read = 0
        """,
        (
            user_id,
            organization_id
        )
    )

    count = cursor.fetchone()[0]
    connection.close()

    return count


def mark_notification_read(
    notification_id,
    user_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE notifications
        SET is_read = 1,
            read_at = ?
        WHERE id = ?
            AND user_id = ?
            AND organization_id = ?
        """,
        (
            _now_iso(),
            notification_id,
            user_id,
            organization_id
        )
    )

    updated = cursor.rowcount
    connection.commit()
    connection.close()

    return updated > 0


def mark_all_notifications_read(
    user_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE notifications
        SET is_read = 1,
            read_at = ?
        WHERE user_id = ?
            AND organization_id = ?
            AND is_read = 0
        """,
        (
            _now_iso(),
            user_id,
            organization_id
        )
    )

    connection.commit()
    connection.close()
