"""
Persistence for per-user Google Calendar OAuth connections.
"""

from __future__ import annotations

from datetime import datetime

from .connection import IntegrityError, execute_insert, get_connection
from .tenant import require_organization_id


STATUS_ACTIVE = "active"
STATUS_ERROR = "error"
STATUS_REVOKED = "revoked"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _build_connection(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "user_id": row[2],
        "google_email": row[3] or "",
        "calendar_id": row[4] or "primary",
        "refresh_token_encrypted": row[5],
        "access_token_encrypted": row[6],
        "access_expires_at": row[7],
        "sync_token": row[8],
        "status": row[9],
        "last_synced_at": row[10],
        "last_error": row[11],
        "events_cache_json": row[12],
        "connected_at": row[13],
        "updated_at": row[14],
    }


_SELECT = """
    SELECT
        id,
        organization_id,
        user_id,
        google_email,
        calendar_id,
        refresh_token_encrypted,
        access_token_encrypted,
        access_expires_at,
        sync_token,
        status,
        last_synced_at,
        last_error,
        events_cache_json,
        connected_at,
        updated_at
    FROM google_calendar_connections
"""


def get_calendar_connection(organization_id, user_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        _SELECT
        + """
        WHERE organization_id = ?
            AND user_id = ?
        LIMIT 1
        """,
        (organization_id, user_id),
    )
    row = cursor.fetchone()
    connection.close()

    return _build_connection(row)


def upsert_calendar_connection(
    organization_id,
    user_id,
    *,
    google_email,
    refresh_token_encrypted,
    access_token_encrypted=None,
    access_expires_at=None,
    calendar_id="primary",
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    existing = get_calendar_connection(organization_id, user_id)
    db = get_connection()
    cursor = db.cursor()

    try:
        if existing is None:
            execute_insert(
                cursor,
                """
                INSERT INTO google_calendar_connections (
                    organization_id,
                    user_id,
                    google_email,
                    calendar_id,
                    refresh_token_encrypted,
                    access_token_encrypted,
                    access_expires_at,
                    status,
                    last_error,
                    connected_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    user_id,
                    google_email,
                    calendar_id or "primary",
                    refresh_token_encrypted,
                    access_token_encrypted,
                    access_expires_at,
                    STATUS_ACTIVE,
                    None,
                    now,
                    now,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE google_calendar_connections
                SET google_email = ?,
                    calendar_id = ?,
                    refresh_token_encrypted = ?,
                    access_token_encrypted = ?,
                    access_expires_at = ?,
                    status = ?,
                    last_error = NULL,
                    events_cache_json = NULL,
                    last_synced_at = NULL,
                    sync_token = NULL,
                    updated_at = ?
                WHERE organization_id = ?
                    AND user_id = ?
                """,
                (
                    google_email,
                    calendar_id or "primary",
                    refresh_token_encrypted,
                    access_token_encrypted,
                    access_expires_at,
                    STATUS_ACTIVE,
                    now,
                    organization_id,
                    user_id,
                ),
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return get_calendar_connection(organization_id, user_id)


def update_calendar_tokens(
    organization_id,
    user_id,
    *,
    access_token_encrypted,
    access_expires_at,
    refresh_token_encrypted=None,
):
    organization_id = require_organization_id(organization_id)
    db = get_connection()
    cursor = db.cursor()
    assignments = [
        "access_token_encrypted = ?",
        "access_expires_at = ?",
        "updated_at = ?",
    ]
    params = [access_token_encrypted, access_expires_at, _now_iso()]

    if refresh_token_encrypted:
        assignments.append("refresh_token_encrypted = ?")
        params.append(refresh_token_encrypted)

    params.extend([organization_id, user_id])

    try:
        cursor.execute(
            f"""
            UPDATE google_calendar_connections
            SET {", ".join(assignments)}
            WHERE organization_id = ?
                AND user_id = ?
            """,
            params,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_calendar_cache(
    organization_id,
    user_id,
    *,
    events_cache_json,
    last_synced_at=None,
    sync_token=None,
):
    organization_id = require_organization_id(organization_id)
    db = get_connection()
    cursor = db.cursor()

    try:
        cursor.execute(
            """
            UPDATE google_calendar_connections
            SET events_cache_json = ?,
                last_synced_at = ?,
                sync_token = COALESCE(?, sync_token),
                status = ?,
                last_error = NULL,
                updated_at = ?
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (
                events_cache_json,
                last_synced_at or _now_iso(),
                sync_token,
                STATUS_ACTIVE,
                _now_iso(),
                organization_id,
                user_id,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def touch_calendar_synced(organization_id, user_id):
    """Record a successful push without replacing the events overlay cache."""
    organization_id = require_organization_id(organization_id)
    db = get_connection()
    cursor = db.cursor()
    now = _now_iso()

    try:
        cursor.execute(
            """
            UPDATE google_calendar_connections
            SET last_synced_at = ?,
                updated_at = ?
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (now, now, organization_id, user_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_calendar_error(organization_id, user_id, error_text, *, revoked=False):
    organization_id = require_organization_id(organization_id)
    db = get_connection()
    cursor = db.cursor()
    status = STATUS_REVOKED if revoked else STATUS_ERROR

    try:
        cursor.execute(
            """
            UPDATE google_calendar_connections
            SET status = ?,
                last_error = ?,
                updated_at = ?
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (
                status,
                (error_text or "")[:500],
                _now_iso(),
                organization_id,
                user_id,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_calendar_connection(organization_id, user_id):
    organization_id = require_organization_id(organization_id)
    db = get_connection()
    cursor = db.cursor()

    try:
        cursor.execute(
            """
            DELETE FROM google_calendar_connections
            WHERE organization_id = ?
                AND user_id = ?
            """,
            (organization_id, user_id),
        )
        deleted = cursor.rowcount
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return bool(deleted)
