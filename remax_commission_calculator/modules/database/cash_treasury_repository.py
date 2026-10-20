"""
Office cash / treasury ledger repository.
"""

from __future__ import annotations

from datetime import datetime

from modules.config import (
    BACKEND_POSTGRES,
    BACKEND_SQLITE,
    get_database_backend,
)

from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import require_organization_id
from .treasury_accounts_repository import (
    resolve_treasury_account_id,
)


OPERATION_COLLECTION_CATEGORY = "commission_collection"
PARTY_ROLES = ("buyer", "seller")
_CLOSED_OPERATION_STATUSES = ("preclose", "preclose_void")


def _money(value):
    return round(float(value or 0), 2)


def _operation_link_label(operation_id, address):
    if operation_id is None:
        return None
    code = f"COM-{int(operation_id):06d}"
    address = (address or "").strip()
    if address:
        return f"{code} · {address}"
    return code


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _build_account_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "currency": row[2],
        "cached_balance": float(row[3] or 0),
        "updated_at": row[4],
    }


def _build_movement_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "movement_number": row[2],
        "movement_type": row[3],
        "currency": row[4],
        "amount": float(row[5] or 0),
        "category": row[6],
        "description": row[7],
        "payment_method": row[8],
        "movement_date": row[9],
        "created_at": row[10],
        "created_by_user_id": row[11],
        "updated_at": row[12],
        "updated_by_user_id": row[13],
        "status": row[14],
        "notes": row[15],
        "attachment_path": row[16],
        "source": row[17],
        "source_reference": row[18],
        "reversal_of_movement_id": row[19],
        "reversal_reason": row[20],
        "balance_before": float(row[21] or 0),
        "balance_after": float(row[22] or 0),
        "merchant": row[23],
        "receipt_number": row[24],
        "attachment_hash": row[25],
        "attachment_content_type": row[26],
        "attachment_original_name": row[27],
        "created_by_username": row[28],
        "treasury_account_id": row[29],
        "treasury_account_name": row[30],
        "display_id": (
            f"CAJ-{int(row[2]):06d}"
            if row[2] is not None
            else None
        ),
        "linked_operation_id": row[31],
        "linked_party_role": row[32],
        "linked_operation_label": _operation_link_label(
            row[31],
            row[33],
        ),
    }


MOVEMENTS_BASE_QUERY = """
    SELECT
        m.id,
        m.organization_id,
        m.movement_number,
        m.movement_type,
        m.currency,
        m.amount,
        m.category,
        m.description,
        m.payment_method,
        m.movement_date,
        m.created_at,
        m.created_by_user_id,
        m.updated_at,
        m.updated_by_user_id,
        m.status,
        m.notes,
        m.attachment_path,
        m.source,
        m.source_reference,
        m.reversal_of_movement_id,
        m.reversal_reason,
        m.balance_before,
        m.balance_after,
        m.merchant,
        m.receipt_number,
        m.attachment_hash,
        m.attachment_content_type,
        m.attachment_original_name,
        u.username,
        m.treasury_account_id,
        ta.name,
        m.linked_operation_id,
        m.linked_party_role,
        linked_property.address
    FROM cash_movements AS m
    LEFT JOIN users AS u
        ON m.created_by_user_id = u.id
    LEFT JOIN treasury_accounts AS ta
        ON m.treasury_account_id = ta.id
        AND ta.organization_id = m.organization_id
    LEFT JOIN operations AS linked_operation
        ON linked_operation.id = m.linked_operation_id
        AND linked_operation.organization_id = m.organization_id
    LEFT JOIN properties AS linked_property
        ON linked_property.id = linked_operation.property_id
        AND linked_property.organization_id = m.organization_id
"""


