"""Safe schema migration for agent-account charge invoice origins."""

from __future__ import annotations

from .connection import get_connection


ACTIVE_STATUS_SQL = "'draft', 'ready_to_issue', 'issued', 'error'"

SQLITE_INVOICES_V3_CREATE = """
CREATE TABLE invoices_phase3c_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    invoice_seq INTEGER NOT NULL,
    invoice_number_internal TEXT NOT NULL,
    operation_id INTEGER,
    agent_id INTEGER NOT NULL,
    issuer_user_id INTEGER,
    issuer_type TEXT NOT NULL,
    issuer_name TEXT NOT NULL,
    issuer_tax_id TEXT NOT NULL,
    issuer_tax_condition TEXT,
    issuer_address TEXT,
    recipient_name TEXT NOT NULL,
    recipient_tax_id TEXT NOT NULL,
    recipient_tax_condition TEXT,
    recipient_address TEXT,
    invoice_type TEXT NOT NULL DEFAULT 'internal',
    service_type TEXT NOT NULL DEFAULT 'services',
    description TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 1,
    unit_price REAL NOT NULL,
    subtotal REAL NOT NULL,
    vat_amount REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'ARS',
    exchange_rate REAL,
    payment_condition TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    source TEXT NOT NULL DEFAULT 'agent_operation',
    external_invoice_number TEXT,
    point_of_sale TEXT,
    cae TEXT,
    cae_expiration TEXT,
    provider TEXT NOT NULL DEFAULT 'internal',
    provider_reference TEXT,
    pdf_path TEXT,
    created_at TEXT NOT NULL,
    created_by_user_id INTEGER,
    confirmed_at TEXT,
    confirmed_by_user_id INTEGER,
    updated_at TEXT NOT NULL,
    cancelled_at TEXT,
    cancelled_by_user_id INTEGER,
    cancellation_reason TEXT,
    cash_movement_id INTEGER,
    side TEXT,
    issuer_profile_id INTEGER,
    issuer_key TEXT,
    recipient_party_id INTEGER,
    fiscal_voucher_type TEXT,
    issued_at TEXT,
    issued_by_user_id INTEGER,
    provider_error TEXT,
    fiscal_environment TEXT,
    issue_attempt_token TEXT,
    origin_type TEXT NOT NULL DEFAULT 'operation',
    agent_account_movement_id INTEGER,
    invoice_purpose TEXT NOT NULL DEFAULT 'standard',
    charge_linked_at TEXT,
    charge_linked_by_user_id INTEGER,
    vat_rate REAL NOT NULL DEFAULT 0,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (operation_id)
        REFERENCES operations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_account_movement_id)
        REFERENCES agent_account_movements(id) ON DELETE RESTRICT,
    FOREIGN KEY (issuer_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (confirmed_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (cancelled_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (charge_linked_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,

    CHECK (status IN (
        'draft', 'ready_to_issue', 'issued', 'error', 'cancelled'
    )),
    CHECK (payment_condition IN ('contado', 'cuenta_corriente')),
    CHECK (issuer_type IN ('agent', 'admin')),
    CHECK (origin_type IN ('operation', 'agent_account_charge')),
    CHECK (quantity > 0),
    CHECK (total_amount > 0),
    CHECK (
        (origin_type = 'operation' AND operation_id IS NOT NULL)
        OR (
            origin_type = 'agent_account_charge'
            AND agent_account_movement_id IS NOT NULL
        )
    ),
    UNIQUE (organization_id, invoice_seq),
    UNIQUE (organization_id, invoice_number_internal)
)
"""

