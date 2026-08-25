"""
PostgreSQL smoke / integration tests.

Runs only when TEST_DATABASE_URL is set. Skips cleanly otherwise.
May DROP SCHEMA public CASCADE on the target database — use a
disposable local database, never production/staging data.
"""

from __future__ import annotations

import os
import unittest

_TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "",
).strip()


@unittest.skipUnless(
    _TEST_DATABASE_URL,
    "TEST_DATABASE_URL not set",
)
class PostgresSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._previous_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
        # Ensure backend selection ignores leftover path-only mode.
        os.environ.pop("DATABASE_BACKEND", None)

        from modules.database import create_tables
        from modules.database.schema_postgres import (
            POSTGRES_SCHEMA_VERSION,
            POSTGRES_TABLES,
        )

        cls._reset_public_schema()
        create_tables()

        cls.POSTGRES_TABLES = POSTGRES_TABLES
        cls.POSTGRES_SCHEMA_VERSION = POSTGRES_SCHEMA_VERSION

    @classmethod
    def tearDownClass(cls):
        if cls._previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls._previous_url

    @classmethod
    def _reset_public_schema(cls):
        from modules.database.connection import (
            get_connection,
        )

        connection = get_connection()
        cursor = connection.cursor()

        try:
            cursor.execute(
                "DROP SCHEMA IF EXISTS public CASCADE"
            )
            cursor.execute("CREATE SCHEMA public")
            cursor.execute(
                "GRANT ALL ON SCHEMA public TO PUBLIC"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def test_01_schema_tables_and_version(self):
        from modules.database.connection import (
            get_connection,
        )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            """
        )
        tables = {row[0] for row in cursor.fetchall()}
        connection.close()

        for table in self.POSTGRES_TABLES:
            self.assertIn(table, tables)

        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT version
            FROM schema_migrations
            WHERE version = ?
            """,
            (self.POSTGRES_SCHEMA_VERSION,),
        )
        row = cursor.fetchone()
        connection.close()
        self.assertIsNotNone(row)

    def test_02_create_core_entities_and_read_back(self):
        from modules.auth import ROLE_ADMIN, hash_password
        from modules.database import (
            IntegrityError,
            add_agent,
            add_operation,
            add_organization,
            add_property,
            add_user,
            create_property_external_listing,
            get_agent_record,
            get_operation_record,
            get_property_record,
            insert_wallet_movement,
            list_wallet_movements_for_agent,
        )
        from modules.database.agent_wallet_repository import (
            MOVEMENT_OWN_COMMISSION,
        )
        from modules.database.property_external_listings_repository import (
            find_listing_by_external_id,
        )

        organization_id = add_organization(
            "PG Smoke Org"
        )
        self.assertGreater(organization_id, 0)

        leader_id = add_agent(
            "Team Leader",
            "Alto",
            organization_id,
        )
        junior_id = add_agent(
            "Junior Agent",
            "Junior",
            organization_id,
            team_leader_agent_id=leader_id,
        )

        junior = get_agent_record(
            junior_id,
            organization_id,
        )
        self.assertEqual(
            junior["team_leader_agent_id"],
            leader_id,
        )

        user_id = add_user(
            "pg_admin",
            hash_password("secret-pass"),
            ROLE_ADMIN,
            organization_id,
        )
        self.assertGreater(user_id, 0)

        property_id = add_property(
            "Av. Test 123",
            "CABA",
            organization_id,
            agent_id=junior_id,
            property_type="apartment",
            listing_price=250000.1234,
            listing_purpose="sale",
        )
        prop = get_property_record(
            property_id,
            organization_id,
        )
        self.assertEqual(prop["address"], "Av. Test 123")
        self.assertAlmostEqual(
            float(prop["listing_price"]),
            250000.1234,
            places=4,
        )

        operation_id = add_operation(
            "15/03/2026",
            junior_id,
            property_id,
            "no",
            0,
            100000,
            3,
            3000,
            2700,
            300,
            1350,
            1350,
            0,
            0,
            organization_id,
        )
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        self.assertEqual(
            operation["date"],
            "15/03/2026",
        )
        self.assertAlmostEqual(
            float(operation["sale_price"]),
            100000,
            places=4,
        )

        listing = create_property_external_listing(
            organization_id,
            property_id,
            "remax_web",
            "https://www.remax.com.ar/listings/pg-smoke-1",
            "active",
            external_id="MLSID-PG-SMOKE-1",
            listing_currency="USD",
            buyer_side_commission_percent=3.5,
            seller_side_commission_percent=3.5,
            created_by_user_id=user_id,
        )
        self.assertEqual(
            listing["external_id"],
            "MLSID-PG-SMOKE-1",
        )

        found = find_listing_by_external_id(
            organization_id,
            "remax_web",
            "MLSID-PG-SMOKE-1",
        )
        self.assertIsNotNone(found)
        self.assertEqual(
            found["id"],
            listing["id"],
        )

        movement = insert_wallet_movement(
            organization_id,
            junior_id,
            movement_type=MOVEMENT_OWN_COMMISSION,
            amount=1350.5,
            currency="USD",
            operation_id=operation_id,
            idempotency_key="pg-smoke-wallet-1",
        )
        # insert_wallet_movement returns the movement dict (same
        # contract as SQLite / modules.agent_wallet), not a bare id.
        self.assertIsInstance(movement, dict)
        self.assertIn("id", movement)
        movements = list_wallet_movements_for_agent(
            organization_id,
            junior_id,
        )
        self.assertTrue(
            any(
                m["id"] == movement["id"]
                for m in movements
            )
        )

        with self.assertRaises(IntegrityError):
            add_user(
                "pg_admin",
                hash_password("other"),
                ROLE_ADMIN,
                organization_id,
            )

        with self.assertRaises(IntegrityError):
            insert_wallet_movement(
                organization_id,
                junior_id,
                movement_type=MOVEMENT_OWN_COMMISSION,
                amount=1,
                currency="USD",
                operation_id=operation_id,
                idempotency_key="pg-smoke-wallet-1",
            )

    def test_03_init_is_idempotent(self):
        from modules.database import create_tables
        from modules.database import add_organization

        create_tables()
        create_tables()

        organization_id = add_organization(
            "PG Idempotent Org"
        )
        self.assertGreater(organization_id, 0)


if __name__ == "__main__":
    unittest.main()
