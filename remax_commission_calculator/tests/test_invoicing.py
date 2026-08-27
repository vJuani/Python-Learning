"""
Tests for Facturación multi-side / multi-issuer.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_invoicing_v2.db"
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
    ensure_parties_for_operation,
    get_operation_record,
    set_operation_party_client_fields,
    update_organization_billing_fields,
    upsert_agent_billing_profile,
    upsert_billing_issuer_profile,
)
from modules.i18n import TRANSLATIONS
from modules.invoicing import (
    InvoicingError,
    SIDE_BUYER,
    SIDE_SELLER,
    cancel_invoice,
    confirm_draft,
    create_draft_for_side,
    set_party_invoice_amount,
)
from web_app import app


class InvoicingV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-billing-v2"
        create_tables()

        cls.org_a = add_organization("Billing Org A")
        cls.org_b = add_organization("Billing Org B")
        pwd = hash_password("Password1")

        cls.agent_a = add_agent("Agent A", "Alto", cls.org_a)
        cls.agent_b = add_agent("Agent B", "Alto", cls.org_a)

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
            default_payment_condition="cuenta_corriente",
            default_invoice_description=(
                "Asesoramiento Integral de Gestión"
            ),
            default_buyer_commission_percent=3,
            default_seller_commission_percent=3,
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

        cls.issuer = upsert_billing_issuer_profile(
            cls.org_a,
            issuer_type="broker",
            display_name="Martillero JP",
            legal_name="Juan Perez Martillero",
            tax_id="20-11223344-5",
            tax_condition="responsable_inscripto",
            fiscal_address="Oficina 1",
            email="martillero@example.com",
            is_default=True,
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

    def _agent_user(self):
        return {
            "id": self.user_agent_a,
            "role": ROLE_AGENT,
            "agent_id": self.agent_a,
        }

    def _admin_user(self):
        return {
            "id": self.admin_a,
            "role": ROLE_ADMIN,
            "agent_id": None,
        }

    def _make_ready_operation(self, *, sides=("buyer",)):
        op_id = add_operation(
            "27/08/2026",
            self.agent_a,
            self.property_a,
            "no",
            0,
            200000,
            3,
            6000,
            5400,
            600,
            2700,
            2700,
            0,
            2700,
            self.org_a,
        )
        ensure_parties_for_operation(self.org_a, op_id)
        for side in sides:
            set_operation_party_client_fields(
                self.org_a,
                op_id,
                side,
                client_legal_name=(
                    "Juan Perez"
                    if side == "buyer"
                    else "Maria Lopez"
                ),
                client_tax_id="20-99887766-5",
                client_tax_condition="consumidor_final",
                client_fiscal_address="Cliente 123",
                client_email="c@example.com",
            )
            set_party_invoice_amount(
                self.org_a,
                op_id,
                side,
                "7850000" if side == "buyer" else "6500000",
                "ARS",
                "1307.50",
                self.admin_a,
                notify=False,
            )
        return op_id

    def test_i18n_keys(self):
        for key in (
            "nav_billing",
            "billing_missing_agent_email",
            "billing_agent_profile_incomplete_title",
            "billing_invoice_buyer",
            "billing_side_seller",
            "notification_operation_side_ready_to_invoice",
        ):
            self.assertIn(key, TRANSLATIONS["es"])
            self.assertIn(key, TRANSLATIONS["en"])

    def test_agent_invoices_client_not_org(self):
        op_id = self._make_ready_operation()
        invoice = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        self.assertEqual(invoice["side"], "buyer")
        self.assertEqual(invoice["recipient_name"], "Juan Perez")
        self.assertNotEqual(
            invoice["recipient_name"],
            "Inmobiliaria Principal",
        )
        self.assertAlmostEqual(
            invoice["total_amount"],
            7850000,
        )
        op = get_operation_record(op_id, self.org_a)
        self.assertEqual(op["was_invoiced"], "no")

        confirmed = confirm_draft(
            self.org_a,
            invoice["id"],
            self._agent_user(),
        )
        self.assertEqual(
            confirmed["status"],
            "ready_to_issue",
        )
        op = get_operation_record(op_id, self.org_a)
        self.assertEqual(op["was_invoiced"], "no")

    def test_office_issuer_same_side(self):
        op_id = self._make_ready_operation()
        agent_inv = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        office_inv = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._admin_user(),
            issuer_mode="office",
            issuer_profile_id=self.issuer["id"],
        )
        self.assertNotEqual(
            agent_inv["id"],
            office_inv["id"],
        )
        self.assertEqual(
            office_inv["issuer_name"],
            "Juan Perez Martillero",
        )

    def test_same_issuer_no_duplicate(self):
        op_id = self._make_ready_operation()
        create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        with self.assertRaises(InvoicingError) as ctx:
            create_draft_for_side(
                self.org_a,
                op_id,
                SIDE_BUYER,
                self._agent_user(),
                issuer_mode="agent",
            )
        self.assertEqual(
            ctx.exception.message_key,
            "invoice_err_already_invoiced",
        )

    def test_buyer_and_seller_independent(self):
        op_id = self._make_ready_operation(
            sides=("buyer", "seller"),
        )
        buyer = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        seller = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_SELLER,
            self._agent_user(),
            issuer_mode="agent",
        )
        self.assertEqual(buyer["side"], "buyer")
        self.assertEqual(seller["side"], "seller")
        self.assertEqual(
            seller["recipient_name"],
            "Maria Lopez",
        )

    def test_cancel_frees_slot(self):
        op_id = self._make_ready_operation()
        inv = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        cancel_invoice(
            self.org_a,
            inv["id"],
            self._admin_user(),
            reason="test",
        )
        again = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        self.assertNotEqual(again["id"], inv["id"])

    def test_agent_cannot_see_other_invoice(self):
        op_id = self._make_ready_operation()
        inv = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        self._login("bill_agent_b")
        response = self.client.get(f"/billing/{inv['id']}")
        self.assertEqual(response.status_code, 302)

    def test_other_org_blocked(self):
        op_id = self._make_ready_operation()
        inv = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        self._login("bill_admin_b")
        response = self.client.get(f"/billing/{inv['id']}")
        self.assertEqual(response.status_code, 302)

    def test_missing_client_blocks(self):
        op_id = add_operation(
            "27/08/2026",
            self.agent_a,
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
        ensure_parties_for_operation(self.org_a, op_id)
        set_party_invoice_amount(
            self.org_a,
            op_id,
            SIDE_BUYER,
            "1000",
            "ARS",
            None,
            self.admin_a,
            notify=False,
        )
        with self.assertRaises(InvoicingError) as ctx:
            create_draft_for_side(
                self.org_a,
                op_id,
                SIDE_BUYER,
                self._agent_user(),
                issuer_mode="agent",
            )
        self.assertIn(
            ctx.exception.message_key,
            (
                "invoice_err_billing_profile_incomplete",
                "invoice_err_client_incomplete",
                "invoice_err_party_client_incomplete",
            ),
        )

    def test_guest_billing_forbidden(self):
        response = self.client.get("/billing")
        self.assertIn(response.status_code, (302, 401, 403))

    def test_amount_snapshot(self):
        op_id = self._make_ready_operation()
        inv = create_draft_for_side(
            self.org_a,
            op_id,
            SIDE_BUYER,
            self._agent_user(),
            issuer_mode="agent",
        )
        set_party_invoice_amount(
            self.org_a,
            op_id,
            SIDE_BUYER,
            "111",
            "ARS",
            None,
            self.admin_a,
            notify=False,
        )
        from modules.invoicing import get_invoice

        again = get_invoice(self.org_a, inv["id"])
        self.assertAlmostEqual(again["total_amount"], 7850000)


if __name__ == "__main__":
    unittest.main()
