"""TA cache persistence: portable UPSERT, encryption, no secret logs."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_arca_ta_cache.db"
)
os.environ.pop("DATABASE_URL", None)
os.environ["INVOICE_PROVIDER"] = "arca"
os.environ["ARCA_ENV"] = "homologation"
os.environ["SECRET_KEY"] = "test-arca-ta-cache-secret"

from modules.arca.connections import ta_cache_key  # noqa: E402
from modules.arca.secrets import ArcaCredentials  # noqa: E402
from modules.arca.wsaa import (  # noqa: E402
    USER_AUTH_ERROR_KEY,
    USER_TA_PERSIST_KEY,
    ArcaTaPersistError,
    TicketAcceso,
    authenticate_wsaa,
)
from modules.config import BACKEND_POSTGRES, BACKEND_SQLITE  # noqa: E402
from modules.database import create_tables  # noqa: E402
from modules.database.arca_repository import (  # noqa: E402
    UPSERT_CACHED_TA_SQL,
    get_cached_ta,
    store_cached_ta,
)
from modules.database.connection import (  # noqa: E402
    adapt_sql,
    get_connection,
)
from modules.database.schema_postgres import POSTGRES_TABLES  # noqa: E402
from modules.arca.issuer_config import test_arca_connection  # noqa: E402


def _ticket(*, token="A-TOKEN", sign="A-SIGN", minutes=60, generated=None):
    now = datetime.now(timezone.utc)
    return TicketAcceso(
        token=token,
        sign=sign,
        expires_at=now + timedelta(minutes=minutes),
        service="wsfe",
        cuit="20300000003",
        environment="homologation",
        generation_time=generated or now,
    )


def _row(cache_key):
    connection = get_connection()
    try:
        return connection.execute(
            """
            SELECT token, sign, expires_at, service, cuit, environment,
                   generation_time, created_at, updated_at
            FROM arca_ta_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()
    finally:
        connection.close()


class ArcaTaCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_tables()

    def setUp(self):
        connection = get_connection()
        connection.execute("DELETE FROM arca_ta_cache")
        connection.commit()
        connection.close()

    def test_upsert_sql_is_portable_postgres_and_sqlite(self):
        self.assertIn("ON CONFLICT (cache_key) DO UPDATE", UPSERT_CACHED_TA_SQL)
        self.assertNotIn("INSERT OR REPLACE", UPSERT_CACHED_TA_SQL)
        self.assertNotIn(
            "created_at = excluded.created_at",
            UPSERT_CACHED_TA_SQL.lower(),
        )
        sqlite_sql = adapt_sql(UPSERT_CACHED_TA_SQL, BACKEND_SQLITE)
        postgres_sql = adapt_sql(UPSERT_CACHED_TA_SQL, BACKEND_POSTGRES)
        self.assertEqual(sqlite_sql.count("?"), 10)
        self.assertNotIn("?", postgres_sql)
        self.assertEqual(postgres_sql.count("%s"), 10)
        self.assertIn("ON CONFLICT (cache_key) DO UPDATE", postgres_sql)
        self.assertIn("arca_ta_cache", POSTGRES_TABLES)

    def test_sqlite_insert_then_get(self):
        key = ta_cache_key(1, 2)
        expires = datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc)
        ticket = TicketAcceso(
            token="PLAIN-TOKEN",
            sign="PLAIN-SIGN",
            expires_at=expires,
            service="wsfe",
            cuit="20300000003",
            environment="homologation",
            generation_time=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        )
        store_cached_ta(key, ticket)
        loaded = get_cached_ta(key)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.token, "PLAIN-TOKEN")
        self.assertEqual(loaded.sign, "PLAIN-SIGN")
        self.assertEqual(loaded.service, "wsfe")
        self.assertEqual(loaded.environment, "homologation")
        self.assertEqual(loaded.cuit, "20300000003")
        self.assertEqual(loaded.expires_at, expires)
        self.assertEqual(
            loaded.generation_time,
            datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        )

    def test_sqlite_second_store_updates_same_row(self):
        key = "1:2:homologation:wsfe"
        store_cached_ta(key, _ticket(token="FIRST", sign="SIGN-1"))
        first = _row(key)
        with patch(
            "modules.database.arca_repository._now_iso",
            return_value="2026-09-08T12:00:01+00:00",
        ):
            store_cached_ta(key, _ticket(token="SECOND", sign="SIGN-2"))
        second = _row(key)
        loaded = get_cached_ta(key)
        self.assertEqual(loaded.token, "SECOND")
        self.assertEqual(loaded.sign, "SIGN-2")
        self.assertEqual(first[7], second[7])
        self.assertNotEqual(first[8], second[8])
        self.assertEqual(second[8], "2026-09-08T12:00:01+00:00")

    def test_created_at_is_preserved_and_updated_at_changes(self):
        key = "9:8:homologation:wsfe"
        with patch(
            "modules.database.arca_repository._now_iso",
            return_value="2026-09-08T10:00:00+00:00",
        ):
            store_cached_ta(key, _ticket())
        with patch(
            "modules.database.arca_repository._now_iso",
            return_value="2026-09-08T10:00:05+00:00",
        ):
            store_cached_ta(key, _ticket(token="LATER"))
        row = _row(key)
        self.assertEqual(row[7], "2026-09-08T10:00:00+00:00")
        self.assertEqual(row[8], "2026-09-08T10:00:05+00:00")

    def test_token_and_sign_are_encrypted_at_rest(self):
        key = "3:4:homologation:wsfe"
        store_cached_ta(key, _ticket(token="SECRET-TOKEN", sign="SECRET-SIGN"))
        row = _row(key)
        self.assertNotEqual(row[0], "SECRET-TOKEN")
        self.assertNotEqual(row[1], "SECRET-SIGN")
        self.assertNotIn("SECRET-TOKEN", row[0])
        self.assertNotIn("SECRET-SIGN", row[1])
        self.assertTrue(str(row[0]).startswith("gAAAA"))
        self.assertTrue(str(row[1]).startswith("gAAAA"))

    def test_persist_failure_after_login_is_not_auth_failed(self):
        class _Transport:
            def wsaa_login(self, cms_b64):
                exp = (
                    datetime.now(timezone.utc) + timedelta(hours=12)
                ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                return (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    "<loginTicketResponse><header>"
                    f"<expirationTime>{exp}</expirationTime>"
                    "<service>wsfe</service></header>"
                    "<credentials>"
                    "<token>SECRET-TOKEN</token>"
                    "<sign>SECRET-SIGN</sign>"
                    "</credentials></loginTicketResponse>"
                )

        def _boom(_key, ticket):
            raise RuntimeError(f"cannot store {ticket.token} {ticket.sign}")

        creds = ArcaCredentials(certificate_pem=b"x", private_key_pem=b"y")
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            with self.assertLogs("modules.arca.wsaa", level="ERROR") as captured:
                with self.assertRaises(ArcaTaPersistError) as raised:
                    authenticate_wsaa(
                        creds,
                        cuit="20300000003",
                        transport=_Transport(),
                        cache_getter=lambda _key: None,
                        cache_setter=_boom,
                        cache_key="11:22:homologation:wsfe",
                    )
        error = raised.exception
        self.assertEqual(error.user_key, USER_TA_PERSIST_KEY)
        self.assertNotEqual(error.user_key, USER_AUTH_ERROR_KEY)
        self.assertEqual(str(error), USER_TA_PERSIST_KEY)
        self.assertEqual(error.organization_id, "11")
        self.assertEqual(error.user_id, "22")
        self.assertEqual(error.environment, "homologation")
        self.assertEqual(error.service, "wsfe")
        self.assertEqual(error.exception_type, "RuntimeError")
        log_text = "\n".join(captured.output)
        self.assertIn("arca_ta_persist_failed", log_text)
        self.assertIn("environment=homologation", log_text)
        self.assertIn("organization_id=11", log_text)
        self.assertIn("user_id=22", log_text)
        self.assertIn("service=wsfe", log_text)
        self.assertIn("db_engine=", log_text)
        self.assertIn("exception_type=RuntimeError", log_text)
        self.assertNotIn("SECRET-TOKEN", log_text)
        self.assertNotIn("SECRET-SIGN", log_text)
        self.assertNotIn("MOCKCMS", log_text)
        self.assertNotIn("BEGIN CERTIFICATE", log_text)
        self.assertNotIn("BEGIN PRIVATE", log_text)

    def test_verify_persist_failure_is_not_auth_failed(self):
        persist_error = ArcaTaPersistError(
            environment="homologation",
            organization_id="1",
            user_id="2",
            service="wsfe",
            db_engine="postgres",
            exception_type="ProgrammingError",
        )
        with patch(
            "modules.arca.connections.load_credentials",
            return_value=ArcaCredentials(
                certificate_pem=b"x",
                private_key_pem=b"y",
            ),
        ), patch(
            "modules.arca.client.ArcaClient.authenticate",
            side_effect=persist_error,
        ):
            status, error_key = test_arca_connection(
                {"tax_id": "20-30000000-3"},
                connection={"point_of_sale": "4"},
                organization_id=1,
                user_id=2,
            )
        self.assertEqual(status, "error")
        self.assertEqual(error_key, USER_TA_PERSIST_KEY)
        self.assertNotEqual(error_key, USER_AUTH_ERROR_KEY)


_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@unittest.skipUnless(
    _TEST_DATABASE_URL,
    "TEST_DATABASE_URL not set",
)
class ArcaTaCachePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._previous_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
        from modules.database import create_tables as _create

        _create()

    @classmethod
    def tearDownClass(cls):
        if cls._previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls._previous_url

    def setUp(self):
        connection = get_connection()
        connection.execute("DELETE FROM arca_ta_cache")
        connection.commit()
        connection.close()

    def test_postgres_upsert_preserves_created_at(self):
        from modules.config import get_database_backend

        self.assertEqual(get_database_backend(), BACKEND_POSTGRES)
        key = "5:6:homologation:wsfe"
        with patch(
            "modules.database.arca_repository._now_iso",
            return_value="2026-09-08T11:00:00+00:00",
        ):
            store_cached_ta(key, _ticket(token="PG-1"))
        with patch(
            "modules.database.arca_repository._now_iso",
            return_value="2026-09-08T11:00:09+00:00",
        ):
            store_cached_ta(key, _ticket(token="PG-2", sign="PG-SIGN-2"))
        loaded = get_cached_ta(key)
        row = _row(key)
        self.assertEqual(loaded.token, "PG-2")
        self.assertEqual(loaded.sign, "PG-SIGN-2")
        self.assertEqual(row[3], "wsfe")
        self.assertEqual(row[5], "homologation")
        self.assertEqual(row[7], "2026-09-08T11:00:00+00:00")
        self.assertEqual(row[8], "2026-09-08T11:00:09+00:00")
        self.assertNotEqual(row[0], "PG-2")
        self.assertTrue(str(row[0]).startswith("gAAAA"))


if __name__ == "__main__":
    unittest.main()
