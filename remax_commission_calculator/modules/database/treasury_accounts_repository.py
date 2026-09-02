"""
Treasury accounts repository — multi-account cash/treasury.
"""

from __future__ import annotations

from datetime import datetime

from modules.config import (
    BACKEND_POSTGRES,
    BACKEND_SQLITE,
    get_database_backend,
)

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


ACCOUNT_TYPES = (
    "cash",
    "bank",
    "digital_wallet",
    "other",
)

CURRENCIES = ("ARS", "USD")

LEGACY_DEFAULT_NAMES = {
    "ARS": "Caja general ARS",
    "USD": "Caja general USD",
}


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
        "name": row[2],
        "account_type": row[3],
        "currency": row[4],
        "bank_name": row[5],
        "account_reference": row[6],
        "is_default": bool(row[7]),
        "is_active": bool(row[8]),
        "cached_balance": float(row[9] or 0),
        "created_at": row[10],
        "created_by_user_id": row[11],
    }


ACCOUNTS_BASE_QUERY = """
    SELECT
        id,
        organization_id,
        name,
        account_type,
        currency,
        bank_name,
        account_reference,
        is_default,
        is_active,
        cached_balance,
        created_at,
        created_by_user_id
    FROM treasury_accounts
"""


def list_treasury_accounts(
    organization_id,
    *,
    currency=None,
    account_type=None,
    active_only=False,
):
    organization_id = require_organization_id(
        organization_id
    )
    clauses = ["organization_id = ?"]
    params = [organization_id]

    if currency:
        clauses.append("currency = ?")
        params.append(currency)

    if account_type:
        clauses.append("account_type = ?")
        params.append(account_type)

    if active_only:
        clauses.append("is_active = 1")

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            ACCOUNTS_BASE_QUERY
            + " WHERE "
            + " AND ".join(clauses)
            + """
            ORDER BY
                currency,
                is_default DESC,
                account_type,
                name
            """,
            params,
        )
        return [
            _build_account_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def get_treasury_account(account_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            ACCOUNTS_BASE_QUERY
            + """
            WHERE id = ?
                AND organization_id = ?
            """,
            (account_id, organization_id),
        )
        return _build_account_dict(cursor.fetchone())
    finally:
        connection.close()


def count_treasury_account_movements(
    cursor,
    organization_id,
    account_id,
):
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM cash_movements
        WHERE organization_id = ?
            AND treasury_account_id = ?
        """,
        (organization_id, account_id),
    )
    return int(cursor.fetchone()[0] or 0)


def ensure_legacy_default_accounts(
    cursor,
    organization_id,
    *,
    created_by_user_id=None,
):
    """Create default ARS/USD accounts and assign legacy movements."""
    now = _now_iso()
    account_ids = {}

    for currency in CURRENCIES:
        cursor.execute(
            """
            SELECT id
            FROM treasury_accounts
            WHERE organization_id = ?
                AND currency = ?
                AND name = ?
            """,
            (
                organization_id,
                currency,
                LEGACY_DEFAULT_NAMES[currency],
            ),
        )
        row = cursor.fetchone()

        if row is None:
            account_id = execute_insert(
                cursor,
                """
                INSERT INTO treasury_accounts (
                    organization_id,
                    name,
                    account_type,
                    currency,
                    bank_name,
                    account_reference,
                    is_default,
                    is_active,
                    cached_balance,
                    created_at,
                    created_by_user_id
                ) VALUES (?, ?, 'cash', ?, NULL, NULL, 1, 1, 0, ?, ?)
                """,
                (
                    organization_id,
                    LEGACY_DEFAULT_NAMES[currency],
                    currency,
                    now,
                    created_by_user_id,
                ),
            )
        else:
            account_id = row[0]

        account_ids[currency] = account_id

        cursor.execute(
            """
            UPDATE cash_movements
            SET treasury_account_id = ?
            WHERE organization_id = ?
                AND currency = ?
                AND treasury_account_id IS NULL
            """,
            (account_id, organization_id, currency),
        )

        cursor.execute(
            """
            SELECT COALESCE(SUM(
                balance_after - balance_before
            ), 0)
            FROM cash_movements
            WHERE organization_id = ?
                AND treasury_account_id = ?
                AND status = 'confirmed'
            """,
            (organization_id, account_id),
        )
        balance = float(cursor.fetchone()[0] or 0)

        cursor.execute(
            """
            UPDATE treasury_accounts
            SET cached_balance = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (balance, account_id, organization_id),
        )

    return account_ids


