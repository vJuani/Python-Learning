"""Reservation wizard: migration, confirm, deposits, and one document row."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_reservation_wizard.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_tasks import complete_task, create_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.properties_repository import get_property_record
from modules.database.reservations_migration import migrate_reservations
from modules.database.reservations_repository import (
    get_open_reservation_for_property,
    get_reservation,
    list_reservation_deposits,
    list_reservation_documents,
    list_reservation_documents_for_operation,
    list_reservation_events,
)
from modules.operation_prefill import preliminary_commission_rate
from modules.organization_time import UTC, organization_timezone
from modules.reservations import (
    add_reinforcement,
    confirm_reservation,
    document_path,
    grouped_deposit_totals,
    link_operation,
    save_document,
)
from modules.visit_close import VisitCloseError, apply_visit_close
from web_app import app


LEGACY_DDL = """
CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE properties (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER,
    commercial_status TEXT,
    listing_price REAL,
    listing_currency TEXT
);
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
CREATE TABLE reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    property_id INTEGER NOT NULL,
    contact_id INTEGER,
    agent_id INTEGER,
    visit_id INTEGER,
    operation_id INTEGER,
    reservation_status TEXT NOT NULL,
    original_property_price REAL,
    original_currency TEXT,
    agreed_property_price REAL,
    agreed_currency TEXT,
    reservation_amount REAL,
    reservation_currency TEXT,
    payment_method TEXT,
    next_milestone TEXT,
    estimated_closing_date TEXT,
    notes TEXT,
    reserved_at TEXT NOT NULL,
    price_decision TEXT,
    cancelled_by_user_id INTEGER,
    cancelled_at TEXT,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO organizations (id, name) VALUES (1, 'Legacy');
