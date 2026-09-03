"""Phase 3C: invoices document agent-account charges without duplicating debt."""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_invoice_agent_account.db"
)

from modules.agent_account import cancel_movement, create_movement
from modules.agent_account_charges import VAT_MODE_ADD, VAT_MODE_NONE
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
    get_agent_balances,
    set_operation_party_client_fields,
    upsert_agent_billing_profile,
    upsert_billing_issuer_profile,
)
from modules.database.agent_account_payment_repository import (
    get_charge_payment_summary,
    list_charge_payment_allocations,
)
from modules.database.agent_account_repository import (
    get_agent_account_movement,
    list_agent_account_movements,
)
from modules.database.connection import get_connection
from modules.database.invoices_repository import (
    get_active_invoice_for_charge,
    list_invoices_for_charge,
)
from modules.invoicing import (
    InvoicingError,
    ISSUER_MODE_AGENT,
    ISSUER_MODE_OFFICE,
    SIDE_BUYER,
    SIDE_SELLER,
    cancel_invoice,
    create_draft_for_charge,
    create_draft_for_side,
    get_charge_invoice_context,
    list_billable_agent_charges,
    set_party_invoice_amount,
)
from web_app import app


class InvoiceAgentAccountIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "phase-3c-secret"
        create_tables()

        cls.org = add_organization("Phase 3C Org")
        cls.other_org = add_organization("Phase 3C Other")
        cls.agent = add_agent("José Luis Barreiro", "Alto", cls.org)
        cls.other_agent = add_agent("Other Agent", "Alto", cls.other_org)
        password = hash_password("Password1")
        cls.admin = add_user(
            "phase3c_admin", password, ROLE_ADMIN, cls.org
        )
        cls.agent_user = add_user(
            "phase3c_agent",
            password,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.other_admin = add_user(
            "phase3c_other",
            password,
            ROLE_ADMIN,
            cls.other_org,
        )
        cls.password = "Password1"
        upsert_agent_billing_profile(
            cls.org,
            cls.agent,
            legal_name="José Luis Barreiro",
            tax_id="20-30123456-7",
            tax_condition="monotributo",
            fiscal_address="Calle Agente 123",
            email="agent@example.com",
        )
        cls.issuer = upsert_billing_issuer_profile(
            cls.org,
            issuer_type="broker",
            display_name="JRH Inmobiliaria",
            legal_name="JRH Inmobiliaria SA",
            tax_id="30-71234567-8",
            tax_condition="responsable_inscripto",
            fiscal_address="Oficina 123",
            email="billing@example.com",
            is_default=True,
        )
        cls.property_id = add_property(
            "Luis García 650",
            "CABA",
            cls.org,
            agent_id=cls.agent,
        )

    def setUp(self):
        self.client = app.test_client()

    def _admin_user(self):
        return {"id": self.admin, "role": ROLE_ADMIN, "agent_id": None}

    def _agent_user(self):
        return {
            "id": self.agent_user,
            "role": ROLE_AGENT,
            "agent_id": self.agent,
        }

    def _login(self, username="phase3c_admin"):
        return self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def _charge(self, **overrides):
        payload = {
            "charge_category": "fee",
            "currency": "USD",
            "amount": "65",
            "vat_mode": VAT_MODE_ADD,
            "vat_rate": "21",
            "billing_period": "Septiembre 2026",
            "movement_date": "2026-09-01",
        }
        payload.update(overrides)
        return create_movement(
            self.org,
            self.agent,
            payload,
            created_by_user_id=self.admin,
        )

    def _invoice(self, charge):
        return create_draft_for_charge(
            self.org,
            charge["id"],
            self._admin_user(),
            issuer_mode=ISSUER_MODE_OFFICE,
            issuer_profile_id=self.issuer["id"],
        )

    def _payment(self, charge, amount):
        return create_movement(
            self.org,
            self.agent,
            {
                "movement_type": "payment",
                "currency": charge["currency"],
                "amount": str(amount),
                "movement_date": "2026-09-02",
                "payment_method": "transfer",
                "applied_to_movement_id": str(charge["id"]),
            },
            created_by_user_id=self.admin,
            idempotency_key=f"phase3c-{charge['id']}-{amount}",
        )

    def _operation(self, side=SIDE_BUYER):
        operation_id = add_operation(
            "03/09/2026",
            self.agent,
            self.property_id,
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
            self.org,
        )
        ensure_parties_for_operation(self.org, operation_id)
        set_operation_party_client_fields(
            self.org,
            operation_id,
            side,
            client_legal_name="Cliente",
            client_tax_id="20-99887766-5",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Cliente 123",
            client_email="client@example.com",
        )
        set_party_invoice_amount(
            self.org,
            operation_id,
            side,
            "100",
            "USD",
            "1",
            self.admin,
            notify=False,
        )
        return operation_id

    def test_01_billable_charge_creates_linked_invoice(self):
        charge = self._charge()
        invoice = self._invoice(charge)
        self.assertEqual(invoice["agent_account_movement_id"], charge["id"])
        self.assertEqual(invoice["origin_type"], "agent_account_charge")

    def test_02_invoice_does_not_create_second_charge(self):
        charge = self._charge()
        before = len(list_agent_account_movements(self.org, self.agent))
        self._invoice(charge)
        after = len(list_agent_account_movements(self.org, self.agent))
        self.assertEqual(before, after)

    def test_03_invoice_total_uses_charge_gross(self):
        charge = self._charge()
        self.assertEqual(self._invoice(charge)["total_amount"], 78.65)

    def test_04_invoice_uses_vat_snapshot(self):
        charge = self._charge()
        invoice = self._invoice(charge)
        self.assertEqual(invoice["subtotal"], 65.0)
        self.assertEqual(invoice["vat_rate"], charge["vat_rate"])
        self.assertEqual(invoice["vat_amount"], 13.65)

    def test_05_usd_remains_usd(self):
        self.assertEqual(self._invoice(self._charge())["currency"], "USD")

    def test_06_ars_remains_ars(self):
        charge = self._charge(
            currency="ARS", amount="1000", vat_mode=VAT_MODE_NONE
        )
        self.assertEqual(self._invoice(charge)["currency"], "ARS")

    def test_07_non_billable_movement_is_rejected(self):
        payment = create_movement(
            self.org,
            self.agent,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "1",
                "movement_date": "2026-09-02",
                "payment_method": "cash",
            },
            created_by_user_id=self.admin,
        )
        with self.assertRaises(InvoicingError):
            self._invoice(payment)

    def test_08_other_org_charge_is_rejected(self):
        charge = self._charge()
        with self.assertRaises(InvoicingError):
            create_draft_for_charge(
                self.other_org,
                charge["id"],
                {"id": self.other_admin, "role": ROLE_ADMIN},
                issuer_mode=ISSUER_MODE_OFFICE,
            )

    def test_09_active_invoice_prevents_duplicate(self):
        charge = self._charge()
        first = self._invoice(charge)
        with self.assertRaises(InvoicingError):
            self._invoice(charge)
        self.assertEqual(
            get_active_invoice_for_charge(
                self.org, charge["id"], "agent_charge"
            )["id"],
            first["id"],
        )

    def test_10_cancelled_invoice_allows_replacement(self):
        charge = self._charge()
        first = self._invoice(charge)
        cancel_invoice(
            self.org, first["id"], self._admin_user(), reason="Replace"
        )
        second = self._invoice(charge)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(list_invoices_for_charge(self.org, charge["id"])), 2)

    def test_11_partial_payment_is_derived(self):
        charge = self._charge(
            amount="100", vat_mode=VAT_MODE_NONE
        )
        self._invoice(charge)
        self._payment(charge, "60")
        state = get_charge_invoice_context(self.org, charge["id"])
        self.assertEqual(state["payment"]["payment_status"], "partially_paid")
        self.assertEqual(state["payment"]["remaining_amount"], 40.0)

    def test_12_full_payment_is_derived(self):
        charge = self._charge(
            amount="100", vat_mode=VAT_MODE_NONE
        )
        self._invoice(charge)
        self._payment(charge, "100")
        self.assertEqual(
            get_charge_payment_summary(
                self.org, charge["id"]
            )["payment_status"],
            "paid",
        )

    def test_13_cancel_invoice_does_not_change_debt(self):
        charge = self._charge()
        before = get_agent_balances(self.org, self.agent)["USD"]
        invoice = self._invoice(charge)
        cancel_invoice(
            self.org, invoice["id"], self._admin_user(), reason="Wrong draft"
        )
        self.assertEqual(get_agent_balances(self.org, self.agent)["USD"], before)

    def test_14_cancel_charge_without_invoice(self):
        charge = self._charge()
        cancel_movement(
            self.org,
            charge["id"],
            created_by_user_id=self.admin,
            reason="Incorrect charge",
        )
        self.assertEqual(
            get_agent_account_movement(
                charge["id"], self.org
            )["status"],
            "reversed",
        )

    def test_15_cancel_charge_cancels_internal_draft(self):
        charge = self._charge()
        invoice = self._invoice(charge)
        cancel_movement(
            self.org,
            charge["id"],
            created_by_user_id=self.admin,
            reason="Incorrect charge",
        )
        self.assertEqual(
            list_invoices_for_charge(
                self.org, charge["id"]
            )[0]["status"],
            "cancelled",
        )
        self.assertEqual(invoice["id"], list_invoices_for_charge(
            self.org, charge["id"]
        )[0]["id"])
        self._login()
        response = self.client.get(f"/billing/{invoice['id']}")
        self.assertEqual(response.status_code, 200)

    def test_16_operation_invoice_still_works(self):
        operation_id = self._operation()
        invoice = create_draft_for_side(
            self.org,
            operation_id,
            SIDE_BUYER,
            self._admin_user(),
            issuer_mode=ISSUER_MODE_OFFICE,
            issuer_profile_id=self.issuer["id"],
        )
        self.assertEqual(invoice["origin_type"], "operation")
        self.assertIsNone(invoice["agent_account_movement_id"])

    def test_17_buyer_and_seller_remain_independent(self):
        operation_id = self._operation(SIDE_BUYER)
        ensure_parties_for_operation(self.org, operation_id)
        set_operation_party_client_fields(
            self.org,
            operation_id,
            SIDE_SELLER,
            client_legal_name="Seller",
            client_tax_id="20-99887766-5",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Seller 123",
            client_email="seller@example.com",
        )
        set_party_invoice_amount(
            self.org, operation_id, SIDE_SELLER, "50", "USD", "1",
            self.admin, notify=False,
        )
        buyer = create_draft_for_side(
            self.org, operation_id, SIDE_BUYER, self._admin_user(),
            issuer_mode=ISSUER_MODE_OFFICE, issuer_profile_id=self.issuer["id"],
        )
        seller = create_draft_for_side(
            self.org, operation_id, SIDE_SELLER, self._admin_user(),
            issuer_mode=ISSUER_MODE_OFFICE, issuer_profile_id=self.issuer["id"],
        )
        self.assertNotEqual(buyer["id"], seller["id"])

    def test_18_multiple_operation_issuers_remain_supported(self):
        operation_id = self._operation()
        office = create_draft_for_side(
            self.org, operation_id, SIDE_BUYER, self._admin_user(),
            issuer_mode=ISSUER_MODE_OFFICE, issuer_profile_id=self.issuer["id"],
        )
        agent = create_draft_for_side(
            self.org, operation_id, SIDE_BUYER, self._admin_user(),
            issuer_mode=ISSUER_MODE_AGENT,
        )
        self.assertNotEqual(office["issuer_key"], agent["issuer_key"])

    def test_19_agent_cannot_generate_or_cancel_charge_invoice(self):
        charge = self._charge()
        with self.assertRaises(InvoicingError):
            create_draft_for_charge(
                self.org,
                charge["id"],
                self._agent_user(),
                issuer_mode=ISSUER_MODE_OFFICE,
                issuer_profile_id=self.issuer["id"],
            )
        invoice = self._invoice(charge)
        with self.assertRaises(InvoicingError):
            cancel_invoice(
                self.org, invoice["id"], self._agent_user(), reason="No"
            )

    def test_20_migration_has_nullable_operation_and_link_columns(self):
        connection = get_connection()
        try:
            columns = {
                row[1]: row
                for row in connection.execute(
                    "PRAGMA table_info(invoices)"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertEqual(columns["operation_id"][3], 0)
        self.assertIn("agent_account_movement_id", columns)
        self.assertIn("origin_type", columns)

    def test_21_create_tables_twice(self):
        create_tables(create_backup=False)
        create_tables(create_backup=False)

    def test_22_agent_account_detail_renders_invoice_link(self):
        charge = self._charge()
        self._invoice(charge)
        self._login()
        response = self.client.get(
            f"/agent-accounts/{self.agent}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"FAC-", response.data)

    def test_23_invoice_detail_renders_origin_and_payment(self):
        charge = self._charge()
        invoice = self._invoice(charge)
        self._login()
        response = self.client.get(f"/billing/{invoice['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cuenta corriente".encode(), response.data)
        self.assertIn("Pendiente".encode(), response.data)

    def test_24_bidirectional_links_and_allocation_cash_audit(self):
        charge = self._charge()
        invoice = self._invoice(charge)
        payment = self._payment(charge, "10")
        allocations = list_charge_payment_allocations(
            self.org, charge["id"]
        )
        self.assertEqual(allocations[0]["payment_movement_id"], payment["id"])
        self.assertEqual(
            allocations[0]["cash_movement_id"], payment["source_id"]
        )
        self.assertEqual(
            get_charge_invoice_context(
                self.org, charge["id"]
            )["active_invoice"]["id"],
            invoice["id"],
        )

    def test_25_decimal_exact_65_plus_21_percent(self):
        charge = self._charge()
        invoice = self._invoice(charge)
        self.assertEqual(
            Decimal(str(invoice["subtotal"]))
            + Decimal(str(invoice["vat_amount"])),
            Decimal("78.65"),
        )

    def test_billing_search_lists_only_uninvoiced_billable_charges(self):
        available = self._charge()
        invoiced = self._charge(billing_period="Octubre 2026")
        self._invoice(invoiced)
        rows = list_billable_agent_charges(
            self.org, agent_id=self.agent
        )
        ids = {row["id"] for row in rows}
        self.assertIn(available["id"], ids)
        self.assertNotIn(invoiced["id"], ids)


if __name__ == "__main__":
    unittest.main()
