"""Organization-scoped marketing generation records. No provider secrets."""

from __future__ import annotations

import json
from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import TenantError, require_organization_id


CONTENT_TYPES = ("post", "story", "carousel", "copy", "whatsapp")
ORIGINS = ("property", "personal_brand", "office", "free")
STATUSES = ("draft", "processing", "completed", "failed", "discarded")

STATUS_DRAFT = "draft"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DISCARDED = "discarded"

_UPDATABLE = frozenset(
    {
        "status",
        "generated_copy",
        "generated_data",
        "provider",
        "model_name",
        "error_message",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
        "completed_at",
        "objective",
        "style",
        "tone",
        "format",
        "prompt_input",
    }
)


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _parse_json(raw):
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _row_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "created_by_user_id": row[2],
        "agent_id": row[3],
        "property_id": row[4],
        "parent_generation_id": row[5],
        "content_type": row[6],
        "origin": row[7],
        "status": row[8],
        "objective": row[9],
        "style": row[10],
        "tone": row[11],
        "format": row[12],
        "prompt_input": row[13],
        "generated_copy": row[14],
        "generated_data": _parse_json(row[15]),
        "provider": row[16],
        "model_name": row[17],
        "error_message": row[18],
        "input_tokens": row[19],
        "output_tokens": row[20],
        "estimated_cost": row[21],
        "created_at": row[22],
        "updated_at": row[23],
        "completed_at": row[24],
        "property_address": row[25] if len(row) > 25 else None,
        "creator_name": row[26] if len(row) > 26 else None,
    }


BASE_SELECT = """
    SELECT g.id, g.organization_id, g.created_by_user_id, g.agent_id, g.property_id,
           g.parent_generation_id, g.content_type, g.origin, g.status, g.objective,
           g.style, g.tone, g.format, g.prompt_input, g.generated_copy,
           g.generated_data, g.provider, g.model_name, g.error_message,
           g.input_tokens, g.output_tokens, g.estimated_cost,
           g.created_at, g.updated_at, g.completed_at,
           p.address,
           TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, ''))
    FROM marketing_generations AS g
    LEFT JOIN properties AS p
        ON p.id = g.property_id
        AND p.organization_id = g.organization_id
    LEFT JOIN users AS u
        ON u.id = g.created_by_user_id
        AND u.organization_id = g.organization_id
"""


def create_marketing_generation(
    organization_id,
    *,
    created_by_user_id,
    content_type,
    origin,
    agent_id=None,
    property_id=None,
    parent_generation_id=None,
    status=STATUS_DRAFT,
    objective=None,
    style=None,
    tone=None,
    format=None,
    prompt_input=None,
):
    organization_id = require_organization_id(organization_id)
    if content_type not in CONTENT_TYPES:
        raise ValueError("invalid content_type")
    if origin not in ORIGINS:
        raise ValueError("invalid origin")
    if status not in STATUSES:
        raise ValueError("invalid status")
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        generation_id = execute_insert(
            cursor,
            """
            INSERT INTO marketing_generations (
                organization_id, created_by_user_id, agent_id, property_id,
                parent_generation_id, content_type, origin, status, objective,
                style, tone, format, prompt_input, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                created_by_user_id,
                agent_id,
                property_id,
                parent_generation_id,
                content_type,
                origin,
                status,
                (objective or "").strip() or None,
                (style or "").strip() or None,
                (tone or "").strip() or None,
                (format or "").strip() or None,
                (prompt_input or "").strip() or None,
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_marketing_generation(generation_id, organization_id)


def get_marketing_generation(generation_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            BASE_SELECT + " WHERE g.id = ? AND g.organization_id = ?",
            (generation_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    return _row_dict(row)


def list_marketing_generations(
    organization_id,
    *,
    created_by_user_id=None,
    agent_id=None,
    content_type=None,
    status=None,
    limit=80,
):
    organization_id = require_organization_id(organization_id)
    clauses = ["g.organization_id = ?"]
    params = [organization_id]
    if created_by_user_id is not None:
        clauses.append("g.created_by_user_id = ?")
        params.append(created_by_user_id)
    if agent_id is not None:
        clauses.append("g.agent_id = ?")
        params.append(agent_id)
    if content_type:
        if content_type not in CONTENT_TYPES:
            raise ValueError("invalid content_type")
        clauses.append("g.content_type = ?")
        params.append(content_type)
    if status:
        if status not in STATUSES:
            raise ValueError("invalid status")
        clauses.append("g.status = ?")
        params.append(status)
    sql = (
        BASE_SELECT
        + " WHERE "
        + " AND ".join(clauses)
        + " ORDER BY g.created_at DESC, g.id DESC LIMIT ?"
    )
    params.append(max(1, min(int(limit), 200)))
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [_row_dict(row) for row in rows]


def update_marketing_generation(generation_id, organization_id, **fields):
    organization_id = require_organization_id(organization_id)
    payload = {key: value for key, value in fields.items() if key in _UPDATABLE}
    if not payload:
        return get_marketing_generation(generation_id, organization_id)
    if "generated_data" in payload and not isinstance(payload["generated_data"], str):
        payload["generated_data"] = json.dumps(payload["generated_data"] or {}, ensure_ascii=False)
    if "status" in payload and payload["status"] not in STATUSES:
        raise ValueError("invalid status")
    assignments = ["updated_at = ?"]
    params = [_now_iso()]
    for key, value in payload.items():
        column = "generated_data" if key == "generated_data" else key
        assignments.append(f"{column} = ?")
        params.append(value)
    params.extend((generation_id, organization_id))
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE marketing_generations
            SET {", ".join(assignments)}
            WHERE id = ? AND organization_id = ?
            """,
            params,
        )
        if cursor.rowcount == 0:
            raise TenantError("marketing generation was not found in this organization")
        connection.commit()
    finally:
        connection.close()
    return get_marketing_generation(generation_id, organization_id)
