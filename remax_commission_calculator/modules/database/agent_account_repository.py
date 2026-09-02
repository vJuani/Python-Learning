"""
Agent current account (cuenta corriente) ledger repository.
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
from .tenant import (
    TenantError,
    require_organization_id,
)


MOVEMENT_TYPE_CHARGE = "charge"
MOVEMENT_TYPE_CREDIT = "credit"
MOVEMENT_TYPE_PAYMENT = "payment"
MOVEMENT_TYPE_FEE = "fee"
MOVEMENT_TYPE_COMMISSION = "commission"
MOVEMENT_TYPE_ADJUSTMENT = "adjustment"

MOVEMENT_TYPES = (
    MOVEMENT_TYPE_CHARGE,
    MOVEMENT_TYPE_CREDIT,
    MOVEMENT_TYPE_PAYMENT,
    MOVEMENT_TYPE_FEE,
    MOVEMENT_TYPE_COMMISSION,
    MOVEMENT_TYPE_ADJUSTMENT,
)

STATUS_CONFIRMED = "confirmed"
STATUS_REVERSED = "reversed"

SOURCE_MANUAL = "manual"
SOURCE_INVOICE = "invoice"
SOURCE_CASH = "cash"
SOURCE_OPERATION = "operation"
SOURCE_FEE = "fee"
SOURCE_COMMISSION = "commission"
SOURCE_SYSTEM = "system"

SOURCE_TYPES = (
    SOURCE_MANUAL,
    SOURCE_INVOICE,
    SOURCE_CASH,
    SOURCE_OPERATION,
    SOURCE_FEE,
    SOURCE_COMMISSION,
    SOURCE_SYSTEM,
)

CURRENCIES = ("ARS", "USD")

PAYMENT_METHODS = (
    "transfer",
    "cash",
    "card",
    "other",
)

AGENT_ACCOUNT_V2_COLUMNS = (
    ("exchange_rate", "REAL"),
    ("exchange_rate_date", "TEXT"),
    ("exchange_rate_source", "TEXT"),
    ("equivalent_amount_ars", "REAL"),
    ("payment_method", "TEXT"),
    ("reference_text", "TEXT"),
    ("notes", "TEXT"),
    ("period_label", "TEXT"),
    ("cancelled_at", "TEXT"),
    ("cancelled_by_user_id", "INTEGER"),
    ("cancellation_reason", "TEXT"),
    ("is_internal_reversal", "INTEGER NOT NULL DEFAULT 0"),
)

AGENT_ACCOUNT_V3_COLUMNS = (
    ("charge_category", "TEXT"),
    ("net_amount", "REAL"),
    ("vat_rate", "REAL"),
    ("vat_amount", "REAL"),
    ("gross_amount", "REAL"),
    ("billing_period", "TEXT"),
    ("recurring", "INTEGER NOT NULL DEFAULT 0"),
    ("recurrence_type", "TEXT DEFAULT 'one_time'"),
)


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _build_movement_dict(row):
    if row is None:
        return None

    base = {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "movement_type": row[3],
        "currency": row[4],
        "amount": float(row[5] or 0),
        "description": row[6],
        "balance_before": float(row[7] or 0),
        "balance_after": float(row[8] or 0),
        "status": row[9],
        "source_type": row[10],
        "source_id": row[11],
        "movement_date": row[12],
        "idempotency_key": row[13],
        "created_by_user_id": row[14],
        "created_at": row[15],
        "reversed_movement_id": row[16],
        "reversal_reason": row[17],
    }

    _extend_movement_from_row(base, row)

    if (
        not base.get("cancellation_reason")
        and base.get("status") == STATUS_REVERSED
        and base.get("reversal_reason")
    ):
        base["cancellation_reason"] = base["reversal_reason"]

    _apply_movement_defaults(base)
    return base


def _apply_movement_defaults(movement):
    gross = movement.get("gross_amount")
    if gross is None:
        gross = movement.get("amount")
        movement["gross_amount"] = float(gross or 0)
    if movement.get("net_amount") is None:
        movement["net_amount"] = float(
            movement.get("gross_amount") or 0
        )
    if movement.get("vat_amount") is None:
        movement["vat_amount"] = 0.0
    if movement.get("vat_rate") is None:
        movement["vat_rate"] = 0.0
    if movement.get("charge_category") is None:
        movement["charge_category"] = None
    if not movement.get("billing_period") and movement.get(
        "period_label"
    ):
        movement["billing_period"] = movement["period_label"]
    if movement.get("recurring") is None:
        movement["recurring"] = 0
    if not movement.get("recurrence_type"):
        movement["recurrence_type"] = "one_time"

    return movement


def _extend_movement_from_row(base, row):
    if len(row) <= 18:
        base.update(
            {
                "exchange_rate": None,
                "exchange_rate_date": None,
                "exchange_rate_source": None,
                "equivalent_amount_ars": None,
                "payment_method": None,
                "reference_text": None,
                "notes": None,
                "period_label": None,
                "cancelled_at": None,
                "cancelled_by_user_id": None,
                "cancellation_reason": None,
                "is_internal_reversal": bool(
                    base.get("reversed_movement_id")
                ),
                "created_by_username": (
                    row[18] if len(row) > 18 else None
                ),
                "cancelled_by_username": None,
                "agent_name": (
                    row[19] if len(row) > 19 else None
                ),
                "charge_category": None,
                "net_amount": None,
                "vat_rate": None,
                "vat_amount": None,
                "gross_amount": None,
                "billing_period": None,
                "recurring": 0,
                "recurrence_type": "one_time",
            }
        )
        return base

    base.update(
        {
            "exchange_rate": (
                float(row[18]) if row[18] is not None else None
            ),
            "exchange_rate_date": row[19],
            "exchange_rate_source": row[20],
            "equivalent_amount_ars": (
                float(row[21]) if row[21] is not None else None
            ),
            "payment_method": row[22],
            "reference_text": row[23],
            "notes": row[24],
            "period_label": row[25],
            "cancelled_at": row[26],
            "cancelled_by_user_id": row[27],
            "cancellation_reason": row[28],
            "is_internal_reversal": bool(row[29] or 0),
            "created_by_username": row[30] if len(row) > 30 else None,
            "cancelled_by_username": row[31] if len(row) > 31 else None,
            "agent_name": row[32] if len(row) > 32 else None,
            "charge_category": row[33] if len(row) > 33 else None,
            "net_amount": (
                float(row[34]) if len(row) > 34 and row[34] is not None else None
            ),
            "vat_rate": (
                float(row[35]) if len(row) > 35 and row[35] is not None else None
            ),
            "vat_amount": (
                float(row[36]) if len(row) > 36 and row[36] is not None else None
            ),
            "gross_amount": (
                float(row[37]) if len(row) > 37 and row[37] is not None else None
            ),
            "billing_period": row[38] if len(row) > 38 else None,
            "recurring": int(row[39] or 0) if len(row) > 39 else 0,
            "recurrence_type": (
                row[40] if len(row) > 40 and row[40] else "one_time"
            ),
        }
    )
    return base


MOVEMENTS_BASE_QUERY = """
    SELECT
        m.id,
        m.organization_id,
        m.agent_id,
        m.movement_type,
        m.currency,
        m.amount,
        m.description,
        m.balance_before,
        m.balance_after,
        m.status,
        m.source_type,
        m.source_id,
        m.movement_date,
        m.idempotency_key,
        m.created_by_user_id,
        m.created_at,
        m.reversed_movement_id,
        m.reversal_reason,
        m.exchange_rate,
        m.exchange_rate_date,
        m.exchange_rate_source,
        m.equivalent_amount_ars,
        m.payment_method,
        m.reference_text,
        m.notes,
        m.period_label,
        m.cancelled_at,
        m.cancelled_by_user_id,
        m.cancellation_reason,
        m.is_internal_reversal,
        creator.username,
        canceller.username,
        a.name,
        m.charge_category,
        m.net_amount,
        m.vat_rate,
        m.vat_amount,
        m.gross_amount,
        m.billing_period,
        m.recurring,
        m.recurrence_type
    FROM agent_account_movements AS m
    LEFT JOIN users AS creator
        ON m.created_by_user_id = creator.id
    LEFT JOIN users AS canceller
        ON m.cancelled_by_user_id = canceller.id
    JOIN agents AS a
        ON m.agent_id = a.id
        AND m.organization_id = a.organization_id
