"""Persistence for an agent's personal WhatsApp and Instagram channels."""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import TenantError, require_organization_id


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _as_bool(value):
    return bool(value) and str(value) not in {"0", "false", "False"}


def _row_to_dict(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "whatsapp_number": row[3] or None,
        "whatsapp_enabled": _as_bool(row[4]),
        "whatsapp_connected_at": row[5] or None,
        "instagram_handle": row[6] or None,
        "instagram_enabled": _as_bool(row[7]),
        "instagram_connected_at": row[8] or None,
        "updated_at": row[9] or None,
    }


def empty_contact_channels(organization_id, agent_id):
    return {
        "id": None,
        "organization_id": organization_id,
        "agent_id": agent_id,
        "whatsapp_number": None,
        "whatsapp_enabled": False,
        "whatsapp_connected_at": None,
        "instagram_handle": None,
        "instagram_enabled": False,
        "instagram_connected_at": None,
        "updated_at": None,
    }


def get_agent_contact_channels(agent_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT
            id,
            organization_id,
            agent_id,
            whatsapp_number,
            whatsapp_enabled,
            whatsapp_connected_at,
            instagram_handle,
            instagram_enabled,
            instagram_connected_at,
            updated_at
        FROM agent_contact_channels
        WHERE agent_id = ?
            AND organization_id = ?
        """,
        (agent_id, organization_id),
    )
    row = _row_to_dict(cursor.fetchone())
    connection.close()
    return row or empty_contact_channels(organization_id, agent_id)


def upsert_agent_contact_channels(agent_id, organization_id, **fields):
    organization_id = require_organization_id(organization_id)
    current = get_agent_contact_channels(agent_id, organization_id)
    payload = {**current, **fields, "updated_at": _now_iso()}
    connection = get_connection()
    cursor = connection.cursor()
    try:
        if current.get("id"):
            cursor.execute(
                """
                UPDATE agent_contact_channels
                SET
                    whatsapp_number = ?,
                    whatsapp_enabled = ?,
                    whatsapp_connected_at = ?,
                    instagram_handle = ?,
                    instagram_enabled = ?,
                    instagram_connected_at = ?,
                    updated_at = ?
                WHERE id = ?
                    AND organization_id = ?
                    AND agent_id = ?
                """,
                (
                    payload.get("whatsapp_number"),
                    1 if payload.get("whatsapp_enabled") else 0,
                    payload.get("whatsapp_connected_at"),
                    payload.get("instagram_handle"),
                    1 if payload.get("instagram_enabled") else 0,
                    payload.get("instagram_connected_at"),
                    payload.get("updated_at"),
                    current["id"],
                    organization_id,
                    agent_id,
                ),
            )
            if cursor.rowcount == 0:
                raise TenantError("Agent contact channels were not found.")
        else:
            execute_insert(
                cursor,
                """
                INSERT INTO agent_contact_channels (
                    organization_id,
                    agent_id,
                    whatsapp_number,
                    whatsapp_enabled,
                    whatsapp_connected_at,
                    instagram_handle,
                    instagram_enabled,
                    instagram_connected_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    agent_id,
                    payload.get("whatsapp_number"),
                    1 if payload.get("whatsapp_enabled") else 0,
                    payload.get("whatsapp_connected_at"),
                    payload.get("instagram_handle"),
                    1 if payload.get("instagram_enabled") else 0,
                    payload.get("instagram_connected_at"),
                    payload.get("updated_at"),
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_agent_contact_channels(agent_id, organization_id)
