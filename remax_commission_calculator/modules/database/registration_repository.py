from datetime import datetime, timedelta

from .connection import get_connection
from .tenant import require_organization_id


STATUS_EMAIL_PENDING = "email_pending"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _expires_iso_minutes(minutes=10):
    return (
        datetime.utcnow() + timedelta(minutes=minutes)
    ).replace(microsecond=0).isoformat()


def build_request_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "first_name": row[2],
        "last_name": row[3],
        "email": row[4],
        "phone": row[5],
        "password_hash": row[6],
        "status": row[7],
        "rejection_reason": row[8],
        "reviewed_by_user_id": row[9],
        "reviewed_at": row[10],
        "created_at": row[11],
        "email_verified_at": row[12],
        "approved_user_id": row[13],
        "approved_agent_id": row[14]
    }


REQUESTS_BASE_QUERY = """
    SELECT
        id,
        organization_id,
        first_name,
        last_name,
        email,
        phone,
        password_hash,
        status,
        rejection_reason,
        reviewed_by_user_id,
        reviewed_at,
        created_at,
        email_verified_at,
        approved_user_id,
        approved_agent_id
    FROM registration_requests
"""


def create_registration_request(
    organization_id,
    first_name,
    last_name,
    email,
    phone,
    password_hash
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO registration_requests (
            organization_id,
            first_name,
            last_name,
            email,
            phone,
            password_hash,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            first_name.strip(),
            last_name.strip(),
            email.strip().lower(),
            phone.strip() if phone else None,
            password_hash,
            STATUS_EMAIL_PENDING,
            _now_iso()
        )
    )

    request_id = cursor.lastrowid
    connection.commit()
    connection.close()

    return request_id


def get_registration_request(request_id, organization_id=None):
    connection = get_connection()
    cursor = connection.cursor()

    if organization_id is None:
        cursor.execute(
            REQUESTS_BASE_QUERY
            + " WHERE id = ?",
            (request_id,)
        )
    else:
        organization_id = require_organization_id(
            organization_id
        )
        cursor.execute(
            REQUESTS_BASE_QUERY
            + """
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                request_id,
                organization_id
            )
        )

    row = cursor.fetchone()
    connection.close()

    return build_request_dict(row)


def get_registration_request_by_email(
    email,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        REQUESTS_BASE_QUERY
        + """
        WHERE organization_id = ?
            AND LOWER(email) = LOWER(?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            organization_id,
            email.strip()
        )
    )

    row = cursor.fetchone()
    connection.close()

    return build_request_dict(row)


def list_registration_requests(
    organization_id,
    status=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = (
        REQUESTS_BASE_QUERY
        + " WHERE organization_id = ?"
    )
    params = [organization_id]

    if status is not None:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY id DESC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()

    return [
        build_request_dict(row)
        for row in rows
    ]


def count_pending_registration_requests(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM registration_requests
        WHERE organization_id = ?
            AND status = ?
        """,
        (
            organization_id,
            STATUS_PENDING_APPROVAL
        )
    )

    count = cursor.fetchone()[0]
    connection.close()

    return count


def _build_token_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "registration_request_id": row[1],
        "token_hash": row[2],
        "expires_at": row[3],
        "used_at": row[4],
        "created_at": row[5],
        "attempt_count": (
            row[6] if len(row) > 6 and row[6] is not None else 0
        ),
        "invalidated_at": row[7] if len(row) > 7 else None,
        "last_sent_at": row[8] if len(row) > 8 else None
    }


TOKEN_BASE_QUERY = """
    SELECT
        id,
        registration_request_id,
        token_hash,
        expires_at,
        used_at,
        created_at,
        attempt_count,
        invalidated_at,
        last_sent_at
    FROM email_verification_tokens
"""


def invalidate_active_verification_tokens(request_id, cursor=None):
    owns_connection = cursor is None

    if owns_connection:
        connection = get_connection()
        cursor = connection.cursor()
    else:
        connection = None

    cursor.execute(
        """
        UPDATE email_verification_tokens
        SET invalidated_at = ?
        WHERE registration_request_id = ?
            AND used_at IS NULL
            AND invalidated_at IS NULL
        """,
        (
            _now_iso(),
            request_id
        )
    )

    if owns_connection:
        connection.commit()
        connection.close()


def create_email_verification_token(
    request_id,
    token_hash,
    minutes=10
):
    connection = get_connection()
    cursor = connection.cursor()

    now = _now_iso()

    invalidate_active_verification_tokens(
        request_id,
        cursor=cursor
    )

    cursor.execute(
        """
        INSERT INTO email_verification_tokens (
            registration_request_id,
            token_hash,
            expires_at,
            created_at,
            attempt_count,
            last_sent_at
        )
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (
            request_id,
            token_hash,
            _expires_iso_minutes(minutes),
            now,
            now
        )
    )

    token_id = cursor.lastrowid
    connection.commit()
    connection.close()

    return token_id


def get_active_verification_token(request_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        TOKEN_BASE_QUERY
        + """
        WHERE registration_request_id = ?
            AND used_at IS NULL
            AND invalidated_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            request_id,
        )
    )

    row = cursor.fetchone()
    connection.close()

    return _build_token_dict(row)


def get_email_verification_token(token_hash):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        TOKEN_BASE_QUERY
        + """
        WHERE token_hash = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            token_hash,
        )
    )

    row = cursor.fetchone()
    connection.close()

    return _build_token_dict(row)


