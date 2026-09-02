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
        "display_id": (
            f"CAJ-{int(row[2]):06d}"
            if row[2] is not None
            else None
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
        u.username
    FROM cash_movements AS m
    LEFT JOIN users AS u
        ON m.created_by_user_id = u.id
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

    try:
        if manage_transaction:
            if backend == BACKEND_SQLITE:
                cursor.execute("BEGIN IMMEDIATE")
            else:
                cursor.execute("BEGIN")

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
            account_id = execute_insert(
                cursor,
                """
                INSERT INTO cash_accounts (
                    organization_id,
                    currency,
                    cached_balance,
                    updated_at
                )
                VALUES (?, ?, 0, ?)
                """,
                (organization_id, currency, now),
            )
            balance_before = 0.0

            if backend == BACKEND_POSTGRES:
                cursor.execute(
                    """
                    SELECT id, cached_balance
                    FROM cash_accounts
                    WHERE id = ?
                    FOR UPDATE
                    """,
                    (account_id,),
                )
                account_row = cursor.fetchone()
                balance_before = float(
                    account_row[1] or 0
                )
        else:
            account_id = account_row[0]
            balance_before = float(account_row[1] or 0)

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
                attachment_original_name
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'confirmed', ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
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
            ),
        )

        cursor.execute(
            """
            UPDATE cash_accounts
            SET cached_balance = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                balance_after,
                now,
                account_id,
                organization_id,
            ),
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

        # Opposite delta of the original effect.
        original_delta = (
            original["balance_after"]
            - original["balance_before"]
        )
        signed_delta = -float(original_delta)

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

        account_id = account_row[0]
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
                balance_after
            )
            VALUES (
                ?, ?, 'reversal', ?, ?, ?, ?, ?, ?, ?, ?,
                'confirmed', ?, 'manual', ?, ?, ?, ?
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

        cursor.execute(
            """
            UPDATE cash_accounts
            SET cached_balance = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                balance_after,
                now,
                account_id,
                organization_id,
            ),
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
