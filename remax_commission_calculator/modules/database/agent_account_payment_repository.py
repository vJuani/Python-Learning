"""
Atomic agent account payment registration with cash treasury integration.
"""

from __future__ import annotations

from datetime import datetime

from modules.config import (
    BACKEND_POSTGRES,
    BACKEND_SQLITE,
    get_database_backend,
)

from .agent_account_repository import (
    CURRENCIES,
    MOVEMENT_TYPE_ADJUSTMENT,
    MOVEMENT_TYPE_PAYMENT,
    SOURCE_CASH,
    SOURCE_MANUAL,
    STATUS_CONFIRMED,
    STATUS_REVERSED,
    _ensure_agent_in_org,
    _fetch_latest_balance,
    get_agent_account_movement,
)
from .cash_treasury_repository import (
    create_cash_movement_atomic,
    reverse_cash_movement_atomic,
)
from .connection import execute_insert, get_connection
from .tenant import require_organization_id


CASH_SOURCE_AGENT_ACCOUNT_PAYMENT = "agent_account_payment"
CASH_CATEGORY_AGENT_PAYMENT = "agent_fee"
PAYMENT_TOLERANCE = 0.0001


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _round_money(value):
    return round(float(value or 0) + 1e-9, 2)


def get_charge_gross_amount(cursor, organization_id, charge_id):
    cursor.execute(
        """
        SELECT
            COALESCE(gross_amount, amount),
            currency,
            agent_id,
            status
        FROM agent_account_movements
        WHERE id = ?
            AND organization_id = ?
        """,
        (charge_id, organization_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "gross_amount": float(row[0] or 0),
        "currency": row[1],
        "agent_id": row[2],
        "status": row[3],
    }


def sum_allocated_to_charge(
    cursor,
    organization_id,
    charge_movement_id,
):
    cursor.execute(
        """
        SELECT COALESCE(SUM(a.amount), 0)
        FROM agent_account_payment_allocations AS a
        JOIN agent_account_movements AS p
            ON p.id = a.payment_movement_id
            AND p.organization_id = a.organization_id
        WHERE a.organization_id = ?
            AND a.charge_movement_id = ?
            AND p.status = ?
            AND COALESCE(p.is_internal_reversal, 0) = 0
        """,
        (
            organization_id,
            charge_movement_id,
            STATUS_CONFIRMED,
        ),
    )
    row = cursor.fetchone()
    return float(row[0] or 0)


def get_charge_remaining_amount(
    cursor,
    organization_id,
    charge_movement_id,
):
    charge = get_charge_gross_amount(
        cursor,
        organization_id,
        charge_movement_id,
    )
    if charge is None or charge["status"] != STATUS_CONFIRMED:
        return None
    allocated = sum_allocated_to_charge(
        cursor,
        organization_id,
        charge_movement_id,
    )
    remaining = _round_money(charge["gross_amount"] - allocated)
    if remaining < 0:
        remaining = 0.0
    return {
        **charge,
        "allocated_amount": allocated,
        "remaining_amount": remaining,
    }


def derive_charge_payment_status(remaining_amount, gross_amount):
    remaining = _round_money(remaining_amount)
    gross = _round_money(gross_amount)
    if remaining <= PAYMENT_TOLERANCE:
        return "paid"
    if remaining < gross - PAYMENT_TOLERANCE:
        return "partially_paid"
    return "pending"


def get_charge_payment_summary(
    organization_id,
    charge_movement_id,
):
    """Return the canonical allocated/remaining state for one charge."""
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        charge = get_charge_remaining_amount(
            cursor,
            organization_id,
            charge_movement_id,
        )
        if charge is None:
            return None
        charge["payment_status"] = derive_charge_payment_status(
            charge["remaining_amount"],
            charge["gross_amount"],
        )
        return charge
    finally:
        connection.close()


def list_charge_payment_allocations(
    organization_id,
    charge_movement_id,
):
    """Audit trail from a charge to confirmed payments and Cash."""
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                a.id,
                a.payment_movement_id,
                a.amount,
                a.currency,
                a.created_at,
                p.movement_date,
                p.source_type,
                p.source_id,
                c.receipt_number
            FROM agent_account_payment_allocations a
            INNER JOIN agent_account_movements p
                ON p.id = a.payment_movement_id
                AND p.organization_id = a.organization_id
            LEFT JOIN cash_movements c
                ON p.source_type = 'cash'
                AND c.id = p.source_id
                AND c.organization_id = p.organization_id
            WHERE a.organization_id = ?
                AND a.charge_movement_id = ?
                AND p.status = 'confirmed'
            ORDER BY a.id ASC
            """,
            (organization_id, charge_movement_id),
        )
        return [
            {
                "id": row[0],
                "payment_movement_id": row[1],
                "amount": float(row[2] or 0),
                "currency": row[3],
                "created_at": row[4],
                "payment_date": row[5],
                "source_type": row[6],
                "cash_movement_id": (
                    row[7] if row[6] == "cash" else None
                ),
                "receipt_number": row[8],
            }
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def list_payment_allocations(
    organization_id,
    payment_movement_id,
):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                a.id,
                a.charge_movement_id,
                a.currency,
                a.amount,
                a.created_at,
                c.description
            FROM agent_account_payment_allocations AS a
            LEFT JOIN agent_account_movements AS c
                ON c.id = a.charge_movement_id
                AND c.organization_id = a.organization_id
            WHERE a.organization_id = ?
                AND a.payment_movement_id = ?
            ORDER BY a.id
            """,
            (organization_id, payment_movement_id),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "charge_movement_id": row[1],
                "currency": row[2],
                "amount": float(row[3] or 0),
                "created_at": row[4],
                "charge_description": row[5],
            }
            for row in rows
        ]
    finally:
        connection.close()


