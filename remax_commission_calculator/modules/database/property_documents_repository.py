"""
Private property documents and file metadata.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_ERROR = "error"

DOCUMENT_TYPE_DEED = "deed"
DOCUMENT_TYPE_FLOOR_PLAN = "floor_plan"
DOCUMENT_TYPE_REGULATIONS = "regulations"
DOCUMENT_TYPE_ABL = "abl"
DOCUMENT_TYPE_EXPENSES = "expenses"
DOCUMENT_TYPE_RESERVATION = "reservation"
DOCUMENT_TYPE_ADDITIONAL = "additional"
DOCUMENT_TYPE_OTHER = "other"

DOCUMENT_TYPES = (
    DOCUMENT_TYPE_DEED,
    DOCUMENT_TYPE_FLOOR_PLAN,
    DOCUMENT_TYPE_REGULATIONS,
    DOCUMENT_TYPE_ABL,
    DOCUMENT_TYPE_EXPENSES,
    DOCUMENT_TYPE_RESERVATION,
    DOCUMENT_TYPE_ADDITIONAL,
    DOCUMENT_TYPE_OTHER,
)

DOCUMENT_TYPE_LABEL_KEYS = {
    DOCUMENT_TYPE_DEED: "property_doc_type_deed",
    DOCUMENT_TYPE_FLOOR_PLAN: "property_doc_type_floor_plan",
    DOCUMENT_TYPE_REGULATIONS: "property_doc_type_regulations",
    DOCUMENT_TYPE_ABL: "property_doc_type_abl",
    DOCUMENT_TYPE_EXPENSES: "property_doc_type_expenses",
    DOCUMENT_TYPE_RESERVATION: "property_doc_type_reservation",
    DOCUMENT_TYPE_ADDITIONAL: "property_doc_type_additional",
    DOCUMENT_TYPE_OTHER: "property_doc_type_other",
}


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _build_document_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "property_id": row[2],
        "document_type": row[3],
        "title": row[4],
        "description": row[5],
        "status": row[6],
        "created_by_user_id": row[7],
        "archived_by_user_id": row[8],
        "archived_at": row[9],
        "normalized_pdf_stored_name": row[10],
        "normalized_pdf_size": row[11],
        "normalize_error": row[12],
        "created_at": row[13],
        "updated_at": row[14],
    }


def _build_file_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "document_id": row[2],
        "original_filename": row[3],
        "stored_name": row[4],
        "mime_type": row[5],
        "file_size": row[6],
        "sort_order": row[7],
        "is_original": bool(row[8]),
        "created_at": row[9],
    }


DOCUMENT_SELECT = """
    SELECT
        id,
        organization_id,
        property_id,
        document_type,
        title,
        description,
        status,
        created_by_user_id,
        archived_by_user_id,
        archived_at,
        normalized_pdf_stored_name,
        normalized_pdf_size,
        normalize_error,
        created_at,
        updated_at
    FROM property_documents
"""

FILE_SELECT = """
    SELECT
        id,
        organization_id,
        document_id,
        original_filename,
        stored_name,
        mime_type,
        file_size,
        sort_order,
        is_original,
        created_at
    FROM property_document_files
"""


def create_property_document(
    organization_id,
    property_id,
    document_type,
    title,
    description=None,
    created_by_user_id=None,
    status=STATUS_ACTIVE,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()
    document_id = execute_insert(
        cursor,
        """
        INSERT INTO property_documents (
            organization_id,
            property_id,
            document_type,
            title,
            description,
            status,
            created_by_user_id,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            property_id,
            document_type,
            title,
            description,
            status,
            created_by_user_id,
            now,
            now,
        ),
    )
    connection.commit()
    connection.close()

    return get_property_document(document_id, organization_id)


def get_property_document(document_id, organization_id):
    organization_id = require_organization_id(organization_id)

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        DOCUMENT_SELECT
        + """
        WHERE id = ?
            AND organization_id = ?
        """,
        (document_id, organization_id),
    )
    row = cursor.fetchone()
    connection.close()
    return _build_document_dict(row)


def list_property_documents(
    organization_id,
    property_id,
    statuses=None,
):
    organization_id = require_organization_id(organization_id)

    connection = get_connection()
    cursor = connection.cursor()

    query = (
        DOCUMENT_SELECT
        + """
        WHERE organization_id = ?
            AND property_id = ?
        """
    )
    params = [organization_id, property_id]

    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        query += f" AND status IN ({placeholders})"
        params.extend(statuses)

    query += " ORDER BY created_at DESC, id DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()
    return [_build_document_dict(row) for row in rows]


def update_property_document_fields(
    document_id,
    organization_id,
    **fields,
):
    organization_id = require_organization_id(organization_id)
    allowed = {
        "status",
        "archived_by_user_id",
        "archived_at",
        "normalized_pdf_stored_name",
        "normalized_pdf_size",
        "normalize_error",
    }
    assignments = []
    params = []

    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Unsupported document field: {key}")
        assignments.append(f"{key} = ?")
        params.append(value)

    if not assignments:
        return get_property_document(document_id, organization_id)

    assignments.append("updated_at = ?")
    params.append(_now_iso())
    params.extend([document_id, organization_id])

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        f"""
        UPDATE property_documents
        SET {", ".join(assignments)}
        WHERE id = ?
            AND organization_id = ?
        """,
        params,
    )
    connection.commit()
    connection.close()
    return get_property_document(document_id, organization_id)


def archive_property_document(
    document_id,
    organization_id,
    archived_by_user_id,
):
    return update_property_document_fields(
        document_id,
        organization_id,
        status=STATUS_ARCHIVED,
        archived_by_user_id=archived_by_user_id,
        archived_at=_now_iso(),
    )


def add_property_document_file(
    organization_id,
    document_id,
    original_filename,
    stored_name,
    mime_type,
    file_size,
    sort_order=0,
    is_original=True,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()
    file_id = execute_insert(
        cursor,
        """
        INSERT INTO property_document_files (
            organization_id,
            document_id,
            original_filename,
            stored_name,
            mime_type,
            file_size,
            sort_order,
            is_original,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            document_id,
            original_filename,
            stored_name,
            mime_type,
            file_size,
            sort_order,
            1 if is_original else 0,
            now,
        ),
    )
    connection.commit()
    connection.close()
    return get_property_document_file(file_id, organization_id)


def get_property_document_file(file_id, organization_id):
    organization_id = require_organization_id(organization_id)

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        FILE_SELECT
        + """
        WHERE id = ?
            AND organization_id = ?
        """,
        (file_id, organization_id),
    )
    row = cursor.fetchone()
    connection.close()
    return _build_file_dict(row)


def list_property_document_files(document_id, organization_id):
    organization_id = require_organization_id(organization_id)

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        FILE_SELECT
        + """
        WHERE document_id = ?
            AND organization_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (document_id, organization_id),
    )
    rows = cursor.fetchall()
    connection.close()
    return [_build_file_dict(row) for row in rows]