"""


def _ensure_agent_in_org(cursor, organization_id, agent_id):
    cursor.execute(
        """
        SELECT id
        FROM agents
        WHERE id = ?
            AND organization_id = ?
        """,
        (agent_id, organization_id),
    )
    if cursor.fetchone() is None:
        raise TenantError(
            "Agent was not found in this organization."
        )


def _fetch_latest_balance(
    cursor,
    organization_id,
    agent_id,
    currency,
    *,
    for_update=False,
):
    lock = " FOR UPDATE" if for_update else ""
    cursor.execute(
        f"""
        SELECT balance_after
        FROM agent_account_movements
        WHERE organization_id = ?
            AND agent_id = ?
            AND currency = ?
            AND status = ?
        ORDER BY movement_date DESC, id DESC
        LIMIT 1
        {lock}
        """,
        (
            organization_id,
            agent_id,
            currency,
            STATUS_CONFIRMED,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return 0.0
    return float(row[0] or 0)


def get_movement_by_idempotency_key(
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
    try:
        cursor.execute(
            MOVEMENTS_BASE_QUERY
            + """
            WHERE m.organization_id = ?
                AND m.idempotency_key = ?
            LIMIT 1
            """,
            (organization_id, idempotency_key),
        )
        return _build_movement_dict(cursor.fetchone())
    finally:
        connection.close()


def get_agent_account_movement(
    movement_id,
    organization_id,
):
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


def get_agent_balance(
    organization_id,
    agent_id,
    currency,
):
    organization_id = require_organization_id(
        organization_id
    )
    if currency not in CURRENCIES:
        raise ValueError("invalid_currency")

    connection = get_connection()
    cursor = connection.cursor()
    try:
        return _fetch_latest_balance(
            cursor,
            organization_id,
            agent_id,
            currency,
        )
    finally:
        connection.close()


def get_agent_balances(organization_id, agent_id):
    return {
        currency: get_agent_balance(
            organization_id,
            agent_id,
            currency,
        )
        for currency in CURRENCIES
    }


def list_agent_account_movements(
    organization_id,
    agent_id,
    *,
    currency=None,
    movement_type=None,
    status=None,
    date_from=None,
    date_to=None,
    limit=None,
    include_internal_reversals=False,
):
    organization_id = require_organization_id(
        organization_id
    )

    clauses = [
        "m.organization_id = ?",
        "m.agent_id = ?",
    ]
    params = [organization_id, agent_id]

    if currency in CURRENCIES:
        clauses.append("m.currency = ?")
        params.append(currency)

    if movement_type in MOVEMENT_TYPES:
        clauses.append("m.movement_type = ?")
        params.append(movement_type)

    if status in (STATUS_CONFIRMED, STATUS_REVERSED):
        clauses.append("m.status = ?")
        params.append(status)

    if date_from:
        clauses.append("m.movement_date >= ?")
        params.append(date_from)

    if date_to:
        clauses.append("m.movement_date <= ?")
        params.append(date_to)

    if not include_internal_reversals:
        clauses.append(
            "COALESCE(m.is_internal_reversal, 0) = 0"
        )
        clauses.append("m.reversed_movement_id IS NULL")

    query = (
        MOVEMENTS_BASE_QUERY
        + " WHERE "
        + " AND ".join(clauses)
        + " ORDER BY m.movement_date DESC, m.id DESC"
    )

    if limit is not None:
        query += " LIMIT ?"
        params.append(int(limit))

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(query, tuple(params))
        return [
            _build_movement_dict(row)
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def list_agents_account_summary(
    organization_id,
    *,
    search_query=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()
    try:
        params = [organization_id]
        search_clause = ""
        if search_query:
            search_clause = (
                " AND LOWER(a.name) LIKE LOWER(?) "
            )
            params.append(f"%{search_query.strip()}%")

        cursor.execute(
            f"""
            SELECT
                a.id,
                a.name,
                a.type,
                (
                    SELECT m.balance_after
                    FROM agent_account_movements AS m
                    WHERE m.organization_id = a.organization_id
                        AND m.agent_id = a.id
                        AND m.currency = 'ARS'
                        AND m.status = 'confirmed'
                    ORDER BY m.movement_date DESC, m.id DESC
                    LIMIT 1
                ) AS balance_ars,
                (
                    SELECT m.balance_after
                    FROM agent_account_movements AS m
                    WHERE m.organization_id = a.organization_id
                        AND m.agent_id = a.id
                        AND m.currency = 'USD'
                        AND m.status = 'confirmed'
                    ORDER BY m.movement_date DESC, m.id DESC
                    LIMIT 1
                ) AS balance_usd,
                (
                    SELECT m.movement_date
                    FROM agent_account_movements AS m
                    WHERE m.organization_id = a.organization_id
                        AND m.agent_id = a.id
                    ORDER BY m.movement_date DESC, m.id DESC
                    LIMIT 1
                ) AS last_movement_date
            FROM agents AS a
            WHERE a.organization_id = ?
                {search_clause}
            ORDER BY LOWER(a.name)
            """,
            tuple(params),
        )

        rows = []
        for row in cursor.fetchall():
            balance_ars = float(row[3] or 0)
            balance_usd = float(row[4] or 0)
            rows.append(
                {
                    "agent_id": row[0],
                    "agent_name": row[1],
                    "agent_level": row[2],
                    "balance_ars": balance_ars,
                    "balance_usd": balance_usd,
                    "last_movement_date": row[5],
                    "has_pending_balance": (
                        balance_ars < -1e-9
                        or balance_usd < -1e-9
                    ),
                    "has_credit_balance": (
                        balance_ars > 1e-9
                        or balance_usd > 1e-9
                    ),
                }
            )
        return rows
    finally:
        connection.close()


def sum_organization_balances(organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    summaries = list_agents_account_summary(
        organization_id
    )
    totals = {currency: 0.0 for currency in CURRENCIES}
    pending_agents = 0

    for row in summaries:
        totals["ARS"] += row["balance_ars"]
        totals["USD"] += row["balance_usd"]
        if row["has_pending_balance"]:
            pending_agents += 1

    return {
        "totals": totals,
        "pending_agents": pending_agents,
    }


def count_movements_in_month(
    organization_id,
    *,
    year_month,
):
    organization_id = require_organization_id(
        organization_id
    )
    prefix = year_month.strip()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM agent_account_movements
            WHERE organization_id = ?
                AND movement_date LIKE ?
                AND COALESCE(is_internal_reversal, 0) = 0
                AND reversed_movement_id IS NULL
            """,
            (organization_id, f"{prefix}%"),
        )
        return int(cursor.fetchone()[0] or 0)
    finally:
        connection.close()


