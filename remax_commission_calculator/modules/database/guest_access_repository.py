from datetime import datetime

from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import require_organization_id


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def create_guest_access(
    organization_id,
    token_hash,
    created_by_user_id,
    label=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    access_id = execute_insert(
        cursor,
        """
        INSERT INTO organization_guest_access (
            organization_id,
            token_hash,
            label,
            created_by_user_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            token_hash,
            label,
            created_by_user_id,
            _now_iso()
        )
    )
    connection.commit()
    connection.close()

    return access_id


def get_guest_access_by_token_hash(token_hash):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            organization_id,
            token_hash,
            label,
            created_by_user_id,
            created_at,
            expires_at,
            revoked_at,
            last_used_at
        FROM organization_guest_access
        WHERE token_hash = ?
        """,
        (
            token_hash,
        )
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "token_hash": row[2],
        "label": row[3],
        "created_by_user_id": row[4],
        "created_at": row[5],
        "expires_at": row[6],
        "revoked_at": row[7],
        "last_used_at": row[8]
    }


def list_guest_accesses(organization_id):
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
            token_hash,
            label,
            created_by_user_id,
            created_at,
            expires_at,
            revoked_at,
            last_used_at
        FROM organization_guest_access
        WHERE organization_id = ?
        ORDER BY id DESC
        """,
        (
            organization_id,
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        {
            "id": row[0],
            "organization_id": row[1],
            "token_hash": row[2],
            "label": row[3],
            "created_by_user_id": row[4],
            "created_at": row[5],
            "expires_at": row[6],
            "revoked_at": row[7],
            "last_used_at": row[8],
            "is_active": row[7] is None
        }
        for row in rows
    ]


def revoke_guest_access(access_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE organization_guest_access
        SET revoked_at = ?
        WHERE id = ?
            AND organization_id = ?
            AND revoked_at IS NULL
        """,
        (
            _now_iso(),
            access_id,
            organization_id
        )
    )

    updated = cursor.rowcount
    connection.commit()
    connection.close()

    return updated > 0


def touch_guest_access(access_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE organization_guest_access
        SET last_used_at = ?
        WHERE id = ?
        """,
        (
            _now_iso(),
            access_id
        )
    )

    connection.commit()
    connection.close()
