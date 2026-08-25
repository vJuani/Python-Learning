"""
Unit tests for dual SQLite/PostgreSQL connection helpers.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ.pop("DATABASE_URL", None)
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_db_connection.db"
)

from modules.config import (  # noqa: E402
    BACKEND_POSTGRES,
    BACKEND_SQLITE,
    get_database_backend,
)
from modules.database import create_tables  # noqa: E402
from modules.database.connection import (  # noqa: E402
    IntegrityError,
    adapt_sql,
    execute_insert,
    get_connection,
)
from modules.database.organizations_repository import (  # noqa: E402
    add_organization,
)


class AdaptSqlTests(unittest.TestCase):
    def test_sqlite_leaves_qmark(self):
        sql = "SELECT id FROM agents WHERE id = ?"
        self.assertEqual(
            adapt_sql(sql, BACKEND_SQLITE),
            sql,
        )

    def test_postgres_replaces_qmarks(self):
        sql = (
            "SELECT id FROM agents "
            "WHERE organization_id = ? AND name LIKE ?"
        )
        adapted = adapt_sql(sql, BACKEND_POSTGRES)
        self.assertEqual(
            adapted,
            "SELECT id FROM agents "
            "WHERE organization_id = %s AND name LIKE %s",
        )
        self.assertNotIn("?", adapted)

    def test_postgres_skips_qmark_inside_quotes(self):
        sql = "SELECT '?' AS q, id FROM t WHERE id = ?"
        adapted = adapt_sql(sql, BACKEND_POSTGRES)
        self.assertEqual(
            adapted,
            "SELECT '?' AS q, id FROM t WHERE id = %s",
        )

    def test_postgres_escapes_percent_outside_literals(self):
        sql = "SELECT 100 % 3, id FROM t WHERE id = ?"
        adapted = adapt_sql(sql, BACKEND_POSTGRES)
        self.assertEqual(
            adapted,
            "SELECT 100 %% 3, id FROM t WHERE id = %s",
        )

    def test_postgres_keeps_percent_inside_string_literal(self):
        sql = (
            "SELECT 1 WHERE url LIKE 'http://%' OR id = ?"
        )
        adapted = adapt_sql(sql, BACKEND_POSTGRES)
        self.assertEqual(
            adapted,
            "SELECT 1 WHERE url LIKE 'http://%' OR id = %s",
        )


class BackendSelectionTests(unittest.TestCase):
    def test_default_is_sqlite_without_database_url(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            self.assertEqual(
                get_database_backend(),
                BACKEND_SQLITE,
            )

    def test_database_url_selects_postgres(self):
        with mock.patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql://u:p@localhost:5432/db"
                ),
                "DATABASE_PATH": "ignored.db",
            },
            clear=False,
        ):
            self.assertEqual(
                get_database_backend(),
                BACKEND_POSTGRES,
            )


class SqliteInsertHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_tables()

    def test_execute_insert_returns_id(self):
        organization_id = add_organization(
            "Conn Layer Org"
        )
        self.assertIsInstance(organization_id, int)
        self.assertGreater(organization_id, 0)

        connection = get_connection()
        cursor = connection.cursor()
        agent_id = execute_insert(
            cursor,
            """
            INSERT INTO agents (
                name,
                type,
                organization_id
            )
            VALUES (?, ?, ?)
            """,
            ("Agent A", "Alto", organization_id),
        )
        connection.commit()
        connection.close()

        self.assertIsInstance(agent_id, int)
        self.assertGreater(agent_id, 0)

    def test_integrity_error_is_catchable(self):
        organization_id = add_organization(
            "Conn Integrity Org"
        )
        connection = get_connection()
        cursor = connection.cursor()
        execute_insert(
            cursor,
            """
            INSERT INTO agents (
                name,
                type,
                organization_id
            )
            VALUES (?, ?, ?)
            """,
            ("Dup Agent", "Alto", organization_id),
        )
        connection.commit()

        with self.assertRaises(IntegrityError):
            # Force FK failure against missing org.
            execute_insert(
                cursor,
                """
                INSERT INTO agents (
                    name,
                    type,
                    organization_id
                )
                VALUES (?, ?, ?)
                """,
                ("Bad FK", "Alto", 999999),
            )
            connection.commit()

        connection.rollback()
        connection.close()


if __name__ == "__main__":
    unittest.main()