INSERT INTO properties (id, organization_id, commercial_status, listing_price, listing_currency)
VALUES (7, 1, 'reserved', 185000, 'USD');
INSERT INTO properties (id, organization_id, commercial_status, listing_price, listing_currency)
VALUES (8, 1, 'available', 90000, 'USD');
INSERT INTO users (id, username) VALUES (1, 'legacy');
INSERT INTO reservations (
    organization_id, property_id, reservation_status, reservation_amount,
    reservation_currency, reserved_at, created_at, updated_at
) VALUES (
    1, 7, 'documentation', 2000, 'USD',
    '2026-01-01T00:00:00', '2026-01-01T00:00:00', '2026-01-01T00:00:00'
);
INSERT INTO reservations (
    organization_id, property_id, reservation_status,
    reserved_at, created_at, updated_at
) VALUES (
    1, 8, 'reserved',
    '2026-02-01T00:00:00', '2026-02-01T00:00:00', '2026-02-01T00:00:00'
);
"""


class ReservationWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="reservation-wizard-test")
        create_tables()
        migrate_reservations()
        cls.org = add_organization("Wizard Org")
        cls.agent = add_agent("Agente Wizard", "Alto", cls.org)
        cls.other_agent = add_agent("Otro Wizard", "Alto", cls.org)
        cls.admin = add_user(
            "wizard_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
        )
        cls.agent_user = add_user(
            "wizard_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.other_user = add_user(
            "wizard_other",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.tz = organization_timezone(cls.org)

    def _now(self):
        return datetime(2026, 9, 24, 15, 0, tzinfo=self.tz).astimezone(UTC)

    def _reserve(self, address, *, listing_price=185000, listing_currency="USD", **extra):
        property_id = add_property(
            address,
            "CABA",
            self.org,
            agent_id=self.agent,
            listing_price=listing_price,
            listing_currency=listing_currency,
        )
        contact = create_agent_contact(
            self.org,
            self.agent,
            {"name": address, "phone": "1155550000", "status": "lead", "source": "manual"},
        )
        visit = create_task(
            self.org,
            self.agent,
            {
                "title": address,
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "11:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": property_id,
            },
            created_by_user_id=self.agent_user,
        )
        visit = complete_task(
            self.org,
            visit["id"],
            agent_id=self.agent,
            actor_user_id=self.agent_user,
        )
        payload = {
            "result": "reserved",
            "reservation_amount": "2.000",
            "reservation_currency": "USD",
            "estimated_closing_date": "2026-10-15",
            "price_change": "keep",
            "note": "seña",
        }
        payload.update(extra)
        apply_visit_close(
            visit,
            payload,
            organization_id=self.org,
            agent_id=self.agent,
            actor_user_id=self.agent_user,
            now=self._now(),
            tz=self.tz,
            language="es",
        )
        return property_id, get_open_reservation_for_property(self.org, property_id)

    def test_legacy_migration_is_idempotent_and_keeps_rows(self):
        previous = os.environ["DATABASE_PATH"]
        path = Path(_TEST_TMP.name) / "legacy_reservations.db"
        os.environ["DATABASE_PATH"] = str(path)
        try:
            raw = sqlite3.connect(path)
            raw.executescript(LEGACY_DDL)
            raw.commit()
            raw.close()
            migrate_reservations()
            migrate_reservations()
            connection = get_connection()
            try:
                cursor = connection.cursor()
                cursor.execute("PRAGMA table_info(reservations)")
                columns = {row[1] for row in cursor.fetchall()}
                self.assertIn("confirmed_by_user_id", columns)
                self.assertIn("is_shared_transaction", columns)
                self.assertIn("deposit_type", columns)
                cursor.execute(
                    """
                    SELECT reservation_status, reservation_amount, deposit_type
                    FROM reservations
                    ORDER BY id
                    """
                )
                rows = cursor.fetchall()
                self.assertEqual(
                    rows,
                    [("documentation", 2000, "reservation"), ("reserved", None, None)],
                )
                cursor.execute("SELECT COUNT(*) FROM reservation_deposits")
                self.assertEqual(cursor.fetchone()[0], 0)
                cursor.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE name = 'idx_reservations_one_open_property'
                    """
                )
                index_sql = cursor.fetchone()[0]
                self.assertIn("confirmed", index_sql)
                self.assertIn("documentation", index_sql)
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM reservations
                    WHERE reservation_status IN ('documentation', 'reserved')
                    """
                )
                self.assertEqual(cursor.fetchone()[0], 2)
            finally:
                connection.close()
        finally:
            os.environ["DATABASE_PATH"] = previous

    def test_fresh_database_survives_a_second_migration(self):
        migrate_reservations()
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'idx_reservations_one_open_property'"
            )
            self.assertIn("confirmed", cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM reservation_deposits")
            self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            connection.close()

    def test_keep_price_shared_name_and_second_open_reservation(self):
        property_id, reservation = self._reserve(
            "Calle Mantener",
            is_shared_transaction="1",
            shared_brokerage_name="Nordelta Homes",
            seller_commission_amount="5.340",
            buyer_commission_amount="3.000",
            agreed_property_price="999.000",
        )
        row = get_property_record(property_id, self.org)
        self.assertEqual(int(row["listing_price"]), 185000)
        self.assertEqual(row["commercial_status"], "reserved")
        self.assertEqual(int(reservation["agreed_property_price"]), 185000)
        self.assertEqual(reservation["is_shared_transaction"], 1)
        self.assertEqual(reservation["shared_brokerage_name"], "Nordelta Homes")
        self.assertEqual(int(reservation["seller_commission_amount"]), 5340)
        self.assertEqual(len(list_reservation_deposits(self.org, reservation["id"])), 0)
        contact = create_agent_contact(
            self.org,
            self.agent,
            {"name": "Segunda", "phone": "1155550001", "status": "lead", "source": "manual"},
        )
        visit = create_task(
            self.org,
            self.agent,
            {
                "title": "Segunda visita",
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "12:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": property_id,
            },
            created_by_user_id=self.agent_user,
        )
        visit = complete_task(
            self.org,
            visit["id"],
            agent_id=self.agent,
            actor_user_id=self.agent_user,
        )
        with self.assertRaises(VisitCloseError) as error:
            apply_visit_close(
                visit,
                {
                    "result": "reserved",
                    "reservation_amount": "1.000",
                    "estimated_closing_date": "2026-11-01",
                },
                organization_id=self.org,
                agent_id=self.agent,
                actor_user_id=self.agent_user,
                now=self._now(),
                tz=self.tz,
            )
        self.assertEqual(error.exception.message_key, "visit_close_err_reservation_open")

    def test_shared_brokerage_is_required_and_cleared_when_own(self):
        with self.assertRaises(VisitCloseError) as missing:
            self._reserve("Calle Compartida", is_shared_transaction="1")
        self.assertEqual(missing.exception.message_key, "visit_close_err_shared_brokerage")
        _property_id, own = self._reserve(
            "Calle Propia",
            is_shared_transaction="0",
            shared_brokerage_name="No debería quedar",
        )
        self.assertEqual(own["is_shared_transaction"], 0)
        self.assertIsNone(own["shared_brokerage_name"])

    def test_changed_price_updates_only_on_confirm(self):
        property_id = add_property(
            "Calle Cambio",
            "CABA",
            self.org,
            agent_id=self.agent,
            listing_price=185000,
            listing_currency="USD",
        )
        contact = create_agent_contact(
            self.org,
            self.agent,
            {"name": "Cambio", "phone": "1155550002", "status": "lead", "source": "manual"},
        )
        visit = create_task(
            self.org,
            self.agent,
            {
                "title": "Visita cambio",
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "13:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": property_id,
            },
            created_by_user_id=self.agent_user,
        )
        client = app.test_client()
        client.post(
            "/login",
            data={"username": "wizard_agent", "password": "Password1"},
            follow_redirects=True,
        )
        page = client.get(f"/agenda/{visit['id']}/visit-close")
        body = page.get_data(as_text=True)
        self.assertIn('data-wizard-step="2"', body)
        self.assertIn('data-wizard-step="3"', body)
        self.assertIn("Confirmar reserva", body)
        self.assertIn('name="shared_brokerage_name"', body)
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "available")
        self.assertIsNone(get_open_reservation_for_property(self.org, property_id))
        visit = complete_task(
            self.org,
            visit["id"],
            agent_id=self.agent,
            actor_user_id=self.agent_user,
        )
        apply_visit_close(
            visit,
            {
                "result": "reserved",
                "price_change": "change",
                "agreed_property_price": "170.000",
                "agreed_property_currency": "USD",
                "reservation_amount": "2.000",
                "estimated_closing_date": "2026-10-20",
            },
            organization_id=self.org,
            agent_id=self.agent,
            actor_user_id=self.agent_user,
            now=self._now(),
            tz=self.tz,
            language="es",
        )
        row = get_property_record(property_id, self.org)
        self.assertEqual(int(row["listing_price"]), 170000)
        self.assertEqual(row["commercial_status"], "reserved")

    def test_staff_confirm_does_not_touch_the_price_and_agents_cannot(self):
        property_id, reservation = self._reserve("Calle Conformar")
        before = get_property_record(property_id, self.org)
        client = app.test_client()
        client.post(
            "/login",
            data={"username": "wizard_agent", "password": "Password1"},
            follow_redirects=False,
        )
        denied = client.post(f"/reservations/{reservation['id']}/confirm")
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "reserved")
        confirmed = confirm_reservation(
            self.org,
            reservation["id"],
            actor_user_id=self.admin,
        )
        self.assertEqual(confirmed["reservation_status"], "confirmed")
        self.assertEqual(confirmed["confirmed_by_user_id"], self.admin)
        self.assertTrue(confirmed["confirmed_at"])
        after = get_property_record(property_id, self.org)
        self.assertEqual(after["listing_price"], before["listing_price"])
        self.assertEqual(after["commercial_status"], "reserved")
        events = list_reservation_events(self.org, reservation["id"])
        self.assertIn("confirmed", [event["event_type"] for event in events])
        self.assertIsNotNone(get_open_reservation_for_property(self.org, property_id))

    def test_reinforcement_is_separate_from_the_initial_amount(self):
        _property_id, reservation = self._reserve("Calle Refuerzo")
        add_reinforcement(
            self.org,
            reservation["id"],
            amount="500",
            currency="ARS",
            note="refuerzo",
            deposited_at="2026-10-01",
            actor_user_id=self.admin,
        )
        deposits = list_reservation_deposits(self.org, reservation["id"])
        self.assertEqual(len(deposits), 1)
        self.assertEqual(deposits[0]["deposit_type"], "reinforcement")
        stored = get_reservation(reservation["id"], self.org)
        self.assertEqual(int(stored["reservation_amount"]), 2000)
        self.assertEqual(stored["deposit_type"], "reservation")
        totals = {
            item["currency"]: item["amount"]
            for item in grouped_deposit_totals(stored, deposits)
        }
        self.assertEqual(totals["USD"], Decimal("2000"))
        self.assertEqual(totals["ARS"], Decimal("500"))

    def test_one_document_is_visible_from_reservation_and_operation(self):
        _property_id, reservation = self._reserve("Calle Documento")
        upload = BytesIO(b"same-file")
        upload.filename = "uif.pdf"
        upload.mimetype = "application/pdf"
        document_id = save_document(
            self.org,
            reservation["id"],
            upload,
            "uif",
            actor_user_id=self.agent_user,
        )
        link_operation(
            self.org,
            reservation["id"],
            404,
            actor_user_id=self.admin,
        )
        by_reservation = list_reservation_documents(self.org, reservation["id"])
        by_operation = list_reservation_documents_for_operation(self.org, 404)
        self.assertEqual(len(by_reservation), 1)
        self.assertEqual(len(by_operation), 1)
        self.assertEqual(by_reservation[0]["stored_name"], by_operation[0]["stored_name"])
        self.assertEqual(by_operation[0]["operation_id"], 404)
        _document, path = document_path(self.org, document_id)
        self.assertTrue(path.is_file())
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM operation_documents WHERE stored_name = ?",
                (by_reservation[0]["stored_name"],),
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            connection.close()
        client = app.test_client()
        client.post(
            "/login",
            data={"username": "wizard_other", "password": "Password1"},
            follow_redirects=False,
        )
        denied = client.post(
            f"/reservations/{reservation['id']}/documents",
            data={"doc_type": "other"},
            content_type="multipart/form-data",
        )
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(len(list_reservation_documents(self.org, reservation["id"])), 1)

    def _official_cents(self, price, rate_text, expected):
        rebuilt = float(price) * float(rate_text) / 100.0
        cents = Decimal(rebuilt).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.assertEqual(cents, expected)
        self.assertLessEqual(abs(Decimal(rebuilt) - expected), Decimal("0.01"))
        return cents

    def test_prefill_rate_reproduces_preliminary_commission_cents(self):
        cases = (
            (Decimal("5340.00"), Decimal("185000"), "USD"),
            (Decimal("3000.00"), Decimal("185000"), "USD"),
            (Decimal("5340.50"), Decimal("185000"), "ARS"),
            (Decimal("3000.25"), Decimal("185000"), "ARS"),
            (Decimal("12.34"), Decimal("99999.99"), "USD"),
        )
        for amount, price, _currency in cases:
            rate = preliminary_commission_rate(amount, price)
            self.assertIsNotNone(rate)
            self._official_cents(price, rate, amount)

        _property_id, usd = self._reserve(
            "Calle Comision USD",
            seller_commission_amount="5.340",
            seller_commission_currency="USD",
            buyer_commission_amount="3.000",
            buyer_commission_currency="USD",
        )
        _ars_property, ars = self._reserve(
            "Calle Comision ARS",
            listing_currency="ARS",
            seller_commission_amount="5.340,50",
            seller_commission_currency="ARS",
            buyer_commission_amount="3.000,25",
            buyer_commission_currency="ARS",
        )
        client = app.test_client()
        client.post(
            "/login",
            data={"username": "wizard_admin", "password": "Password1"},
            follow_redirects=False,
        )
        plain = client.get("/operations/new")
        self.assertRegex(
            plain.get_data(as_text=True),
            r'step="0\.01"[^>]*id="seller_commission_rate"',
        )
        expectations = (
            (usd, "USD", Decimal("185000"), Decimal("5340.00"), Decimal("3000.00")),
            (ars, "ARS", Decimal("185000"), Decimal("5340.50"), Decimal("3000.25")),
        )
        for reservation, currency, price, seller_amount, buyer_amount in expectations:
            form = client.get(f"/operations/new?reservation_id={reservation['id']}")
            html = form.get_data(as_text=True)
            self.assertEqual(form.status_code, 200)
            self.assertIn(f'value="{currency}" selected', html)
            self.assertRegex(html, r'step="any"[^>]*id="seller_commission_rate"')
            self.assertRegex(html, r'step="any"[^>]*id="buyer_commission_rate"')
            seller_rate = re.search(
                r'id="seller_commission_rate"[^>]*value="([^"]*)"',
                html,
            )
            buyer_rate = re.search(
                r'id="buyer_commission_rate"[^>]*value="([^"]*)"',
                html,
            )
            self.assertIsNotNone(seller_rate)
            self.assertIsNotNone(buyer_rate)
            self._official_cents(price, seller_rate.group(1), seller_amount)
            self._official_cents(price, buyer_rate.group(1), buyer_amount)


if __name__ == "__main__":
    unittest.main()
