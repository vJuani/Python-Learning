"""Organization-scoped marketing asset persistence. Snapshots are immutable."""

from __future__ import annotations

import json
from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import TenantError, require_organization_id


STATUS_GENERATED = "generated"
STATUS_SELECTED = "selected"
STATUS_ARCHIVED = "archived"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _parse_json(raw, default=None):
    if raw in (None, ""):
        return {} if default is None else default
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {} if default is None else default
    return data if isinstance(data, dict) else ({} if default is None else default)


def _asset_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "created_by_user_id": row[3],
        "property_id": row[4],
        "generation_id": row[5],
        "format": row[6],
        "style": row[7],
        "tone": row[8],
        "template": row[9],
        "status": row[10],
        "copy_snapshot": _parse_json(row[11]),
        "property_snapshot": _parse_json(row[12]),
        "agent_branding_snapshot": _parse_json(row[13]),
        "options": _parse_json(row[14]),
        "storage_key": row[15],
        "pdf_storage_key": row[16],
        "created_at": row[17],
        "updated_at": row[18],
        "property_address": row[19] if len(row) > 19 else None,
    }


BASE_SELECT = """
    SELECT a.id, a.organization_id, a.agent_id, a.created_by_user_id, a.property_id,
           a.generation_id, a.format, a.style, a.tone, a.template, a.status,
           a.copy_snapshot_json, a.property_snapshot_json,
           a.agent_branding_snapshot_json, a.options_json, a.storage_key,
           a.pdf_storage_key, a.created_at, a.updated_at, p.address
    FROM marketing_assets AS a
    LEFT JOIN properties AS p
        ON p.id = a.property_id
        AND p.organization_id = a.organization_id
"""


def create_marketing_asset(
    organization_id,
    *,
    property_id,
    generation_id,
    format,
    style,
    tone,
    template,
    copy_snapshot,
    property_snapshot,
    agent_branding_snapshot=None,
    options=None,
    agent_id=None,
    created_by_user_id=None,
    status=STATUS_GENERATED,
    storage_key=None,
    pdf_storage_key=None,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        asset_id = execute_insert(
            cursor,
            """
            INSERT INTO marketing_assets (
                organization_id, agent_id, created_by_user_id, property_id,
                generation_id, format, style, tone, template, status,
                copy_snapshot_json, property_snapshot_json,
                agent_branding_snapshot_json, options_json, storage_key,
                pdf_storage_key, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                agent_id,
                created_by_user_id,
                property_id,
                str(generation_id),
                str(format),
                str(style),
                str(tone),
                str(template),
                status,
                json.dumps(copy_snapshot or {}, ensure_ascii=False),
                json.dumps(property_snapshot or {}, ensure_ascii=False),
                json.dumps(agent_branding_snapshot or {}, ensure_ascii=False),
                json.dumps(options or {}, ensure_ascii=False),
                storage_key,
                pdf_storage_key,
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_marketing_asset(asset_id, organization_id)


def get_marketing_asset(asset_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            BASE_SELECT + " WHERE a.id = ? AND a.organization_id = ?",
            (asset_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    return _asset_dict(row)


def list_marketing_assets(organization_id, *, agent_id=None, limit=40):
    organization_id = require_organization_id(organization_id)
    sql = BASE_SELECT + " WHERE a.organization_id = ?"
    params = [organization_id]
    if agent_id is not None:
        sql += " AND a.agent_id = ?"
        params.append(agent_id)
    sql += " ORDER BY a.id DESC LIMIT ?"
    params.append(int(limit))
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [_asset_dict(row) for row in rows]


def list_generation_assets(organization_id, generation_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            BASE_SELECT
            + """
            WHERE a.organization_id = ? AND a.generation_id = ?
            ORDER BY a.id
            """,
            (organization_id, str(generation_id)),
        ).fetchall()
    finally:
        connection.close()
    return [_asset_dict(row) for row in rows]


def update_marketing_asset(asset_id, organization_id, **fields):
    organization_id = require_organization_id(organization_id)
    allowed = {
        "status",
        "copy_snapshot_json",
        "options_json",
        "storage_key",
        "pdf_storage_key",
        "agent_branding_snapshot_json",
    }
    assignments = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key.endswith("_json") and isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        assignments.append(f"{key} = ?")
        params.append(value)
    if not assignments:
        return get_marketing_asset(asset_id, organization_id)
    assignments.append("updated_at = ?")
    params.append(_now_iso())
    params.extend([asset_id, organization_id])
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE marketing_assets
            SET {", ".join(assignments)}
            WHERE id = ? AND organization_id = ?
            """,
            params,
        )
        if cursor.rowcount == 0:
            raise TenantError("Marketing asset not found in organization.")
        connection.commit()
    finally:
        connection.close()
    return get_marketing_asset(asset_id, organization_id)


def create_marketing_batch(
    organization_id,
    *,
    batch_id,
    property_id,
    prompt,
    request,
    idempotency_key=None,
    agent_id=None,
    created_by_user_id=None,
    status="queued",
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO marketing_batches (
                id, organization_id, property_id, agent_id, created_by_user_id,
                prompt, request_json, idempotency_key, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(batch_id),
                organization_id,
                property_id,
                agent_id,
                created_by_user_id,
                prompt,
                json.dumps(request or {}, ensure_ascii=False),
                idempotency_key,
                status,
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_marketing_batch(batch_id, organization_id)


def get_marketing_batch(batch_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, property_id, agent_id, created_by_user_id,
                   prompt, request_json, idempotency_key, status, created_at, updated_at
            FROM marketing_batches
            WHERE id = ? AND organization_id = ?
            """,
            (str(batch_id), organization_id),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "property_id": row[2],
        "agent_id": row[3],
        "created_by_user_id": row[4],
        "prompt": row[5],
        "request": _parse_json(row[6]),
        "idempotency_key": row[7],
        "status": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }


def find_batch_by_idempotency(organization_id, idempotency_key):
    if not idempotency_key:
        return None
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id FROM marketing_batches
            WHERE organization_id = ? AND idempotency_key = ?
            ORDER BY created_at DESC
            """,
            (organization_id, str(idempotency_key)),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return get_marketing_batch(row[0], organization_id)


def update_marketing_batch(batch_id, organization_id, **fields):
    organization_id = require_organization_id(organization_id)
    allowed = {"status", "request_json"}
    assignments = []
    params = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key.endswith("_json") and isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        assignments.append(f"{key} = ?")
        params.append(value)
    if not assignments:
        return get_marketing_batch(batch_id, organization_id)
    assignments.append("updated_at = ?")
    params.append(_now_iso())
    params.extend([str(batch_id), organization_id])
    connection = get_connection()
    try:
        connection.execute(
            f"UPDATE marketing_batches SET {', '.join(assignments)} WHERE id = ? AND organization_id = ?",
            params,
        )
        connection.commit()
    finally:
        connection.close()
    return get_marketing_batch(batch_id, organization_id)
