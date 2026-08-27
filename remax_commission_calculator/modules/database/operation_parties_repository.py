"""
Repository for operation buyer/seller billing parties.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


VALID_PARTY_ROLES = frozenset({"buyer", "seller"})


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


PARTY_SELECT = """
    SELECT
        id,
        organization_id,
        operation_id,
        party_role,
        is_participating,
        commission_percent,
        commission_amount,
        client_legal_name,
        client_tax_id,
        client_tax_condition,
        client_fiscal_address,
        client_email,
        client_phone,
        invoice_amount,
        invoice_currency,
        invoice_exchange_rate,
        invoice_amount_set_at,
        invoice_amount_set_by_user_id,
        billing_enabled,
        billing_enabled_at,
        billing_enabled_by_user_id,
        created_at,
        updated_at
    FROM operation_parties
"""


def build_party_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "operation_id": row[2],
        "party_role": row[3],
        "is_participating": bool(row[4]),
        "commission_percent": row[5],
        "commission_amount": row[6],
        "client_legal_name": row[7] or "",
        "client_tax_id": row[8] or "",
        "client_tax_condition": row[9] or "",
        "client_fiscal_address": row[10] or "",
        "client_email": row[11] or "",
        "client_phone": row[12] or "",
        "invoice_amount": row[13],
        "invoice_currency": row[14],
        "invoice_exchange_rate": row[15],
        "invoice_amount_set_at": row[16],
        "invoice_amount_set_by_user_id": row[17],
        "billing_enabled": bool(row[18]),
        "billing_enabled_at": row[19],
        "billing_enabled_by_user_id": row[20],
        "created_at": row[21],
        "updated_at": row[22],
    }


def get_parties_for_operation(organization_id, operation_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            PARTY_SELECT
            + """
            WHERE organization_id = ?
                AND operation_id = ?
            ORDER BY
                CASE party_role
                    WHEN 'buyer' THEN 0
                    WHEN 'seller' THEN 1
                    ELSE 2
                END,
                id ASC
            """,
            (organization_id, operation_id),
        )
        return [
            build_party_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def get_party(organization_id, operation_id, party_role):
    organization_id = require_organization_id(
        organization_id
    )
    party_role = (party_role or "").strip()
    if party_role not in VALID_PARTY_ROLES:
        raise ValueError("party_role must be buyer or seller")

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            PARTY_SELECT
            + """
            WHERE organization_id = ?
                AND operation_id = ?
                AND party_role = ?
            """,
            (organization_id, operation_id, party_role),
        )
        return build_party_dict(cursor.fetchone())
    finally:
        connection.close()


def upsert_party(
    organization_id,
    operation_id,
    party_role,
    *,
    is_participating=None,
    commission_percent=None,
    commission_amount=None,
    client_legal_name=None,
    client_tax_id=None,
    client_tax_condition=None,
    client_fiscal_address=None,
    client_email=None,
    client_phone=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    party_role = (party_role or "").strip()
    if party_role not in VALID_PARTY_ROLES:
        raise ValueError("party_role must be buyer or seller")

    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            PARTY_SELECT
            + """
            WHERE organization_id = ?
                AND operation_id = ?
                AND party_role = ?
            """,
            (organization_id, operation_id, party_role),
        )
        existing = cursor.fetchone()

        if existing is None:
            execute_insert(
                cursor,
                """
                INSERT INTO operation_parties (
                    organization_id,
                    operation_id,
                    party_role,
                    is_participating,
                    commission_percent,
                    commission_amount,
                    client_legal_name,
                    client_tax_id,
                    client_tax_condition,
                    client_fiscal_address,
                    client_email,
                    client_phone,
                    billing_enabled,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?
                )
                """,
                (
                    organization_id,
                    operation_id,
                    party_role,
                    (
                        1
                        if (
                            is_participating
                            if is_participating is not None
                            else True
                        )
                        else 0
                    ),
                    commission_percent,
                    commission_amount,
                    (
                        (client_legal_name or "").strip()
                        or None
                    ),
                    (
                        (client_tax_id or "").strip()
                        or None
                    ),
                    (
                        (client_tax_condition or "").strip()
                        or None
                    ),
                    (
                        (client_fiscal_address or "").strip()
                        or None
                    ),
                    (
                        (client_email or "").strip()
                        or None
                    ),
                    (
                        (client_phone or "").strip()
                        or None
                    ),
                    now,
                    now,
                ),
            )
        else:
            party_id = existing[0]
            clauses = ["updated_at = ?"]
            params = [now]

            if is_participating is not None:
                clauses.append("is_participating = ?")
                params.append(1 if is_participating else 0)
            if commission_percent is not None:
                clauses.append("commission_percent = ?")
                params.append(commission_percent)
            if commission_amount is not None:
                clauses.append("commission_amount = ?")
                params.append(commission_amount)
            if client_legal_name is not None:
                clauses.append("client_legal_name = ?")
                params.append(
                    client_legal_name.strip() or None
                )
            if client_tax_id is not None:
                clauses.append("client_tax_id = ?")
                params.append(client_tax_id.strip() or None)
            if client_tax_condition is not None:
                clauses.append("client_tax_condition = ?")
                params.append(
                    client_tax_condition.strip() or None
                )
            if client_fiscal_address is not None:
                clauses.append("client_fiscal_address = ?")
                params.append(
                    client_fiscal_address.strip() or None
                )
            if client_email is not None:
                clauses.append("client_email = ?")
                params.append(client_email.strip() or None)
            if client_phone is not None:
                clauses.append("client_phone = ?")
                params.append(client_phone.strip() or None)

            params.extend(
                [party_id, organization_id]
            )
            cursor.execute(
                f"""
                UPDATE operation_parties
                SET {", ".join(clauses)}
                WHERE id = ?
                    AND organization_id = ?
                """,
                params,
            )

        connection.commit()
        return get_party(
            organization_id,
            operation_id,
            party_role,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_invoice_amount(
    organization_id,
    operation_id,
    party_role,
    *,
    invoice_amount,
    invoice_currency=None,
    invoice_exchange_rate=None,
    set_by_user_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    party_role = (party_role or "").strip()
    if party_role not in VALID_PARTY_ROLES:
        raise ValueError("party_role must be buyer or seller")

    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            UPDATE operation_parties
            SET
                invoice_amount = ?,
                invoice_currency = ?,
                invoice_exchange_rate = ?,
                invoice_amount_set_at = ?,
                invoice_amount_set_by_user_id = ?,
                updated_at = ?
            WHERE organization_id = ?
                AND operation_id = ?
                AND party_role = ?
            """,
            (
                invoice_amount,
                invoice_currency,
                invoice_exchange_rate,
                now,
                set_by_user_id,
                now,
                organization_id,
                operation_id,
                party_role,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Operation party not found")

        connection.commit()
        return get_party(
            organization_id,
            operation_id,
            party_role,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_billing_enabled(
    organization_id,
    operation_id,
    party_role,
    *,
    enabled,
    by_user_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    party_role = (party_role or "").strip()
    if party_role not in VALID_PARTY_ROLES:
        raise ValueError("party_role must be buyer or seller")

    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        if enabled:
            cursor.execute(
                """
                UPDATE operation_parties
                SET
                    billing_enabled = 1,
                    billing_enabled_at = ?,
                    billing_enabled_by_user_id = ?,
                    updated_at = ?
                WHERE organization_id = ?
                    AND operation_id = ?
                    AND party_role = ?
                """,
                (
                    now,
                    by_user_id,
                    now,
                    organization_id,
                    operation_id,
                    party_role,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE operation_parties
                SET
                    billing_enabled = 0,
                    updated_at = ?
                WHERE organization_id = ?
                    AND operation_id = ?
                    AND party_role = ?
                """,
                (
                    now,
                    organization_id,
                    operation_id,
                    party_role,
                ),
            )

        if cursor.rowcount == 0:
            raise ValueError("Operation party not found")

        connection.commit()
        return get_party(
            organization_id,
            operation_id,
            party_role,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_client_fields(
    organization_id,
    operation_id,
    party_role,
    *,
    client_legal_name=None,
    client_tax_id=None,
    client_tax_condition=None,
    client_fiscal_address=None,
    client_email=None,
    client_phone=None,
):
    return upsert_party(
        organization_id,
        operation_id,
        party_role,
        client_legal_name=client_legal_name,
        client_tax_id=client_tax_id,
        client_tax_condition=client_tax_condition,
        client_fiscal_address=client_fiscal_address,
        client_email=client_email,
        client_phone=client_phone,
    )


def ensure_parties_for_operation(
    organization_id,
    operation_id,
    *,
    commission_rate=None,
    total_commission=None,
    invoice_amount=None,
    invoice_currency=None,
    invoice_exchange_rate=None,
    invoice_amount_set_at=None,
    invoice_amount_set_by_user_id=None,
):
    """
    Ensure buyer and seller rows exist for an operation.

    Legacy single-rate ops: buyer gets the full commission_rate /
    total_commission; seller is not participating.
    """
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        if commission_rate is None or total_commission is None:
            cursor.execute(
                """
                SELECT
                    commission_rate,
                    total_commission,
                    invoice_amount,
                    invoice_currency,
                    invoice_exchange_rate,
                    invoice_amount_set_at,
                    invoice_amount_set_by_user_id
                FROM operations
                WHERE organization_id = ?
                    AND id = ?
                """,
                (organization_id, operation_id),
            )
            op_row = cursor.fetchone()
            if op_row is None:
                raise ValueError("Operation not found")

            if commission_rate is None:
                commission_rate = op_row[0]
            if total_commission is None:
                total_commission = op_row[1]
            if invoice_amount is None:
                invoice_amount = op_row[2]
            if invoice_currency is None:
                invoice_currency = op_row[3]
            if invoice_exchange_rate is None:
                invoice_exchange_rate = op_row[4]
            if invoice_amount_set_at is None:
                invoice_amount_set_at = op_row[5]
            if invoice_amount_set_by_user_id is None:
                invoice_amount_set_by_user_id = op_row[6]

        cursor.execute(
            """
            SELECT party_role
            FROM operation_parties
            WHERE organization_id = ?
                AND operation_id = ?
            """,
            (organization_id, operation_id),
        )
        existing_roles = {
            row[0] for row in cursor.fetchall()
        }

        if "buyer" not in existing_roles:
            cursor.execute(
                """
                INSERT INTO operation_parties (
                    organization_id,
                    operation_id,
                    party_role,
                    is_participating,
                    commission_percent,
                    commission_amount,
                    invoice_amount,
                    invoice_currency,
                    invoice_exchange_rate,
                    invoice_amount_set_at,
                    invoice_amount_set_by_user_id,
                    billing_enabled,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, 'buyer', 1, ?, ?,
                    ?, ?, ?, ?, ?, 0, ?, ?
                )
                """,
                (
                    organization_id,
                    operation_id,
                    commission_rate,
                    total_commission,
                    invoice_amount,
                    invoice_currency,
                    invoice_exchange_rate,
                    invoice_amount_set_at,
                    invoice_amount_set_by_user_id,
                    now,
                    now,
                ),
            )

        if "seller" not in existing_roles:
            cursor.execute(
                """
                INSERT INTO operation_parties (
                    organization_id,
                    operation_id,
                    party_role,
                    is_participating,
                    commission_percent,
                    commission_amount,
                    billing_enabled,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, 'seller', 0, 0, 0, 0, ?, ?
                )
                """,
                (
                    organization_id,
                    operation_id,
                    now,
                    now,
                ),
            )

        connection.commit()
        return get_parties_for_operation(
            organization_id,
            operation_id,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_parties_for_new_operation(
    organization_id,
    operation_id,
    *,
    seller_active,
    buyer_active,
    seller_commission_percent,
    buyer_commission_percent,
    seller_commission_amount,
    buyer_commission_amount,
):
    organization_id = require_organization_id(
        organization_id
    )
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        for party_role, active, percent, amount in (
            (
                "seller",
                seller_active,
                seller_commission_percent,
                seller_commission_amount,
            ),
            (
                "buyer",
                buyer_active,
                buyer_commission_percent,
                buyer_commission_amount,
            ),
        ):
            cursor.execute(
                """
                INSERT INTO operation_parties (
                    organization_id,
                    operation_id,
                    party_role,
                    is_participating,
                    commission_percent,
                    commission_amount,
                    billing_enabled,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, 0, ?, ?
                )
                """,
                (
                    organization_id,
                    operation_id,
                    party_role,
                    1 if active else 0,
                    percent if active else 0,
                    amount if active else 0,
                    now,
                    now,
                ),
            )

        connection.commit()
        return get_parties_for_operation(
            organization_id,
            operation_id,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
