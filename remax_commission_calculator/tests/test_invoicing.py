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
    get_operation_party,
    set_operation_party_billing_enabled,
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
    list_pending_operations,
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

    def _collect_commission(self, operation_id, side):
        from modules.cash_treasury import (
            confirm_movement,
            validate_movement_payload,
        )

        party = get_operation_party(self.org_a, operation_id, side)
        amount = float((party or {}).get("commission_amount") or 0)
        if amount <= 0.009:
            return
        operation = get_operation_record(operation_id, self.org_a)
        errors, values = validate_movement_payload(
            {
                "movement_type": "income",
                "currency": (operation or {}).get("currency") or "USD",
                "amount": str(amount),
                "category": "commission_collection",
                "description": "Cobro de comisión",
                "payment_method": "cash",
                "movement_date": "2026-10-15",
                "notes": "",
                "treasury_account_id": "",
                "linked_operation_id": str(operation_id),
                "linked_party_role": side,
            }
        )
        self.assertEqual(errors, [])
        return confirm_movement(
            self.org_a,
            values,
            user_id=self.admin_a,
        )

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
            self._collect_commission(op_id, side)
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
        billing_keys = (
            "nav_billing",
            "billing_missing_agent_email",
            "billing_agent_profile_incomplete_title",
            "billing_invoice_buyer",
            "billing_side_seller",
            "billing_settings_title",
            "billing_ai_found_operation",
            "billing_cta_open_operation",
            "billing_cta_complete_profile",
            "billing_cta_manage_issuers",
            "billing_cta_view_invoice",
            "billing_cta_complete_client",
            "notification_operation_side_ready_to_invoice",
            "invoice_err_amount_invalid",
            "invoice_err_currency_invalid",
            "invoice_err_exchange_invalid",
            "invoice_err_operation_not_found",
            "invoice_err_amount_not_set",
            "invoice_err_already_invoiced",
            "invoice_err_billing_profile_incomplete",
            "invoice_err_payment_condition_invalid",
            "invoice_err_forbidden",
            "invoice_err_not_found",
            "invoice_err_invalid_transition",
            "invoice_err_cannot_cancel_issued",
            "invoice_err_fiscal_issue_unavailable",
            "invoice_err_arca_not_configured",
            "invoice_err_side_billing_disabled",
            "invoice_err_party_not_participating",
            "invoice_err_client_incomplete",
            "invoice_err_party_client_incomplete",
            "invoice_err_side_invalid",
            "invoice_err_agents_cannot_invoice",
            "invoice_err_office_cannot_invoice",
            "invoice_err_issuer_profile_required",
            "invoice_err_issuer_profile_not_found",
            "invoice_err_issuer_mode_invalid",
            "invoice_err_retry_same_invoice",
            "invoice_err_use_create_draft_for_side",
        )
        for key in billing_keys:
            self.assertIn(key, TRANSLATIONS["es"], key)
            self.assertIn(key, TRANSLATIONS["en"], key)

    def test_side_billing_disabled_error(self):
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
            "invoice_err_side_billing_disabled",
        )

    def test_amount_not_set_error(self):
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
        self._collect_commission(op_id, SIDE_BUYER)
        set_operation_party_billing_enabled(
            self.org_a,
            op_id,
            SIDE_BUYER,
            enabled=True,
            by_user_id=self.admin_a,
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
            "invoice_err_amount_not_set",
        )

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
        self.assertIn(response.status_code, (302, 404))

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
        self._collect_commission(op_id, SIDE_BUYER)
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

    def test_billing_prepare_route(self):
        op_id = self._make_ready_operation()
        self._login("bill_agent_a")
        response = self.client.get(
            f"/billing/operations/{op_id}/buyer/prepare"
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Revisar factura", body)
        self.assertIn("Hubac 4702", body)
        self.assertIn("Crear borrador", body)

    def test_billing_ai_prepare_redirect(self):
        op_id = self._make_ready_operation()
        self._login("bill_agent_a")
        response = self.client.post(
            "/billing/ai/prepare",
            data={
                "prompt": (
                    f"Facturá COM-{op_id:06d} al comprador"
                ),
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "/billing",
            response.headers.get("Location", ""),
        )

    def test_billing_cuit_persisted(self):
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
        set_operation_party_client_fields(
            self.org_a,
            op_id,
            SIDE_BUYER,
            client_legal_name="Juan Perez",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Cliente 123",
        )
        set_party_invoice_amount(
            self.org_a,
            op_id,
            SIDE_BUYER,
            "7850000",
            "ARS",
            "1307.50",
            self.admin_a,
            notify=False,
        )
        self._login("bill_agent_a")
        response = self.client.post(
            f"/billing/operations/{op_id}/buyer/client-field",
            data={
                "field_name": "client_tax_id",
                "field_value": "20-99887766-5",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        party = get_operation_party(
            self.org_a,
            op_id,
            SIDE_BUYER,
        )
        self.assertEqual(
            party.get("client_tax_id"),
            "20-99887766-5",
        )

    def test_billing_pending_list_one_card_per_side(self):
        op_id = self._make_ready_operation(
            sides=("buyer", "seller"),
        )
        self._login("bill_agent_a")
        response = self.client.get("/billing?view=history&tab=pending")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Facturar al comprador", body)
        self.assertIn("Facturar al vendedor", body)
        self.assertNotIn(
            f'>{self.t("billing_facturar")}<',
            body,
        )
        self.assertEqual(
            body.count(
                f"/billing/operations/{op_id}/buyer/prepare"
            ),
            1,
        )
        self.assertEqual(
            body.count(
                f"/billing/operations/{op_id}/seller/prepare"
            ),
            1,
        )

    def test_billing_pending_buyer_flow(self):
        op_id = self._make_ready_operation(sides=("buyer",))
        self._login("bill_agent_a")
        prepare_url = (
            f"/billing/operations/{op_id}/buyer/prepare"
        )
        response = self.client.get(
            prepare_url,
            follow_redirects=False,
        )
        self.assertNotIn(
            "/operations/",
            response.headers.get("Location", ""),
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Revisar factura", body)
        self.assertIn("Crear borrador", body)
        self.assertIn("7.850.000,00", body)

        response = self.client.post(
            f"/billing/operations/{op_id}/buyer/new",
            data={"issuer_mode": "agent"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/billing/", response.headers["Location"])

    def test_billing_pending_seller_flow(self):
        op_id = self._make_ready_operation(sides=("seller",))
        self._login("bill_agent_a")
        prepare_url = (
            f"/billing/operations/{op_id}/seller/prepare"
        )
        response = self.client.get(
            prepare_url,
            follow_redirects=False,
        )
        self.assertNotIn(
            "/operations/",
            response.headers.get("Location", ""),
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Revisar factura", body)
        self.assertIn("6.500.000,00", body)

    def test_billing_missing_cuit_shows_form_not_operations(
        self,
    ):
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
        set_operation_party_client_fields(
            self.org_a,
            op_id,
            SIDE_BUYER,
            client_legal_name="Juan Perez",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Cliente 123",
        )
        set_party_invoice_amount(
            self.org_a,
            op_id,
            SIDE_BUYER,
            "7850000",
            "ARS",
            "1307.50",
            self.admin_a,
            notify=False,
        )
        self._login("bill_agent_a")
        response = self.client.get(
            f"/billing/operations/{op_id}/buyer/prepare",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Falta un dato", body)
        self.assertIn("CUIT de Juan Perez", body)
        self.assertIn("/billing/operations/", body)

    def _input_value(self, body, field_id):
        import re

        match = re.search(
            rf'<input id="{field_id}"[^>]*value="([^"]*)"',
            body,
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def _two_side_operation(self):
        from modules.database.operation_parties_repository import (
            create_parties_for_new_operation,
        )

        operation_id = add_operation(
            "20/10/2026",
            self.agent_a,
            self.property_a,
            "no",
            0,
            150000,
            3,
            7500,
            7500,
            0,
            0,
            0,
            0,
            0,
            self.org_a,
            currency="USD",
        )
        create_parties_for_new_operation(
            self.org_a,
            operation_id,
            seller_active=True,
            buyer_active=True,
            seller_commission_percent=3,
            buyer_commission_percent=2,
            seller_commission_amount=4500,
            buyer_commission_amount=3000,
        )
        for side, name in (
            (SIDE_BUYER, "Comprador Fiscal"),
            (SIDE_SELLER, "Vendedor Fiscal"),
        ):
            set_operation_party_client_fields(
                self.org_a,
                operation_id,
                side,
                client_legal_name=name,
                client_tax_id="20-99887766-5",
                client_tax_condition="consumidor_final",
                client_fiscal_address="Calle 1",
                client_email="cliente@example.com",
            )
        return operation_id

    def test_prepare_proposes_commission_without_treasury_fiscal_amount(self):
        from modules.cash_treasury import reverse_movement
        from modules.invoicing import get_invoice, side_billing_position

        operation_id = self._two_side_operation()
        self._login("bill_admin_a")
        warning = "Tesorería todavía no definió el importe fiscal"
        pending = "El cobro de la comisión todavía está pendiente."

        for side in (SIDE_BUYER, SIDE_SELLER):
            party = get_operation_party(self.org_a, operation_id, side)
            position = side_billing_position(
                self.org_a,
                operation_id,
                party,
                operation_status="approved",
            )
            self.assertFalse(position["ready"])
            self.assertIsNone(position["proposal"])
            self.assertAlmostEqual(position["invoiced"], 0)
            page = self.client.get(
                f"/billing/operations/{operation_id}/{side}/prepare"
            )
            body = page.get_data(as_text=True)
            self.assertIn(pending, body)
            self.assertNotIn(warning, body)
            self.assertEqual(self._input_value(body, "invoice_amount"), "")

        self._collect_commission(operation_id, SIDE_BUYER)
        seller_collection = self._collect_commission(operation_id, SIDE_SELLER)
        for side, expected in ((SIDE_BUYER, "3000.00"), (SIDE_SELLER, "4500.00")):
            party = get_operation_party(self.org_a, operation_id, side)
            position = side_billing_position(
                self.org_a,
                operation_id,
                party,
                operation_status="approved",
            )
            self.assertTrue(position["ready"])
            self.assertAlmostEqual(position["proposal"], float(expected))
            self.assertAlmostEqual(position["pending"], 0)
            page = self.client.get(
                f"/billing/operations/{operation_id}/{side}/prepare"
            )
            body = page.get_data(as_text=True)
            self.assertNotIn(warning, body)
            self.assertNotIn(pending, body)
            self.assertEqual(self._input_value(body, "invoice_amount"), expected)
            self.assertIn('value="USD" selected', body)
            self.assertIsNone(party["invoice_amount"])

        partial_id = self._two_side_operation()
        from modules.cash_treasury import confirm_movement, validate_movement_payload

        errors, values = validate_movement_payload(
            {
                "movement_type": "income",
                "currency": "USD",
                "amount": "1000",
                "category": "commission_collection",
                "description": "Cobro parcial",
                "payment_method": "cash",
                "movement_date": "2026-10-15",
                "notes": "",
                "treasury_account_id": "",
                "linked_operation_id": str(partial_id),
                "linked_party_role": SIDE_BUYER,
            }
        )
        self.assertEqual(errors, [])
        confirm_movement(self.org_a, values, user_id=self.admin_a)
        partial_party = get_operation_party(self.org_a, partial_id, SIDE_BUYER)
        partial_position = side_billing_position(
            self.org_a,
            partial_id,
            partial_party,
            operation_status="approved",
        )
        self.assertFalse(partial_position["ready"])
        self.assertAlmostEqual(partial_position["collected"], 1000)
        self.assertAlmostEqual(partial_position["pending"], 2000)
        self.assertIsNone(partial_position["proposal"])

        foreign = side_billing_position(
            self.org_b,
            operation_id,
            get_operation_party(self.org_a, operation_id, SIDE_BUYER),
            operation_status="approved",
        )
        self.assertAlmostEqual(foreign["collected"], 0)
        self.assertAlmostEqual(foreign["expected"], 0)
        self.assertIsNone(foreign["proposal"])
        isolated = side_billing_position(
            self.org_a,
            operation_id,
            get_operation_party(self.org_a, operation_id, SIDE_BUYER),
            operation_status="approved",
        )
        self.assertAlmostEqual(isolated["collected"], 3000)
        self.assertAlmostEqual(isolated["proposal"], 3000)

        set_party_invoice_amount(
            self.org_a,
            operation_id,
            SIDE_BUYER,
            "3000.00",
            "USD",
            None,
            self.admin_a,
            notify=False,
        )
        invoice = create_draft_for_side(
            self.org_a,
            operation_id,
            SIDE_BUYER,
            self._admin_user(),
            issuer_mode="office",
            issuer_profile_id=self.issuer["id"],
        )
        self.assertAlmostEqual(float(invoice["total_amount"]), 3000)
        again = self.client.get(
            f"/billing/operations/{operation_id}/buyer/prepare",
            follow_redirects=True,
        )
        self.assertIn(
            "Esta punta ya tiene un borrador o factura activa.",
            again.get_data(as_text=True),
        )
        reverse_movement(
            self.org_a,
            seller_collection["id"],
            user_id=self.admin_a,
            reason="Reversión de prueba",
        )
        preserved = get_invoice(self.org_a, invoice["id"])
        self.assertEqual(preserved["status"], invoice["status"])
        self.assertAlmostEqual(float(preserved["total_amount"]), 3000)
        after_reversal = side_billing_position(
            self.org_a,
            operation_id,
            get_operation_party(self.org_a, operation_id, SIDE_SELLER),
            operation_status="approved",
        )
        self.assertFalse(after_reversal["ready"])
        self.assertAlmostEqual(after_reversal["collected"], 0)
        self.assertAlmostEqual(after_reversal["pending"], 4500)
        buyer_after = side_billing_position(
            self.org_a,
            operation_id,
            get_operation_party(self.org_a, operation_id, SIDE_BUYER),
            operation_status="approved",
        )
        self.assertTrue(buyer_after["ready"])
        self.assertAlmostEqual(buyer_after["invoiced"], 3000)
        self.assertAlmostEqual(buyer_after["billable"], 0)
        self.assertIsNone(buyer_after["proposal"])

    def test_billing_legacy_new_from_operation_stays_in_billing(
        self,
    ):
        op_id = self._make_ready_operation(sides=("buyer",))
        self._login("bill_agent_a")
        response = self.client.get(
            f"/billing/operations/{op_id}/new",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers.get("Location", "")
        self.assertIn("/billing", location)
        self.assertNotIn("/operations/", location)

    @staticmethod
    def t(key):
        return TRANSLATIONS["es"][key]


if __name__ == "__main__":
    unittest.main()
