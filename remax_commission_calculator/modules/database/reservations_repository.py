"""Reservation rows. Writes that must roll back with the property update take an open cursor."""

from __future__ import annotations

import json

from .connection import IntegrityError, execute_insert, get_connection
from .tenant import require_organization_id


OPEN_STATUSES = (
    "reserved",
    "documentation",
    "financing",
    "contract",
    "deed_scheduled",
    "in_operation",
)

RESERVATION_COLUMNS = (
    "id",
    "organization_id",
    "property_id",
    "contact_id",
    "agent_id",
    "visit_id",
    "operation_id",
    "reservation_status",
    "original_property_price",
    "original_currency",
    "agreed_property_price",
    "agreed_currency",
    "reservation_amount",
    "reservation_currency",
    "payment_method",
    "next_milestone",
    "estimated_closing_date",
    "notes",
    "reserved_at",
    "price_decision",
    "cancelled_by_user_id",
    "cancelled_at",
    "created_by_user_id",
    "created_at",
    "updated_at",
)


class ReservationConflict(Exception):
    """Another open reservation already exists for this property."""


def _row(row):
    if row is None:
        return None
    data = dict(zip(RESERVATION_COLUMNS, row))
    return data


def _select():
    return "SELECT " + ", ".join(RESERVATION_COLUMNS) + " FROM reservations"


def insert_reservation(cursor, payload):
    try:
        return execute_insert(
            cursor,
            """
            INSERT INTO reservations (
                organization_id, property_id, contact_id, agent_id, visit_id,
                operation_id, reservation_status, original_property_price,
                original_currency, agreed_property_price, agreed_currency,
                reservation_amount, reservation_currency, payment_method,
                next_milestone, estimated_closing_date, notes, reserved_at,
                created_by_user_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["organization_id"],
                payload["property_id"],
                payload.get("contact_id"),
                payload.get("agent_id"),
                payload.get("visit_id"),
                payload.get("operation_id"),
                payload["reservation_status"],
                payload.get("original_property_price"),
                payload.get("original_currency"),
                payload.get("agreed_property_price"),
                payload.get("agreed_currency"),
                payload.get("reservation_amount"),
                payload.get("reservation_currency"),
                payload.get("payment_method"),
                payload.get("next_milestone"),
                payload.get("estimated_closing_date"),
                payload.get("notes"),
                payload["reserved_at"],
                payload.get("created_by_user_id"),
                payload["created_at"],
                payload["updated_at"],
            ),
        )
    except IntegrityError as error:
        raise ReservationConflict("reservation_already_open") from error


def insert_reservation_event(cursor, payload):
    return execute_insert(
        cursor,
        """
        INSERT INTO reservation_events (
            organization_id, reservation_id, event_type, actor_user_id,
            payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload["organization_id"],
            payload["reservation_id"],
            payload["event_type"],
            payload.get("actor_user_id"),
            json.dumps(payload.get("payload") or {}, ensure_ascii=False),
            payload["created_at"],
        ),
    )


def get_reservation(reservation_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            _select() + " WHERE id = ? AND organization_id = ?",
            (reservation_id, organization_id),
        )
        return _row(cursor.fetchone())
    finally:
        connection.close()


