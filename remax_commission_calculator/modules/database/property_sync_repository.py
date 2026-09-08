"""Idempotent external identity for properties. No portal sync."""

from __future__ import annotations

from datetime import datetime

from .connection import get_connection
from .tenant import require_organization_id


SYNC_PENDING = "pending"
SYNC_OK = "ok"
SYNC_ERROR = "error"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def find_property_by_external_identity(
    organization_id,
    *,
    external_source,
    external_id,
):
    organization_id = require_organization_id(organization_id)
    source = str(external_source or "").strip()
    identity = str(external_id or "").strip()
    if not source or not identity:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, external_source, external_id,
                   external_updated_at, sync_status
            FROM properties
            WHERE organization_id = ?
              AND external_source = ?
              AND external_id = ?
            """,
            (organization_id, source, identity),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "external_source": row[2],
        "external_id": row[3],
        "external_updated_at": row[4],
        "sync_status": row[5],
    }


def upsert_property_external_identity(
    organization_id,
    property_id,
    *,
    external_source,
    external_id,
    sync_status=SYNC_OK,
    external_updated_at=None,
):
    """Attach source identity to an existing property. Never creates a listing."""
    organization_id = require_organization_id(organization_id)
    source = str(external_source or "").strip()
    identity = str(external_id or "").strip()
    if not source or not identity:
        raise ValueError("external_source and external_id are required")
    existing = find_property_by_external_identity(
        organization_id,
        external_source=source,
        external_id=identity,
    )
    if existing and int(existing["id"]) != int(property_id):
        return existing
    stamp = external_updated_at or _now_iso()
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE properties
            SET external_source = ?,
                external_id = ?,
                external_updated_at = ?,
                sync_status = ?
            WHERE id = ? AND organization_id = ?
            """,
            (
                source,
                identity,
                stamp,
                sync_status or SYNC_OK,
                property_id,
                organization_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return find_property_by_external_identity(
        organization_id,
        external_source=source,
        external_id=identity,
    )
