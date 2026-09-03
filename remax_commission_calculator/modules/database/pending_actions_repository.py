"""
Aggregated reads for the Pending Center (Phase 4A).

Every pending action is derived from the existing domain tables, so
nothing here stores or caches a pending state. Each function answers a
whole organization with a single query to keep the bell badge and the
dashboard block free of per-agent lookups.
"""

from __future__ import annotations

import json

from .connection import get_connection
from .tenant import require_organization_id


# Mirrors modules.billing_issuer_validation.validate_agent_billing_profile
# with require_email=True. CUIT is stored normalized (11 digits), so the
# length check is equivalent to the Python regex for persisted rows.
_PROFILE_READY_SQL = """
    profile.id IS NOT NULL
    AND TRIM(COALESCE(profile.legal_name, '')) != ''
    AND TRIM(COALESCE(profile.fiscal_address, '')) != ''
    AND COALESCE(profile.tax_condition, '') IN (
        'responsable_inscripto',
        'monotributo',
        'exento',
        'consumidor_final'
    )
    AND LENGTH(
        REPLACE(REPLACE(COALESCE(profile.tax_id, ''), '-', ''), ' ', '')
    ) = 11
    AND COALESCE(profile.email, '') LIKE '%_@_%._%'
"""

# A charge is billable when it is a confirmed, non-reversal debit with a
# positive gross amount (modules.invoicing.charge_is_billable).
_BILLABLE_CHARGE_SQL = """
    movement.movement_type IN ('charge', 'fee')
    AND movement.status = 'confirmed'
    AND COALESCE(movement.is_internal_reversal, 0) = 0
    AND COALESCE(movement.gross_amount, movement.amount) > 0
"""

_UNINVOICED_CHARGE_FROM = f"""
    FROM agent_account_movements AS movement
    INNER JOIN agents AS agent
        ON agent.id = movement.agent_id
        AND agent.organization_id = movement.organization_id
    LEFT JOIN agent_billing_profiles AS profile
        ON profile.agent_id = movement.agent_id
        AND profile.organization_id = movement.organization_id
    LEFT JOIN invoices AS invoice
        ON invoice.agent_account_movement_id = movement.id
        AND invoice.organization_id = movement.organization_id
        AND invoice.origin_type = 'agent_account_charge'
        AND invoice.status IN (
            'draft', 'ready_to_issue', 'issued', 'error'
        )
    WHERE movement.organization_id = ?
        AND {_BILLABLE_CHARGE_SQL}
        AND invoice.id IS NULL
"""


def _fetch_all(sql, params):
    connection = get_connection()

    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def _fetch_count(sql, params):
    rows = _fetch_all(sql, params)

    return int(rows[0][0] or 0) if rows else 0


def count_charges_without_invoice(
    organization_id,
    *,
    agent_id=None,
    only_ready_profiles=True,
):
    """
    Count billable charges that have no active invoice.

    ``only_ready_profiles`` excludes agents whose fiscal profile still
    blocks invoicing, because for those the actionable pending is the
    profile itself rather than the invoice.
    """
    organization_id = require_organization_id(organization_id)
    sql = f"SELECT COUNT(*) {_UNINVOICED_CHARGE_FROM}"
    params = [organization_id]

    if only_ready_profiles:
        sql += f" AND ({_PROFILE_READY_SQL})"

    if agent_id is not None:
        sql += " AND movement.agent_id = ?"
        params.append(agent_id)

    return _fetch_count(sql, params)


def list_charges_without_invoice(
    organization_id,
    *,
    agent_id=None,
    only_ready_profiles=True,
    limit=5,
):
    organization_id = require_organization_id(organization_id)
    sql = f"""
        SELECT
            movement.id,
            movement.agent_id,
            agent.name,
            movement.description,
            movement.currency,
            COALESCE(movement.gross_amount, movement.amount),
            movement.movement_date,
            movement.billing_period,
            movement.created_at
        {_UNINVOICED_CHARGE_FROM}
    """
    params = [organization_id]

    if only_ready_profiles:
        sql += f" AND ({_PROFILE_READY_SQL})"

    if agent_id is not None:
        sql += " AND movement.agent_id = ?"
        params.append(agent_id)

    sql += """
        ORDER BY movement.movement_date ASC, movement.id ASC
        LIMIT ?
    """
    params.append(int(limit))

    return [
        {
            "charge_movement_id": row[0],
            "agent_id": row[1],
            "agent_name": row[2],
            "description": row[3],
            "currency": row[4],
            "amount": float(row[5] or 0),
            "movement_date": row[6],
            "billing_period": row[7],
            "created_at": row[8],
        }
        for row in _fetch_all(sql, params)
    ]