def create_treasury_account(
    organization_id,
    *,
    name,
    account_type,
    currency,
    bank_name=None,
    account_reference=None,
    is_default=False,
    created_by_user_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    name = (name or "").strip()
    account_type = (account_type or "").strip()
    currency = (currency or "").strip().upper()

    if not name:
        raise ValueError("name_required")
    if account_type not in ACCOUNT_TYPES:
        raise ValueError("invalid_account_type")
    if currency not in CURRENCIES:
        raise ValueError("invalid_currency")

    now = _now_iso()
    backend = get_database_backend()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        if backend == BACKEND_SQLITE:
            cursor.execute("BEGIN IMMEDIATE")
        else:
            cursor.execute("BEGIN")

        if is_default:
            cursor.execute(
                """
                UPDATE treasury_accounts
                SET is_default = 0
                WHERE organization_id = ?
                    AND currency = ?
                """,
                (organization_id, currency),
            )

        account_id = execute_insert(
            cursor,
            """
            INSERT INTO treasury_accounts (
                organization_id,
                name,
                account_type,
                currency,
                bank_name,
                account_reference,
                is_default,
                is_active,
                cached_balance,
                created_at,
                created_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
            """,
            (
                organization_id,
                name,
                account_type,
                currency,
                bank_name,
                account_reference,
                1 if is_default else 0,
                now,
                created_by_user_id,
            ),
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_treasury_account(account_id, organization_id)


def update_treasury_account(
    organization_id,
    account_id,
    *,
    name=None,
    bank_name=None,
    account_reference=None,
    is_active=None,
    is_default=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    account = get_treasury_account(
        account_id,
        organization_id,
    )
    if account is None:
        raise ValueError("account_not_found")

    backend = get_database_backend()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        if backend == BACKEND_SQLITE:
            cursor.execute("BEGIN IMMEDIATE")
        else:
            cursor.execute("BEGIN")

        if is_default:
            cursor.execute(
                """
                UPDATE treasury_accounts
                SET is_default = 0
                WHERE organization_id = ?
                    AND currency = ?
                """,
                (
                    organization_id,
                    account["currency"],
                ),
            )

        fields = []
        params = []

        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("name_required")
            fields.append("name = ?")
            params.append(clean_name)

        if bank_name is not None:
            fields.append("bank_name = ?")
            params.append(bank_name.strip() or None)

        if account_reference is not None:
            fields.append("account_reference = ?")
            params.append(account_reference.strip() or None)

        if is_active is not None:
            fields.append("is_active = ?")
            params.append(1 if is_active else 0)

        if is_default is not None:
            fields.append("is_default = ?")
            params.append(1 if is_default else 0)

        if not fields:
            connection.rollback()
            return account

        params.extend([account_id, organization_id])
        cursor.execute(
            f"""
            UPDATE treasury_accounts
            SET {", ".join(fields)}
            WHERE id = ?
                AND organization_id = ?
            """,
            params,
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_treasury_account(account_id, organization_id)


def get_default_treasury_account(
    organization_id,
    currency,
    *,
    account_type=None,
    active_only=True,
):
    organization_id = require_organization_id(
        organization_id
    )
    currency = (currency or "").strip().upper()
    clauses = [
        "organization_id = ?",
        "currency = ?",
    ]
    params = [organization_id, currency]

    if active_only:
        clauses.append("is_active = 1")

    if account_type:
        clauses.append("account_type = ?")
        params.append(account_type)
        order = "is_default DESC, id ASC"
    else:
        order = "is_default DESC, id ASC"

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            ACCOUNTS_BASE_QUERY
            + " WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY {order} LIMIT 1",
            params,
        )
        account = _build_account_dict(cursor.fetchone())
        if account is not None:
            return account

        if account_type:
            cursor.execute(
                ACCOUNTS_BASE_QUERY
                + """
                WHERE organization_id = ?
                    AND currency = ?
                    AND is_active = 1
                ORDER BY is_default DESC, id ASC
                LIMIT 1
                """,
                (organization_id, currency),
            )
            return _build_account_dict(cursor.fetchone())

        return None
    finally:
        connection.close()


def suggest_treasury_account_for_payment(
    organization_id,
    currency,
    payment_method,
):
    payment_method = (payment_method or "").strip().lower()
    if payment_method == "cash":
        preferred_type = "cash"
    elif payment_method in ("transfer", "debit", "credit"):
        preferred_type = "bank"
    elif payment_method == "wallet":
        preferred_type = "digital_wallet"
    else:
        preferred_type = None

    if preferred_type:
        account = get_default_treasury_account(
            organization_id,
            currency,
            account_type=preferred_type,
        )
        if account is not None:
            return account

    return get_default_treasury_account(
        organization_id,
        currency,
    )


def resolve_treasury_account_id(
    organization_id,
    currency,
    treasury_account_id=None,
    payment_method=None,
):
    if treasury_account_id is not None:
        account = get_treasury_account(
            treasury_account_id,
            organization_id,
        )
        if account is None:
            raise ValueError("invalid_treasury_account")
        if not account["is_active"]:
            raise ValueError("inactive_treasury_account")
        if account["currency"] != currency:
            raise ValueError("treasury_currency_mismatch")
        return account["id"]

    if payment_method:
        suggested = suggest_treasury_account_for_payment(
            organization_id,
            currency,
            payment_method,
        )
        if suggested is not None:
            return suggested["id"]

    default = get_default_treasury_account(
        organization_id,
        currency,
    )
    if default is None:
        raise ValueError("treasury_account_missing")
    return default["id"]


def sum_treasury_balances_by_currency(organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                currency,
                COALESCE(SUM(cached_balance), 0)
            FROM treasury_accounts
            WHERE organization_id = ?
                AND is_active = 1
            GROUP BY currency
            """,
            (organization_id,),
        )
        rows = {
            row[0]: float(row[1] or 0)
            for row in cursor.fetchall()
        }
        return {
            "ARS": rows.get("ARS", 0.0),
            "USD": rows.get("USD", 0.0),
        }
    finally:
        connection.close()


def get_treasury_account_summaries(organization_id):
    accounts = list_treasury_accounts(
        organization_id,
        active_only=False,
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        summaries = []
        for account in accounts:
            cursor.execute(
                """
                SELECT
                    movement_date,
                    description,
                    amount,
                    movement_type
                FROM cash_movements
                WHERE organization_id = ?
                    AND treasury_account_id = ?
                    AND status = 'confirmed'
                ORDER BY movement_date DESC, id DESC
                LIMIT 1
                """,
                (
                    organization_id,
                    account["id"],
                ),
            )
            last_row = cursor.fetchone()
            last_movement = None
            if last_row is not None:
                last_movement = {
                    "movement_date": last_row[0],
                    "description": last_row[1],
                    "amount": float(last_row[2] or 0),
                    "movement_type": last_row[3],
                }

            summaries.append(
                {
                    **account,
                    "last_movement": last_movement,
                }
            )
        return summaries
    finally:
        connection.close()
