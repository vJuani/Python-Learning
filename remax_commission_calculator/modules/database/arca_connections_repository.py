"""Per-user ARCA connection rows. Fiscal identity lives on billing profiles."""

from __future__ import annotations

from datetime import datetime, timezone

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


ENV_HOMOLOGATION = "homologation"
ENV_PRODUCTION = "production"

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_CONFIGURING = "configuring"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"

VALID_ENVIRONMENTS = (ENV_HOMOLOGATION, ENV_PRODUCTION)
VALID_STATUSES = (
    STATUS_NOT_CONFIGURED,
    STATUS_CONFIGURING,
    STATUS_CONNECTED,
    STATUS_ERROR,
)


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_to_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "user_id": row[2],
        "environment": row[3],
        "connection_status": row[4],
        "point_of_sale": row[5] or "",
        "certificate_encrypted": row[6],
        "private_key_encrypted": row[7],
        "csr_encrypted": row[8],
        "certificate_subject": row[9] or "",
        "certificate_serial": row[10] or "",
        "certificate_expires_at": row[11] or "",
        "last_verified_at": row[12] or "",
        "last_error": row[13] or "",
        "created_at": row[14],
        "updated_at": row[15],
    }


_SELECT = """
    SELECT
        id,
        organization_id,
        user_id,
        environment,
        connection_status,
        point_of_sale,
        certificate_encrypted,
        private_key_encrypted,
        csr_encrypted,
        certificate_subject,
        certificate_serial,
        certificate_expires_at,
        last_verified_at,
        last_error,
        created_at,
        updated_at
    FROM arca_connections
"""


def get_arca_connection(
    organization_id,
    user_id,
    *,
    environment=ENV_HOMOLOGATION,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            _SELECT
            + """
            WHERE organization_id = ?
                AND user_id = ?
                AND environment = ?
            """,
            (organization_id, user_id, environment),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        connection.close()


def get_arca_connection_by_id(organization_id, connection_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            _SELECT + " WHERE organization_id = ? AND id = ?",
            (organization_id, connection_id),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        connection.close()


def upsert_arca_connection(
    organization_id,
    user_id,
    *,
    environment=ENV_HOMOLOGATION,
    connection_status=None,
    point_of_sale=None,
    certificate_encrypted=None,
    private_key_encrypted=None,
    csr_encrypted=None,
    certificate_subject=None,
    certificate_serial=None,
    certificate_expires_at=None,
    last_verified_at=None,
    last_error=None,
    clear_error=False,
    clear_credentials=False,
):
    organization_id = require_organization_id(organization_id)
    if environment not in VALID_ENVIRONMENTS:
        environment = ENV_HOMOLOGATION
    existing = get_arca_connection(
        organization_id,
        user_id,
        environment=environment,
    )
    now = _now_iso()
    db = get_connection()
    cursor = db.cursor()
    try:
        if existing is None:
            status = connection_status or STATUS_NOT_CONFIGURED
            if status not in VALID_STATUSES:
                status = STATUS_NOT_CONFIGURED
            execute_insert(
                cursor,
                """
                INSERT INTO arca_connections (
                    organization_id,
                    user_id,
                    environment,
                    connection_status,
                    point_of_sale,
                    certificate_encrypted,
                    private_key_encrypted,
                    csr_encrypted,
                    certificate_subject,
                    certificate_serial,
                    certificate_expires_at,
                    last_verified_at,
                    last_error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    user_id,
                    environment,
                    status,
                    point_of_sale,
                    certificate_encrypted,
                    private_key_encrypted,
                    csr_encrypted,
                    certificate_subject,
                    certificate_serial,
                    certificate_expires_at,
                    last_verified_at,
                    last_error,
                    now,
                    now,
                ),
            )
        else:
            status = connection_status or existing["connection_status"]
            if status not in VALID_STATUSES:
                status = existing["connection_status"]
            cert = (
                None
                if clear_credentials
                else (
                    certificate_encrypted
                    if certificate_encrypted is not None
                    else existing["certificate_encrypted"]
                )
            )
            key = (
                None
                if clear_credentials
                else (
                    private_key_encrypted
                    if private_key_encrypted is not None
                    else existing["private_key_encrypted"]
                )
            )
            csr = (
                None
                if clear_credentials
                else (
                    csr_encrypted
                    if csr_encrypted is not None
                    else existing["csr_encrypted"]
                )
            )
            cursor.execute(
                """
                UPDATE arca_connections
                SET connection_status = ?,
                    point_of_sale = ?,
                    certificate_encrypted = ?,
                    private_key_encrypted = ?,
                    csr_encrypted = ?,
                    certificate_subject = ?,
                    certificate_serial = ?,
                    certificate_expires_at = ?,
                    last_verified_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE organization_id = ?
                    AND user_id = ?
                    AND environment = ?
                """,
                (
                    status,
                    existing["point_of_sale"]
                    if point_of_sale is None
                    else point_of_sale,
                    cert,
                    key,
                    csr,
                    existing["certificate_subject"]
                    if certificate_subject is None
                    else certificate_subject,
                    existing["certificate_serial"]
                    if certificate_serial is None
                    else certificate_serial,
                    existing["certificate_expires_at"]
                    if certificate_expires_at is None
                    else certificate_expires_at,
                    existing["last_verified_at"]
                    if last_verified_at is None
                    else last_verified_at,
                    None
                    if clear_error or clear_credentials
                    else (
                        last_error
                        if last_error is not None
                        else existing["last_error"] or None
                    ),
                    now,
                    organization_id,
                    user_id,
                    environment,
                ),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return get_arca_connection(
        organization_id,
        user_id,
        environment=environment,
    )


def delete_arca_connection(
    organization_id,
    user_id,
    *,
    environment=ENV_HOMOLOGATION,
):
    organization_id = require_organization_id(organization_id)
    db = get_connection()
    try:
        db.execute(
            """
            DELETE FROM arca_connections
            WHERE organization_id = ?
                AND user_id = ?
                AND environment = ?
            """,
            (organization_id, user_id, environment),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