def get_cash_account(organization_id, currency):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                id,
                organization_id,
                currency,
                cached_balance,
                updated_at
            FROM cash_accounts
            WHERE organization_id = ?
                AND currency = ?
            """,
            (organization_id, currency),
        )
        return _build_account_dict(cursor.fetchone())
    finally:
        connection.close()


def list_cash_accounts(organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                id,
                organization_id,
                currency,
                cached_balance,
                updated_at
            FROM cash_accounts
            WHERE organization_id = ?
            ORDER BY currency
            """,
            (organization_id,),
        )
        return [
            _build_account_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def get_cash_movement(movement_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            MOVEMENTS_BASE_QUERY
            + """
            WHERE m.id = ?
                AND m.organization_id = ?
            """,
            (movement_id, organization_id),
        )
        return _build_movement_dict(cursor.fetchone())
    finally:
        connection.close()


def list_cash_movements(
    organization_id,
    *,
    currency=None,
    movement_type=None,
    category=None,
    payment_method=None,
    created_by_user_id=None,
    date_from=None,
    date_to=None,
    search=None,
    status=None,
    treasury_account_id=None,
    exclude_internal_transfers=False,
    limit=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    clauses = ["m.organization_id = ?"]
    params = [organization_id]

    if currency:
        clauses.append("m.currency = ?")
        params.append(currency)

    if movement_type:
        clauses.append("m.movement_type = ?")
        params.append(movement_type)

    if category:
        clauses.append("m.category = ?")
        params.append(category)

    if payment_method:
        clauses.append("m.payment_method = ?")
        params.append(payment_method)

    if created_by_user_id:
        clauses.append("m.created_by_user_id = ?")
        params.append(created_by_user_id)

    if date_from:
        clauses.append("m.movement_date >= ?")
        params.append(date_from)

    if date_to:
        clauses.append("m.movement_date <= ?")
        params.append(date_to)

    if status:
        clauses.append("m.status = ?")
        params.append(status)

    if treasury_account_id:
        clauses.append("m.treasury_account_id = ?")
        params.append(int(treasury_account_id))

    if exclude_internal_transfers:
        clauses.append("m.source <> 'internal_transfer'")

    if search:
        like = f"%{search.strip()}%"
        clauses.append(
            """
            (
                m.description LIKE ?
                OR m.notes LIKE ?
                OR CAST(m.movement_number AS TEXT) LIKE ?
            )
            """
        )
        params.extend([like, like, like])

    sql = (
        MOVEMENTS_BASE_QUERY
        + " WHERE "
        + " AND ".join(clauses)
        + """
        ORDER BY m.movement_date DESC, m.id DESC
        """
    )

    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(sql, params)
        return [
            _build_movement_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def sum_movements_by_type(
    organization_id,
    *,
    currency,
    movement_type,
    date_from=None,
    date_to=None,
    status="confirmed",
    treasury_account_id=None,
    exclude_internal_transfers=True,
):
    organization_id = require_organization_id(
        organization_id
    )
    clauses = [
        "organization_id = ?",
        "currency = ?",
        "movement_type = ?",
        "status = ?",
    ]
    params = [
        organization_id,
        currency,
        movement_type,
        status,
    ]

    if date_from:
        clauses.append("movement_date >= ?")
        params.append(date_from)

    if date_to:
        clauses.append("movement_date <= ?")
        params.append(date_to)

    if treasury_account_id:
        clauses.append("treasury_account_id = ?")
        params.append(int(treasury_account_id))

    if exclude_internal_transfers:
        clauses.append("source <> 'internal_transfer'")

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0)
            FROM cash_movements
            WHERE {" AND ".join(clauses)}
            """,
            params,
        )
        row = cursor.fetchone()
        return float(row[0] or 0)
    finally:
        connection.close()


def _lock_treasury_account(
    cursor,
    organization_id,
    treasury_account_id,
    *,
    for_update=False,
):
    backend = get_database_backend()
    lock_clause = ""
    if for_update and backend == BACKEND_POSTGRES:
        lock_clause = " FOR UPDATE"

    cursor.execute(
        f"""
        SELECT
            id,
            currency,
            cached_balance,
            is_active
        FROM treasury_accounts
        WHERE id = ?
            AND organization_id = ?
        {lock_clause}
        """,
        (treasury_account_id, organization_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("invalid_treasury_account")
    if not bool(row[3]):
        raise ValueError("inactive_treasury_account")
    return {
        "id": row[0],
        "currency": row[1],
        "cached_balance": float(row[2] or 0),
    }


def _sync_consolidated_cash_account(
    cursor,
    organization_id,
    currency,
    signed_delta,
    now,
    *,
    for_update=False,
):
    backend = get_database_backend()
    lock_clause = ""
    if for_update and backend == BACKEND_POSTGRES:
        lock_clause = " FOR UPDATE"

    cursor.execute(
        f"""
        SELECT id, cached_balance
        FROM cash_accounts
        WHERE organization_id = ?
            AND currency = ?
        {lock_clause}
        """,
        (organization_id, currency),
    )
    row = cursor.fetchone()

    if row is None:
        execute_insert(
            cursor,
            """
            INSERT INTO cash_accounts (
                organization_id,
                currency,
                cached_balance,
                updated_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                organization_id,
                currency,
                max(0.0, signed_delta),
                now,
            ),
        )
        return

    new_balance = float(row[1] or 0) + signed_delta
    cursor.execute(
        """
        UPDATE cash_accounts
        SET cached_balance = ?,
            updated_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (new_balance, now, row[0], organization_id),
    )


def _lock_operation_row(cursor, organization_id, operation_id):
    """Serialize concurrent collections of the same operation.

    SQLite writers already queue on BEGIN IMMEDIATE. Postgres needs
    a row lock so the pending sum is read after the other transaction
    commits.
    """
    if get_database_backend() != BACKEND_POSTGRES:
        return
    cursor.execute(
        """
        SELECT id
        FROM operations
        WHERE id = ?
            AND organization_id = ?
        FOR UPDATE
        """,
        (int(operation_id), organization_id),
    )


def collection_block_reason(state, amount, currency):
    """Return a ValueError code when a collection cannot be posted."""
    if state is None or state.get("closed"):
        return "invalid_operation"
    if not state.get("participating"):
        return "invalid_operation_side"
    if state.get("currency") != currency:
        return "operation_currency_mismatch"
    if state.get("expected", 0) <= 0.009:
        return "operation_commission_missing"
    if state.get("remaining", 0) <= 0.009:
        return "operation_side_settled"
    if float(amount) > float(state["remaining"]) + 0.009:
        return "operation_side_exceeded"
    return None


def _read_operation_side(cursor, organization_id, operation_id, party_role):
    cursor.execute(
        """
        SELECT
            operations.id,
            COALESCE(operations.currency, 'USD'),
            COALESCE(operations.status, 'approved'),
            properties.address
        FROM operations
        JOIN properties
            ON properties.id = operations.property_id
        WHERE operations.id = ?
            AND operations.organization_id = ?
        """,
        (operation_id, organization_id),
    )
    operation = cursor.fetchone()
    if operation is None:
        return None

    cursor.execute(
        """
        SELECT is_participating, commission_amount
        FROM operation_parties
        WHERE organization_id = ?
            AND operation_id = ?
            AND party_role = ?
        """,
        (organization_id, operation_id, party_role),
    )
    party = cursor.fetchone()
    participating = bool(party[0]) if party is not None else False
    expected = _money(party[1]) if participating else 0.0

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(amount), 0),
            COUNT(*)
        FROM cash_movements
        WHERE organization_id = ?
            AND linked_operation_id = ?
            AND linked_party_role = ?
            AND currency = ?
            AND movement_type = 'income'
            AND status = 'confirmed'
            AND category = ?
        """,
        (
            organization_id,
            operation_id,
            party_role,
            operation[1],
            OPERATION_COLLECTION_CATEGORY,
        ),
    )
    collected_row = cursor.fetchone()
    collected = _money(collected_row[0])
    remaining = _money(max(0.0, expected - collected))
    status = operation[2] or "approved"
    return {
        "operation_id": operation[0],
        "display_id": f"COM-{int(operation[0]):06d}",
        "property": (operation[3] or "").strip(),
        "label": _operation_link_label(operation[0], operation[3]),
        "currency": operation[1] or "USD",
        "status": status,
        "closed": status in _CLOSED_OPERATION_STATUSES,
        "party_role": party_role,
        "participating": participating,
        "expected": expected,
        "collected": collected,
        "remaining": remaining,
        "count": int(collected_row[1] or 0),
    }


def get_operation_side_collection(
    organization_id,
    operation_id,
    party_role,
):
    organization_id = require_organization_id(organization_id)
    if party_role not in PARTY_ROLES:
        return None
    connection = get_connection()
    cursor = connection.cursor()
    try:
        return _read_operation_side(
            cursor,
            organization_id,
            int(operation_id),
            party_role,
        )
    finally:
        connection.close()


def list_participating_operation_sides(organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                operations.id,
                COALESCE(operations.currency, 'USD'),
                properties.address,
                operation_parties.party_role,
                operation_parties.commission_amount
            FROM operations
            JOIN properties
                ON properties.id = operations.property_id
            JOIN operation_parties
                ON operation_parties.operation_id = operations.id
                AND operation_parties.organization_id = operations.organization_id
            WHERE operations.organization_id = ?
                AND COALESCE(operations.status, 'approved')
                    NOT IN ('preclose', 'preclose_void')
                AND operation_parties.is_participating = 1
                AND COALESCE(operation_parties.commission_amount, 0) > 0
            ORDER BY operations.id DESC,
                CASE operation_parties.party_role
                    WHEN 'buyer' THEN 0
                    WHEN 'seller' THEN 1
                    ELSE 2
                END
            """,
            (organization_id,),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT
                linked_operation_id,
                linked_party_role,
                currency,
                COALESCE(SUM(amount), 0),
                COUNT(*)
            FROM cash_movements
            WHERE organization_id = ?
                AND movement_type = 'income'
                AND status = 'confirmed'
                AND category = ?
                AND linked_operation_id IS NOT NULL
            GROUP BY
                linked_operation_id,
                linked_party_role,
                currency
            """,
            (organization_id, OPERATION_COLLECTION_CATEGORY),
        )
        collected = {
            (row[0], row[1], row[2]): (
                _money(row[3]),
                int(row[4] or 0),
            )
            for row in cursor.fetchall()
        }
    finally:
        connection.close()

    grouped = []
    by_id = {}
    for operation_id, currency, address, role, expected_raw in rows:
        expected = _money(expected_raw)
        collected_amount, count = collected.get(
            (operation_id, role, currency),
            (0.0, 0),
        )
        operation = by_id.get(operation_id)
        if operation is None:
            operation = {
                "id": operation_id,
                "display_id": f"COM-{int(operation_id):06d}",
                "property": (address or "").strip(),
                "label": _operation_link_label(
                    operation_id,
                    address,
                ),
                "currency": currency or "USD",
                "sides": [],
            }
            by_id[operation_id] = operation
            grouped.append(operation)
        remaining = _money(
            max(0.0, expected - collected_amount)
        )
        operation["sides"].append({
            "role": role,
            "expected": expected,
            "collected": collected_amount,
            "remaining": remaining,
            "selectable": remaining > 0.009,
            "count": count,
        })
    return grouped


def create_cash_movement_atomic(
    organization_id,
    *,
    movement_type,
    currency,
    amount,
    category,
    description,
    payment_method,
    movement_date,
    created_by_user_id,
    notes=None,
    source="manual",
    source_reference=None,
    attachment_path=None,
    attachment_hash=None,
    attachment_content_type=None,
    attachment_original_name=None,
    merchant=None,
    receipt_number=None,
    signed_delta=None,
    allow_negative=False,
    treasury_account_id=None,
    payment_method_for_default=None,
    linked_operation_id=None,
    linked_party_role=None,
    connection=None,
    manage_transaction=True,
):
    """
    Insert a confirmed movement and update cached balance
    in one transaction. ``signed_delta`` is the balance
    change (+income, -expense). Defaults from movement_type.
    """
    organization_id = require_organization_id(
        organization_id
    )
    amount = float(amount)

    if signed_delta is None:
        if movement_type in (
            "income",
            "opening_balance",
            "adjustment",
        ):
            signed_delta = amount
        elif movement_type == "expense":
            signed_delta = -amount
        else:
            raise ValueError(
                "signed_delta required for this type"
            )
    else:
        signed_delta = float(signed_delta)

    now = _now_iso()
    backend = get_database_backend()
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    cursor = connection.cursor()

    resolved_account_id = resolve_treasury_account_id(
        organization_id,
        currency,
        treasury_account_id=treasury_account_id,
        payment_method=(
            payment_method_for_default or payment_method
        ),
    )

    try:
        if manage_transaction:
            if backend == BACKEND_SQLITE:
                cursor.execute("BEGIN IMMEDIATE")
            else:
                cursor.execute("BEGIN")

        treasury_account = _lock_treasury_account(
            cursor,
            organization_id,
            resolved_account_id,
            for_update=True,
        )

        if treasury_account["currency"] != currency:
            if manage_transaction:
                connection.rollback()
            raise ValueError("treasury_currency_mismatch")

        if linked_operation_id is not None:
            _lock_operation_row(
                cursor,
                organization_id,
                int(linked_operation_id),
            )
            state = _read_operation_side(
                cursor,
                organization_id,
                int(linked_operation_id),
                linked_party_role,
            )
            reason = collection_block_reason(
                state,
                amount,
                currency,
            )
            if reason:
                if manage_transaction:
                    connection.rollback()
                raise ValueError(reason)

        balance_before = treasury_account["cached_balance"]
        balance_after = balance_before + signed_delta

        if (
            not allow_negative
            and signed_delta < 0
            and balance_after < -1e-9
        ):
            if manage_transaction:
                connection.rollback()
            raise ValueError("insufficient_balance")

        cursor.execute(
            """
            SELECT COALESCE(MAX(movement_number), 0)
            FROM cash_movements
            WHERE organization_id = ?
            """,
            (organization_id,),
        )
        next_number = int(cursor.fetchone()[0] or 0) + 1

        movement_id = execute_insert(
            cursor,
            """
            INSERT INTO cash_movements (
                organization_id,
                movement_number,
                movement_type,
                currency,
                amount,
                category,
                description,
                payment_method,
                movement_date,
                created_at,
                created_by_user_id,
                status,
                notes,
                attachment_path,
                source,
                source_reference,
                balance_before,
                balance_after,
                merchant,
                receipt_number,
                attachment_hash,
                attachment_content_type,
                attachment_original_name,
                treasury_account_id,
                linked_operation_id,
                linked_party_role
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'confirmed', ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                next_number,
                movement_type,
                currency,
                amount,
                category,
                description,
                payment_method,
                movement_date,
                now,
                created_by_user_id,
                notes,
                attachment_path,
                source or "manual",
                source_reference,
                balance_before,
                balance_after,
                merchant,
                receipt_number,
                attachment_hash,
                attachment_content_type,
                attachment_original_name,
                resolved_account_id,
                linked_operation_id,
                linked_party_role,
            ),
        )

        cursor.execute(
            """
            UPDATE treasury_accounts
            SET cached_balance = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                balance_after,
                resolved_account_id,
                organization_id,
            ),
        )

        _sync_consolidated_cash_account(
            cursor,
            organization_id,
            currency,
            signed_delta,
            now,
            for_update=True,
        )

        if manage_transaction:
            connection.commit()
        return movement_id
    except Exception:
        if manage_transaction:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def reverse_cash_movement_atomic(
    organization_id,
    movement_id,
    *,
    reversed_by_user_id,
    reversal_reason,
    connection=None,
    manage_transaction=True,
):
    organization_id = require_organization_id(
        organization_id
    )
    reason = (reversal_reason or "").strip()

    if not reason:
        raise ValueError("reversal_reason_required")

    now = _now_iso()
    backend = get_database_backend()
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    cursor = connection.cursor()

    try:
        if manage_transaction:
            if backend == BACKEND_SQLITE:
                cursor.execute("BEGIN IMMEDIATE")
            else:
                cursor.execute("BEGIN")

        cursor.execute(
            MOVEMENTS_BASE_QUERY
            + """
            WHERE m.id = ?
                AND m.organization_id = ?
            """,
            (movement_id, organization_id),
        )
        original = _build_movement_dict(
            cursor.fetchone()
        )

        if original is None:
            if manage_transaction:
                connection.rollback()
            raise ValueError("movement_not_found")

        if original["status"] != "confirmed":
            if manage_transaction:
                connection.rollback()
            raise ValueError("already_reversed")

        if original["movement_type"] == "reversal":
            if manage_transaction:
                connection.rollback()
            raise ValueError("cannot_reverse_reversal")

        currency = original["currency"]
        amount = float(original["amount"])
        treasury_account_id = original.get(
            "treasury_account_id"
        )

        # Opposite delta of the original effect.
        original_delta = (
            original["balance_after"]
            - original["balance_before"]
        )
        signed_delta = -float(original_delta)

        if treasury_account_id:
            treasury_account = _lock_treasury_account(
                cursor,
                organization_id,
                treasury_account_id,
                for_update=True,
            )
            balance_before = treasury_account[
                "cached_balance"
            ]
        else:
            if backend == BACKEND_POSTGRES:
                cursor.execute(
                    """
                    SELECT id, cached_balance
                    FROM cash_accounts
                    WHERE organization_id = ?
                        AND currency = ?
                    FOR UPDATE
                    """,
                    (organization_id, currency),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, cached_balance
                    FROM cash_accounts
                    WHERE organization_id = ?
                        AND currency = ?
                    """,
                    (organization_id, currency),
                )
            account_row = cursor.fetchone()
            if account_row is None:
                if manage_transaction:
                    connection.rollback()
                raise ValueError("account_missing")
            balance_before = float(account_row[1] or 0)

        balance_after = balance_before + signed_delta

        if balance_after < -1e-9:
            if manage_transaction:
                connection.rollback()
            raise ValueError("insufficient_balance")

        cursor.execute(
            """
            SELECT COALESCE(MAX(movement_number), 0)
            FROM cash_movements
            WHERE organization_id = ?
            """,
            (organization_id,),
        )
        next_number = int(cursor.fetchone()[0] or 0) + 1

        reversal_id = execute_insert(
            cursor,
            """
            INSERT INTO cash_movements (
                organization_id,
                movement_number,
                movement_type,
                currency,
                amount,
                category,
                description,
                payment_method,
                movement_date,
                created_at,
                created_by_user_id,
                status,
                notes,
                source,
                reversal_of_movement_id,
                reversal_reason,
                balance_before,
                balance_after,
                treasury_account_id
            )
            VALUES (
                ?, ?, 'reversal', ?, ?, ?, ?, ?, ?, ?, ?,
                'confirmed', ?, 'manual', ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                next_number,
                currency,
                amount,
                original["category"],
                f"Reversal of {original['display_id']}",
                original["payment_method"],
                original["movement_date"],
                now,
                reversed_by_user_id,
                reason,
                movement_id,
                reason,
                balance_before,
                balance_after,
                treasury_account_id,
            ),
        )

        cursor.execute(
            """
            UPDATE cash_movements
            SET status = 'reversed',
                updated_at = ?,
                updated_by_user_id = ?,
                reversal_reason = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                now,
                reversed_by_user_id,
                reason,
                movement_id,
                organization_id,
            ),
        )

        if treasury_account_id:
            cursor.execute(
                """
                UPDATE treasury_accounts
                SET cached_balance = ?
                WHERE id = ?
                    AND organization_id = ?
                """,
                (
                    balance_after,
                    treasury_account_id,
                    organization_id,
                ),
            )

        _sync_consolidated_cash_account(
            cursor,
            organization_id,
            currency,
            signed_delta,
            now,
            for_update=True,
        )

        if manage_transaction:
            connection.commit()
        return reversal_id
    except Exception:
        if manage_transaction:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def find_duplicate_cash_movements(
    organization_id,
    *,
    attachment_hash=None,
    amount=None,
    currency=None,
    movement_date=None,
    merchant=None,
    receipt_number=None,
    limit=10,
):
    """Find confirmed movements that look like duplicates."""
    organization_id = require_organization_id(
        organization_id
    )
    matches = []
    connection = get_connection()
    cursor = connection.cursor()

    try:
        if attachment_hash:
            cursor.execute(
                MOVEMENTS_BASE_QUERY
                + """
                WHERE m.organization_id = ?
                    AND m.status = 'confirmed'
                    AND m.attachment_hash = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (
                    organization_id,
                    attachment_hash,
                    int(limit),
                ),
            )
            matches.extend(
                _build_movement_dict(row)
                for row in cursor.fetchall()
            )

        has_second_signal = bool(
            (merchant or "").strip()
            or (receipt_number or "").strip()
        )

        if (
            amount is not None
            and currency
            and movement_date
            and has_second_signal
        ):
            clauses = [
                "m.organization_id = ?",
                "m.status = 'confirmed'",
                "m.currency = ?",
                "m.movement_date = ?",
                "ABS(m.amount - ?) < 0.01",
            ]
            params = [
                organization_id,
                currency,
                movement_date,
                float(amount),
            ]

            if merchant:
                clauses.append(
                    "LOWER(COALESCE(m.merchant, '')) = ?"
                )
                params.append(merchant.strip().lower())

            if receipt_number:
                clauses.append(
                    "LOWER(COALESCE(m.receipt_number, '')) = ?"
                )
                params.append(
                    receipt_number.strip().lower()
                )

            params.append(int(limit))
            cursor.execute(
                MOVEMENTS_BASE_QUERY
                + " WHERE "
                + " AND ".join(clauses)
                + """
                ORDER BY m.id DESC
                LIMIT ?
                """,
                params,
            )
            existing = {item["id"] for item in matches}
            for row in cursor.fetchall():
                item = _build_movement_dict(row)
                if item["id"] not in existing:
                    matches.append(item)

        return matches[:limit]
    finally:
        connection.close()


