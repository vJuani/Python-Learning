"""
Staging batches for CSV import preview → confirm.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from .connection import get_connection
from .tenant import require_organization_id


BATCH_TTL_HOURS = 6


def _now():
    return datetime.utcnow().replace(microsecond=0)


def _iso(value):
    return value.isoformat()


def create_csv_import_batch(
    organization_id,
    *,
    filename,
    payload,
    preview,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now()
    batch_id = str(uuid.uuid4())
    expires_at = now + timedelta(hours=BATCH_TTL_HOURS)

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO csv_import_batches (
            id,
            organization_id,
            filename,
            payload_json,
            preview_json,
            created_at,
            expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            organization_id,
            filename,
            json.dumps(payload),
            json.dumps(preview),
            _iso(now),
            _iso(expires_at),
        ),
    )

    connection.commit()
    connection.close()

    return get_csv_import_batch(batch_id, organization_id)


def delete_csv_import_batch(batch_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM csv_import_batches
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            batch_id,
            organization_id,
        ),
    )

    deleted = cursor.rowcount > 0
    connection.commit()
    connection.close()

    return deleted


def get_csv_import_batch(batch_id, organization_id):
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
            filename,
            payload_json,
            preview_json,
            created_at,
            expires_at
        FROM csv_import_batches
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            batch_id,
            organization_id,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    expires_at = datetime.fromisoformat(row[6])

    if expires_at < _now():
        delete_csv_import_batch(batch_id, organization_id)
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "filename": row[2],
        "payload": json.loads(row[3]),
        "preview": json.loads(row[4]),
        "created_at": row[5],
        "expires_at": row[6],
    }


def update_csv_import_batch(
    batch_id,
    organization_id,
    *,
    payload,
    preview,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE csv_import_batches
        SET payload_json = ?,
            preview_json = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            json.dumps(payload),
            json.dumps(preview),
            batch_id,
            organization_id,
        ),
    )

    updated = cursor.rowcount > 0
    connection.commit()
    connection.close()

    if not updated:
        return None

    return get_csv_import_batch(batch_id, organization_id)