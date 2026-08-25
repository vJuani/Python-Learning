"""
Tests for SQLite → PostgreSQL ETL mapper / migration.

PostgreSQL integration runs only when TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "",
).strip()


class EtlMapperUnitTests(unittest.TestCase):
    def test_to_decimal_preserves_scale(self):
        from modules.database.etl_sqlite_to_postgres import (
            to_decimal,
        )

        self.assertEqual(
            to_decimal(250000.1234),
            Decimal("250000.1234"),
        )
        self.assertEqual(to_decimal(None), None)
        self.assertEqual(to_decimal(3), Decimal("3"))

    def test_to_flag(self):
        from modules.database.etl_sqlite_to_postgres import (
            to_flag,
        )

        self.assertEqual(to_flag(1), 1)
        self.assertEqual(to_flag(0), 0)
        self.assertEqual(to_flag(True), 1)
        self.assertIsNone(to_flag(None))

    def test_migration_table_order_covers_core(self):
        from modules.database.etl_sqlite_to_postgres import (
            MIGRATION_TABLE_ORDER,
        )

        self.assertEqual(
            MIGRATION_TABLE_ORDER[0],
            "organizations",
        )
        self.assertIn("agents", MIGRATION_TABLE_ORDER)
        self.assertIn(
            "agent_wallet_movements",
            MIGRATION_TABLE_ORDER,
        )
        self.assertLess(
            MIGRATION_TABLE_ORDER.index("agents"),
            MIGRATION_TABLE_ORDER.index("users"),
        )
        self.assertLess(
            MIGRATION_TABLE_ORDER.index("properties"),
            MIGRATION_TABLE_ORDER.index("operations"),
        )
        self.assertLess(
            MIGRATION_TABLE_ORDER.index("operations"),
            MIGRATION_TABLE_ORDER.index(
                "agent_wallet_movements"
            ),
        )


@unittest.skipUnless(
    _TEST_DATABASE_URL,
    "TEST_DATABASE_URL not set",
)
class EtlPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg_url = _TEST_DATABASE_URL

    def _build_sqlite_fixture(self) -> Path:
        from modules.auth import ROLE_ADMIN, hash_password
        from modules.database import (
            add_agent,
            add_operation,
            add_organization,
            add_property,
            add_user,
            create_tables,
            create_property_external_listing,
            insert_wallet_movement,
        )
        from modules.database.agent_wallet_repository import (
            MOVEMENT_OWN_COMMISSION,
        )

        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "etl_source.db"

        os.environ.pop("DATABASE_URL", None)
        os.environ["DATABASE_PATH"] = str(db_path)

        # Fresh sqlite schema for fixture.
        create_tables()

        org = add_organization("ETL Org")
        leader = add_agent("Leader", "Alto", org)
        junior = add_agent(
            "Junior",
            "Junior",
            org,
            team_leader_agent_id=leader,
        )
        add_user(
            "etl_admin",
            hash_password("secret"),
            ROLE_ADMIN,
            org,
        )
        prop = add_property(
            "Calle ETL 1",
            "CABA",
            org,
            agent_id=junior,
            listing_price=123456.789,
        )
        op = add_operation(
            "10/02/2026",
            junior,
            prop,
            "no",
            0,
            1000.5,
            3,
            30.015,
            27.0135,
            3.0015,
            13.50675,
            13.50675,
            0,
            0,
            org,
        )
        create_property_external_listing(
            org,
            prop,
            "remax_web",
            "https://www.remax.com.ar/listings/etl-1",
            "active",
            external_id="ETL-MLSID-1",
            listing_currency="USD",
            buyer_side_commission_percent=3.25,
        )
        insert_wallet_movement(
            org,
            junior,
            movement_type=MOVEMENT_OWN_COMMISSION,
            amount=13.50675,
            operation_id=op,
            idempotency_key="etl-wallet-1",
        )

        return db_path

    def test_dry_run_and_real_migration(self):
        from modules.database.etl_sqlite_to_postgres import (
            EtlError,
            count_rows,
            open_postgres,
            open_sqlite_readonly,
            run_migration,
        )

        sqlite_path = self._build_sqlite_fixture()
        logs: list[str] = []

        def capture(message: str) -> None:
            logs.append(message)

        dry = run_migration(
            str(sqlite_path),
            self.pg_url,
            dry_run=True,
            force=True,
            reset_schema=True,
            progress=capture,
        )
        self.assertTrue(dry.dry_run)
        self.assertTrue(dry.validation.passed)

        # Dry-run must leave destination empty after rollback.
        # This is by design — not a failed real migration.
        with open_postgres(self.pg_url) as pg:
            pg.autocommit = True
            self.assertEqual(
                count_rows(pg, "organizations"),
                0,
            )
            self.assertEqual(
                count_rows(pg, "agents"),
                0,
            )

        real = run_migration(
            str(sqlite_path),
            self.pg_url,
            dry_run=False,
            force=True,
            reset_schema=False,
            progress=capture,
        )
        self.assertFalse(real.dry_run)
        self.assertTrue(real.validation.passed)

        agents_result = next(
            r
            for r in real.table_results
            if r.table == "agents"
        )
        self.assertEqual(agents_result.source_count, 2)
        self.assertEqual(agents_result.dest_count, 2)
        self.assertTrue(agents_result.ok)

        sqlite_conn = open_sqlite_readonly(str(sqlite_path))
        try:
            with open_postgres(self.pg_url) as pg:
                pg.autocommit = True
                self.assertEqual(
                    count_rows(sqlite_conn, "agents"),
                    2,
                )
                self.assertEqual(
                    count_rows(pg, "agents"),
                    2,
                )
                self.assertEqual(
                    count_rows(sqlite_conn, "agents"),
                    count_rows(pg, "agents"),
                )
                self.assertEqual(
                    count_rows(
                        sqlite_conn,
                        "agent_wallet_movements",
                    ),
                    count_rows(
                        pg,
                        "agent_wallet_movements",
                    ),
                )
                tl = pg.execute(
                    """
                    SELECT COUNT(*)
                    FROM agents
                    WHERE team_leader_agent_id IS NOT NULL
                    """
                ).fetchone()[0]
                self.assertEqual(int(tl), 1)
        finally:
            sqlite_conn.close()

        # Non-force on nonempty should fail.
        with self.assertRaises(EtlError):
            run_migration(
                str(sqlite_path),
                self.pg_url,
                dry_run=False,
                force=False,
                progress=lambda _m: None,
            )


if __name__ == "__main__":
    unittest.main()
