from datetime import datetime

from .connection import get_connection
from .tenant import require_organization_id


DOC_TYPE_MARTILLERO_CLIENT = "martillero_client"
DOC_TYPE_AGENT_CLIENT = "agent_client"

VALID_DOC_TYPES = (
    DOC_TYPE_MARTILLERO_CLIENT,
    DOC_TYPE_AGENT_CLIENT,
)


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def build_document_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "operation_id": row[2],
        "doc_type": row[3],
        "stored_name": row[4],
        "original_filename": row[5],
        "content_type": row[6],
        "size_bytes": row[7],
        "uploaded_by_user_id": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }


DOCUMENTS_BASE_QUERY = """
    SELECT
        id,
        organization_id,
        operation_id,
        doc_type,
        stored_name,
        original_filename,
        content_type,
        size_bytes,
        uploaded_by_user_id,
        created_at,
        updated_at
    FROM operation_vat_documents
"""


def get_vat_document(document_id, organization_id=None):
    connection = get_connection()
    cursor = connection.cursor()

    if organization_id is None:
        cursor.execute(
            DOCUMENTS_BASE_QUERY + " WHERE id = ?",
            (document_id,)
        )
    else:
        organization_id = require_organization_id(
            organization_id
        )
        cursor.execute(
            DOCUMENTS_BASE_QUERY
            + """
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                document_id,
                organization_id
            )
        )

    row = cursor.fetchone()
    connection.close()

    return build_document_dict(row)


def get_vat_document_by_type(
    organization_id,
    operation_id,
    doc_type
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        DOCUMENTS_BASE_QUERY
        + """
        WHERE organization_id = ?
            AND operation_id = ?
            AND doc_type = ?
        """,
        (
            organization_id,
            operation_id,
            doc_type
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_document_dict(row)


def list_vat_documents_for_operation(
    organization_id,
    operation_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        DOCUMENTS_BASE_QUERY
        + """
        WHERE organization_id = ?
            AND operation_id = ?
        ORDER BY doc_type ASC
        """,
        (
            organization_id,
            operation_id
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return [build_document_dict(row) for row in rows]


def upsert_vat_document(
    organization_id,
    operation_id,
    doc_type,
    stored_name,
    original_filename,
    content_type,
    size_bytes,
    uploaded_by_user_id
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id, stored_name
        FROM operation_vat_documents
        WHERE organization_id = ?
            AND operation_id = ?
            AND doc_type = ?
        """,
        (
            organization_id,
            operation_id,
            doc_type
        )
    )
    row = cursor.fetchone()

    previous_stored_name = None

    if row is None:
        cursor.execute(
            """
            INSERT INTO operation_vat_documents (
                organization_id,
                operation_id,
                doc_type,
                stored_name,
                original_filename,
                content_type,
                size_bytes,
                uploaded_by_user_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                operation_id,
                doc_type,
                stored_name,
                original_filename,
                content_type,
                size_bytes,
                uploaded_by_user_id,
                now,
                now
            )
        )
        document_id = cursor.lastrowid
    else:
        document_id = row[0]
        previous_stored_name = row[1]
        cursor.execute(
            """
            UPDATE operation_vat_documents
            SET
                stored_name = ?,
                original_filename = ?,
                content_type = ?,
                size_bytes = ?,
                uploaded_by_user_id = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                stored_name,
                original_filename,
                content_type,
                size_bytes,
                uploaded_by_user_id,
                now,
                document_id,
                organization_id
            )
        )

    connection.commit()
    connection.close()

    document = get_vat_document(
        document_id,
        organization_id
    )

    return document, previous_stored_name


def delete_vat_document(document_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    document = get_vat_document(
        document_id,
        organization_id
    )

    if document is None:
        return None

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM operation_vat_documents
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            document_id,
            organization_id
        )
    )

    deleted = cursor.rowcount > 0
    connection.commit()
    connection.close()

    if not deleted:
        return None

    return document
