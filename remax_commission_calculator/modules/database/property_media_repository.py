"""Normalized property gallery media. Org-scoped. No physical delete on source removal."""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import TenantError, require_organization_id


MEDIA_PHOTO = "photo"
STATUS_ACTIVE = "active"
STATUS_REMOVED = "removed_from_source"
STRATEGY_REMOTE = "remote_reference"
STRATEGY_COPY = "managed_copy"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _media_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "property_id": row[2],
        "media_type": row[3],
        "source": row[4],
        "external_media_id": row[5],
        "original_url": row[6],
        "storage_key": row[7],
        "storage_strategy": row[8],
        "url_kind": row[9],
        "position": row[10],
        "is_cover": bool(row[11]),
        "width": row[12],
        "height": row[13],
        "content_type": row[14],
        "content_hash": row[15],
        "external_updated_at": row[16],
        "last_synced_at": row[17],
        "status": row[18],
    }


MEDIA_SELECT = """
    SELECT id, organization_id, property_id, media_type, source,
           external_media_id, original_url, storage_key, storage_strategy,
           url_kind, position, is_cover, width, height, content_type,
           content_hash, external_updated_at, last_synced_at, status
    FROM property_media
"""


def get_property_media(media_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            MEDIA_SELECT + " WHERE id = ? AND organization_id = ?",
            (media_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    return _media_dict(row)


def find_media_by_external_id(organization_id, property_id, source, external_media_id):
    organization_id = require_organization_id(organization_id)
    source = str(source or "").strip()
    identity = str(external_media_id or "").strip()
    if not source or not identity or property_id is None:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            MEDIA_SELECT
            + """
            WHERE organization_id = ? AND property_id = ?
              AND source = ? AND external_media_id = ?
            """,
            (organization_id, int(property_id), source, identity),
        ).fetchone()
    finally:
        connection.close()
    return _media_dict(row)


def find_media_by_hash(organization_id, property_id, content_hash):
    organization_id = require_organization_id(organization_id)
    digest = str(content_hash or "").strip()
    if not digest:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            MEDIA_SELECT
            + """
            WHERE organization_id = ? AND property_id = ? AND content_hash = ?
            """,
            (organization_id, property_id, digest),
        ).fetchone()
    finally:
        connection.close()
    return _media_dict(row)


def list_property_media(organization_id, property_id, *, include_removed=False):
    organization_id = require_organization_id(organization_id)
    sql = MEDIA_SELECT + " WHERE organization_id = ? AND property_id = ?"
    params = [organization_id, property_id]
    if not include_removed:
        sql += " AND status = ?"
        params.append(STATUS_ACTIVE)
    sql += " ORDER BY is_cover DESC, position ASC, id ASC"
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [_media_dict(row) for row in rows]


def list_property_media_for_properties(organization_id, property_ids, *, include_removed=False):
    organization_id = require_organization_id(organization_id)
    ids = []
    for value in property_ids or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    sql = MEDIA_SELECT + f" WHERE organization_id = ? AND property_id IN ({placeholders})"
    params = [organization_id, *ids]
    if not include_removed:
        sql += " AND status = ?"
        params.append(STATUS_ACTIVE)
    sql += " ORDER BY is_cover DESC, position ASC, id ASC"
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [_media_dict(row) for row in rows]


def upsert_property_media(
    organization_id,
    property_id,
    *,
    source,
    external_media_id=None,
    media_type=MEDIA_PHOTO,
    original_url=None,
    storage_key=None,
    storage_strategy=STRATEGY_REMOTE,
    url_kind=None,
    position=0,
    is_cover=False,
    width=None,
    height=None,
    content_type=None,
    content_hash=None,
    external_updated_at=None,
    status=STATUS_ACTIVE,
):
    organization_id = require_organization_id(organization_id)
    existing = None
    if external_media_id:
        existing = find_media_by_external_id(
            organization_id, property_id, source, external_media_id
        )
        if existing and int(existing["organization_id"]) != int(organization_id):
            raise TenantError("Media belongs to another organization.")
        if existing and int(existing["property_id"]) != int(property_id):
            raise TenantError("Media belongs to another property.")
    if existing is None and content_hash and not external_media_id:
        existing = find_media_by_hash(organization_id, property_id, content_hash)
    now = _now_iso()
    connection = get_connection()
    try:
        if existing:
            connection.execute(
                """
                UPDATE property_media
                SET original_url = COALESCE(?, original_url),
                    storage_key = COALESCE(?, storage_key),
                    storage_strategy = ?,
                    url_kind = ?,
                    position = ?,
                    is_cover = ?,
                    width = COALESCE(?, width),
                    height = COALESCE(?, height),
                    content_type = COALESCE(?, content_type),
                    content_hash = COALESCE(?, content_hash),
                    external_updated_at = COALESCE(?, external_updated_at),
                    last_synced_at = ?,
                    status = ?
                WHERE id = ? AND organization_id = ?
                """,
                (
                    original_url,
                    storage_key,
                    storage_strategy,
                    url_kind,
                    int(position or 0),
                    1 if is_cover else 0,
                    width,
                    height,
                    content_type,
                    content_hash,
                    external_updated_at,
                    now,
                    status,
                    existing["id"],
                    organization_id,
                ),
            )
            connection.commit()
            media_id = existing["id"]
        else:
            media_id = execute_insert(
                connection.cursor(),
                """
                INSERT INTO property_media (
                    organization_id, property_id, media_type, source,
                    external_media_id, original_url, storage_key, storage_strategy,
                    url_kind, position, is_cover, width, height, content_type,
                    content_hash, external_updated_at, last_synced_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    property_id,
                    media_type,
                    source,
                    str(external_media_id).strip() if external_media_id else None,
                    original_url,
                    storage_key,
                    storage_strategy,
                    url_kind,
                    int(position or 0),
                    1 if is_cover else 0,
                    width,
                    height,
                    content_type,
                    content_hash,
                    external_updated_at,
                    now,
                    status,
                ),
            )
            connection.commit()
    finally:
        connection.close()
    return get_property_media(media_id, organization_id)


def mark_media_removed_from_source(media_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE property_media
            SET status = ?, last_synced_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (STATUS_REMOVED, _now_iso(), media_id, organization_id),
        )
        connection.commit()
    finally:
        connection.close()