_BLOCKING_PROFILE_FROM = f"""
    FROM agent_account_movements AS movement
    INNER JOIN agents AS agent
        ON agent.id = movement.agent_id
        AND agent.organization_id = movement.organization_id
    LEFT JOIN agent_billing_profiles AS profile
        ON profile.agent_id = movement.agent_id
        AND profile.organization_id = movement.organization_id
    LEFT JOIN invoices AS invoice
        ON invoice.agent_account_movement_id = movement.id
        AND invoice.organization_id = movement.organization_id
        AND invoice.origin_type = 'agent_account_charge'
        AND invoice.status IN (
            'draft', 'ready_to_issue', 'issued', 'error'
        )
    WHERE movement.organization_id = ?
        AND {_BILLABLE_CHARGE_SQL}
        AND invoice.id IS NULL
        AND NOT ({_PROFILE_READY_SQL})
"""


def count_agents_blocked_by_fiscal_profile(
    organization_id,
    *,
    agent_id=None,
):
    """Count agents whose incomplete profile blocks a real invoice."""
    organization_id = require_organization_id(organization_id)
    sql = f"""
        SELECT COUNT(*) FROM (
            SELECT movement.agent_id
            {_BLOCKING_PROFILE_FROM}
    """
    params = [organization_id]

    if agent_id is not None:
        sql += " AND movement.agent_id = ?"
        params.append(agent_id)

    sql += """
            GROUP BY movement.agent_id
        ) AS blocked
    """

    return _fetch_count(sql, params)


def list_agents_blocked_by_fiscal_profile(
    organization_id,
    *,
    agent_id=None,
    limit=5,
):
    organization_id = require_organization_id(organization_id)
    sql = f"""
        SELECT
            movement.agent_id,
            agent.name,
            COUNT(*),
            MIN(movement.movement_date)
        {_BLOCKING_PROFILE_FROM}
    """
    params = [organization_id]

    if agent_id is not None:
        sql += " AND movement.agent_id = ?"
        params.append(agent_id)

    sql += """
        GROUP BY movement.agent_id, agent.name
        ORDER BY COUNT(*) DESC, movement.agent_id ASC
        LIMIT ?
    """
    params.append(int(limit))

    return [
        {
            "agent_id": row[0],
            "agent_name": row[1],
            "charge_count": int(row[2] or 0),
            "oldest_charge_date": row[3],
        }
        for row in _fetch_all(sql, params)
    ]


# Commission readiness mirrors
# modules.operation_commission_credit.build_operation_commission_state:
# approved + closed operation, valid currency, linked agent, positive
# agent_payment and no active confirmed commission movement.
_COMMISSION_READY_FROM = """
    FROM operations AS op
    INNER JOIN agents AS agent
        ON agent.id = op.agent_id
        AND agent.organization_id = op.organization_id
    LEFT JOIN properties AS property
        ON property.id = op.property_id
        AND property.organization_id = op.organization_id
    WHERE op.organization_id = ?
        AND op.status = 'approved'
        AND op.was_invoiced = 'yes'
        AND op.currency IN ('USD', 'ARS')
        AND COALESCE(op.agent_payment, 0) > 0
        AND NOT EXISTS (
            SELECT 1
            FROM agent_account_movements AS movement
            WHERE movement.organization_id = op.organization_id
                AND movement.source_type = 'operation'
                AND movement.source_id = op.id
                AND movement.agent_id = op.agent_id
                AND movement.movement_type = 'commission'
                AND movement.status = 'confirmed'
                AND movement.commission_purpose = 'own_commission'
        )
"""


def count_commissions_ready_to_credit(
    organization_id,
    *,
    agent_id=None,
):
    organization_id = require_organization_id(organization_id)
    sql = f"SELECT COUNT(*) {_COMMISSION_READY_FROM}"
    params = [organization_id]

    if agent_id is not None:
        sql += " AND op.agent_id = ?"
        params.append(agent_id)

    return _fetch_count(sql, params)


def list_commissions_ready_to_credit(
    organization_id,
    *,
    agent_id=None,
    limit=5,
):
    organization_id = require_organization_id(organization_id)
    sql = f"""
        SELECT
            op.id,
            op.agent_id,
            agent.name,
            op.agent_payment,
            property.address,
            op.operation_date
        {_COMMISSION_READY_FROM}
    """
    params = [organization_id]

    if agent_id is not None:
        sql += " AND op.agent_id = ?"
        params.append(agent_id)

    sql += """
        ORDER BY op.operation_date ASC, op.id ASC
        LIMIT ?
    """
    params.append(int(limit))

    return [
        {
            "operation_id": row[0],
            "operation_reference": f"COM-{row[0]:06d}",
            "agent_id": row[1],
            "agent_name": row[2],
            "amount": float(row[3] or 0),
            "currency": "USD",
            "property_address": row[4],
            "operation_date": row[5],
        }
        for row in _fetch_all(sql, params)
    ]


def count_open_ai_drafts(
    organization_id,
    table,
    statuses,
    *,
    created_by_user_id=None,
):
    """Count AI receipt drafts still waiting for a human decision."""
    organization_id = require_organization_id(organization_id)

    if table not in ("agent_payment_ai_drafts", "cash_ai_drafts"):
        raise ValueError("invalid_ai_draft_table")

    if not statuses:
        return 0

    placeholders = ", ".join("?" for _ in statuses)
    sql = f"""
        SELECT COUNT(*)
        FROM {table}
        WHERE organization_id = ?
            AND status IN ({placeholders})
    """
    params = [organization_id, *statuses]

    if created_by_user_id is not None:
        sql += " AND created_by_user_id = ?"
        params.append(created_by_user_id)

    return _fetch_count(sql, params)


