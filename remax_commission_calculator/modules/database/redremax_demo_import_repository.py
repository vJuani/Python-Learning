"""Short-lived RedREMAX demo import sessions. Listings JSON is sanitized and cleared."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

from .connection import execute_insert, get_connection
from .tenant import require_organization_id
from modules.property_sync.redremax.demo_import import SESSION_TTL_SECONDS

STATUS_PREVIEW = "preview"
STATUS_CONFIRMING = "confirming"
STATUS_CONFIRMED = "confirmed"
STATUS_EXPIRED = "expired"
STATUS_SUPERSEDED = "superseded"
STATUS_FAILED = "failed"


def _now():
    return datetime.utcnow().replace(microsecond=0)


def _now_iso():
    return _now().isoformat()


def _parse_json(raw, default=None):
    if raw in (None, ""):
        return [] if default is None else default
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [] if default is None else default
    return data


def _row_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "created_by": row[2],
        "created_at": row[3],
        "expires_at": row[4],
        "status": row[5],
        "source_total": row[6],
        "valid_count": row[7],
        "warning_count": row[8],
        "failed_count": row[9],
        "confirm_token": row[10],
        "preview": _parse_json(row[11], default={}) if row[11] else {},
        "listings": _parse_json(row[12], default=[]),
    }


def expire_stale_demo_imports(organization_id=None):
    connection = get_connection()
    try:
        if organization_id is None:
            connection.execute(
                """
                UPDATE redremax_demo_imports
                SET status = ?, listings_json = NULL
                WHERE status IN (?, ?) AND expires_at <= ?
                """,
                (STATUS_EXPIRED, STATUS_PREVIEW, STATUS_CONFIRMING, _now_iso()),
            )
        else:
            organization_id = require_organization_id(organization_id)
            connection.execute(
                """
                UPDATE redremax_demo_imports
                SET status = ?, listings_json = NULL
                WHERE organization_id = ?
                  AND status IN (?, ?)
                  AND expires_at <= ?
                """,
                (
                    STATUS_EXPIRED,
                    organization_id,
                    STATUS_PREVIEW,
                    STATUS_CONFIRMING,
                    _now_iso(),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def create_demo_import_session(
    organization_id,
    *,
    created_by,
    listings,
    preview,
    source_total,
    valid_count,
    warning_count,
    failed_count,
):
    organization_id = require_organization_id(organization_id)
    expire_stale_demo_imports(organization_id)
    now = _now()
    expires_at = (now + timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()
    token = secrets.token_urlsafe(32)
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE redremax_demo_imports
            SET status = ?, listings_json = NULL
            WHERE organization_id = ? AND status = ?
            """,
            (STATUS_SUPERSEDED, organization_id, STATUS_PREVIEW),
        )
        session_id = execute_insert(
            connection.cursor(),
            """
            INSERT INTO redremax_demo_imports (
                organization_id, created_by, created_at, expires_at, status,
                source_total, valid_count, warning_count, failed_count,
                confirm_token, preview_json, listings_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                created_by,
                now.isoformat(),
                expires_at,
                STATUS_PREVIEW,
                int(source_total or 0),
                int(valid_count or 0),
                int(warning_count or 0),
                int(failed_count or 0),
                token,
                json.dumps(preview or {}, ensure_ascii=False),
                json.dumps(listings or [], ensure_ascii=False),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_demo_import_session(session_id, organization_id)


def get_demo_import_session(session_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, created_by, created_at, expires_at, status,
                   source_total, valid_count, warning_count, failed_count,
                   confirm_token, preview_json, listings_json
            FROM redremax_demo_imports
            WHERE id = ? AND organization_id = ?
            """,
            (session_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    return _row_dict(row)


def get_demo_import_by_token(organization_id, confirm_token):
    organization_id = require_organization_id(organization_id)
    token = str(confirm_token or "").strip()
    if not token:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, created_by, created_at, expires_at, status,
                   source_total, valid_count, warning_count, failed_count,
                   confirm_token, preview_json, listings_json
            FROM redremax_demo_imports
            WHERE organization_id = ? AND confirm_token = ?
            """,
            (organization_id, token),
        ).fetchone()
    finally:
        connection.close()
    return _row_dict(row)


def claim_demo_import_session(organization_id, confirm_token):
    organization_id = require_organization_id(organization_id)
    expire_stale_demo_imports(organization_id)
    token = str(confirm_token or "").strip()
    if not token:
        return None
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE redremax_demo_imports
            SET status = ?
            WHERE organization_id = ?
              AND confirm_token = ?
              AND status = ?
              AND expires_at > ?
            """,
            (
                STATUS_CONFIRMING,
                organization_id,
                token,
                STATUS_PREVIEW,
                _now_iso(),
            ),
        )
        connection.commit()
        if cursor.rowcount == 0:
            return None
    finally:
        connection.close()
    return get_demo_import_by_token(organization_id, token)


def finish_demo_import_session(session_id, organization_id, *, status, preview=None):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        if preview is None:
            connection.execute(
                """
                UPDATE redremax_demo_imports
                SET status = ?, listings_json = NULL
                WHERE id = ? AND organization_id = ?
                """,
                (status, session_id, organization_id),
            )
        else:
            connection.execute(
                """
                UPDATE redremax_demo_imports
                SET status = ?, listings_json = NULL, preview_json = ?
                WHERE id = ? AND organization_id = ?
                """,
                (
                    status,
                    json.dumps(preview, ensure_ascii=False),
                    session_id,
                    organization_id,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def session_contains_forbidden_text(organization_id, needles):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT preview_json, listings_json, confirm_token
            FROM redremax_demo_imports
            WHERE organization_id = ?
            """,
            (organization_id,),
        ).fetchall()
    finally:
        connection.close()
    blob = " ".join(
        str(part or "") for row in rows for part in row
    ).lower()
    return any(str(needle or "").lower() in blob for needle in needles if needle)
