"""
Tests for Facturación / Billing v1.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_invoicing.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_operation,
    add_organization,
    add_property,
    add_user,
    create_tables,
    get_operation_record,
    update_organization_billing_fields,
    upsert_agent_billing_profile,
)
from modules.i18n import TRANSLATIONS
from modules.invoicing import (
    InvoicingError,
    confirm_draft,
    create_draft_from_operation,
    set_operation_invoice_amount,
)
from web_app import app


class InvoicingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-billing"
        create_tables()

        cls.org_a = add_organization("Billing Org A")
        cls.org_b = add_organization("Billing Org B")
        pwd = hash_password("Password1")

        cls.agent_a = add_agent("Agent A", "Alto", cls.org_a)
        cls.agent_b = add_agent("Agent B", "Alto", cls.org_a)
        cls.agent_other_org = add_agent(
            "Agent Other",
            "Alto",
            cls.org_b,
        )

        cls.admin_a = add_user(
            "bill_admin_a",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.user_agent_a = add_user(
            "bill_agent_a",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.user_agent_b = add_user(
            "bill_agent_b",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_b,
        )
        cls.admin_b = add_user(
            "bill_admin_b",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.password = "Password1"

        update_organization_billing_fields(
            cls.org_a,
            legal_name="Inmobiliaria Principal",
            tax_id="30-71234567-8",
            tax_condition="responsable_inscripto",
            fiscal_address="Calle Falsa 123",
            trade_name="Principal",
            billing_email="admin@example.com",
            default_payment_condition="cuenta_corriente",
        )

        for agent_id, name in (
            (cls.agent_a, "Pablo Reynals"),
            (cls.agent_b, "Otro Agente"),
        ):
            upsert_agent_billing_profile(
                cls.org_a,
                agent_id,
                legal_name=name,
                tax_id="20-30123456-7",
                tax_condition="monotributo",
                fiscal_address="Domicilio Agente 1",
                email="agent@example.com",
            )

        cls.property_a = add_property(
            "Hubac 4702",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a,
        )

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        return self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def _make_operation(self, agent_id, with_amount=False):
        op_id = add_operation(
            "27/08/2026",
            agent_id,
            self.property_a,
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
            1350,
            self.org_a,
        )
        if with_amount:
            set_operation_invoice_amount(
                self.org_a,
                op_id,
                "7850000",
                "ARS",
                "1307.50",
                self.admin_a,
                notify=False,
            )
        return op_id

    def test_i18n_nav_billing(self):
        self.assertIn("nav_billing", TRANSLATIONS["es"])
        self.assertIn("nav_billing", TRANSLATIONS["en"])

    def test_guest_billing_forbidden(self):
        response = self.client.get("/billing")
        self.assertIn(response.status_code, (302, 401, 403))

    def test_operation_without_amount_not_invoiceable(self):
        self._login("bill_agent_a")
        op_id = self._make_operation(self.agent_a)
        response = self.client.get(
            f"/billing/operations/{op_id}/new",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"importe",
            response.data.lower(),
        )

    def test_create_draft_snapshot_and_confirm(self):
        self._login("bill_agent_a")
        op_id = self._make_operation(
            self.agent_a,
            with_amount=True,
        )
        response = self.client.post(
            f"/billing/operations/{op_id}/new",
            data={
                "payment_condition": "contado",
                "issue_date": "2026-08-27",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("/billing/", location)
        self.assertIn("/review", location)

        invoice_id = int(
            location.rstrip("/").split("/")[-2]
            if location.endswith("/review")
            else location.split("/")[-1]
        )
        # Path like /billing/1/review
        parts = [p for p in location.split("/") if p]
        invoice_id = int(parts[parts.index("billing") + 1])

        from modules.database import get_operation_record as gor
        # Change operation amount after draft
        set_operation_invoice_amount(
            self.org_a,
            op_id,
            "999",
            "ARS",
            None,
            self.admin_a,
            notify=False,
        )

        from modules.invoicing import get_invoice
        invoice = get_invoice(self.org_a, invoice_id)
        self.assertAlmostEqual(invoice["total_amount"], 7850000)
        self.assertEqual(invoice["currency"], "ARS")
        self.assertEqual(
            invoice["description"],
            "Asesoramiento Integral de Gestión",
        )
        self.assertEqual(invoice["status"], "draft")

        op = get_operation_record(op_id, self.org_a)
        self.assertEqual(op["was_invoiced"], "no")

        confirmed = confirm_draft(
            self.org_a,
            invoice_id,
            {
                "id": self.user_agent_a,
                "role": ROLE_AGENT,
                "agent_id": self.agent_a,
            },
        )
        self.assertEqual(confirmed["status"], "ready_to_issue")
        op = get_operation_record(op_id, self.org_a)
        self.assertEqual(op["was_invoiced"], "no")

    def test_agent_cannot_see_other_invoice(self):
        op_id = self._make_operation(
            self.agent_a,
            with_amount=True,
        )
        invoice = create_draft_from_operation(
            self.org_a,
            op_id,
            {
                "id": self.user_agent_a,
                "role": ROLE_AGENT,
                "agent_id": self.agent_a,
            },
        )
        self._login("bill_agent_b")
        response = self.client.get(
            f"/billing/{invoice['id']}"
        )
        # App errorhandlers redirect 403/404 to dashboard.
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith("/")
            or "dashboard" in response.headers["Location"]
        )

    def test_admin_sees_org_invoices(self):
        op_id = self._make_operation(
            self.agent_a,
            with_amount=True,
        )
        create_draft_from_operation(
            self.org_a,
            op_id,
            {
                "id": self.user_agent_a,
                "role": ROLE_AGENT,
                "agent_id": self.agent_a,
            },
        )
        self._login("bill_admin_a")
        response = self.client.get("/billing")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"FAC-", response.data)

    def test_other_org_blocked(self):
        op_id = self._make_operation(
            self.agent_a,
            with_amount=True,
        )
        invoice = create_draft_from_operation(
            self.org_a,
            op_id,
            {
                "id": self.user_agent_a,
                "role": ROLE_AGENT,
                "agent_id": self.agent_a,
            },
        )
        self._login("bill_admin_b")
        response = self.client.get(
            f"/billing/{invoice['id']}"
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith("/")
            or "dashboard" in response.headers["Location"]
        )

    def test_double_invoice_blocked(self):
        op_id = self._make_operation(
            self.agent_a,
            with_amount=True,
        )
        user = {
            "id": self.user_agent_a,
            "role": ROLE_AGENT,
            "agent_id": self.agent_a,
        }
        create_draft_from_operation(self.org_a, op_id, user)
        with self.assertRaises(InvoicingError) as ctx:
            create_draft_from_operation(
                self.org_a,
                op_id,
                user,
            )
        self.assertEqual(
            ctx.exception.message_key,
            "invoice_err_already_invoiced",
        )

    def test_cancel_frees_slot(self):
        from modules.invoicing import cancel_invoice

        op_id = self._make_operation(
            self.agent_a,
            with_amount=True,
        )
        user = {
            "id": self.admin_a,
            "role": ROLE_ADMIN,
            "agent_id": None,
        }
        invoice = create_draft_from_operation(
            self.org_a,
            op_id,
            {
                "id": self.user_agent_a,
                "role": ROLE_AGENT,
                "agent_id": self.agent_a,
            },
        )
        cancel_invoice(
            self.org_a,
            invoice["id"],
            user,
            reason="test",
        )
        again = create_draft_from_operation(
            self.org_a,
            op_id,
            {
                "id": self.user_agent_a,
                "role": ROLE_AGENT,
                "agent_id": self.agent_a,
            },
        )
        self.assertNotEqual(again["id"], invoice["id"])
        self.assertEqual(again["status"], "draft")

    def test_missing_profile_blocks(self):
        agent_c = add_agent("No Profile", "Junior", self.org_a)
        user_c = add_user(
            "bill_agent_c",
            hash_password("Password1"),
            ROLE_AGENT,
            self.org_a,
            agent_id=agent_c,
        )
        prop = add_property(
            "Sin Perfil 1",
            "CABA",
            self.org_a,
            agent_id=agent_c,
        )
        op_id = add_operation(
            "27/08/2026",
            agent_c,
            prop,
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
            1350,
            self.org_a,
        )
        set_operation_invoice_amount(
            self.org_a,
            op_id,
            "1000",
            "ARS",
            None,
            self.admin_a,
            notify=False,
        )
        with self.assertRaises(InvoicingError) as ctx:
            create_draft_from_operation(
                self.org_a,
                op_id,
                {
                    "id": user_c,
                    "role": ROLE_AGENT,
                    "agent_id": agent_c,
                },
            )
        self.assertEqual(
            ctx.exception.message_key,
            "invoice_err_billing_profile_incomplete",
        )


if __name__ == "__main__":
    unittest.main()
