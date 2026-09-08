"""
Persistence for agent-owned commercial contacts.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


STATUSES = ("lead", "active", "inactive", "closed")
SOURCES = ("manual", "whatsapp", "agenda", "operation", "other")
CONTACT_TYPES = (
    "prospect",
    "buyer",
    "seller",
    "owner",
    "client",
    "referral",
    "other",
)
SOURCE_TYPES = (
    "manual",
    "phone_import",
    "vcard",
    "agenda",
    "whatsapp",
    "operation",
    "other",
)
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
        "first_name": row[15] if len(row) > 15 else "",
        "last_name": row[16] if len(row) > 16 else "",
        "company": row[17] if len(row) > 17 else "",
        "contact_type": row[18] if len(row) > 18 else "",
        "archived_at": row[19] if len(row) > 19 else None,
        "phone_normalized": row[20] if len(row) > 20 else "",
        "email_normalized": row[21] if len(row) > 21 else "",
        "source_type": row[22] if len(row) > 22 else "",
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
        agent.name,
        contact.first_name,
        contact.last_name,
        contact.company,
        contact.contact_type,
        contact.archived_at,
        contact.phone_normalized,
        contact.email_normalized,
        contact.source_type
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
    first_name=None,
    last_name=None,
    company=None,
    contact_type=None,
    phone_normalized=None,
    email_normalized=None,
    source_type=None,
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
                updated_at,
                first_name,
                last_name,
                company,
                contact_type,
                phone_normalized,
                email_normalized,
                source_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                first_name,
                last_name,
                company,
                contact_type,
                phone_normalized,
                email_normalized,
                source_type or source or SOURCE_MANUAL,
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
    contact_type=None,
    include_archived=False,
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

    if contact_type:
        clauses.append("contact.contact_type = ?")
        params.append(contact_type)

    if not include_archived:
        clauses.append("contact.archived_at IS NULL")

    if search:
        needle = f"%{search.strip().lower()}%"
        clauses.append(
            """
            (
                LOWER(contact.name) LIKE ?
                OR LOWER(COALESCE(contact.phone, '')) LIKE ?
                OR LOWER(COALESCE(contact.email, '')) LIKE ?
                OR LOWER(COALESCE(contact.first_name, '')) LIKE ?
                OR LOWER(COALESCE(contact.last_name, '')) LIKE ?
                OR LOWER(COALESCE(contact.company, '')) LIKE ?
            )
            """
        )
        params.extend([needle, needle, needle, needle, needle, needle])

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
    first_name=None,
    last_name=None,
    company=None,
    contact_type=None,
    phone_normalized=None,
    email_normalized=None,
    source_type=None,
    archived_at=None,
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
        ("first_name", first_name),
        ("last_name", last_name),
        ("company", company),
        ("contact_type", contact_type),
        ("phone_normalized", phone_normalized),
        ("email_normalized", email_normalized),
        ("source_type", source_type),
        ("archived_at", archived_at),
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


def archive_contact(contact_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            UPDATE contacts
            SET archived_at = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (_now_iso(), _now_iso(), contact_id, organization_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_contact(contact_id, organization_id)


def restore_contact(contact_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            UPDATE contacts
            SET archived_at = NULL,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (_now_iso(), contact_id, organization_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_contact(contact_id, organization_id)


def find_contacts_by_normalized(
    organization_id,
    *,
    agent_id,
    phone_normalized=None,
    email_normalized=None,
    include_archived=False,
):
    organization_id = require_organization_id(organization_id)
    clauses = [
        "contact.organization_id = ?",
        "contact.agent_id = ?",
    ]
    params = [organization_id, agent_id]
    if not include_archived:
        clauses.append("contact.archived_at IS NULL")
    matches = []
    if phone_normalized:
        connection = get_connection()
        try:
            rows = connection.execute(
                _SELECT
                + f"""
                WHERE {" AND ".join(clauses)}
                    AND contact.phone_normalized = ?
                """,
                [*params, phone_normalized],
            ).fetchall()
        finally:
            connection.close()
        matches.extend(_build_contact(row) for row in rows)
    if email_normalized:
        connection = get_connection()
        try:
            rows = connection.execute(
                _SELECT
                + f"""
                WHERE {" AND ".join(clauses)}
                    AND contact.email_normalized = ?
                """,
                [*params, email_normalized],
            ).fetchall()
        finally:
            connection.close()
        matches.extend(_build_contact(row) for row in rows)
    seen = set()
    unique = []
    for item in matches:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        unique.append(item)
    return unique


def record_property_interaction(
    organization_id,
    agent_id,
    *,
    contact_id,
    property_id=None,
    interaction_type="shared",
    activity_id=None,
    label=None,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        interaction_id = execute_insert(
            cursor,
            """
            INSERT INTO contact_property_interactions (
                organization_id,
                agent_id,
                contact_id,
                property_id,
                interaction_type,
                activity_id,
                label,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                agent_id,
                contact_id,
                property_id,
                interaction_type,
                activity_id,
                label,
                now,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return interaction_id


def list_property_interactions(organization_id, contact_id, *, limit=20):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT
                id,
                organization_id,
                agent_id,
                contact_id,
                property_id,
                interaction_type,
                activity_id,
                label,
                created_at
            FROM contact_property_interactions
            WHERE organization_id = ?
                AND contact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (organization_id, contact_id, int(limit)),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row[0],
            "organization_id": row[1],
            "agent_id": row[2],
            "contact_id": row[3],
            "property_id": row[4],
            "interaction_type": row[5],
            "activity_id": row[6],
            "label": row[7] or "",
            "created_at": row[8],
        }
        for row in rows
    ]


def get_import_batch(organization_id, agent_id, import_token):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, agent_id, import_token,
                   source_type, created_count, updated_count, created_at
            FROM contact_import_batches
            WHERE organization_id = ?
                AND agent_id = ?
                AND import_token = ?
            """,
            (organization_id, agent_id, import_token),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "import_token": row[3],
        "source_type": row[4],
        "created_count": row[5],
        "updated_count": row[6],
        "created_at": row[7],
    }


def record_import_batch(
    organization_id,
    agent_id,
    *,
    import_token,
    source_type,
    created_count,
    updated_count,
):
    organization_id = require_organization_id(organization_id)
    existing = get_import_batch(organization_id, agent_id, import_token)
    if existing:
        return existing
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        execute_insert(
            cursor,
            """
            INSERT INTO contact_import_batches (
                organization_id,
                agent_id,
                import_token,
                source_type,
                created_count,
                updated_count,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                agent_id,
                import_token,
                source_type,
                created_count,
                updated_count,
                now,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        existing = get_import_batch(organization_id, agent_id, import_token)
        if existing:
            return existing
        raise
    finally:
        connection.close()
    return get_import_batch(organization_id, agent_id, import_token)


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