def list_open_agent_payment_drafts(
    organization_id,
    statuses,
    *,
    limit=5,
):
    """
    List AI payment drafts waiting for review with the agent name.

    The extracted amount/currency live in ``draft_json``; it is parsed
    in Python so this stays a single query for the whole organization.
    """
    organization_id = require_organization_id(organization_id)

    if not statuses:
        return []

    placeholders = ", ".join("?" for _ in statuses)
    rows = _fetch_all(
        f"""
        SELECT
            draft.id,
            draft.status,
            draft.agent_id,
            agent.name,
            draft.draft_json,
            draft.created_at
        FROM agent_payment_ai_drafts AS draft
        LEFT JOIN agents AS agent
            ON agent.id = draft.agent_id
            AND agent.organization_id = draft.organization_id
        WHERE draft.organization_id = ?
            AND draft.status IN ({placeholders})
        ORDER BY draft.created_at ASC, draft.id ASC
        LIMIT ?
        """,
        [organization_id, *statuses, int(limit)],
    )

    drafts = []

    for row in rows:
        payload = {}

        if row[4]:
            try:
                payload = json.loads(row[4]) or {}
            except (TypeError, ValueError):
                payload = {}

        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            amount = None

        drafts.append(
            {
                "draft_id": row[0],
                "status": row[1],
                "agent_id": row[2],
                "agent_name": row[3],
                "amount": amount,
                "currency": payload.get("currency"),
                "created_at": row[5],
            }
        )

    return drafts


def count_recurring_charges_due(organization_id, *, as_of, agent_id=None):
    organization_id = require_organization_id(organization_id)
    sql = """
        SELECT COUNT(*)
        FROM agent_recurring_charges
        WHERE organization_id = ?
            AND status = 'active'
            AND next_run_date <= ?
            AND (end_date IS NULL OR next_run_date <= end_date)
    """
    params = [organization_id, as_of]

    if agent_id is not None:
        sql += " AND agent_id = ?"
        params.append(agent_id)

    return _fetch_count(sql, params)


def list_agent_unpaid_charges(
    organization_id,
    agent_id,
    *,
    limit=5,
):
    """
    List the agent's confirmed charges with an outstanding balance.

    The remaining amount is derived from confirmed payment allocations,
    exactly like the current-account detail view.
    """
    organization_id = require_organization_id(organization_id)
    rows = _fetch_all(
        """
        SELECT
            movement.id,
            movement.description,
            movement.currency,
            COALESCE(movement.gross_amount, movement.amount),
            COALESCE(allocated.total, 0),
            movement.movement_date
        FROM agent_account_movements AS movement
        LEFT JOIN (
            SELECT
                allocation.charge_movement_id AS charge_id,
                SUM(allocation.amount) AS total
            FROM agent_account_payment_allocations AS allocation
            INNER JOIN agent_account_movements AS payment
                ON payment.id = allocation.payment_movement_id
                AND payment.organization_id = allocation.organization_id
            WHERE allocation.organization_id = ?
                AND payment.status = 'confirmed'
            GROUP BY allocation.charge_movement_id
        ) AS allocated
            ON allocated.charge_id = movement.id
        WHERE movement.organization_id = ?
            AND movement.agent_id = ?
            AND movement.movement_type IN ('charge', 'fee')
            AND movement.status = 'confirmed'
            AND COALESCE(movement.is_internal_reversal, 0) = 0
            AND COALESCE(movement.gross_amount, movement.amount)
                - COALESCE(allocated.total, 0) > 0.005
        ORDER BY movement.movement_date ASC, movement.id ASC
        LIMIT ?
        """,
        (
            organization_id,
            organization_id,
            agent_id,
            int(limit),
        ),
    )

    charges = []

    for row in rows:
        gross = float(row[3] or 0)
        allocated = float(row[4] or 0)
        charges.append(
            {
                "charge_movement_id": row[0],
                "description": row[1],
                "currency": row[2],
                "amount": gross,
                "allocated_amount": allocated,
                "remaining_amount": round(gross - allocated, 2),
                "movement_date": row[5],
            }
        )

    return charges


def list_agent_available_invoices(
    organization_id,
    agent_id,
    *,
    limit=5,
):
    """List the agent's invoices that already exist and can be viewed."""
    organization_id = require_organization_id(organization_id)
    rows = _fetch_all(
        """
        SELECT
            id,
            invoice_number_internal,
            status,
            currency,
            total_amount,
            description,
            created_at
        FROM invoices
        WHERE organization_id = ?
            AND agent_id = ?
            AND origin_type = 'agent_account_charge'
            AND status IN ('draft', 'ready_to_issue', 'issued')
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            organization_id,
            agent_id,
            int(limit),
        ),
    )

    return [
        {
            "invoice_id": row[0],
            "invoice_number_internal": row[1],
            "status": row[2],
            "currency": row[3],
            "total_amount": float(row[4] or 0),
            "description": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]