def sum_receivable_balances(organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    summaries = list_agents_account_summary(
        organization_id
    )
    receivable = {currency: 0.0 for currency in CURRENCIES}
    pending_agents = 0

    for row in summaries:
        for currency_key, balance in (
            ("ARS", row["balance_ars"]),
            ("USD", row["balance_usd"]),
        ):
            if balance < -1e-9:
                receivable[currency_key] += abs(balance)
        if row["has_pending_balance"]:
            pending_agents += 1

    return {
        "receivable": receivable,
        "pending_agents": pending_agents,
    }


def sum_payments_collected_month(
    organization_id,
    *,
    year_month,
):
    organization_id = require_organization_id(
        organization_id
    )
    prefix = year_month.strip()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        totals = {currency: 0.0 for currency in CURRENCIES}
        cursor.execute(
            """
            SELECT currency, COALESCE(SUM(amount), 0)
            FROM agent_account_movements
            WHERE organization_id = ?
                AND movement_type = ?
                AND status = ?
                AND movement_date LIKE ?
                AND COALESCE(is_internal_reversal, 0) = 0
                AND reversed_movement_id IS NULL
            GROUP BY currency
            """,
            (
                organization_id,
                MOVEMENT_TYPE_PAYMENT,
                STATUS_CONFIRMED,
                f"{prefix}%",
            ),
        )
        for row in cursor.fetchall():
            totals[row[0]] = float(row[1] or 0)
        return totals
    finally:
        connection.close()


def get_agent_account_metadata(
    organization_id,
    agent_id,
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
                MIN(movement_date),
                MAX(movement_date)
            FROM agent_account_movements
            WHERE organization_id = ?
                AND agent_id = ?
                AND COALESCE(is_internal_reversal, 0) = 0
                AND reversed_movement_id IS NULL
            """,
            (organization_id, agent_id),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "first_movement_date": None,
                "last_movement_date": None,
            }
        return {
            "first_movement_date": row[0],
            "last_movement_date": row[1],
        }
    finally:
        connection.close()


def create_agent_account_movement_atomic(
    organization_id,
    agent_id,
    *,
    movement_type,
    currency,
    amount,
    signed_delta,
    description,
    movement_date,
    created_by_user_id,
    source_type=SOURCE_MANUAL,
    source_id=None,
    idempotency_key=None,
    exchange_rate=None,
    exchange_rate_date=None,
    exchange_rate_source=None,
    equivalent_amount_ars=None,
    payment_method=None,
    reference_text=None,
    notes=None,
    period_label=None,
    charge_category=None,
    net_amount=None,
    vat_rate=None,
    vat_amount=None,
    gross_amount=None,
    billing_period=None,
    recurring=0,
    recurrence_type="one_time",
):
    organization_id = require_organization_id(
        organization_id
    )

    if movement_type not in MOVEMENT_TYPES:
        raise ValueError("invalid_movement_type")
    if currency not in CURRENCIES:
        raise ValueError("invalid_currency")
    if float(amount) <= 0:
        raise ValueError("invalid_amount")
    if source_type not in SOURCE_TYPES:
        raise ValueError("invalid_source_type")

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
            cursor.execute(
                """
                SELECT id
                FROM agent_account_movements
                WHERE organization_id = ?
                    AND idempotency_key = ?
                """,
                (organization_id, idempotency_key),
            )
            existing_id = cursor.fetchone()
            if existing_id is not None:
                connection.commit()
                return get_agent_account_movement(
                    existing_id[0],
                    organization_id,
                )

        _ensure_agent_in_org(
            cursor,
            organization_id,
            agent_id,
        )

        balance_before = _fetch_latest_balance(
            cursor,
            organization_id,
            agent_id,
            currency,
            for_update=(backend == BACKEND_POSTGRES),
        )
        balance_after = balance_before + float(signed_delta)

        movement_id = execute_insert(
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
                period_label,
                is_internal_reversal,
                charge_category,
                net_amount,
                vat_rate,
                vat_amount,
                gross_amount,
                billing_period,
                recurring,
                recurrence_type
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, 0,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                agent_id,
                movement_type,
                currency,
                float(amount),
                description or "",
                balance_before,
                balance_after,
                STATUS_CONFIRMED,
                source_type,
                source_id,
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
                period_label,
                charge_category,
                float(net_amount)
                if net_amount is not None
                else None,
                float(vat_rate) if vat_rate is not None else None,
                float(vat_amount)
                if vat_amount is not None
                else None,
                float(gross_amount)
                if gross_amount is not None
                else float(amount),
                billing_period,
                int(recurring or 0),
                recurrence_type or "one_time",
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_agent_account_movement(
        movement_id,
        organization_id,
    )


def reverse_agent_account_movement_atomic(
    organization_id,
    movement_id,
    *,
    created_by_user_id,
    reversal_reason,
):
    organization_id = require_organization_id(
        organization_id
    )

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
                movement_type,
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
            (movement_id, organization_id),
        )
        original = cursor.fetchone()
        if original is None:
            connection.rollback()
            raise ValueError("movement_not_found")

        if original[8] != STATUS_CONFIRMED:
            connection.rollback()
            raise ValueError("movement_not_reversible")

        balance_before = float(original[7] or 0)
        balance_after = float(original[6] or 0)

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
                reversal_reason or "",
                movement_id,
                organization_id,
            ),
        )

        reversal_description = (
            f"[internal] Cancellation of #{movement_id}"
        )
        if original[5]:
            reversal_description += f": {original[5]}"

        reversal_id = execute_insert(
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
                original[3],
                float(original[4] or 0),
                reversal_description,
                balance_before,
                balance_after,
                STATUS_CONFIRMED,
                SOURCE_MANUAL,
                movement_id,
                now[:10],
                created_by_user_id,
                now,
                movement_id,
                reversal_reason or "",
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_agent_account_movement(
        reversal_id,
        organization_id,
    )
