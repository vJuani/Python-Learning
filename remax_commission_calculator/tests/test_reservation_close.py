"""Pre-close is one hidden operation until the deal is confirmed closed."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_reservation_close.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_tasks import complete_task, create_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import (
    add_agent,
    add_operation,
    add_organization,
    add_property,
    add_user,
    create_tables,
    ensure_parties_for_operation,
    upsert_agent_billing_profile,
)
from modules.invoicing import set_party_invoice_amount
from modules.database.connection import get_connection
from modules.database.operation_parties_repository import (
    get_parties_for_operation,
    get_party,
    set_client_fields,
)
from modules.database.operations_repository import (
    closed_month_totals,
    filter_operations,
    get_operation_record,
    get_operations,
)
from modules.database.properties_repository import (
    get_property_record,
    list_available_properties_for_operation,
    list_match_candidates,
)
from modules.property_inventory import is_commercially_available
from modules.database.reservations_repository import (
    get_open_reservation_for_property,
    get_reservation,
    update_reservation_fields,
)
from modules.operation_commission_credit import build_operation_commission_state
from modules.operations_directory import build_operations_directory
from modules.organization_time import UTC, organization_timezone
from modules.reservation_board import build_reservation_board
from modules.reservation_close import (
    begin_billing,
    confirm_close,
    revert_preclose,
)
from modules.reservations import ReservationError, confirm_reservation, format_money
from modules.search import search_operations
from modules.visit_close import VisitCloseError, apply_visit_close
from web_app import app


class _MockArcaTransport:
    def __init__(self):
        self.last_voucher = 41
        self.issue_count = 0

    def wsaa_login(self, cms_b64: str) -> str:
        exp = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<loginTicketResponse>
  <header>
    <expirationTime>{exp}</expirationTime>
    <service>wsfe</service>
  </header>
  <credentials>
    <token>MOCK-TOKEN</token>
    <sign>MOCK-SIGN</sign>
  </credentials>
</loginTicketResponse>"""

    def wsfe_call(self, action, envelope, ticket, cuit):
        if action == "FECompUltimoAutorizado":
            return f"""<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
              <soap:Body>
                <FECompUltimoAutorizadoResponse>
                  <FECompUltimoAutorizadoResult>
                    <CbteNro>{self.last_voucher}</CbteNro>
                  </FECompUltimoAutorizadoResult>
                </FECompUltimoAutorizadoResponse>
              </soap:Body>
            </soap:Envelope>"""
        self.issue_count += 1
        next_num = self.last_voucher + self.issue_count
        return f"""<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <FECAESolicitarResponse>
              <FECAESolicitarResult>
                <FeDetResp>
                  <FECAEDetResponse>
                    <Resultado>A</Resultado>
                    <CAE>71000000000001</CAE>
                    <CAEFchVto>20261231</CAEFchVto>
                    <CbteDesde>{next_num}</CbteDesde>
                  </FECAEDetResponse>
                </FeDetResp>
              </FECAESolicitarResult>
            </FECAESolicitarResponse>
          </soap:Body>
        </soap:Envelope>"""


class ReservationCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="reservation-close-test")
        create_tables()
        cls.org = add_organization("Cierre Org")
        cls.agent = add_agent("Agente Cierre", "Alto", cls.org)
        cls.admin = add_user(
            "cierre_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
        )
        cls.agent_user = add_user(
            "cierre_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.tz = organization_timezone(cls.org)

    def _now(self):
        return datetime(2026, 9, 24, 15, 0, tzinfo=self.tz).astimezone(UTC)

    def _confirmed(self, address, *, purpose="sale"):
        property_id = add_property(
            address,
            "CABA",
            self.org,
            agent_id=self.agent,
            listing_price=178000,
            listing_currency="USD",
            listing_purpose=purpose,
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
        apply_visit_close(
            visit,
            {
                "result": "reserved",
                "reservation_amount": "2000",
                "reservation_currency": "USD",
                "agreed_property_price": "178000",
                "agreed_property_currency": "USD",
                "estimated_closing_date": "2026-10-15",
                "payment_method": "cash",
                "next_milestone": "boleto" if purpose == "sale" else "keys",
                "note": "seña",
            },
            organization_id=self.org,
            agent_id=self.agent,
            actor_user_id=self.agent_user,
            now=self._now(),
            tz=self.tz,
            language="es",
        )
        reservation = get_open_reservation_for_property(self.org, property_id)
        confirm_reservation(self.org, reservation["id"], actor_user_id=self.admin)
        update_reservation_fields(
            self.org,
            reservation["id"],
            {
                "seller_commission_amount": 5340,
                "seller_commission_currency": "USD",
                "buyer_commission_amount": 3000,
                "buyer_commission_currency": "USD",
            },
        )
        return property_id, get_reservation(reservation["id"], self.org)

    def _login(self, client, username):
        client.post(
            "/login",
            data={"username": username, "password": "Password1"},
            follow_redirects=True,
        )

    def _count_operations(self, property_id):
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM operations
                WHERE organization_id = ? AND property_id = ?
                """,
                (self.org, property_id),
            ).fetchone()
        finally:
            connection.close()
        return int(row[0])

    def _parties(self, operation_id):
        return {
            row["party_role"]: row
            for row in get_parties_for_operation(self.org, operation_id)
        }

    def _disable_billing(self, operation_id, amounts=None):
        connection = get_connection()
        try:
            if amounts:
                for role, amount in amounts.items():
                    connection.execute(
                        """
                        UPDATE operation_parties
                        SET billing_enabled = 0,
                            billing_enabled_at = NULL,
                            commission_amount = ?
                        WHERE organization_id = ?
                            AND operation_id = ?
                            AND party_role = ?
                        """,
                        (amount, self.org, operation_id, role),
                    )
            else:
                connection.execute(
                    """
                    UPDATE operation_parties
                    SET billing_enabled = 0, billing_enabled_at = NULL
                    WHERE organization_id = ? AND operation_id = ?
                    """,
                    (self.org, operation_id),
                )
            connection.commit()
        finally:
            connection.close()

    def _issued_invoices(self, operation_id):
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM invoices
                WHERE organization_id = ? AND operation_id = ? AND status = 'issued'
                """,
                (self.org, operation_id),
            ).fetchone()
        finally:
            connection.close()
        return int(row[0])

    def _input_value(self, body, field_id):
        match = re.search(rf'id="{field_id}"[^>]*value="([^"]*)"', body)
        self.assertIsNotNone(match)
        return match.group(1)

    def _invoice_count(self, operation_id):
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM invoices
                WHERE organization_id = ? AND operation_id = ?
                """,
                (self.org, operation_id),
            ).fetchone()
        finally:
            connection.close()
        return int(row[0])

    def _issue_invoice(self, operation_id, *, status="issued", side=None):
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT COALESCE(MAX(invoice_seq), 0) + 1 FROM invoices WHERE organization_id = ?", (self.org,))
            seq = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO invoices (
                    organization_id, invoice_seq, invoice_number_internal,
                    operation_id, agent_id, issuer_type, issuer_name, issuer_tax_id,
                    recipient_name, recipient_tax_id, description,
                    quantity, unit_price, subtotal, vat_amount, total_amount,
                    currency, payment_condition, issue_date, status, side,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 'admin', 'Oficina', '20111111112',
                    'Cliente', '20999999991', 'Comision',
                    1, 1000, 1000, 0, 1000,
                    'USD', 'contado', '2026-10-01', ?, ?,
                    '2026-10-01T12:00:00Z', '2026-10-01T12:00:00Z'
                )
                """,
                (self.org, seq, f"INT-{seq}", operation_id, self.agent, status, side),
            )
            invoice_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()
        return invoice_id

    def test_money_formatter_hides_binary_noise(self):
        self.assertEqual(format_money(5733.000000000001, "USD"), "USD 5.733,00")
        self.assertEqual(format_money("185000", "USD"), "USD 185.000,00")
        self.assertNotIn("000000", format_money(5733.000000000001, "USD"))

    def test_double_post_reuses_the_same_preclose_operation(self):
        property_id, reservation = self._confirmed("Calle Doble 1")
        client = app.test_client()
        self._login(client, "cierre_admin")
        url = f"/reservations/{reservation['id']}/preclose"
        opened = client.post(url, data={"action": "open"})
        self.assertEqual(opened.status_code, 302)
        payload = {
            "action": "bill",
            "seller_vat_amount": "0",
            "buyer_vat_amount": "500",
        }
        first = client.post(url, data=payload)
        second = client.post(url, data=payload)
        self.assertEqual(self._count_operations(property_id), 1)
        stored = get_reservation(reservation["id"], self.org)
        self.assertEqual(stored["reservation_status"], "pre_close")
        operation = get_operation_record(stored["operation_id"], self.org)
        self.assertEqual(operation["status"], "preclose")
        self.assertIn(f"/billing/operations/{operation['db_id']}/", first.location)
        self.assertIn(f"/billing/operations/{operation['db_id']}/", second.location)
        self.assertEqual(first.location, second.location)
        parties = {
            row["party_role"]: row
            for row in get_parties_for_operation(self.org, operation["db_id"])
        }
        self.assertTrue(parties["buyer"]["billing_enabled"])
        self.assertTrue(parties["seller"]["billing_enabled"])

    def test_preclose_stays_out_of_operations_search_and_reports(self):
        _property_id, reservation = self._confirmed("Calle Invisible 9")
        operation_id = begin_billing(
            self.org,
            reservation["id"],
            actor_user_id=self.admin,
            buyer_vat="500",
        )
        visible_ids = [row["db_id"] for row in get_operations(self.org)]
        self.assertNotIn(operation_id, visible_ids)
        self.assertNotIn(operation_id, [row["db_id"] for row in filter_operations(self.org)])
        self.assertEqual(search_operations("Calle Invisible 9", self.org), [])
        client = app.test_client()
        self._login(client, "cierre_admin")
        page = client.get("/operations")
        self.assertNotIn("Calle Invisible 9", page.get_data(as_text=True))
        hidden = client.get(f"/operations/{operation_id}")
        self.assertEqual(hidden.status_code, 404)
        set_client_fields(
            self.org,
            operation_id,
            "buyer",
            client_legal_name="Cliente Invisible",
            client_tax_id="20-99887766-5",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Calle 1",
        )
        billing = client.get(f"/billing/operations/{operation_id}/buyer/prepare")
        self.assertEqual(billing.status_code, 200)
        body = billing.get_data(as_text=True)
        self.assertIn("Total del comprobante", body)
        self.assertIn("Moneda del comprobante", body)
        self.assertNotIn("Calcular IVA", body)
        self.assertNotIn("Guardar importe", body)

    def test_reservation_billing_prepare_skips_treasury_gate(self):
        property_id, reservation = self._confirmed("Calle Factura Directa 11")
        client = app.test_client()
        self._login(client, "cierre_admin")
        url = f"/reservations/{reservation['id']}/preclose"
        client.post(url, data={"action": "open"})
        payload = {
            "action": "bill",
            "seller_vat_amount": "0",
            "buyer_vat_amount": "0",
        }
        first = client.post(url, data=payload)
        second = client.post(url, data=payload)
        self.assertEqual(self._count_operations(property_id), 1)
        stored = get_reservation(reservation["id"], self.org)
        operation_id = stored["operation_id"]
        self.assertEqual(stored["reservation_status"], "pre_close")
        self.assertIn(f"/billing/operations/{operation_id}/buyer/prepare", first.location)
        self.assertEqual(first.location, second.location)
        operation = get_operation_record(operation_id, self.org)
        self.assertEqual(operation["status"], "preclose")
        self.assertEqual(operation["was_invoiced"], "no")
        parties = self._parties(operation_id)
        self.assertTrue(parties["buyer"]["billing_enabled"])
        self.assertTrue(parties["seller"]["billing_enabled"])
        self.assertIsNone(parties["buyer"]["invoice_amount"])
        self.assertIsNone(parties["seller"]["invoice_amount"])

        warning = "Esta punta todavía no está habilitada para facturar"
        for side in ("buyer", "seller"):
            set_client_fields(
                self.org,
                operation_id,
                side,
                client_legal_name=f"Cliente {side}",
                client_tax_id="20-99887766-5",
                client_tax_condition="consumidor_final",
                client_fiscal_address="Calle 1",
            )
            page = client.get(f"/billing/operations/{operation_id}/{side}/prepare")
            self.assertEqual(page.status_code, 200)
            body = page.get_data(as_text=True)
            self.assertNotIn(warning, body)
            self.assertNotIn("Tesorería todavía no definió el importe fiscal", body)
            self.assertIn("Total del comprobante", body)
            amount = self._input_value(body, "invoice_amount")
            self.assertEqual(
                Decimal(amount),
                Decimal(str(parties[side]["commission_amount"])).quantize(
                    Decimal("0.01")
                ),
            )
            self.assertIn('value="USD" selected', body)
            tag = re.search(r'<input id="invoice_amount"[^>]*>', body)
            self.assertIsNotNone(tag)
            self.assertNotIn("readonly", tag.group(0))

        prepared = client.post(
            f"/operations/{operation_id}/parties/buyer/invoice-amount",
            data={
                "next": "prepare",
                "enable_billing": "1",
                "invoice_amount": "1500",
                "invoice_currency": "USD",
            },
        )
        self.assertEqual(prepared.status_code, 302)
        buyer = get_party(self.org, operation_id, "buyer")
        self.assertEqual(Decimal(str(buyer["invoice_amount"])), Decimal("1500"))
        self.assertEqual(buyer["invoice_currency"], "USD")
        operation = get_operation_record(operation_id, self.org)
        self.assertEqual(operation["was_invoiced"], "no")
        self.assertEqual(operation["status"], "preclose")
        stored = get_reservation(reservation["id"], self.org)
        self.assertEqual(stored["reservation_status"], "pre_close")
        self.assertEqual(self._issued_invoices(operation_id), 0)
        self.assertEqual(self._count_operations(property_id), 1)

    def test_existing_preclose_prepare_reuses_operation_and_prefills_amount(self):
        property_id, reservation = self._confirmed("Calle Reuso Preclose 15")
        client = app.test_client()
        self._login(client, "cierre_admin")
        url = f"/reservations/{reservation['id']}/preclose"
        client.post(url, data={"action": "open"})
        client.post(
            url,
            data={
                "action": "bill",
                "seller_vat_amount": "0",
                "buyer_vat_amount": "0",
            },
        )
        stored = get_reservation(reservation["id"], self.org)
        operation_id = stored["operation_id"]
        self.assertEqual(stored["reservation_status"], "pre_close")
        self._disable_billing(
            operation_id,
            {"buyer": 3200, "seller": 4800},
        )
        parties = self._parties(operation_id)
        self.assertFalse(parties["buyer"]["billing_enabled"])
        self.assertFalse(parties["seller"]["billing_enabled"])

        reentered = client.get(url)
        self.assertEqual(reentered.status_code, 200)
        self.assertEqual(self._count_operations(property_id), 1)
        parties = self._parties(operation_id)
        self.assertTrue(parties["buyer"]["billing_enabled"])
        self.assertTrue(parties["seller"]["billing_enabled"])

        self._disable_billing(operation_id)
        warning = "Esta punta todavía no está habilitada para facturar"
        for side, expected in (("buyer", Decimal("3200")), ("seller", Decimal("4800"))):
            set_client_fields(
                self.org,
                operation_id,
                side,
                client_legal_name=f"Cliente {side}",
                client_tax_id="20-99887766-5",
                client_tax_condition="consumidor_final",
                client_fiscal_address="Calle 1",
            )
            page = client.get(f"/billing/operations/{operation_id}/{side}/prepare")
            self.assertEqual(page.status_code, 200)
            body = page.get_data(as_text=True)
            self.assertNotIn(warning, body)
            self.assertNotIn("Tesorería todavía no definió el importe fiscal", body)
            self.assertEqual(Decimal(self._input_value(body, "invoice_amount")), expected)
            self.assertIn('value="USD" selected', body)
            self.assertTrue(self._parties(operation_id)[side]["billing_enabled"])
            self.assertIsNone(self._parties(operation_id)[side]["invoice_amount"])

        stored = get_reservation(reservation["id"], self.org)
        operation = get_operation_record(operation_id, self.org)
        self.assertEqual(stored["operation_id"], operation_id)
        self.assertEqual(stored["reservation_status"], "pre_close")
        self.assertEqual(operation["status"], "preclose")
        self.assertEqual(operation["was_invoiced"], "no")
        self.assertEqual(self._issued_invoices(operation_id), 0)
        self.assertEqual(self._count_operations(property_id), 1)

    def _assert_preclose_back(self, body, reservation_id):
        self.assertIn(
            f'href="/reservations/{reservation_id}/preclose" class="btn btn-secondary">Volver</a>',
            body,
        )

    def _invoice_id(self, operation_id):
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT id
                FROM invoices
                WHERE organization_id = ? AND operation_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.org, operation_id),
            ).fetchone()
        finally:
            connection.close()
        return row[0]

    def test_preclose_invoice_back_returns_to_the_reservation(self):
        property_id, reservation = self._confirmed("Calle Volver Factura 16")
        client = app.test_client()
        self._login(client, "cierre_admin")
        url = f"/reservations/{reservation['id']}/preclose"
        client.post(url, data={"action": "open"})
        client.post(
            url,
            data={"action": "bill", "seller_vat_amount": "0", "buyer_vat_amount": "0"},
        )
        stored = get_reservation(reservation["id"], self.org)
        operation_id = stored["operation_id"]
        self.assertEqual(stored["reservation_status"], "pre_close")
        self.assertEqual(get_operation_record(operation_id, self.org)["status"], "preclose")
        upsert_agent_billing_profile(
            self.org,
            self.agent,
            legal_name="QA Cierre",
            tax_id="20-99999990-6",
            tax_condition="monotributo",
            fiscal_address="Calle QA 1",
            email="qa.cierre@preview.invalid",
        )
        set_client_fields(
            self.org,
            operation_id,
            "buyer",
            client_legal_name="Cliente Volver",
            client_tax_id="20-99887766-5",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Calle 1",
        )
        set_party_invoice_amount(
            self.org,
            operation_id,
            "buyer",
            "3000",
            "USD",
            None,
            self.admin,
            notify=False,
        )
        prepare = client.get(f"/billing/operations/{operation_id}/buyer/prepare")
        self.assertEqual(prepare.status_code, 200)
        self._assert_preclose_back(prepare.get_data(as_text=True), reservation["id"])

        self._issue_invoice(operation_id, status="ready_to_issue")
        invoice_id = self._invoice_id(operation_id)
        detail = client.get(f"/billing/{invoice_id}")
        review = client.get(f"/billing/{invoice_id}/review")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(review.status_code, 200)
        self._assert_preclose_back(detail.get_data(as_text=True), reservation["id"])
        self._assert_preclose_back(review.get_data(as_text=True), reservation["id"])

        stored = get_reservation(reservation["id"], self.org)
        operation = get_operation_record(operation_id, self.org)
        self.assertEqual(stored["reservation_status"], "pre_close")
        self.assertEqual(stored["operation_id"], operation_id)
        self.assertEqual(operation["status"], "preclose")
        self.assertEqual(operation["was_invoiced"], "no")
        self.assertEqual(self._count_operations(property_id), 1)
        self.assertEqual(self._issued_invoices(operation_id), 0)
        connection = get_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM invoices WHERE organization_id = ? AND operation_id = ?",
                (self.org, operation_id),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_manual_invoice_back_stays_on_billing(self):
        property_id = add_property(
            "Calle Manual Volver 17",
            "CABA",
            self.org,
            agent_id=self.agent,
            listing_price=100000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        operation_id = add_operation(
            "06/10/2026",
            self.agent,
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
            1350,
            self.org,
            currency="USD",
            status="approved",
        )
        self._issue_invoice(operation_id, status="draft")
        client = app.test_client()
        self._login(client, "cierre_admin")
        detail = client.get(f"/billing/{self._invoice_id(operation_id)}")
        body = detail.get_data(as_text=True)
        self.assertIn('href="/billing" class="btn btn-secondary">Volver</a>', body)
        self.assertNotIn("/reservations/", body)
        self.assertEqual(get_operation_record(operation_id, self.org)["status"], "approved")

    def test_preclose_billing_enables_only_participating_sides(self):
        _property_id, reservation = self._confirmed("Calle Una Punta 13")
        update_reservation_fields(
            self.org,
            reservation["id"],
            {
                "seller_commission_amount": 0,
                "seller_commission_currency": "USD",
            },
        )
        operation_id = begin_billing(
            self.org,
            reservation["id"],
            actor_user_id=self.admin,
        )
        parties = self._parties(operation_id)
        self.assertTrue(parties["buyer"]["is_participating"])
        self.assertTrue(parties["buyer"]["billing_enabled"])
        self.assertFalse(parties["seller"]["is_participating"])
        self.assertFalse(parties["seller"]["billing_enabled"])
        self.assertEqual(get_operation_record(operation_id, self.org)["was_invoiced"], "no")

    def test_manual_operation_still_requires_billing_enablement(self):
        property_id = add_property(
            "Calle Manual 12",
            "CABA",
            self.org,
            agent_id=self.agent,
            listing_price=100000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        operation_id = add_operation(
            "06/10/2026",
            self.agent,
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
            1350,
            self.org,
            currency="USD",
            status="approved",
        )
        ensure_parties_for_operation(self.org, operation_id)
        set_client_fields(
            self.org,
            operation_id,
            "buyer",
            client_legal_name="Cliente Manual",
            client_tax_id="20-99887766-5",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Calle 1",
        )
        self.assertFalse(self._parties(operation_id)["buyer"]["billing_enabled"])
        client = app.test_client()
        self._login(client, "cierre_admin")
        page = client.get(f"/billing/operations/{operation_id}/buyer/prepare")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn(
            "Esta punta todavía no está habilitada para facturar",
            body,
        )
        self.assertFalse(self._parties(operation_id)["buyer"]["billing_enabled"])
        operation = get_operation_record(operation_id, self.org)
        self.assertEqual(operation["status"], "approved")
        self.assertEqual(operation["was_invoiced"], "no")

    def test_close_without_billing_creates_one_closed_operation(self):
        property_id, reservation = self._confirmed("Calle Sin Factura 2")
        client = app.test_client()
        self._login(client, "cierre_admin")
        url = f"/reservations/{reservation['id']}/preclose"
        client.post(url, data={"action": "open"})
        closed = client.post(
            url,
            data={"action": "close", "seller_vat_amount": "0", "buyer_vat_amount": "0"},
        )
        self.assertEqual(closed.status_code, 302)
        self.assertEqual(self._count_operations(property_id), 1)
        stored = get_reservation(reservation["id"], self.org)
        operation = get_operation_record(stored["operation_id"], self.org)
        self.assertEqual(operation["status"], "closed")
        self.assertEqual(stored["reservation_status"], "closed")
        self.assertIn(f"/operations/{operation['db_id']}", closed.location)
        property_row = get_property_record(property_id, self.org)
        self.assertEqual(property_row["commercial_status"], "sold")
        active = build_reservation_board(self.org, filters={"view": "active"})
        history = build_reservation_board(self.org, filters={"view": "history"})
        self.assertNotIn(reservation["id"], [row["id"] for row in active["rows"]])
        historic = next(row for row in history["rows"] if row["id"] == reservation["id"])
        self.assertEqual(historic["operation_id"], operation["db_id"])
        detail = client.get(f"/operations/{operation['db_id']}")
        body = detail.get_data(as_text=True)
        self.assertIn("IVA cargado en la operación", body)
        self.assertIn("Preparar factura", body)
        self.assertNotIn("Guardar importe", body)
        self.assertNotIn("Calcular IVA", body)
        parties = get_parties_for_operation(self.org, operation["db_id"])
        self.assertTrue(parties)
        self.assertFalse(any(row["billing_enabled"] for row in parties))

    def test_close_without_billing_makes_agent_commission_ready(self):
        _property_id, reservation = self._confirmed("Calle Comision Lista 8")
        client = app.test_client()
        self._login(client, "cierre_admin")
        url = f"/reservations/{reservation['id']}/preclose"
        client.post(url, data={"action": "open"})
        stored = get_reservation(reservation["id"], self.org)
        self.assertEqual(stored["reservation_status"], "pre_close")
        client.post(
            url,
            data={
                "action": "close",
                "seller_vat_amount": "0",
                "buyer_vat_amount": "0",
            },
        )
        stored = get_reservation(reservation["id"], self.org)
        operation = get_operation_record(stored["operation_id"], self.org)
        self.assertEqual(operation["status"], "closed")
        self.assertNotEqual(operation["status"], "approved")
        state = build_operation_commission_state(self.org, operation["db_id"])
        self.assertEqual(state["state"], "ready")
        self.assertNotIn("operation_commission_not_approved", state["reasons"])
        self.assertNotIn("operation_commission_not_closed", state["reasons"])
        self.assertEqual(state["reasons"], [])
        detail = client.get(f"/operations/{operation['db_id']}")
        body = detail.get_data(as_text=True)
        self.assertIn("Lista para acreditar", body)
        self.assertNotIn("La operación todavía no está aprobada.", body)
        self.assertNotIn("La operación todavía no está cerrada o escriturada.", body)

    def test_billing_does_not_close_and_confirm_reuses_the_operation(self):
        property_id, reservation = self._confirmed("Calle Con Factura 3")
        before = closed_month_totals(self.org)
        operation_id = begin_billing(
            self.org,
            reservation["id"],
            actor_user_id=self.admin,
            buyer_vat="500",
        )
        self._issue_invoice(operation_id, status="issued")
        stored = get_reservation(reservation["id"], self.org)
        operation = get_operation_record(operation_id, self.org)
        self.assertEqual(stored["reservation_status"], "pre_close")
        self.assertEqual(operation["status"], "preclose")
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "reserved")
        with self.assertRaises(ReservationError) as raised:
            revert_preclose(self.org, reservation["id"], actor_user_id=self.admin)
        self.assertEqual(raised.exception.message_key, "reservation_err_preclose_invoiced")
        self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "pre_close")
        self._issue_invoice(operation_id, status="issued", side="buyer")
        self._issue_invoice(operation_id, status="issued", side="seller")
        closed_id = confirm_close(self.org, reservation["id"], actor_user_id=self.admin)
        self.assertEqual(closed_id, operation_id)
        self.assertEqual(self._count_operations(property_id), 1)
        self.assertEqual(get_operation_record(operation_id, self.org)["status"], "closed")
        self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "closed")
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "sold")
        again = confirm_close(self.org, reservation["id"], actor_user_id=self.admin)
        self.assertEqual(again, operation_id)
        self.assertEqual(self._count_operations(property_id), 1)

        after = closed_month_totals(self.org)
        self.assertEqual(after["count"], before["count"] + 1)
        closed = get_operation_record(operation_id, self.org)
        self.assertAlmostEqual(
            _usd_amount(after, "commission") - _usd_amount(before, "commission"),
            closed["total_commission"],
            places=2,
        )

    def test_revert_without_invoice_removes_the_preclose_operation(self):
        property_id, reservation = self._confirmed("Calle Volver 4")
        operation_id = begin_billing(self.org, reservation["id"], actor_user_id=self.admin)
        self._issue_invoice(operation_id, status="draft")
        revert_preclose(self.org, reservation["id"], actor_user_id=self.admin)
        stored = get_reservation(reservation["id"], self.org)
        self.assertEqual(stored["reservation_status"], "confirmed")
        self.assertIsNone(stored["operation_id"])
        self.assertEqual(self._count_operations(property_id), 0)
        self.assertIsNone(get_operation_record(operation_id, self.org))
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "reserved")

    def _property_row_html(self, body, address):
        match = re.search(
            rf"<tr>.*?{re.escape(address)}.*?</tr>",
            body,
            re.S,
        )
        self.assertIsNotNone(match)
        return match.group(0)

    def _assert_closed_listing(self, address, property_id, commercial_label):
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["commercial_status"], commercial_label)
        self.assertFalse(is_commercially_available(row))
        match_ids = {item["id"] for item in list_match_candidates(self.org)}
        available_ids = {
            item["id"] for item in list_available_properties_for_operation(self.org)
        }
        self.assertNotIn(property_id, match_ids)
        self.assertNotIn(property_id, available_ids)
        client = app.test_client()
        self._login(client, "cierre_admin")
        page = client.get(f"/properties?q={address}")
        self.assertEqual(page.status_code, 200)
        chunk = self._property_row_html(page.get_data(as_text=True), address)
        expected = "Vendida" if commercial_label == "sold" else "Alquilada"
        self.assertIn(expected, chunk)
        self.assertIn("Publicación: Aprobada", chunk)
        self.assertNotIn(">Activa<", chunk)
        detail = client.get(f"/properties/{property_id}")
        self.assertEqual(detail.status_code, 200)
        detail_body = detail.get_data(as_text=True)
        self.assertIn(expected, detail_body)
        self.assertIn("Publicación: Aprobada", detail_body)

    def test_closed_sale_lists_the_property_as_sold(self):
        property_id, reservation = self._confirmed("Calle Vendida UI 9")
        self.assertEqual(get_property_record(property_id, self.org)["status"], "approved")
        confirm_close(self.org, reservation["id"], actor_user_id=self.admin)
        self._assert_closed_listing("Calle Vendida UI 9", property_id, "sold")
        visit = create_task(
            self.org,
            self.agent,
            {
                "title": "Segunda visita vendida",
                "task_type": "visit",
                "due_date": "2026-09-25",
                "due_time": "12:00",
                "contact_id": reservation["contact_id"],
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
        with self.assertRaises(VisitCloseError) as raised:
            apply_visit_close(
                visit,
                {
                    "result": "reserved",
                    "reservation_amount": "2000",
                    "reservation_currency": "USD",
                    "agreed_property_price": "178000",
                    "agreed_property_currency": "USD",
                    "estimated_closing_date": "2026-10-20",
                    "payment_method": "cash",
                    "next_milestone": "boleto",
                },
                organization_id=self.org,
                agent_id=self.agent,
                actor_user_id=self.agent_user,
                now=self._now(),
                tz=self.tz,
            )
        self.assertEqual(
            raised.exception.message_key,
            "visit_close_err_property_unavailable",
        )
        kept = get_property_record(property_id, self.org)
        self.assertEqual(kept["commercial_status"], "sold")
        self.assertEqual(kept["status"], "approved")
        self.assertIsNone(get_open_reservation_for_property(self.org, property_id))

    def test_closed_rental_lists_the_property_as_rented(self):
        property_id, reservation = self._confirmed("Calle Alquilada UI 10", purpose="rental")
        confirm_close(self.org, reservation["id"], actor_user_id=self.admin)
        self._assert_closed_listing("Calle Alquilada UI 10", property_id, "rented")

    def test_rental_close_marks_the_property_rented(self):
        property_id, reservation = self._confirmed("Calle Alquiler 5", purpose="rental")
        confirm_close(self.org, reservation["id"], actor_user_id=self.admin)
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "rented")
        stored = get_reservation(reservation["id"], self.org)
        operation = get_operation_record(stored["operation_id"], self.org)
        panel = build_operations_directory(self.org, [operation], {})
        row = panel["rows"][0]
        self.assertEqual(row["type_label"], "Alquiler")
        self.assertEqual(row["client_name"], "Calle Alquiler 5")
        self.assertEqual(row["stage_label"], "Cierre")
        self.assertEqual(row["workflow_status"]["label"], "Cerrada")
        self.assertNotEqual(row["stage_label"], "Reservada")

    def test_confirmed_cta_says_close_the_deal(self):
        _property_id, reservation = self._confirmed("Calle Boton 6")
        client = app.test_client()
        self._login(client, "cierre_admin")
        page = client.get(f"/reservations/{reservation['id']}")
        body = page.get_data(as_text=True)
        self.assertIn("Cerrar negocio", body)
        self.assertNotIn("Preparar operación", body)
        denied = app.test_client()
        self._login(denied, "cierre_agent")
        blocked = denied.post(
            f"/reservations/{reservation['id']}/preclose",
            data={"action": "open"},
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "confirmed")

    def test_month_kpis_ignore_preclose(self):
        _property_id, reservation = self._confirmed("Calle Kpi 7")
        before = closed_month_totals(self.org)
        begin_billing(self.org, reservation["id"], actor_user_id=self.admin, buyer_vat="800")
        during = closed_month_totals(self.org)
        self.assertEqual(during["count"], before["count"])
        self.assertEqual(_usd_amount(during, "commission"), _usd_amount(before, "commission"))

    def test_preclose_confirm_waits_for_issued_invoices_on_each_side(self):
        property_id, reservation = self._confirmed("Calle Estados Factura 17")
        operation_id = begin_billing(
            self.org,
            reservation["id"],
            actor_user_id=self.admin,
        )
        client = app.test_client()
        self._login(client, "cierre_admin")
        url = f"/reservations/{reservation['id']}/preclose"

        def page():
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            return response.get_data(as_text=True)

        body = page()
        self.assertEqual(body.count("No preparada"), 2)
        self.assertEqual(body.count("Preparar factura"), 2)
        self.assertNotIn("Borrador preparado", body)
        self.assertNotIn("Ver borrador", body)
        self.assertIn("Falta emitir factura compradora", body)
        self.assertIn("Falta emitir factura vendedora", body)
        self.assertIn("disabled", body)
        blocked = client.post(url, data={"action": "close"})
        self.assertEqual(blocked.status_code, 302)
        self.assertIn(f"/reservations/{reservation['id']}/preclose", blocked.location)
        self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "pre_close")
        self.assertEqual(get_operation_record(operation_id, self.org)["status"], "preclose")
        self.assertEqual(get_operation_record(operation_id, self.org)["was_invoiced"], "no")
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "reserved")

        buyer_draft = self._issue_invoice(operation_id, status="draft", side="buyer")
        body = page()
        self.assertIn("Borrador preparado", body)
        self.assertIn("No preparada", body)
        self.assertEqual(body.count("Preparar factura"), 1)
        self.assertIn(f'href="/billing/{buyer_draft}"', body)
        self.assertIn("Ver borrador", body)
        self.assertIn("Falta emitir factura compradora", body)
        self.assertIn("Falta emitir factura vendedora", body)
        self.assertIn("disabled", body)

        seller_draft = self._issue_invoice(operation_id, status="ready_to_issue", side="seller")
        body = page()
        again = page()
        self.assertEqual(body, again)
        self.assertEqual(body.count("Borrador preparado"), 2)
        self.assertNotIn("No preparada", body)
        self.assertNotIn("Preparar factura", body)
        self.assertEqual(body.count("Ver borrador"), 2)
        self.assertIn(f'href="/billing/{buyer_draft}"', body)
        self.assertIn(f'href="/billing/{seller_draft}"', body)
        self.assertIn("Falta emitir factura compradora", body)
        self.assertIn("Falta emitir factura vendedora", body)
        self.assertIn("disabled", body)
        self.assertEqual(self._invoice_count(operation_id), 2)
        for invoice_id in (buyer_draft, seller_draft):
            opened = client.get(f"/billing/{invoice_id}")
            self.assertEqual(opened.status_code, 200)
        self.assertEqual(self._invoice_count(operation_id), 2)
        self.assertEqual(self._count_operations(property_id), 1)
        self.assertEqual(get_operation_record(operation_id, self.org)["was_invoiced"], "no")

        connection = get_connection()
        try:
            connection.execute(
                "UPDATE invoices SET status = 'issued' WHERE id = ? AND organization_id = ?",
                (buyer_draft, self.org),
            )
            connection.commit()
        finally:
            connection.close()
        body = page()
        self.assertIn("Emitida", body)
        self.assertIn("Ver factura", body)
        self.assertIn(f'href="/billing/{buyer_draft}"', body)
        self.assertEqual(body.count("Borrador preparado"), 1)
        self.assertIn("Falta emitir factura vendedora", body)
        self.assertNotIn("Falta emitir factura compradora", body)
        self.assertIn("disabled", body)
        opened = client.get(f"/billing/{buyer_draft}")
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(self._invoice_count(operation_id), 2)

        connection = get_connection()
        try:
            connection.execute(
                "UPDATE invoices SET status = 'issued' WHERE id = ? AND organization_id = ?",
                (seller_draft, self.org),
            )
            connection.commit()
        finally:
            connection.close()
        body = page()
        self.assertEqual(body.count("Emitida"), 2)
        self.assertEqual(body.count("Ver factura"), 2)
        self.assertNotIn("disabled", body)
        self.assertNotIn("Falta emitir factura", body)
        self.assertIn("Confirmar cierre", body)
        closed = client.post(url, data={"action": "close"})
        self.assertEqual(closed.status_code, 302)
        self.assertEqual(self._count_operations(property_id), 1)
        self.assertEqual(get_operation_record(operation_id, self.org)["status"], "closed")
        self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "closed")
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "sold")
        self.assertEqual(self._invoice_count(operation_id), 2)

    def test_preclose_ready_draft_without_arca_asks_to_link(self):
        property_id, reservation = self._confirmed("Calle Sin Arca 18")
        operation_id = begin_billing(self.org, reservation["id"], actor_user_id=self.admin)
        buyer_id = self._issue_invoice(operation_id, status="ready_to_issue", side="buyer")
        seller_id = self._issue_invoice(operation_id, status="ready_to_issue", side="seller")
        manual_id = add_operation(
            "07/10/2026",
            self.agent,
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
            1350,
            self.org,
            currency="USD",
            status="approved",
        )
        manual_invoice = self._issue_invoice(manual_id, status="ready_to_issue", side="buyer")
        client = app.test_client()
        self._login(client, "cierre_admin")
        preclose = f"/reservations/{reservation['id']}/preclose"
        for invoice_id in (buyer_id, seller_id):
            page = client.get(f"/billing/{invoice_id}")
            self.assertEqual(page.status_code, 200)
            body = page.get_data(as_text=True)
            self.assertIn("Borrador preparado", body)
            self.assertIn("ARCA no vinculado", body)
            self.assertNotIn("Lista para emitir", body)
            self.assertIn("Vincular ARCA", body)
            self.assertIn('href="/settings/arca"', body)
            self.assertNotIn("Emitir factura", body)
            self.assertNotIn("/issue-arca", body)
            self.assertIn(f'href="{preclose}" class="btn btn-secondary">Volver</a>', body)
            link = client.get("/settings/arca")
            self.assertEqual(link.status_code, 200)
        board = client.get(preclose).get_data(as_text=True)
        self.assertEqual(board.count("Borrador preparado"), 2)
        self.assertNotIn("Emitida", board)
        self.assertIn("Falta emitir factura compradora", board)
        self.assertIn("Falta emitir factura vendedora", board)
        self.assertIn("disabled", board)
        self.assertEqual(self._invoice_status(buyer_id), "ready_to_issue")
        self.assertEqual(self._invoice_status(seller_id), "ready_to_issue")
        self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "pre_close")
        self.assertEqual(get_operation_record(operation_id, self.org)["status"], "preclose")
        self.assertEqual(get_operation_record(operation_id, self.org)["was_invoiced"], "no")
        self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "reserved")
        manual = client.get(f"/billing/{manual_invoice}")
        manual_body = manual.get_data(as_text=True)
        self.assertNotIn("ARCA no vinculado", manual_body)
        self.assertNotIn("Emitir factura", manual_body)
        self.assertNotIn("Volver al pre-cierre", manual_body)
        self.assertIn('href="/billing" class="btn btn-secondary">Volver</a>', manual_body)

    def test_preclose_issue_posts_once_and_does_not_close(self):
        property_id, reservation = self._confirmed("Calle Emitir 19")
        operation_id = begin_billing(self.org, reservation["id"], actor_user_id=self.admin)
        upsert_agent_billing_profile(
            self.org,
            self.agent,
            legal_name="Agente Cierre",
            tax_id="20-30000000-3",
            tax_condition="monotributo",
            fiscal_address="Calle QA 1",
            email="cierre@preview.invalid",
        )
        buyer_id = self._ready_fiscal_invoice(operation_id, "buyer")
        seller_id = self._ready_fiscal_invoice(operation_id, "seller")
        manual_property = add_property(
            "Calle Manual Emitir 19",
            "CABA",
            self.org,
            agent_id=self.agent,
            listing_price=100000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        manual_id = add_operation(
            "07/10/2026",
            self.agent,
            manual_property,
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
            self.org,
            currency="ARS",
            status="approved",
        )
        manual_invoice = self._ready_fiscal_invoice(manual_id, "buyer")
        client = app.test_client()
        self._login(client, "cierre_admin")
        preclose = f"/reservations/{reservation['id']}/preclose"
        transport = _MockArcaTransport()
        with self._arca_environment():
            self._link_admin_arca("20-30000000-3")
            buyer_page = client.get(f"/billing/{buyer_id}").get_data(as_text=True)
            seller_page = client.get(f"/billing/{seller_id}").get_data(as_text=True)
            self.assertEqual(self._invoice_status(buyer_id), "ready_to_issue")
            self.assertEqual(self._invoice_status(seller_id), "ready_to_issue")
            self.assertEqual(transport.issue_count, 0)
            self.assertIn("Emitir factura", buyer_page)
            self.assertIn("Emitir factura", seller_page)
            self.assertIn(f'action="/billing/{buyer_id}/issue-arca"', buyer_page)
            self.assertIn(f'action="/billing/{seller_id}/issue-arca"', seller_page)
            self.assertNotIn("ARCA no vinculado", buyer_page)
            self.assertNotIn("ARCA no vinculado", seller_page)
            manual_body = client.get(f"/billing/{manual_invoice}").get_data(as_text=True)
            self.assertIn("Emitir en ARCA (Homologación)", manual_body)
            self.assertNotIn("Volver al pre-cierre", manual_body)
            self.assertNotIn(">Emitir factura<", manual_body)

            from modules.invoicing import issue_fiscal_invoice as real_issue

            def _issue(organization_id, invoice_id, user, **kwargs):
                kwargs.setdefault("transport", transport)
                return real_issue(organization_id, invoice_id, user, **kwargs)

            with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
                with patch("modules.billing_routes.issue_fiscal_invoice", side_effect=_issue):
                    first = client.post(f"/billing/{buyer_id}/issue-arca")
                    second = client.post(f"/billing/{buyer_id}/issue-arca")
            self.assertEqual(first.status_code, 302)
            self.assertEqual(second.status_code, 302)
            self.assertEqual(transport.issue_count, 1)
            issued = self._invoice_row(buyer_id)
            self.assertEqual(issued["status"], "issued")
            self.assertEqual(issued["provider"], "arca")
            self.assertEqual(issued["cae"], "71000000000001")
            self.assertTrue(issued["cae_expiration"])
            self.assertTrue(issued["external_invoice_number"])
            self.assertTrue(issued["point_of_sale"])
            self.assertTrue(issued["fiscal_voucher_type"])
            self.assertEqual(self._invoice_count(operation_id), 2)
            self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "pre_close")
            self.assertEqual(get_operation_record(operation_id, self.org)["status"], "preclose")
            self.assertEqual(get_operation_record(operation_id, self.org)["was_invoiced"], "no")
            self.assertEqual(get_property_record(property_id, self.org)["commercial_status"], "reserved")
            detail = client.get(f"/billing/{buyer_id}").get_data(as_text=True)
            self.assertIn(f'href="{preclose}" class="btn btn-primary">Volver al pre-cierre</a>', detail)
            self.assertIn(f'href="{preclose}" class="btn btn-secondary">Volver</a>', detail)
            board = client.get(preclose).get_data(as_text=True)
            self.assertIn("Emitida", board)
            self.assertIn("Borrador preparado", board)
            self.assertIn("Falta emitir factura vendedora", board)
            self.assertNotIn("Falta emitir factura compradora", board)
            self.assertIn("disabled", board)

            with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
                with patch("modules.billing_routes.issue_fiscal_invoice", side_effect=_issue):
                    seller_post = client.post(f"/billing/{seller_id}/issue-arca")
                    seller_again = client.post(f"/billing/{seller_id}/issue-arca")
            self.assertEqual(seller_post.status_code, 302)
            self.assertEqual(seller_again.status_code, 302)
            self.assertEqual(transport.issue_count, 2)
            self.assertEqual(self._invoice_status(seller_id), "issued")
            self.assertEqual(self._invoice_count(operation_id), 2)
            self.assertEqual(get_reservation(reservation["id"], self.org)["reservation_status"], "pre_close")
            self.assertEqual(get_operation_record(operation_id, self.org)["status"], "preclose")
            board = client.get(preclose).get_data(as_text=True)
            self.assertEqual(board.count("Emitida"), 2)
            self.assertNotIn("Borrador preparado", board)
            self.assertNotIn("disabled", board)
            self.assertNotIn("Falta emitir factura", board)
            self.assertIn("Confirmar cierre", board)

    def _invoice_status(self, invoice_id):
        return self._invoice_row(invoice_id)["status"]

    def _invoice_row(self, invoice_id):
        from modules.database import get_invoice

        return get_invoice(self.org, invoice_id)

    def _ready_fiscal_invoice(self, operation_id, side):
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COALESCE(MAX(invoice_seq), 0) + 1 FROM invoices WHERE organization_id = ?",
                (self.org,),
            )
            seq = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO invoices (
                    organization_id, invoice_seq, invoice_number_internal,
                    operation_id, agent_id, issuer_type, issuer_name, issuer_tax_id,
                    issuer_tax_condition, recipient_name, recipient_tax_id,
                    recipient_tax_condition, description,
                    quantity, unit_price, subtotal, vat_amount, total_amount,
                    currency, payment_condition, issue_date, status, side,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 'agent', 'Agente Cierre', '20-30000000-3',
                    'monotributo', 'Cliente Fiscal', '20-12345678-6',
                    'consumidor_final', 'Comision',
                    1, 3200, 3200, 0, 3200,
                    'ARS', 'contado', '2026-10-07', 'ready_to_issue', ?,
                    '2026-10-07T12:00:00Z', '2026-10-07T12:00:00Z'
                )
                """,
                (self.org, seq, f"FAC-{seq:06d}", operation_id, self.agent, side),
            )
            invoice_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()
        return invoice_id

    def _link_admin_arca(self, cuit):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509.oid import NameOID

        from modules.arca.connections import generate_key_and_csr, store_credentials

        key_pem, _csr = generate_key_and_csr(common_name="JRH Test", cuit=cuit)
        key = serialization.load_pem_private_key(key_pem, password=None)
        digits = "".join(ch for ch in str(cuit) if ch.isdigit())
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "JRH Test"),
                x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {digits}"),
            ]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
            .sign(key, hashes.SHA256())
        )
        store_credentials(
            self.org,
            self.admin,
            certificate_pem=cert.public_bytes(serialization.Encoding.PEM),
            private_key_pem=key_pem,
            connection_status="connected",
            point_of_sale="5",
            last_verified_at=datetime.now(timezone.utc).isoformat(),
        )

    @contextmanager
    def _arca_environment(self):
        keys = ("INVOICE_PROVIDER", "ARCA_ENV")
        previous = {key: os.environ.get(key) for key in keys}
        os.environ["INVOICE_PROVIDER"] = "arca"
        os.environ["ARCA_ENV"] = "homologation"
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_seller_prepare_asks_for_the_seller_not_the_property(self):
        address = "Av. Punta Fiscal 300"
        property_id, reservation = self._confirmed(address)
        operation_id = begin_billing(
            self.org,
            reservation["id"],
            actor_user_id=self.admin,
        )
        parties = self._parties(operation_id)
        self.assertTrue(parties["buyer"]["is_participating"])
        self.assertTrue(parties["seller"]["is_participating"])
        self.assertFalse((parties["seller"]["client_legal_name"] or "").strip())
        self.assertFalse((parties["seller"]["client_tax_id"] or "").strip())
        upsert_agent_billing_profile(
            self.org,
            self.agent,
            legal_name="QA Cierre",
            tax_id="20-99999990-6",
            tax_condition="monotributo",
            fiscal_address="Calle QA 1",
            email="qa.cierre@preview.invalid",
        )
        set_client_fields(
            self.org,
            operation_id,
            "buyer",
            client_legal_name="Comprador Fiscal QA",
            client_tax_id="20-12345678-6",
            client_tax_condition="consumidor_final",
            client_fiscal_address="Avenida Siempreviva 742",
        )
        set_party_invoice_amount(
            self.org,
            operation_id,
            "buyer",
            "3000",
            "USD",
            None,
            self.admin,
            notify=False,
        )
        client = app.test_client()
        self._login(client, "cierre_admin")
        buyer = client.get(f"/billing/operations/{operation_id}/buyer/prepare")
        seller = client.get(f"/billing/operations/{operation_id}/seller/prepare")
        self.assertEqual(buyer.status_code, 200)
        self.assertEqual(seller.status_code, 200)
        buyer_body = buyer.get_data(as_text=True)
        seller_body = seller.get_data(as_text=True)
        recipient = re.search(
            r'class="billing-recipient">(.*?)</dd>',
            buyer_body,
            re.S,
        )
        self.assertIsNotNone(recipient)
        self.assertIn("Comprador Fiscal QA", recipient.group(1))
        self.assertIn("20-12345678-6", recipient.group(1))
        self.assertNotIn(address, recipient.group(1))
        self.assertNotIn(f"CUIT de {address}", buyer_body)
        self.assertIn("Nombre / Razón social del vendedor", seller_body)
        self.assertIn("CUIT del vendedor", seller_body)
        self.assertIn("Condición frente al IVA del vendedor", seller_body)
        self.assertNotIn('name="client_email"', seller_body)
        self.assertNotIn(address, seller_body)
        self.assertNotIn(f"CUIT de {address}", seller_body)
        saved = client.post(
            f"/billing/operations/{operation_id}/seller/client-field",
            data={
                "recipient_form": "1",
                "client_legal_name": "Vendedor Fiscal QA",
                "client_tax_id": "27-33444555-9",
                "client_tax_condition": "responsable_inscripto",
                "client_fiscal_address": "Calle del Vendedor 10",
            },
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 302)
        self.assertIn(f"/billing/operations/{operation_id}/seller/prepare", saved.location)
        parties = self._parties(operation_id)
        self.assertEqual(parties["seller"]["client_legal_name"], "Vendedor Fiscal QA")
        self.assertEqual(parties["seller"]["client_tax_id"], "27-33444555-9")
        self.assertEqual(parties["seller"]["client_tax_condition"], "responsable_inscripto")
        self.assertEqual(parties["buyer"]["client_legal_name"], "Comprador Fiscal QA")
        self.assertEqual(get_property_record(property_id, self.org)["address"], address)
        stored = get_reservation(reservation["id"], self.org)
        operation = get_operation_record(operation_id, self.org)
        self.assertEqual(stored["reservation_status"], "pre_close")
        self.assertEqual(operation["status"], "preclose")
        self.assertEqual(operation["was_invoiced"], "no")
        self.assertEqual(self._issued_invoices(operation_id), 0)


def _usd_amount(totals, field):
    for item in totals["currencies"]:
        if item["currency"] == "USD":
            return float(item[field] or 0)
    return 0.0


if __name__ == "__main__":
    unittest.main()
