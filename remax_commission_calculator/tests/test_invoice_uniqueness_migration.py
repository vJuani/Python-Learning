"""
Regression: invoice uniqueness migration on legacy SQLite DBs.

Production crash: CREATE UNIQUE INDEX idx_invoices_one_active_per_operation
failed when multiple active invoices existed for the same operation.
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


def _insert_invoice(
    connection,
    *,
    invoice_id: int,
    organization_id: int = 1,
    operation_id: int = 1,
    agent_id: int = 1,
    status: str = "draft",
) -> None:
    now = "2026-01-01T00:00:00"
    connection.execute(
        """
        INSERT INTO invoices (
            id,
            organization_id,
            invoice_seq,
            invoice_number_internal,
            operation_id,
            agent_id,
            issuer_type,
            issuer_name,
            issuer_tax_id,
            recipient_name,
            recipient_tax_id,
            description,
            unit_price,
            subtotal,
            total_amount,
            payment_condition,
            issue_date,
            status,
            created_at,
            updated_at
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


def _build_legacy_invoicing_db(db_path: Path) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
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
                (2, 'Agent Two', 'Alto', 1);

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
                cash_movement_id INTEGER,
                UNIQUE (organization_id, invoice_seq),
                UNIQUE (organization_id, invoice_number_internal)
            );
            """
        )
        _insert_invoice(connection, invoice_id=1, agent_id=1, status="draft")
        _insert_invoice(connection, invoice_id=2, agent_id=2, status="issued")
        connection.commit()
    finally:
        connection.close()


class InvoiceUniquenessMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "legacy_invoices.db"
        _build_legacy_invoicing_db(self.db_path)

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

    def test_legacy_duplicate_active_invoices_boots_and_preserves_rows(self):
        from modules.database import create_tables
        from modules.database.connection import get_connection

        create_tables(create_backup=False)

        connection = get_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM invoices"
            ).fetchone()[0]
            indexes = _index_names(connection)
            sides = connection.execute(
                """
                SELECT id, side, issuer_key, status
                FROM invoices
                ORDER BY id
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(count, 2)
        self.assertNotIn(LEGACY_INDEX_NAME, indexes)
        self.assertIn(NEW_INDEX_NAME, indexes)
        self.assertEqual(sides[0][1], "buyer")
        self.assertEqual(sides[1][1], "seller")
        self.assertTrue(sides[0][2])
        self.assertTrue(sides[1][2])

    def test_migration_is_idempotent(self):
        from modules.database import create_tables

        create_tables(create_backup=False)
        create_tables(create_backup=False)

        connection = sqlite3.connect(str(self.db_path))
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM invoices"
            ).fetchone()[0]
            indexes = _index_names(connection)
        finally:
            connection.close()

        self.assertEqual(count, 2)
        self.assertIn(NEW_INDEX_NAME, indexes)
        self.assertNotIn(LEGACY_INDEX_NAME, indexes)

    def test_two_issuers_same_operation_allowed(self):
        from modules.database import create_tables
        from modules.database.connection import get_connection

        create_tables(create_backup=False)

        connection = get_connection()
        try:
            rows = connection.execute(
                """
                SELECT agent_id, side, issuer_key
                FROM invoices
                ORDER BY id
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(rows[0][0], 1)
        self.assertEqual(rows[1][0], 2)
        self.assertNotEqual(rows[0][1], rows[1][1])
        self.assertNotEqual(rows[0][2], rows[1][2])

    def test_legacy_rows_without_side_get_backfilled(self):
        from modules.database import create_tables
        from modules.database.connection import get_connection

        create_tables(create_backup=False)

        raw = sqlite3.connect(str(self.db_path))
        try:
            raw.execute(
                "UPDATE invoices SET side = NULL, issuer_key = NULL"
            )
            raw.commit()
        finally:
            raw.close()

        create_tables(create_backup=False)

        connection = get_connection()
        try:
            rows = connection.execute(
                """
                SELECT side, issuer_key
                FROM invoices
                ORDER BY id
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertTrue(all(row[0] for row in rows))
        self.assertTrue(all(row[1] for row in rows))


if __name__ == "__main__":
    unittest.main()
