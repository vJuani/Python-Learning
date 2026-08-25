"""
Private operation documents repository.
"""

from datetime import datetime

from .connection import get_connection
from .tenant import require_organization_id


DOC_TYPE_MARTILLERO_CLIENT = "martillero_client"
DOC_TYPE_AGENT_CLIENT = "agent_client"
DOC_TYPE_UIF_FORM = "uif_form"
DOC_TYPE_UIF_ADDITIONAL = "uif_additional"
DOC_TYPE_TRANSFER = "transfer_receipt"
DOC_TYPE_RESERVATION = "reservation_deposit"
DOC_TYPE_DEED_CONTRACT = "deed_contract"
DOC_TYPE_OTHER = "other"

STRUCTURED_DOC_TYPES = (
    DOC_TYPE_MARTILLERO_CLIENT,
    DOC_TYPE_AGENT_CLIENT,
    DOC_TYPE_UIF_FORM,
    DOC_TYPE_UIF_ADDITIONAL,
    DOC_TYPE_TRANSFER,
    DOC_TYPE_RESERVATION,
    DOC_TYPE_DEED_CONTRACT,
)

VALID_DOC_TYPES = STRUCTURED_DOC_TYPES + (DOC_TYPE_OTHER,)

DOC_CATEGORIES = (
    {
        "key": "billing",
        "label_key": "docs_category_billing",
        "doc_types": (
            DOC_TYPE_MARTILLERO_CLIENT,
            DOC_TYPE_AGENT_CLIENT,
        ),
    },
    {
        "key": "uif",
        "label_key": "docs_category_uif",
        "doc_types": (
            DOC_TYPE_UIF_FORM,
            DOC_TYPE_UIF_ADDITIONAL,
        ),
    },
    {
        "key": "payments",
        "label_key": "docs_category_payments",
        "doc_types": (
            DOC_TYPE_TRANSFER,
            DOC_TYPE_RESERVATION,
            DOC_TYPE_DEED_CONTRACT,
        ),
    },
    {
        "key": "other",
        "label_key": "docs_category_other",
        "doc_types": (DOC_TYPE_OTHER,),
    },
)

DOC_TYPE_LABEL_KEYS = {
    DOC_TYPE_MARTILLERO_CLIENT: "docs_type_martillero_client",
    DOC_TYPE_AGENT_CLIENT: "docs_type_agent_client",
    DOC_TYPE_UIF_FORM: "docs_type_uif_form",
    DOC_TYPE_UIF_ADDITIONAL: "docs_type_uif_additional",
    DOC_TYPE_TRANSFER: "docs_type_transfer",
    DOC_TYPE_RESERVATION: "docs_type_reservation",
    DOC_TYPE_DEED_CONTRACT: "docs_type_deed_contract",
    DOC_TYPE_OTHER: "docs_type_other",
}


def allows_multiple_documents(doc_type):
    return doc_type == DOC_TYPE_OTHER


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
    FROM operation_documents
"""


def get_operation_document(document_id, organization_id=None):
    connection = get_connection()
    cursor = connection.cursor()

    if organization_id is None:
        cursor.execute(
            DOCUMENTS_BASE_QUERY + " WHERE id = ?",
            (document_id,),
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
                organization_id,
            ),
        )

    row = cursor.fetchone()
    connection.close()

    return build_document_dict(row)


def get_operation_document_by_type(
    organization_id,
    operation_id,
    doc_type,
):
    if allows_multiple_documents(doc_type):
        raise ValueError(
            "doc_type 'other' supports multiple documents."
        )

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
            doc_type,
        ),
    )
    row = cursor.fetchone()
    connection.close()

    return build_document_dict(row)


def list_operation_documents(
    organization_id,
    operation_id,
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
        ORDER BY doc_type ASC, id ASC
        """,
        (
            organization_id,
            operation_id,
        ),
    )
    rows = cursor.fetchall()
    connection.close()

    return [build_document_dict(row) for row in rows]


def _insert_document(
    cursor,
    organization_id,
    operation_id,
    doc_type,
    stored_name,
    original_filename,
    content_type,
    size_bytes,
    uploaded_by_user_id,
    now,
):
    cursor.execute(
        """
        INSERT INTO operation_documents (
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
            now,
        ),
    )
    return cursor.lastrowid


def upsert_operation_document(
    organization_id,
    operation_id,
    doc_type,
    stored_name,
    original_filename,
    content_type,
    size_bytes,
    uploaded_by_user_id,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    previous_stored_name = None

    if allows_multiple_documents(doc_type):
        document_id = _insert_document(
            cursor,
            organization_id,
            operation_id,
            doc_type,
            stored_name,
            original_filename,
            content_type,
            size_bytes,
            uploaded_by_user_id,
            now,
        )
    else:
        cursor.execute(
            """
            SELECT id, stored_name
            FROM operation_documents
            WHERE organization_id = ?
                AND operation_id = ?
                AND doc_type = ?
            """,
            (
                organization_id,
                operation_id,
                doc_type,
            ),
        )
        row = cursor.fetchone()

        if row is None:
            document_id = _insert_document(
                cursor,
                organization_id,
                operation_id,
                doc_type,
                stored_name,
                original_filename,
                content_type,
                size_bytes,
                uploaded_by_user_id,
                now,
            )
        else:
            document_id = row[0]
            previous_stored_name = row[1]
            cursor.execute(
                """
                UPDATE operation_documents
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
                    organization_id,
                ),
            )

    connection.commit()
    connection.close()

    document = get_operation_document(
        document_id,
        organization_id,
    )
    return document, previous_stored_name


def delete_operation_document(document_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    document = get_operation_document(
        document_id,
        organization_id,
    )

    if document is None:
        return None

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        DELETE FROM operation_documents
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            document_id,
            organization_id,
        ),
    )
    deleted = cursor.rowcount > 0
    connection.commit()
    connection.close()

    if not deleted:
        return None

    return document


# Transition aliases
get_vat_document = get_operation_document
get_vat_document_by_type = get_operation_document_by_type
list_vat_documents_for_operation = list_operation_documents
upsert_vat_document = upsert_operation_document
delete_vat_document = delete_operation_document