def register_agent_payment_atomic(
    organization_id,
    agent_id,
    *,
    currency,
    amount,
    payment_method,
    movement_date,
    description,
    created_by_user_id,
    idempotency_key=None,
    exchange_rate=None,
    exchange_rate_date=None,
    exchange_rate_source=None,
    equivalent_amount_ars=None,
    reference_text=None,
    notes=None,
    charge_movement_id=None,
    agent_name=None,
    cash_description=None,
    treasury_account_id=None,
    attachment=None,
    receipt_number=None,
):
    """
    ``attachment`` optionally carries a stored private receipt
    (keys: path, hash, content_type, original_name) so the cash
    income keeps a link to the image inside the same transaction.
    """
    organization_id = require_organization_id(
        organization_id
    )
    if currency not in CURRENCIES:
        raise ValueError("invalid_currency")

    amount = _round_money(amount)
    if amount <= 0:
        raise ValueError("invalid_amount")

    payment_method = (payment_method or "").strip()
    if not payment_method:
        raise ValueError("payment_method_required")

    backend = get_database_backend()
    connection = get_connection()
    cursor = connection.cursor()
    now = _now_iso()
    payment_id = None

    try:
        if backend == BACKEND_SQLITE:
            cursor.execute("BEGIN IMMEDIATE")
        else:
            cursor.execute("BEGIN")

        if idempotency_key:
            cursor.execute(
                """
                SELECT id
                FROM agent_account_movements
                WHERE organization_id = ?
                    AND idempotency_key = ?
                """,
                (organization_id, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is not None:
                connection.commit()
                return get_agent_account_movement(
                    existing[0],
                    organization_id,
                )

        _ensure_agent_in_org(
            cursor,
            organization_id,
            agent_id,
        )

        allocation_amount = 0.0
        charge_description = None
        if charge_movement_id is not None:
            charge_state = get_charge_remaining_amount(
                cursor,
                organization_id,
                charge_movement_id,
            )
            if (
                charge_state is None
                or charge_state["agent_id"] != agent_id
                or charge_state["currency"] != currency
                or charge_state["remaining_amount"] <= PAYMENT_TOLERANCE
            ):
                connection.rollback()
                raise ValueError("invalid_applied_charge")

            cursor.execute(
                """
                SELECT description
                FROM agent_account_movements
                WHERE id = ?
                    AND organization_id = ?
                """,
                (charge_movement_id, organization_id),
            )
            charge_row = cursor.fetchone()
            charge_description = (
                charge_row[0] if charge_row else None
            )
            allocation_amount = min(
                amount,
                charge_state["remaining_amount"],
            )
            allocation_amount = _round_money(allocation_amount)

        balance_before = _fetch_latest_balance(
            cursor,
            organization_id,
            agent_id,
            currency,
            for_update=(backend == BACKEND_POSTGRES),
        )
        balance_after = balance_before + amount

        payment_id = execute_insert(
            cursor,
            """
            INSERT INTO agent_account_movements (
                organization_id,
                agent_id,
                movement_type,
                currency,
                amount,
                description,
                balance_before,
                balance_after,
                status,
                source_type,
                source_id,
                movement_date,
                idempotency_key,
                created_by_user_id,
                created_at,
                exchange_rate,
                exchange_rate_date,
                exchange_rate_source,
                equivalent_amount_ars,
                payment_method,
                reference_text,
                notes,
                is_internal_reversal
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, 0
            )
            """,
            (
                organization_id,
                agent_id,
                MOVEMENT_TYPE_PAYMENT,
                currency,
                amount,
                description or "Pago recibido",
                balance_before,
                balance_after,
                STATUS_CONFIRMED,
                SOURCE_MANUAL,
                charge_movement_id,
                movement_date,
                idempotency_key,
                created_by_user_id,
                now,
                float(exchange_rate)
                if exchange_rate is not None
                else None,
                exchange_rate_date,
                exchange_rate_source,
                float(equivalent_amount_ars)
                if equivalent_amount_ars is not None
                else None,
                payment_method,
                reference_text,
                notes,
            ),
        )

        cash_label = cash_description or description or "Pago recibido"
        if charge_description:
            cash_label = f"{agent_name or 'Agente'} — Pago {charge_description}"
        elif agent_name:
            cash_label = f"{agent_name} — {cash_label}"

        attachment = attachment or {}
        cash_movement_id = create_cash_movement_atomic(
            organization_id,
            movement_type="income",
            currency=currency,
            amount=amount,
            category=CASH_CATEGORY_AGENT_PAYMENT,
            description=cash_label,
            payment_method=payment_method,
            movement_date=movement_date,
            created_by_user_id=created_by_user_id,
            notes=notes,
            source=CASH_SOURCE_AGENT_ACCOUNT_PAYMENT,
            source_reference=str(payment_id),
            treasury_account_id=treasury_account_id,
            payment_method_for_default=payment_method,
            attachment_path=attachment.get("path"),
            attachment_hash=attachment.get("hash"),
            attachment_content_type=attachment.get(
                "content_type"
            ),
            attachment_original_name=attachment.get(
                "original_name"
            ),
            receipt_number=receipt_number,
            connection=connection,
            manage_transaction=False,
        )

        cursor.execute(
            """
            UPDATE agent_account_movements
            SET
                source_type = ?,
                source_id = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                SOURCE_CASH,
                cash_movement_id,
                payment_id,
                organization_id,
            ),
        )

        if charge_movement_id and allocation_amount > PAYMENT_TOLERANCE:
            execute_insert(
                cursor,
                """
                INSERT INTO agent_account_payment_allocations (
                    organization_id,
                    payment_movement_id,
                    charge_movement_id,
                    currency,
                    amount,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    payment_id,
                    charge_movement_id,
                    currency,
                    allocation_amount,
                    now,
                ),
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_agent_account_movement(
        payment_id,
        organization_id,
    )


def cancel_agent_payment_atomic(
    organization_id,
    payment_movement_id,
    *,
    created_by_user_id,
    reversal_reason,
):
    organization_id = require_organization_id(
        organization_id
    )
    reason = (reversal_reason or "").strip()
    if not reason:
        raise ValueError("reversal_reason_required")

    payment = get_agent_account_movement(
        payment_movement_id,
        organization_id,
    )
    if payment is None:
        raise ValueError("movement_not_found")
    if payment["movement_type"] != MOVEMENT_TYPE_PAYMENT:
        raise ValueError("movement_not_reversible")
    if payment["status"] != STATUS_CONFIRMED:
        raise ValueError("movement_not_reversible")

    cash_movement_id = None
    if payment.get("source_type") == SOURCE_CASH:
        cash_movement_id = payment.get("source_id")

    backend = get_database_backend()
    connection = get_connection()
    cursor = connection.cursor()
    now = _now_iso()

    try:
        if backend == BACKEND_SQLITE:
            cursor.execute("BEGIN IMMEDIATE")
        else:
            cursor.execute("BEGIN")

        cursor.execute(
            """
            SELECT
                id,
                agent_id,
                currency,
                amount,
                description,
                balance_before,
                balance_after,
                status
            FROM agent_account_movements
            WHERE id = ?
                AND organization_id = ?
            """,
            (payment_movement_id, organization_id),
        )
        original = cursor.fetchone()
        if original is None or original[7] != STATUS_CONFIRMED:
            connection.rollback()
            raise ValueError("movement_not_reversible")

        cursor.execute(
            """
            UPDATE agent_account_movements
            SET
                status = ?,
                cancelled_at = ?,
                cancelled_by_user_id = ?,
                cancellation_reason = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                STATUS_REVERSED,
                now,
                created_by_user_id,
                reason,
                payment_movement_id,
                organization_id,
            ),
        )

        reversal_description = (
            f"[internal] Cancellation of #{payment_movement_id}"
        )
        if original[4]:
            reversal_description += f": {original[4]}"

        execute_insert(
            cursor,
            """
            INSERT INTO agent_account_movements (
                organization_id,
                agent_id,
                movement_type,
                currency,
                amount,
                description,
                balance_before,
                balance_after,
                status,
                source_type,
                source_id,
                movement_date,
                created_by_user_id,
                created_at,
                reversed_movement_id,
                reversal_reason,
                is_internal_reversal
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1
            )
            """,
            (
                organization_id,
                original[1],
                MOVEMENT_TYPE_ADJUSTMENT,
                original[2],
                float(original[3] or 0),
                reversal_description,
                float(original[6] or 0),
                float(original[5] or 0),
                STATUS_CONFIRMED,
                SOURCE_MANUAL,
                payment_movement_id,
                now[:10],
                created_by_user_id,
                now,
                payment_movement_id,
                reason,
            ),
        )

        if cash_movement_id:
            reverse_cash_movement_atomic(
                organization_id,
                cash_movement_id,
                reversed_by_user_id=created_by_user_id,
                reversal_reason=reason,
                connection=connection,
                manage_transaction=False,
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_agent_account_movement(
        payment_movement_id,
        organization_id,
    )
