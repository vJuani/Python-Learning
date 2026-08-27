"""
Repository for agent billing / fiscal profiles.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def build_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "legal_name": row[3] or "",
        "tax_id": row[4] or "",
        "tax_condition": row[5] or "",
        "fiscal_address": row[6] or "",
        "email": row[7] or "",
        "point_of_sale": row[8],
        "allowed_invoice_types": row[9],
        "created_at": row[10],
        "updated_at": row[11] if len(row) > 11 else row[10],
    }


PROFILE_SELECT = """
    SELECT
        id,
        organization_id,
        agent_id,
        legal_name,
        tax_id,
        tax_condition,
        fiscal_address,
        email,
        point_of_sale,
        allowed_invoice_types,
        created_at,
        updated_at
    FROM agent_billing_profiles
"""


def get_by_agent(organization_id, agent_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            PROFILE_SELECT
            + """
            WHERE organization_id = ?
                AND agent_id = ?
            """,
            (organization_id, agent_id),
        )
        return build_dict(cursor.fetchone())
    finally:
        connection.close()


def upsert_profile(
    organization_id,
    agent_id,
    *,
    legal_name,
    tax_id,
    tax_condition,
    fiscal_address,
    email,
    point_of_sale=None,
    allowed_invoice_types=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            PROFILE_SELECT
            + """
            WHERE organization_id = ?
                AND agent_id = ?
            """,
            (organization_id, agent_id),
        )
        existing = cursor.fetchone()

        if existing is None:
            profile_id = execute_insert(
                cursor,
                """
                INSERT INTO agent_billing_profiles (
                    organization_id,
                    agent_id,
                    legal_name,
                    tax_id,
                    tax_condition,
                    fiscal_address,
                    email,
                    point_of_sale,
                    allowed_invoice_types,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    organization_id,
                    agent_id,
                    (legal_name or "").strip(),
                    (tax_id or "").strip(),
                    (tax_condition or "").strip(),
                    (fiscal_address or "").strip(),
                    (email or "").strip(),
                    point_of_sale,
                    allowed_invoice_types,
                    now,
                    now,
                ),
            )
        else:
            profile_id = existing[0]
            cursor.execute(
                """
                UPDATE agent_billing_profiles
                SET
                    legal_name = ?,
                    tax_id = ?,
                    tax_condition = ?,
                    fiscal_address = ?,
                    email = ?,
                    point_of_sale = ?,
                    allowed_invoice_types = ?,
                    updated_at = ?
                WHERE id = ?
                    AND organization_id = ?
                """,
                (
                    (legal_name or "").strip(),
                    (tax_id or "").strip(),
                    (tax_condition or "").strip(),
                    (fiscal_address or "").strip(),
                    (email or "").strip(),
                    point_of_sale,
                    allowed_invoice_types,
                    now,
                    profile_id,
                    organization_id,
                ),
            )

        connection.commit()
        return get_by_agent(organization_id, agent_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
