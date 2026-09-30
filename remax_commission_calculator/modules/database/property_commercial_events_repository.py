"""Property commercial-status and price audit written with a visit reservation."""

from __future__ import annotations

from modules.database.connection import execute_insert, get_connection
from .tenant import require_organization_id


def list_property_commercial_events(organization_id, property_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                id,
                organization_id,
                property_id,
                actor_user_id,
                agent_id,
                task_id,
                previous_commercial_status,
                commercial_status,
                previous_price,
                previous_currency,
                listing_price,
                listing_currency,
                reservation_amount,
                reservation_currency,
                created_at
            FROM property_commercial_events
            WHERE organization_id = ?
                AND property_id = ?
            ORDER BY id DESC
            """,
            (organization_id, property_id),
        )
        rows = cursor.fetchall()
    finally:
        connection.close()
    return [_event_dict(row) for row in rows]


def insert_property_commercial_event(cursor, payload):
    return execute_insert(
        cursor,
        """
        INSERT INTO property_commercial_events (
            organization_id,
            property_id,
            actor_user_id,
            agent_id,
            task_id,
            previous_commercial_status,
            commercial_status,
            previous_price,
            previous_currency,
            listing_price,
            listing_currency,
            reservation_amount,
            reservation_currency,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["organization_id"],
            payload["property_id"],
            payload.get("actor_user_id"),
            payload.get("agent_id"),
            payload.get("task_id"),
            payload.get("previous_commercial_status"),
            payload["commercial_status"],
            payload.get("previous_price"),
            payload.get("previous_currency"),
            payload.get("listing_price"),
            payload.get("listing_currency"),
            payload.get("reservation_amount"),
            payload.get("reservation_currency"),
            payload["created_at"],
        ),
    )


def delete_property_commercial_event(organization_id, event_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM property_commercial_events
            WHERE id = ?
                AND organization_id = ?
            """,
            (event_id, organization_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _event_dict(row):
    return {
        "id": row[0],
        "organization_id": row[1],
        "property_id": row[2],
        "actor_user_id": row[3],
        "agent_id": row[4],
        "task_id": row[5],
        "previous_commercial_status": row[6],
        "commercial_status": row[7],
        "previous_price": row[8],
        "previous_currency": row[9],
        "listing_price": row[10],
        "listing_currency": row[11],
        "reservation_amount": row[12],
        "reservation_currency": row[13],
        "created_at": row[14],
    }
