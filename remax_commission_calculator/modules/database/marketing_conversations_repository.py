"""Organization-scoped marketing chat threads. No provider secrets."""

from __future__ import annotations

import json
from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


ROLES = ("user", "assistant")
MESSAGE_TYPES = ("text", "generation", "status")


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


def _dump_json(value):
    if not value:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _conversation_row(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "user_id": row[2],
        "title": row[3],
        "property_id": row[4],
        "created_at": row[5],
        "updated_at": row[6],
        "property_address": row[7] if len(row) > 7 else None,
        "context": _parse_json(row[8]) if len(row) > 8 else {},
    }


def _message_row(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "conversation_id": row[1],
        "role": row[2],
        "message_type": row[3],
        "content": row[4],
        "generation_id": row[5],
        "metadata": _parse_json(row[6]),
        "created_at": row[7],
    }


CONVERSATION_SELECT = """
    SELECT c.id, c.organization_id, c.user_id, c.title, c.property_id,
           c.created_at, c.updated_at, p.address, c.context_json
    FROM marketing_conversations AS c
    LEFT JOIN properties AS p
        ON p.id = c.property_id
        AND p.organization_id = c.organization_id
"""


def create_marketing_conversation(
    organization_id,
    *,
    user_id,
    title=None,
    property_id=None,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        conversation_id = execute_insert(
            cursor,
            """
            INSERT INTO marketing_conversations (
                organization_id, user_id, title, property_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                user_id,
                (title or "").strip() or None,
                property_id,
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_marketing_conversation(conversation_id, organization_id)


def get_marketing_conversation(conversation_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            CONVERSATION_SELECT + " WHERE c.id = ? AND c.organization_id = ?",
            (conversation_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    return _conversation_row(row)


def list_marketing_conversations(organization_id, *, user_id, limit=40):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            CONVERSATION_SELECT
            + """
            WHERE c.organization_id = ? AND c.user_id = ?
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT ?
            """,
            (organization_id, user_id, int(limit)),
        ).fetchall()
    finally:
        connection.close()
    return [_conversation_row(row) for row in rows]


def update_marketing_conversation(
    conversation_id,
    organization_id,
    *,
    title=None,
    property_id=None,
    context=None,
    touch=True,
):
    organization_id = require_organization_id(organization_id)
    assignments = []
    params = []
    if title is not None:
        assignments.append("title = ?")
        params.append((title or "").strip() or None)
    if property_id is not None:
        assignments.append("property_id = ?")
        params.append(property_id or None)
    if context is not None:
        assignments.append("context_json = ?")
        params.append(_dump_json(context))
    if touch or assignments:
        assignments.append("updated_at = ?")
        params.append(_now_iso())
    if not assignments:
        return get_marketing_conversation(conversation_id, organization_id)
    params.extend([conversation_id, organization_id])
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE marketing_conversations SET "
            + ", ".join(assignments)
            + " WHERE id = ? AND organization_id = ?",
            tuple(params),
        )
        connection.commit()
    finally:
        connection.close()
    return get_marketing_conversation(conversation_id, organization_id)


def add_marketing_message(
    conversation_id,
    organization_id,
    *,
    role,
    message_type="text",
    content=None,
    generation_id=None,
    metadata=None,
):
    organization_id = require_organization_id(organization_id)
    if role not in ROLES:
        raise ValueError("invalid role")
    if message_type not in MESSAGE_TYPES:
        raise ValueError("invalid message_type")
    conversation = get_marketing_conversation(conversation_id, organization_id)
    if conversation is None:
        return None
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        message_id = execute_insert(
            cursor,
            """
            INSERT INTO marketing_messages (
                conversation_id, role, message_type, content,
                generation_id, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                role,
                message_type,
                (content or "").strip() or None,
                generation_id,
                _dump_json(metadata),
                now,
            ),
        )
        connection.execute(
            """
            UPDATE marketing_conversations
            SET updated_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (now, conversation_id, organization_id),
        )
        connection.commit()
    finally:
        connection.close()
    return get_marketing_message(message_id, organization_id)


def get_marketing_message(message_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT m.id, m.conversation_id, m.role, m.message_type, m.content,
                   m.generation_id, m.metadata_json, m.created_at
            FROM marketing_messages AS m
            INNER JOIN marketing_conversations AS c
                ON c.id = m.conversation_id
                AND c.organization_id = ?
            WHERE m.id = ?
            """,
            (organization_id, message_id),
        ).fetchone()
    finally:
        connection.close()
    return _message_row(row)


def list_marketing_messages(conversation_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT m.id, m.conversation_id, m.role, m.message_type, m.content,
                   m.generation_id, m.metadata_json, m.created_at
            FROM marketing_messages AS m
            INNER JOIN marketing_conversations AS c
                ON c.id = m.conversation_id
                AND c.organization_id = ?
            WHERE m.conversation_id = ?
            ORDER BY m.id
            """,
            (organization_id, conversation_id),
        ).fetchall()
    finally:
        connection.close()
    return [_message_row(row) for row in rows]


def last_generation_id_for_conversation(conversation_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT m.generation_id
            FROM marketing_messages AS m
            INNER JOIN marketing_conversations AS c
                ON c.id = m.conversation_id
                AND c.organization_id = ?
            WHERE m.conversation_id = ?
              AND m.generation_id IS NOT NULL
            ORDER BY m.id DESC
            LIMIT 1
            """,
            (organization_id, conversation_id),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return row[0]