def increment_verification_attempt(token_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE email_verification_tokens
        SET attempt_count = COALESCE(attempt_count, 0) + 1
        WHERE id = ?
        """,
        (
            token_id,
        )
    )

    cursor.execute(
        """
        SELECT attempt_count
        FROM email_verification_tokens
        WHERE id = ?
        """,
        (
            token_id,
        )
    )

    row = cursor.fetchone()
    connection.commit()
    connection.close()

    if row is None:
        return 0

    return row[0]


def mark_email_verified(request_id, token_id):
    connection = get_connection()
    cursor = connection.cursor()

    now = _now_iso()

    cursor.execute(
        """
        UPDATE email_verification_tokens
        SET used_at = ?
        WHERE id = ?
            AND used_at IS NULL
            AND invalidated_at IS NULL
        """,
        (
            now,
            token_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        return False

    cursor.execute(
        """
        UPDATE registration_requests
        SET
            status = ?,
            email_verified_at = ?
        WHERE id = ?
            AND status = ?
        """,
        (
            STATUS_PENDING_APPROVAL,
            now,
            request_id,
            STATUS_EMAIL_PENDING
        )
    )

    updated = cursor.rowcount > 0
    connection.commit()
    connection.close()

    return updated


def reject_registration_request(
    request_id,
    organization_id,
    reviewed_by_user_id,
    rejection_reason
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE registration_requests
        SET
            status = ?,
            rejection_reason = ?,
            reviewed_by_user_id = ?,
            reviewed_at = ?
        WHERE id = ?
            AND organization_id = ?
            AND status = ?
        """,
        (
            STATUS_REJECTED,
            rejection_reason.strip(),
            reviewed_by_user_id,
            _now_iso(),
            request_id,
            organization_id,
            STATUS_PENDING_APPROVAL
        )
    )

    updated = cursor.rowcount
    connection.commit()
    connection.close()

    return updated > 0


def mark_registration_approved(
    request_id,
    organization_id,
    reviewed_by_user_id,
    user_id,
    agent_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE registration_requests
        SET
            status = ?,
            reviewed_by_user_id = ?,
            reviewed_at = ?,
            approved_user_id = ?,
            approved_agent_id = ?
        WHERE id = ?
            AND organization_id = ?
            AND status = ?
        """,
        (
            STATUS_APPROVED,
            reviewed_by_user_id,
            _now_iso(),
            user_id,
            agent_id,
            request_id,
            organization_id,
            STATUS_PENDING_APPROVAL
        )
    )

    updated = cursor.rowcount
    connection.commit()
    connection.close()

    return updated > 0


def delete_pending_registration_request(request_id):
    """
    Delete a registration request that is still pending
    (email_pending or pending_approval), including its tokens.
    Does not touch users or approved/rejected requests.
    """
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id, status, email, organization_id
        FROM registration_requests
        WHERE id = ?
        """,
        (request_id,)
    )
    row = cursor.fetchone()

    if row is None:
        connection.close()
        return None

    status = row[1]

    if status not in (
        STATUS_EMAIL_PENDING,
        STATUS_PENDING_APPROVAL
    ):
        connection.close()
        return None

    cursor.execute(
        """
        DELETE FROM email_verification_tokens
        WHERE registration_request_id = ?
        """,
        (request_id,)
    )
    cursor.execute(
        """
        DELETE FROM registration_requests
        WHERE id = ?
            AND status IN (?, ?)
        """,
        (
            request_id,
            STATUS_EMAIL_PENDING,
            STATUS_PENDING_APPROVAL
        )
    )

    deleted = cursor.rowcount > 0
    connection.commit()
    connection.close()

    if not deleted:
        return None

    return {
        "id": row[0],
        "status": status,
        "email": row[2],
        "organization_id": row[3]
    }
