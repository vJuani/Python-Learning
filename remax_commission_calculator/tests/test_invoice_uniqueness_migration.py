"""
Regression: invoice uniqueness migration on legacy SQLite DBs.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.database.invoice_uniqueness_migration import (
    LEGACY_INDEX_NAME,
    NEW_INDEX_NAME,
    migrate_invoices_active_uniqueness_sqlite,
)


def _index_names(connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'index'
        """
    ).fetchall()
    return {row[0] for row in rows}


def _duplicate_active_groups(connection):
    return connection.execute(
        """
        SELECT
            organization_id,
            operation_id,
            side,
            issuer_key,
            COUNT(*) AS row_count
        FROM invoices
        WHERE status IN (
            'draft', 'ready_to_issue', 'issued', 'error'
        )
        GROUP BY organization_id, operation_id, side, issuer_key
        HAVING COUNT(*) > 1
        """
    ).fetchall()


def _insert_invoice(
    connection,
    *,
    invoice_id: int,
    organization_id: int = 1,
    operation_id: int = 1,
    agent_id: int = 1,
    status: str = "draft",
    side: str | None = None,
    issuer_key: str | None = None,
    with_identity_columns: bool = False,
) -> None:
    now = "2026-01-01T00:00:00"
    if with_identity_columns:
        connection.execute(
            """
            INSERT INTO invoices (
                id, organization_id, invoice_seq,
                invoice_number_internal, operation_id, agent_id,
                issuer_type, issuer_name, issuer_tax_id,
                recipient_name, recipient_tax_id, description,
                unit_price, subtotal, total_amount,
                payment_condition, issue_date, status,
                created_at, updated_at, side, issuer_key
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, 'agent', 'Issuer', '20-11111111-1',
                'Client', '20-22222222-2', 'Services', 100, 100, 121,
                'cuenta_corriente', '2026-01-01', ?, ?, ?, ?, ?
            )
            """,
            (
                invoice_id,
                organization_id,
                invoice_id,
                f"INV-{invoice_id:06d}",
                operation_id,
                agent_id,
                status,
                now,
                now,
                side,
                issuer_key,
            ),
        )
        return

    connection.execute(
        """
        INSERT INTO invoices (
            id, organization_id, invoice_seq,
            invoice_number_internal, operation_id, agent_id,
            issuer_type, issuer_name, issuer_tax_id,
            recipient_name, recipient_tax_id, description,
            unit_price, subtotal, total_amount,
            payment_condition, issue_date, status,
            created_at, updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, 'agent', 'Issuer', '20-11111111-1',
            'Client', '20-22222222-2', 'Services', 100, 100, 121,
            'cuenta_corriente', '2026-01-01', ?, ?, ?
        )
        """,
        (
            invoice_id,
            organization_id,
            invoice_id,
            f"INV-{invoice_id:06d}",
            operation_id,
            agent_id,
            status,
            now,
            now,
        ),
    )


def _build_legacy_schema(connection, *, with_identity_columns: bool = False) -> None:
    identity_cols = ""
    if with_identity_columns:
        identity_cols = """
            , side TEXT
            , issuer_key TEXT
        """

    connection.executescript(
        f"""
        CREATE TABLE organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO organizations (id, name) VALUES (1, 'Legacy Org');

        CREATE TABLE agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            organization_id INTEGER NOT NULL
        );
        INSERT INTO agents (id, name, type, organization_id)
        VALUES
            (1, 'Agent One', 'Alto', 1),
            (2, 'Agent Two', 'Alto', 1),
            (3, 'Agent Three', 'Alto', 1);

        CREATE TABLE operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_date TEXT NOT NULL,
            agent_id INTEGER NOT NULL,
            property_id INTEGER,
            organization_id INTEGER NOT NULL,
            sale_price REAL NOT NULL DEFAULT 0,
            commission_rate REAL NOT NULL DEFAULT 0,
            total_commission REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open'
        );
        INSERT INTO operations (
            id, operation_date, agent_id, organization_id
        )
        VALUES (1, '2026-01-01', 1, 1);

        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            invoice_seq INTEGER NOT NULL,
            invoice_number_internal TEXT NOT NULL,
            operation_id INTEGER NOT NULL,
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
            cash_movement_id INTEGER
            {identity_cols},
            UNIQUE (organization_id, invoice_seq),
            UNIQUE (organization_id, invoice_number_internal)
        );
        """
    )


class InvoiceUniquenessMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "legacy_invoices.db"

        self._env = mock.patch.dict(
            os.environ,
            {"DATABASE_PATH": str(self.db_path)},
            clear=False,
        )
        self._env.start()
        os.environ.pop("DATABASE_URL", None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _connect(self):
        return sqlite3.connect(str(self.db_path))

    def test_legacy_two_active_same_operation(self):
        connection = self._connect()
        try:
            _build_legacy_schema(connection)
            _insert_invoice(connection, invoice_id=1, agent_id=1, status="draft")
            _insert_invoice(connection, invoice_id=2, agent_id=2, status="issued")
            connection.commit()
        finally:
            connection.close()

        from modules.database import create_tables

        create_tables(create_backup=False)

        connection = self._connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM invoices"
                ).fetchone()[0],
                2,
            )
            self.assertIn(NEW_INDEX_NAME, _index_names(connection))
            self.assertNotIn(LEGACY_INDEX_NAME, _index_names(connection))
        finally:
            connection.close()

    def test_buyer_and_seller_explicit(self):
        connection = self._connect()
        try:
            _build_legacy_schema(connection, with_identity_columns=True)
            _insert_invoice(
                connection,
                invoice_id=1,
                agent_id=1,
                status="draft",
                side="buyer",
                issuer_key="agent:1",
                with_identity_columns=True,
            )
            _insert_invoice(
                connection,
                invoice_id=2,
                agent_id=2,
                status="issued",
                side="seller",
                issuer_key="agent:2",
                with_identity_columns=True,
            )
            connection.commit()
        finally:
            connection.close()

        from modules.database import create_tables

        create_tables(create_backup=False)

        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT side, issuer_key
                FROM invoices
                ORDER BY id
                """
            ).fetchall()
            self.assertEqual(rows[0][0], "buyer")
            self.assertEqual(rows[1][0], "seller")
        finally:
            connection.close()

    def test_same_side_two_issuers(self):
        connection = self._connect()
        try:
            _build_legacy_schema(connection, with_identity_columns=True)
            _insert_invoice(
                connection,
                invoice_id=1,
                agent_id=1,
                status="draft",
                side="buyer",
                issuer_key="agent:1",
                with_identity_columns=True,
            )
            _insert_invoice(
                connection,
                invoice_id=2,
                agent_id=2,
                status="issued",
                side="buyer",
                issuer_key="agent:2",
                with_identity_columns=True,
            )
            connection.commit()
        finally:
            connection.close()

        from modules.database import create_tables

        create_tables(create_backup=False)

        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT issuer_key
                FROM invoices
                ORDER BY id
                """
            ).fetchall()
            self.assertNotEqual(rows[0][0], rows[1][0])
            self.assertEqual(len(_duplicate_active_groups(connection)), 0)
        finally:
            connection.close()

    def test_three_active_same_operation(self):
        connection = self._connect()
        try:
            _build_legacy_schema(connection)
            _insert_invoice(connection, invoice_id=1, agent_id=1, status="draft")
            _insert_invoice(connection, invoice_id=2, agent_id=2, status="issued")
            _insert_invoice(connection, invoice_id=3, agent_id=3, status="error")
            connection.commit()
        finally:
            connection.close()

        from modules.database import create_tables

        create_tables(create_backup=False)

        connection = self._connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM invoices"
                ).fetchone()[0],
                3,
            )
            self.assertEqual(len(_duplicate_active_groups(connection)), 0)
        finally:
            connection.close()

    def test_null_side_and_issuer_backfilled(self):
        connection = self._connect()
        try:
            _build_legacy_schema(connection, with_identity_columns=True)
            _insert_invoice(
                connection,
                invoice_id=1,
                status="draft",
                with_identity_columns=True,
            )
            _insert_invoice(
                connection,
                invoice_id=2,
                agent_id=2,
                status="issued",
                with_identity_columns=True,
            )
            connection.commit()
        finally:
            connection.close()

        from modules.database import create_tables

        create_tables(create_backup=False)

        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT side, issuer_key FROM invoices ORDER BY id"
            ).fetchall()
            self.assertTrue(all(row[0] and row[1] for row in rows))
        finally:
            connection.close()

    def test_new_index_already_exists_before_migration(self):
        """Reproduce production crash: index exists while rows need normalization.

        SQLite treats NULL as distinct in UNIQUE indexes, so two active invoices
        with NULL side/issuer_key can coexist under the partial unique index.
        Backfilling them to the same buyer/agent identity then fails if the
        index is still active during UPDATE.
        """
        connection = self._connect()
        try:
            _build_legacy_schema(connection, with_identity_columns=True)
            _insert_invoice(
                connection,
                invoice_id=1,
                agent_id=1,
                status="draft",
                side=None,
                issuer_key=None,
                with_identity_columns=True,
            )
            _insert_invoice(
                connection,
                invoice_id=2,
                agent_id=1,
                status="issued",
                side=None,
                issuer_key=None,
                with_identity_columns=True,
            )
            connection.execute(
                f"""
                CREATE UNIQUE INDEX {NEW_INDEX_NAME}
                ON invoices (
                    organization_id,
                    operation_id,
                    side,
                    issuer_key
                )
                WHERE status IN (
                    'draft', 'ready_to_issue', 'issued', 'error'
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        from modules.database.connection import get_connection

        connection = get_connection()
        try:
            cursor = connection.cursor()
            migrate_invoices_active_uniqueness_sqlite(cursor)
            connection.commit()
        finally:
            connection.close()

        connection = self._connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM invoices"
                ).fetchone()[0],
                2,
            )
            self.assertIn(NEW_INDEX_NAME, _index_names(connection))
            self.assertEqual(len(_duplicate_active_groups(connection)), 0)
        finally:
            connection.close()

    def test_migration_runs_twice(self):
        connection = self._connect()
        try:
            _build_legacy_schema(connection)
            _insert_invoice(connection, invoice_id=1, status="draft")
            _insert_invoice(connection, invoice_id=2, agent_id=2, status="issued")
            connection.commit()
        finally:
            connection.close()

        from modules.database.connection import get_connection

        connection = get_connection()
        try:
            cursor = connection.cursor()
            migrate_invoices_active_uniqueness_sqlite(cursor)
            connection.commit()
            migrate_invoices_active_uniqueness_sqlite(cursor)
            connection.commit()
        finally:
            connection.close()

    def test_create_tables_twice(self):
        connection = self._connect()
        try:
            _build_legacy_schema(connection)
            _insert_invoice(connection, invoice_id=1, status="draft")
            _insert_invoice(connection, invoice_id=2, agent_id=2, status="issued")
            connection.commit()
        finally:
            connection.close()

        from modules.database import create_tables

        create_tables(create_backup=False)
        create_tables(create_backup=False)

        connection = self._connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM invoices"
                ).fetchone()[0],
                2,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
