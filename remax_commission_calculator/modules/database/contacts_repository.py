"""
Persistence for agent-owned commercial contacts.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


STATUSES = ("lead", "active", "inactive", "closed")
SOURCES = ("manual", "whatsapp", "agenda", "operation", "other")
VISIBILITIES = ("private", "team", "organization")

STATUS_LEAD = "lead"
STATUS_ACTIVE = "active"
VISIBILITY_PRIVATE = "private"
SOURCE_MANUAL = "manual"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _build_contact(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "name": row[3],
        "phone": row[4] or "",
        "email": row[5] or "",
        "status": row[6],
        "source": row[7],
        "visibility": row[8],
        "notes": row[9] or "",
        "preferences_json": row[10] or "",
        "last_interacted_at": row[11],
        "created_at": row[12],
        "updated_at": row[13],
        "agent_name": row[14] if len(row) > 14 else None,
    }


_SELECT = """
    SELECT
        contact.id,
        contact.organization_id,
        contact.agent_id,
        contact.name,
        contact.phone,
        contact.email,
        contact.status,
        contact.source,
        contact.visibility,
        contact.notes,
        contact.preferences_json,
        contact.last_interacted_at,
        contact.created_at,
        contact.updated_at,
        agent.name
    FROM contacts AS contact
    LEFT JOIN agents AS agent
        ON agent.id = contact.agent_id
        AND agent.organization_id = contact.organization_id
"""


def create_contact(
    organization_id,
    agent_id,
    *,
    name,
    phone=None,
    email=None,
    status=STATUS_LEAD,
    source=SOURCE_MANUAL,
    visibility=VISIBILITY_PRIVATE,
    notes=None,
    preferences_json=None,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        contact_id = execute_insert(
            cursor,
            """
            INSERT INTO contacts (
                organization_id,
                agent_id,
                name,
                phone,
                email,
                status,
                source,
                visibility,
                notes,
                preferences_json,
                last_interacted_at,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                agent_id,
                name,
                phone,
                email,
                status,
                source,
                visibility,
                notes,
                preferences_json,
                None,
                now,
                now,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_contact(contact_id, organization_id)


def get_contact(contact_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()

    try:
        row = connection.execute(
            _SELECT
            + """
            WHERE contact.id = ?
                AND contact.organization_id = ?
            """,
            (contact_id, organization_id),
        ).fetchone()
    finally:
        connection.close()

    return _build_contact(row)


def list_contacts(
    organization_id,
    *,
    agent_id=None,
    status=None,
    search=None,
    limit=100,
):
    organization_id = require_organization_id(organization_id)
    clauses = ["contact.organization_id = ?"]
    params = [organization_id]

    if agent_id is not None:
        clauses.append("contact.agent_id = ?")
        params.append(agent_id)

    if status:
        clauses.append("contact.status = ?")
        params.append(status)

    if search:
        needle = f"%{search.strip().lower()}%"
        clauses.append(
            """
            (
                LOWER(contact.name) LIKE ?
                OR LOWER(COALESCE(contact.phone, '')) LIKE ?
                OR LOWER(COALESCE(contact.email, '')) LIKE ?
            )
            """
        )
        params.extend([needle, needle, needle])

    connection = get_connection()

    try:
        rows = connection.execute(
            _SELECT
            + f"""
            WHERE {" AND ".join(clauses)}
            ORDER BY COALESCE(contact.last_interacted_at, contact.updated_at) DESC,
                contact.id DESC
            LIMIT ?
            """,
            [*params, int(limit)],
        ).fetchall()
    finally:
        connection.close()

    return [_build_contact(row) for row in rows]


def update_contact(
    contact_id,
    organization_id,
    *,
    name=None,
    phone=None,
    email=None,
    status=None,
    source=None,
    notes=None,
    preferences_json=None,
    last_interacted_at=None,
):
    organization_id = require_organization_id(organization_id)
    assignments = []
    params = []

    for column, value in (
        ("name", name),
        ("phone", phone),
        ("email", email),
        ("status", status),
        ("source", source),
        ("notes", notes),
        ("preferences_json", preferences_json),
        ("last_interacted_at", last_interacted_at),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            params.append(value)

    if not assignments:
        return get_contact(contact_id, organization_id)

    assignments.append("updated_at = ?")
    params.append(_now_iso())
    params.extend([contact_id, organization_id])

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE contacts
            SET {", ".join(assignments)}
            WHERE id = ?
                AND organization_id = ?
            """,
            params,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_contact(contact_id, organization_id)


def set_task_contact_id(task_id, organization_id, contact_id):
    """Link a JRH task to a contact. Never infers from contact_name."""
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE agent_tasks
            SET contact_id = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (contact_id, _now_iso(), task_id, organization_id),
        )
        connection.commit()
        updated = cursor.rowcount
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return updated
