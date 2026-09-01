"""
HTTP tests for the admin dashboard (Panel) route.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.auth import ROLE_ADMIN, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
)
from modules.database.connection import get_connection
from modules.database.schema import migrate_schema
from modules.i18n import translate
from web_app import app


class DashboardPanelRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "dashboard_panel.db"
        self._env = mock.patch.dict(
            os.environ,
            {
                "DATABASE_PATH": str(self.db_path),
                "PRIVATE_UPLOAD_ROOT": str(
                    Path(self._tmp.name) / "uploads"
                ),
            },
            clear=False,
        )
        self._env.start()
        os.environ.pop("DATABASE_URL", None)

        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        create_tables()
        migrate_schema(create_backup=False)

        self.org = add_organization("Panel Org")
        self.agent = add_agent("Panel Agent", "Alto", self.org)
        pwd = hash_password("Password1")
        self.admin = add_user(
            "panel_admin",
            pwd,
            ROLE_ADMIN,
            self.org,
            email="panel_admin@example.com",
        )
        self.password = "Password1"
        self.client = app.test_client()

    def tearDown(self):
        self._env.stop()
        try:
            self._tmp.cleanup()
        except PermissionError:
            pass

    def _login_admin(self):
        return self.client.post(
            "/login",
            data={
                "username": "panel_admin",
                "password": self.password,
            },
            follow_redirects=False,
        )

    def _get_panel(self):
        return self.client.get("/", follow_redirects=True)

    def _insert_property(self, address="Panel Property"):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO properties (
                address, jurisdiction, organization_id, agent_id, status
            )
            VALUES (?, 'CABA', ?, ?, 'approved')
            """,
            (address, self.org, self.agent),
        )
        property_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return property_id

    def _insert_operation(
        self,
        *,
        property_id,
        operation_date="10/09/2026",
        status="approved",
        was_invoiced="no",
    ):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO operations (
                operation_date, agent_id, property_id, organization_id,
                was_invoiced, vat_amount, sale_price, commission_rate,
                total_commission, commission_after_abao, abao, martillero,
                agent_payment, office_payment, office_total, currency,
                original_amount, exchange_rate, status, created_by_user_id
            )
            VALUES (
                ?, ?, ?, ?, ?, 0, 100000, 3, 3000, 3000, 0, 0,
                1800, 1200, 1200, 'USD', 100000, 1, ?, ?
            )
            """,
            (
                operation_date,
                self.agent,
                property_id,
                self.org,
                was_invoiced,
                status,
                self.admin,
            ),
        )
        operation_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return operation_id

    def _insert_invoice(
        self,
        *,
        operation_id,
        invoice_seq,
        side="buyer",
        issuer_key="agent:1:inv:1",
        status="draft",
    ):
        connection = get_connection()
        cursor = connection.cursor()
        now = "2026-09-01T00:00:00"
        cursor.execute(
            """
            INSERT INTO invoices (
                organization_id, invoice_seq,
                invoice_number_internal, operation_id, agent_id,
                issuer_type, issuer_name, issuer_tax_id,
                recipient_name, recipient_tax_id, description,
                unit_price, subtotal, total_amount,
                payment_condition, issue_date, status,
                created_at, updated_at, side, issuer_key
            )
            VALUES (
                ?, ?, ?, ?, ?, 'agent', 'Issuer', '20-11111111-1',
                'Client', '20-22222222-2', 'Services', 100, 100, 121,
                'cuenta_corriente', '2026-09-01', ?, ?, ?, ?, ?
            )
            """,
            (
                self.org,
                invoice_seq,
                f"INV-{invoice_seq:06d}",
                operation_id,
                self.agent,
                status,
                now,
                now,
                side,
                issuer_key,
            ),
        )
        invoice_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return invoice_id

    def test_translate_without_placeholder_kwargs(self):
        self.assertEqual(
            translate("dashboard_welcome", language="es"),
            "Bienvenido, {name}",
        )
        self.assertIn(
            "JRH",
            translate("billing_empty_invoices_agent", language="es"),
        )

    def test_panel_empty_database_returns_200(self):
        self._login_admin()
        response = self._get_panel()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hola, panel_admin", response.data)
        self.assertNotIn(b"Error del servidor", response.data)

    def test_panel_with_operations_returns_200(self):
        property_id = self._insert_property("Ops Property")
        self._insert_operation(property_id=property_id, was_invoiced="yes")
        self._insert_operation(
            property_id=property_id,
            operation_date="12/09/2026",
            status="pending",
            was_invoiced="no",
        )

        self._login_admin()
        response = self._get_panel()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ops Property", response.data)

    def test_panel_with_legacy_and_new_invoices_returns_200(self):
        property_id = self._insert_property("Invoice Property")
        operation_id = self._insert_operation(
            property_id=property_id,
            was_invoiced="yes",
        )
        self._insert_invoice(
            operation_id=operation_id,
            invoice_seq=1,
            side="buyer",
            issuer_key="agent:1:inv:1",
            status="issued",
        )
        self._insert_invoice(
            operation_id=operation_id,
            invoice_seq=2,
            side="seller",
            issuer_key="legacy:2",
            status="draft",
        )

        self._login_admin()
        response = self._get_panel()
        self.assertEqual(response.status_code, 200)

    def test_panel_with_partial_data_returns_200(self):
        property_id = self._insert_property("Partial Property")
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO operations (
                operation_date, agent_id, property_id, organization_id,
                was_invoiced, vat_amount, sale_price, commission_rate,
                total_commission, commission_after_abao, abao, martillero,
                agent_payment, office_payment, office_total, currency,
                original_amount, exchange_rate, status, created_by_user_id
            )
            VALUES (
                '01/01/2020', ?, ?, ?, 'no', 0, 0, 0,
                0, 0, 0, 0, 0, 0, 0, 'USD',
                0, 1, 'approved', ?
            )
            """,
            (self.agent, property_id, self.org, self.admin),
        )
        connection.commit()
        connection.close()

        self._login_admin()
        response = self._get_panel()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Error del servidor", response.data)


if __name__ == "__main__":
    unittest.main()
