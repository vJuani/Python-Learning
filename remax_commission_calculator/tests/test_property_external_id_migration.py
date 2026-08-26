"""
Regression: migrate properties.external_id on an existing SQLite DB.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _column_names(connection, table_name):
    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()
    return [row[1] for row in rows]


def _build_legacy_properties_db(db_path: Path) -> None:
    """Schema shaped like a pre-external_id Railway SQLite volume."""
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

            INSERT INTO organizations (id, name, is_active)
            VALUES (1, 'Legacy Org', 1);

            CREATE TABLE agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                organization_id INTEGER NOT NULL,
                FOREIGN KEY (organization_id)
                    REFERENCES organizations(id)
            );

            INSERT INTO agents (id, name, type, organization_id)
            VALUES (1, 'Legacy Agent', 'Alto', 1);

            CREATE TABLE properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                jurisdiction TEXT NOT NULL,
                organization_id INTEGER NOT NULL,
                agent_id INTEGER,
                status TEXT NOT NULL DEFAULT 'approved',
                FOREIGN KEY (organization_id)
                    REFERENCES organizations(id),
                FOREIGN KEY (agent_id)
                    REFERENCES agents(id)
            );

            INSERT INTO properties (
                id,
                address,
                jurisdiction,
                organization_id,
                agent_id,
                status
            )
            VALUES
                (1, 'Calle Vieja 100', 'CABA', 1, 1, 'approved'),
                (2, 'Calle Vieja 200', 'PBA', 1, 1, 'approved');
            """
        )
        connection.commit()
    finally:
        connection.close()


class PropertyExternalIdMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "legacy.db"
        _build_legacy_properties_db(self.db_path)

        self._env = mock.patch.dict(
            os.environ,
            {
                "DATABASE_PATH": str(self.db_path),
            },
            clear=False,
        )
        self._env.start()
        os.environ.pop("DATABASE_URL", None)

        raw = sqlite3.connect(str(self.db_path))
        try:
            columns = _column_names(raw, "properties")
            self.assertNotIn("external_id", columns)
            self.assertEqual(
                raw.execute(
                    "SELECT COUNT(*) FROM properties"
                ).fetchone()[0],
                2,
            )
        finally:
            raw.close()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_create_tables_adds_external_id_preserves_rows(self):
        from modules.database import (
            create_tables,
            get_database_path,
            get_properties,
        )
        from modules.database.connection import get_connection
        from modules.database.schema import (
            _column_exists,
            ensure_properties_external_id,
            migrate_schema,
        )

        self.assertEqual(
            get_database_path(),
            str(self.db_path),
        )

        create_tables(create_backup=False)

        connection = get_connection()
        try:
            cursor = connection.cursor()
            self.assertTrue(
                _column_exists(
                    cursor,
                    "properties",
                    "external_id",
                )
            )
            rows = cursor.execute(
                """
                SELECT id, address, external_id
                FROM properties
                ORDER BY id
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "Calle Vieja 100")
        self.assertEqual(rows[1][1], "Calle Vieja 200")

        properties = get_properties(1)
        self.assertEqual(len(properties), 2)
        by_id = {item["id"]: item for item in properties}
        self.assertEqual(by_id[1]["address"], "Calle Vieja 100")
        self.assertIn("external_id", by_id[1])

        # Second pass must be idempotent (release + wsgi / redeploy).
        create_tables(create_backup=False)
        migrate_schema(create_backup=False)
        ensure_properties_external_id()

        connection = get_connection()
        try:
            cursor = connection.cursor()
            self.assertTrue(
                _column_exists(
                    cursor,
                    "properties",
                    "external_id",
                )
            )
            count = cursor.execute(
                "SELECT COUNT(*) FROM properties"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(count, 2)
        self.assertEqual(len(get_properties(1)), 2)

    def test_backfill_runs_only_after_column_exists(self):
        from modules.database.connection import get_connection
        from modules.database.schema import (
            _backfill_properties_external_id,
            _column_exists,
            ensure_properties_external_id,
        )

        connection = get_connection()
        try:
            cursor = connection.cursor()
            self.assertFalse(
                _column_exists(
                    cursor,
                    "properties",
                    "external_id",
                )
            )
            with self.assertRaises(Exception):
                _backfill_properties_external_id(cursor)
            connection.rollback()
        finally:
            connection.close()

        ensure_properties_external_id()

        connection = get_connection()
        try:
            cursor = connection.cursor()
            self.assertTrue(
                _column_exists(
                    cursor,
                    "properties",
                    "external_id",
                )
            )
            # Insert listing after column exists, then backfill.
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS
                property_external_listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id INTEGER NOT NULL,
                    property_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_label TEXT,
                    external_id TEXT,
                    url TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    listing_currency TEXT,
                    buyer_side_commission_percent REAL,
                    seller_side_commission_percent REAL,
                    created_at TEXT,
                    updated_at TEXT,
                    last_synced_at TEXT,
                    created_by_user_id INTEGER,
                    updated_by_user_id INTEGER
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO property_external_listings (
                    organization_id,
                    property_id,
                    provider,
                    external_id,
                    status
                )
                VALUES (1, 1, 'remax_web', 'MLSID-LEGACY-1', 'active')
                """
            )
            connection.commit()
        finally:
            connection.close()

        ensure_properties_external_id()

        connection = get_connection()
        try:
            external_id = connection.execute(
                """
                SELECT external_id
                FROM properties
                WHERE id = 1
                """
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(external_id, "MLSID-LEGACY-1")


if __name__ == "__main__":
    unittest.main()