COPY_COLUMNS = (
    "id",
    "organization_id",
    "invoice_seq",
    "invoice_number_internal",
    "operation_id",
    "agent_id",
    "issuer_user_id",
    "issuer_type",
    "issuer_name",
    "issuer_tax_id",
    "issuer_tax_condition",
    "issuer_address",
    "recipient_name",
    "recipient_tax_id",
    "recipient_tax_condition",
    "recipient_address",
    "invoice_type",
    "service_type",
    "description",
    "quantity",
    "unit_price",
    "subtotal",
    "vat_amount",
    "total_amount",
    "currency",
    "exchange_rate",
    "payment_condition",
    "issue_date",
    "status",
    "source",
    "external_invoice_number",
    "point_of_sale",
    "cae",
    "cae_expiration",
    "provider",
    "provider_reference",
    "pdf_path",
    "created_at",
    "created_by_user_id",
    "confirmed_at",
    "confirmed_by_user_id",
    "updated_at",
    "cancelled_at",
    "cancelled_by_user_id",
    "cancellation_reason",
    "cash_movement_id",
    "side",
    "issuer_profile_id",
    "issuer_key",
    "recipient_party_id",
    "fiscal_voucher_type",
    "issued_at",
    "issued_by_user_id",
    "provider_error",
    "fiscal_environment",
    "issue_attempt_token",
)


def _create_indexes(cursor):
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invoices_org_status
        ON invoices (organization_id, status, id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invoices_org_agent
        ON invoices (organization_id, agent_id, id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invoices_org_issue_date
        ON invoices (organization_id, issue_date, id)
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_invoices_one_active_per_issuer_side
        ON invoices (
            organization_id, operation_id, side, issuer_key
        )
        WHERE origin_type = 'operation'
            AND status IN (
                'draft', 'ready_to_issue', 'issued', 'error'
            )
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_invoices_one_active_per_charge
        ON invoices (
            organization_id,
            agent_account_movement_id,
            invoice_purpose
        )
        WHERE origin_type = 'agent_account_charge'
            AND status IN (
                'draft', 'ready_to_issue', 'issued', 'error'
            )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invoices_charge_history
        ON invoices (
            organization_id,
            agent_account_movement_id,
            id
        )
        WHERE origin_type = 'agent_account_charge'
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_invoices_fiscal_voucher_unique
        ON invoices (
            organization_id,
            issuer_key,
            point_of_sale,
            fiscal_voucher_type,
            external_invoice_number
        )
        WHERE status = 'issued'
            AND provider = 'arca'
            AND external_invoice_number IS NOT NULL
        """
    )


def _needs_rebuild(cursor):
    rows = cursor.execute("PRAGMA table_info(invoices)").fetchall()
    columns = {row[1]: row for row in rows}
    if "origin_type" not in columns or "vat_rate" not in columns:
        return True
    operation = columns.get("operation_id")
    return operation is None or bool(operation[3])


def migrate_invoice_charge_origin_sqlite():
    """
    Rebuild once to relax ``operation_id NOT NULL``.

    Legacy rows are copied exactly and explicitly classified as operation
    invoices. No description-based matching or charge backfill is attempted.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        if not cursor.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'invoices'
            """
        ).fetchone():
            return

        if not _needs_rebuild(cursor):
            _create_indexes(cursor)
            connection.commit()
            return

        before = cursor.execute(
            "SELECT COUNT(*) FROM invoices"
        ).fetchone()[0]
        old_columns = {
            row[1]
            for row in cursor.execute(
                "PRAGMA table_info(invoices)"
            ).fetchall()
        }
        missing = [
            column
            for column in COPY_COLUMNS
            if column not in old_columns
        ]
        if missing:
            raise RuntimeError(
                "Cannot migrate invoices; missing columns: "
                + ", ".join(missing)
            )

        connection.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("DROP TABLE IF EXISTS invoices_phase3c_new")
        cursor.execute(SQLITE_INVOICES_V3_CREATE)
        columns_sql = ", ".join(COPY_COLUMNS)
        cursor.execute(
            f"""
            INSERT INTO invoices_phase3c_new (
                {columns_sql},
                origin_type,
                invoice_purpose
            )
            SELECT
                {columns_sql},
                'operation',
                'standard'
            FROM invoices
            """
        )
        cursor.execute("DROP TABLE invoices")
        cursor.execute(
            "ALTER TABLE invoices_phase3c_new RENAME TO invoices"
        )
        _create_indexes(cursor)

        after = cursor.execute(
            "SELECT COUNT(*) FROM invoices"
        ).fetchone()[0]
        if before != after:
            raise RuntimeError(
                f"Invoice migration row mismatch: {before} != {after}"
            )
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
