"""Safe SQLite migration for recurring agent charges."""

from __future__ import annotations

from .connection import get_connection


RECURRING_CHARGES_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agent_recurring_charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    charge_category TEXT NOT NULL,
    description TEXT,
    currency TEXT NOT NULL,
    input_amount REAL NOT NULL,
    vat_mode TEXT NOT NULL,
    net_amount REAL NOT NULL,
    vat_rate REAL NOT NULL,
    vat_amount REAL NOT NULL,
    gross_amount REAL NOT NULL,
    recurrence_type TEXT NOT NULL,
    billing_day INTEGER,
    start_date TEXT NOT NULL,
    end_date TEXT,
    next_run_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    updated_by_user_id INTEGER,
    updated_at TEXT NOT NULL,
    last_generated_at TEXT,
    paused_at TEXT,
    paused_by_user_id INTEGER,
    ended_at TEXT,
    ended_by_user_id INTEGER,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (updated_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (paused_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (ended_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,

    CHECK (currency IN ('USD', 'ARS')),
    CHECK (vat_mode IN ('none', 'add_vat', 'gross_includes_vat')),
    CHECK (recurrence_type IN ('monthly', 'annual')),
    CHECK (status IN ('active', 'paused', 'ended')),
    CHECK (input_amount > 0),
    CHECK (gross_amount > 0),
    CHECK (
        (recurrence_type = 'monthly' AND billing_day BETWEEN 1 AND 28)
        OR recurrence_type = 'annual'
    )
)
"""

MOVEMENT_COLUMNS = (
    "id", "organization_id", "agent_id", "movement_type", "currency",
    "amount", "description", "balance_before", "balance_after", "status",
    "source_type", "source_id", "movement_date", "idempotency_key",
    "created_by_user_id", "created_at", "reversed_movement_id",
    "reversal_reason", "exchange_rate", "exchange_rate_date",
    "exchange_rate_source", "equivalent_amount_ars", "payment_method",
    "reference_text", "notes", "period_label", "cancelled_at",
    "cancelled_by_user_id", "cancellation_reason", "is_internal_reversal",
    "charge_category", "net_amount", "vat_rate", "vat_amount",
    "gross_amount", "billing_period", "recurring", "recurrence_type",
    "commission_side", "commission_purpose", "commission_source_amount",
    "commission_source_currency",
)

MOVEMENTS_CREATE_SQL = """
CREATE TABLE agent_account_movements_recurring_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    movement_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount REAL NOT NULL,
    description TEXT NOT NULL,
    balance_before REAL NOT NULL,
    balance_after REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_id INTEGER,
    movement_date TEXT NOT NULL,
    idempotency_key TEXT,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    reversed_movement_id INTEGER,
    reversal_reason TEXT,
    exchange_rate REAL,
    exchange_rate_date TEXT,
    exchange_rate_source TEXT,
    equivalent_amount_ars REAL,
    payment_method TEXT,
    reference_text TEXT,
    notes TEXT,
    period_label TEXT,
    cancelled_at TEXT,
    cancelled_by_user_id INTEGER,
    cancellation_reason TEXT,
    is_internal_reversal INTEGER NOT NULL DEFAULT 0,
    charge_category TEXT,
    net_amount REAL,
    vat_rate REAL,
    vat_amount REAL,
    gross_amount REAL,
    billing_period TEXT,
    recurring INTEGER NOT NULL DEFAULT 0,
    recurrence_type TEXT DEFAULT 'one_time',
    commission_side TEXT,
    commission_purpose TEXT,
    commission_source_amount REAL,
    commission_source_currency TEXT,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (cancelled_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (reversed_movement_id)
        REFERENCES agent_account_movements_recurring_new(id)
        ON DELETE SET NULL,

    CHECK (movement_type IN (
        'charge', 'credit', 'payment', 'fee', 'commission', 'adjustment'
    )),
    CHECK (currency IN ('USD', 'ARS')),
    CHECK (amount > 0),
    CHECK (status IN ('confirmed', 'reversed')),
    CHECK (source_type IN (
        'manual', 'invoice', 'cash', 'operation', 'fee', 'commission',
        'system', 'recurring_charge'
    ))
)
"""


def _create_indexes(cursor):
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_recurring_org_agent
        ON agent_recurring_charges (
            organization_id, agent_id, status, next_run_date, id
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_recurring_due
        ON agent_recurring_charges (
            organization_id, status, next_run_date, id
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_account_org_agent_currency
        ON agent_account_movements (
            organization_id, agent_id, currency, movement_date, id
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_account_org_date
        ON agent_account_movements (
            organization_id, movement_date, id
        )
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_account_idempotency
        ON agent_account_movements (organization_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL AND idempotency_key != ''
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_aa_active_operation_commission
        ON agent_account_movements (
            organization_id, source_id, agent_id,
            commission_side, commission_purpose
        )
        WHERE movement_type = 'commission'
            AND source_type = 'operation'
            AND status = 'confirmed'
            AND commission_side IS NOT NULL
            AND commission_purpose IS NOT NULL
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_aa_active_operation_commission_consolidated
        ON agent_account_movements (
            organization_id, source_id, agent_id, commission_purpose
        )
        WHERE movement_type = 'commission'
            AND source_type = 'operation'
            AND status = 'confirmed'
            AND commission_purpose = 'own_commission'
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_aa_recurring_charge_period
        ON agent_account_movements (
            organization_id, source_id, billing_period
        )
        WHERE movement_type = 'charge'
            AND source_type = 'recurring_charge'
            AND source_id IS NOT NULL
            AND billing_period IS NOT NULL
        """
    )


def migrate_recurring_charges_sqlite():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(RECURRING_CHARGES_CREATE_SQL)
        row = cursor.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'agent_account_movements'
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return
        table_sql = row[0] or ""
        if "'recurring_charge'" not in table_sql:
            old_columns = {
                item[1]
                for item in cursor.execute(
                    "PRAGMA table_info(agent_account_movements)"
                ).fetchall()
            }
            missing = [
                column for column in MOVEMENT_COLUMNS
                if column not in old_columns
            ]
            if missing:
                raise RuntimeError(
                    "Cannot migrate recurring charges; missing movement "
                    "columns: " + ", ".join(missing)
                )
            before = cursor.execute(
                "SELECT COUNT(*) FROM agent_account_movements"
            ).fetchone()[0]
            connection.commit()
            connection.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "DROP TABLE IF EXISTS agent_account_movements_recurring_new"
            )
            cursor.execute(MOVEMENTS_CREATE_SQL)
            columns = ", ".join(MOVEMENT_COLUMNS)
            cursor.execute(
                f"""
                INSERT INTO agent_account_movements_recurring_new ({columns})
                SELECT {columns} FROM agent_account_movements
                """
            )
            cursor.execute("DROP TABLE agent_account_movements")
            cursor.execute(
                """
                ALTER TABLE agent_account_movements_recurring_new
                RENAME TO agent_account_movements
                """
            )
            after = cursor.execute(
                "SELECT COUNT(*) FROM agent_account_movements"
            ).fetchone()[0]
            if before != after:
                raise RuntimeError(
                    f"Movement migration row mismatch: {before} != {after}"
                )
        _create_indexes(cursor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
        connection.close()