def get_open_reservation_for_property(organization_id, property_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        placeholders = ", ".join("?" for _ in OPEN_STATUSES)
        cursor.execute(
            _select()
            + f"""
            WHERE organization_id = ?
                AND property_id = ?
                AND reservation_status IN ({placeholders})
            ORDER BY id DESC
            LIMIT 1
            """,
            (organization_id, property_id, *OPEN_STATUSES),
        )
        return _row(cursor.fetchone())
    finally:
        connection.close()


def get_reservation_by_operation(organization_id, operation_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            _select()
            + " WHERE organization_id = ? AND operation_id = ? ORDER BY id DESC LIMIT 1",
            (organization_id, operation_id),
        )
        return _row(cursor.fetchone())
    finally:
        connection.close()


def list_reservations(
    organization_id,
    *,
    agent_id=None,
    status=None,
    property_id=None,
    date_from=None,
    date_to=None,
):
    organization_id = require_organization_id(organization_id)
    clauses = ["reservations.organization_id = ?"]
    params = [organization_id]
    if agent_id is not None:
        clauses.append("reservations.agent_id = ?")
        params.append(agent_id)
    if status:
        clauses.append("reservations.reservation_status = ?")
        params.append(status)
    if property_id:
        clauses.append("reservations.property_id = ?")
        params.append(property_id)
    if date_from:
        clauses.append("reservations.reserved_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("reservations.reserved_at <= ?")
        params.append(date_to)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                reservations.id,
                reservations.organization_id,
                reservations.property_id,
                reservations.contact_id,
                reservations.agent_id,
                reservations.visit_id,
                reservations.operation_id,
                reservations.reservation_status,
                reservations.original_property_price,
                reservations.original_currency,
                reservations.agreed_property_price,
                reservations.agreed_currency,
                reservations.reservation_amount,
                reservations.reservation_currency,
                reservations.payment_method,
                reservations.next_milestone,
                reservations.estimated_closing_date,
                reservations.notes,
                reservations.reserved_at,
                reservations.price_decision,
                reservations.cancelled_by_user_id,
                reservations.cancelled_at,
                reservations.created_by_user_id,
                reservations.created_at,
                reservations.updated_at,
                properties.address,
                properties.neighborhood,
                properties.external_id,
                properties.listing_purpose,
                contacts.name,
                agents.name
            FROM reservations
            LEFT JOIN properties
                ON properties.id = reservations.property_id
                AND properties.organization_id = reservations.organization_id
            LEFT JOIN contacts
                ON contacts.id = reservations.contact_id
                AND contacts.organization_id = reservations.organization_id
            LEFT JOIN agents
                ON agents.id = reservations.agent_id
                AND agents.organization_id = reservations.organization_id
            WHERE """
            + " AND ".join(clauses)
            + " ORDER BY reservations.updated_at DESC, reservations.id DESC",
            params,
        )
        rows = []
        for row in cursor.fetchall():
            base = len(RESERVATION_COLUMNS)
            item = _row(row[:base])
            item["property_address"] = row[base]
            item["neighborhood"] = row[base + 1]
            item["property_external_id"] = row[base + 2]
            item["listing_purpose"] = row[base + 3]
            item["contact_name"] = row[base + 4]
            item["agent_name"] = row[base + 5]
            rows.append(item)
        return rows
    finally:
        connection.close()


def update_reservation_fields(organization_id, reservation_id, fields):
    organization_id = require_organization_id(organization_id)
    allowed = {
        "reservation_status",
        "payment_method",
        "next_milestone",
        "estimated_closing_date",
        "notes",
        "operation_id",
        "price_decision",
        "cancelled_by_user_id",
        "cancelled_at",
        "updated_at",
        "agreed_property_price",
        "agreed_currency",
        "reservation_amount",
        "reservation_currency",
    }
    assignments = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        params.append(value)
    if not assignments:
        return get_reservation(reservation_id, organization_id)
    params.extend([reservation_id, organization_id])
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE reservations
            SET {", ".join(assignments)}
            WHERE id = ? AND organization_id = ?
            """,
            params,
        )
        if cursor.rowcount == 0:
            connection.rollback()
            return None
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_reservation(reservation_id, organization_id)


def delete_reservation(organization_id, reservation_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM reservation_events WHERE organization_id = ? AND reservation_id = ?",
            (organization_id, reservation_id),
        )
        cursor.execute(
            "DELETE FROM reservation_notes WHERE organization_id = ? AND reservation_id = ?",
            (organization_id, reservation_id),
        )
        cursor.execute(
            "DELETE FROM reservation_documents WHERE organization_id = ? AND reservation_id = ?",
            (organization_id, reservation_id),
        )
        cursor.execute(
            "DELETE FROM reservations WHERE organization_id = ? AND id = ?",
            (organization_id, reservation_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def add_reservation_note(organization_id, reservation_id, body, *, actor_user_id, created_at):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        note_id = execute_insert(
            cursor,
            """
            INSERT INTO reservation_notes (
                organization_id, reservation_id, body, actor_user_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (organization_id, reservation_id, body, actor_user_id, created_at),
        )
        connection.commit()
        return note_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_reservation_notes(organization_id, reservation_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, organization_id, reservation_id, body, actor_user_id, created_at
            FROM reservation_notes
            WHERE organization_id = ? AND reservation_id = ?
            ORDER BY created_at, id
            """,
            (organization_id, reservation_id),
        )
        return [
            {
                "id": row[0],
                "organization_id": row[1],
                "reservation_id": row[2],
                "body": row[3],
                "actor_user_id": row[4],
                "created_at": row[5],
            }
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def list_reservation_events(organization_id, reservation_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, organization_id, reservation_id, event_type, actor_user_id, payload_json, created_at
            FROM reservation_events
            WHERE organization_id = ? AND reservation_id = ?
            ORDER BY created_at, id
            """,
            (organization_id, reservation_id),
        )
        events = []
        for row in cursor.fetchall():
            try:
                payload = json.loads(row[5] or "{}")
            except (TypeError, ValueError):
                payload = {}
            events.append(
                {
                    "id": row[0],
                    "organization_id": row[1],
                    "reservation_id": row[2],
                    "event_type": row[3],
                    "actor_user_id": row[4],
                    "payload": payload,
                    "created_at": row[6],
                }
            )
        return events
    finally:
        connection.close()


def add_reservation_event(organization_id, reservation_id, event_type, *, actor_user_id, payload, created_at):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        event_id = insert_reservation_event(
            cursor,
            {
                "organization_id": organization_id,
                "reservation_id": reservation_id,
                "event_type": event_type,
                "actor_user_id": actor_user_id,
                "payload": payload,
                "created_at": created_at,
            },
        )
        connection.commit()
        return event_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def insert_reservation_document(organization_id, payload):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        document_id = execute_insert(
            cursor,
            """
            INSERT INTO reservation_documents (
                organization_id, reservation_id, doc_type, original_filename,
                stored_name, content_type, size_bytes, uploaded_by_user_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                payload["reservation_id"],
                payload["doc_type"],
                payload["original_filename"],
                payload["stored_name"],
                payload.get("content_type"),
                payload.get("size_bytes"),
                payload.get("uploaded_by_user_id"),
                payload["created_at"],
            ),
        )
        connection.commit()
        return document_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_reservation_documents(organization_id, reservation_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, organization_id, reservation_id, doc_type, original_filename,
                stored_name, content_type, size_bytes, uploaded_by_user_id, created_at
            FROM reservation_documents
            WHERE organization_id = ? AND reservation_id = ?
            ORDER BY id
            """,
            (organization_id, reservation_id),
        )
        return [
            {
                "id": row[0],
                "organization_id": row[1],
                "reservation_id": row[2],
                "doc_type": row[3],
                "original_filename": row[4],
                "stored_name": row[5],
                "content_type": row[6],
                "size_bytes": row[7],
                "uploaded_by_user_id": row[8],
                "created_at": row[9],
            }
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def get_reservation_document(organization_id, document_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, organization_id, reservation_id, doc_type, original_filename,
                stored_name, content_type, size_bytes, uploaded_by_user_id, created_at
            FROM reservation_documents
            WHERE organization_id = ? AND id = ?
            """,
            (organization_id, document_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "organization_id": row[1],
            "reservation_id": row[2],
            "doc_type": row[3],
            "original_filename": row[4],
            "stored_name": row[5],
            "content_type": row[6],
            "size_bytes": row[7],
            "uploaded_by_user_id": row[8],
            "created_at": row[9],
        }
    finally:
        connection.close()