INTERNAL_TRANSFER_CATEGORY = "internal_transfer"


def create_internal_transfer_atomic(
    organization_id,
    *,
    from_account_id,
    to_account_id,
    amount,
    movement_date,
    created_by_user_id,
    description=None,
    notes=None,
    idempotency_key=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    amount = float(amount)
    if amount <= 0:
        raise ValueError("invalid_amount")

    if from_account_id == to_account_id:
        raise ValueError("same_account_transfer")

    backend = get_database_backend()
    connection = get_connection()
    cursor = connection.cursor()
    now = _now_iso()

    try:
        if backend == BACKEND_SQLITE:
            cursor.execute("BEGIN IMMEDIATE")
        else:
            cursor.execute("BEGIN")

        if idempotency_key:
            token = f"transfer:{idempotency_key}"
            cursor.execute(
                """
                SELECT id
                FROM cash_movements
                WHERE organization_id = ?
                    AND source = 'internal_transfer'
                    AND source_reference = ?
                LIMIT 1
                """,
                (organization_id, token),
            )
            existing = cursor.fetchone()
            if existing is not None:
                connection.commit()
                return {
                    "out_movement_id": existing[0],
                    "in_movement_id": None,
                }

        source_account = _lock_treasury_account(
            cursor,
            organization_id,
            from_account_id,
            for_update=True,
        )
        dest_account = _lock_treasury_account(
            cursor,
            organization_id,
            to_account_id,
            for_update=True,
        )

        if source_account["currency"] != dest_account["currency"]:
            connection.rollback()
            raise ValueError("cross_currency_transfer")

        currency = source_account["currency"]
        transfer_ref = (
            f"transfer:{idempotency_key}"
            if idempotency_key
            else f"transfer:{now}:{from_account_id}:{to_account_id}:{amount}"
        )
        label = description or (
            f"Transfer {source_account['id']} → {dest_account['id']}"
        )

        out_id = create_cash_movement_atomic(
            organization_id,
            movement_type="adjustment",
            currency=currency,
            amount=amount,
            category=INTERNAL_TRANSFER_CATEGORY,
            description=f"Transfer out — {label}",
            payment_method="transfer",
            movement_date=movement_date,
            created_by_user_id=created_by_user_id,
            notes=notes,
            source="internal_transfer",
            source_reference=transfer_ref,
            signed_delta=-amount,
            treasury_account_id=from_account_id,
            connection=connection,
            manage_transaction=False,
        )

        in_id = create_cash_movement_atomic(
            organization_id,
            movement_type="adjustment",
            currency=currency,
            amount=amount,
            category=INTERNAL_TRANSFER_CATEGORY,
            description=f"Transfer in — {label}",
            payment_method="transfer",
            movement_date=movement_date,
            created_by_user_id=created_by_user_id,
            notes=notes,
            source="internal_transfer",
            source_reference=transfer_ref,
            signed_delta=amount,
            treasury_account_id=to_account_id,
            connection=connection,
            manage_transaction=False,
        )

        connection.commit()
        return {
            "out_movement_id": out_id,
            "in_movement_id": in_id,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
