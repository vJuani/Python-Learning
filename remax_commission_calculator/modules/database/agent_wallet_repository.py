"""
Agent wallet / ledger movements.
"""

from __future__ import annotations

from datetime import datetime

from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    require_organization_id,
)


MOVEMENT_OWN_COMMISSION = "own_commission"
MOVEMENT_TEAM_LEADER_INCOME = "team_leader_income"
MOVEMENT_ADJUSTMENT = "adjustment"
MOVEMENT_REVERSAL = "reversal"

MOVEMENT_TYPES = (
    MOVEMENT_OWN_COMMISSION,
    MOVEMENT_TEAM_LEADER_INCOME,
    MOVEMENT_ADJUSTMENT,
    MOVEMENT_REVERSAL,
)


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _build_movement_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "operation_id": row[3],
        "movement_type": row[4],
        "amount": row[5],
        "currency": row[6],
        "source_agent_id": row[7],
        "related_movement_id": row[8],
        "description": row[9],
        "reference": row[10],
        "idempotency_key": row[11],
        "created_at": row[12],
        "agent_name": row[13] if len(row) > 13 else None,
        "source_agent_name": (
            row[14] if len(row) > 14 else None
        ),
    }


MOVEMENTS_BASE_QUERY = """
    SELECT
        m.id,
        m.organization_id,
        m.agent_id,
        m.operation_id,
        m.movement_type,
        m.amount,
        m.currency,
        m.source_agent_id,
        m.related_movement_id,
        m.description,
        m.reference,
        m.idempotency_key,
        m.created_at,
        beneficiary.name,
        source.name
    FROM agent_wallet_movements AS m
    JOIN agents AS beneficiary
        ON m.agent_id = beneficiary.id
        AND m.organization_id = beneficiary.organization_id
    LEFT JOIN agents AS source
        ON m.source_agent_id = source.id
        AND m.organization_id = source.organization_id
"""


def get_wallet_movement_by_idempotency_key(
    organization_id,
    idempotency_key,
):
    organization_id = require_organization_id(
        organization_id
    )

    if not idempotency_key:
        return None

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        MOVEMENTS_BASE_QUERY
        + """
        WHERE m.organization_id = ?
            AND m.idempotency_key = ?
        LIMIT 1
        """,
        (
            organization_id,
            idempotency_key,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    return _build_movement_dict(row)


def insert_wallet_movement(
    organization_id,
    agent_id,
    *,
    movement_type,
    amount,
    currency="USD",
    operation_id=None,
    source_agent_id=None,
    related_movement_id=None,
    description=None,
    reference=None,
    idempotency_key=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    if movement_type not in MOVEMENT_TYPES:
        raise ValueError("invalid_wallet_movement_type")

    if currency not in ("USD", "ARS"):
        raise ValueError("invalid_wallet_currency")

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT id
            FROM agents
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                agent_id,
                organization_id,
            ),
        )

        if cursor.fetchone() is None:
            raise TenantError(
                "Agent was not found in this organization."
            )

        if source_agent_id is not None:
            cursor.execute(
                """
                SELECT id
                FROM agents
                WHERE id = ?
                    AND organization_id = ?
                """,
                (
                    source_agent_id,
                    organization_id,
                ),
            )

            if cursor.fetchone() is None:
                raise TenantError(
                    "Source agent was not found in this organization."
                )

        movement_id = execute_insert(
            cursor,
            """
            INSERT INTO agent_wallet_movements (
                organization_id,
                agent_id,
                operation_id,
                movement_type,
                amount,
                currency,
                source_agent_id,
                related_movement_id,
                description,
                reference,
                idempotency_key,
                created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                agent_id,
                operation_id,
                movement_type,
                float(amount),
                currency,
                source_agent_id,
                related_movement_id,
                description,
                reference,
                idempotency_key,
                _now_iso(),
            ),
        )
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return get_wallet_movement(movement_id, organization_id)


def get_wallet_movement(movement_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        MOVEMENTS_BASE_QUERY
        + """
        WHERE m.id = ?
            AND m.organization_id = ?
        """,
        (
            movement_id,
            organization_id,
        ),
    )

    row = cursor.fetchone()
    connection.close()

    return _build_movement_dict(row)


def list_wallet_movements_for_agent(
    organization_id,
    agent_id,
    *,
    limit=50,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        MOVEMENTS_BASE_QUERY
        + """
        WHERE m.organization_id = ?
            AND m.agent_id = ?
        ORDER BY m.id DESC
        LIMIT ?
        """,
        (
            organization_id,
            agent_id,
            limit,
        ),
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_movement_dict(row)
        for row in rows
    ]


def list_wallet_movements_for_operation(
    organization_id,
    operation_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        MOVEMENTS_BASE_QUERY
        + """
        WHERE m.organization_id = ?
            AND m.operation_id = ?
        ORDER BY m.id
        """,
        (
            organization_id,
            operation_id,
        ),
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_movement_dict(row)
        for row in rows
    ]


def sum_wallet_by_type(
    organization_id,
    agent_id,
    *,
    currency=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if currency:
        cursor.execute(
            """
            SELECT
                movement_type,
                COALESCE(SUM(amount), 0)
            FROM agent_wallet_movements
            WHERE organization_id = ?
                AND agent_id = ?
                AND currency = ?
            GROUP BY movement_type
            """,
            (
                organization_id,
                agent_id,
                currency,
            ),
        )
    else:
        cursor.execute(
            """
            SELECT
                movement_type,
                COALESCE(SUM(amount), 0)
            FROM agent_wallet_movements
            WHERE organization_id = ?
                AND agent_id = ?
            GROUP BY movement_type
            """,
            (
                organization_id,
                agent_id,
            ),
        )

    rows = cursor.fetchall()
    connection.close()

    totals = {
        MOVEMENT_OWN_COMMISSION: 0.0,
        MOVEMENT_TEAM_LEADER_INCOME: 0.0,
        MOVEMENT_ADJUSTMENT: 0.0,
        MOVEMENT_REVERSAL: 0.0,
    }

    for movement_type, amount in rows:
        if movement_type in totals:
            totals[movement_type] = float(amount or 0)

    totals["total"] = sum(totals.values())
    return totals


def count_credit_generations(
    organization_id,
    operation_id,
    movement_type,
    agent_id,
):
    """How many non-reversal credits exist for this tuple."""
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM agent_wallet_movements
        WHERE organization_id = ?
            AND operation_id = ?
            AND agent_id = ?
            AND movement_type = ?
        """,
        (
            organization_id,
            operation_id,
            agent_id,
            movement_type,
        ),
    )

    count = cursor.fetchone()[0]
    connection.close()

    return int(count or 0)
