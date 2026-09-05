"""
ARCA provider integration tests (mock transport — no real AFIP calls).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_arca_integration.db"
)
os.environ["INVOICE_PROVIDER"] = "arca"
os.environ["ARCA_ENV"] = "homologation"
os.environ["SECRET_KEY"] = "test-arca-secret"

from modules.arca.secrets import ArcaCredentials

from modules.arca.client import ArcaClient
from modules.arca.wsaa import TicketAcceso, build_tra_xml, sign_tra_cms
from modules.arca.wsfev1 import CaeIssueResult
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
    set_operation_party_client_fields,
    set_operation_party_billing_enabled,
    update_agent_arca_config,
    update_issuer_arca_config,
    upsert_agent_billing_profile,
    upsert_billing_issuer_profile,
)
from modules.database.connection import get_connection
from modules.invoicing import (
    SIDE_BUYER,
    SIDE_SELLER,
    confirm_draft,
    create_draft_for_side,
    issue_fiscal_invoice,
    retry_error_invoice,
)
from web_app import app


class MockArcaTransport:
    def __init__(self):
        self.last_voucher = 41
        self.issue_count = 0
        self.auth_fail = False

    def wsaa_login(self, cms_b64: str) -> str:
        if self.auth_fail:
            raise ValueError("auth failed")
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

    def wsfe_call(
        self,
        action: str,
        envelope: str,
        ticket: TicketAcceso,
        cuit: str,
    ) -> str:
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
        next_num = self.last_voucher + 1
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


class ArcaIntegrationTests(unittest.TestCase):
    _voucher_seq = 1000

    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-arca"
        create_tables()

        cls.org = add_organization("ARCA Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Agent ARCA", "Alto", cls.org)
        cls.admin_id = add_user(
            "arca_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
        )
        cls.user_agent = add_user(
            "arca_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.property_id = add_property(
            "Test 123",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
        )

        upsert_agent_billing_profile(
            cls.org,
            cls.agent_id,
            legal_name="Agente ARCA",
            tax_id="20-30000000-3",
            tax_condition="monotributo",
            fiscal_address="Dir Agente",
            email="a@test.com",
        )
        cls.broker = upsert_billing_issuer_profile(
            cls.org,
            issuer_type="broker",
            display_name="Broker",
            legal_name="Broker SA",
            tax_id="20-11223344-5",
            tax_condition="responsable_inscripto",
            fiscal_address="Dir Broker",
            is_default=True,
        )
        update_agent_arca_config(
            cls.org,
            cls.agent_id,
            arca_connection_status="connected",
            arca_point_of_sale="5",
            arca_environment="homologation",
            arca_certificate_ref=f"agent:{cls.agent_id}",
        )
        update_issuer_arca_config(
            cls.org,
            cls.broker["id"],
            arca_connection_status="connected",
            arca_point_of_sale="5",
            arca_environment="homologation",
            arca_certificate_ref=f"issuer:{cls.broker['id']}",
        )

    def setUp(self):
        os.environ["INVOICE_PROVIDER"] = "arca"
        os.environ["ARCA_ENV"] = "homologation"
        type(self)._voucher_seq += 50
        connection = get_connection()
        connection.execute("DELETE FROM arca_ta_cache")
        connection.execute("DELETE FROM arca_connections")
        connection.commit()
        connection.close()

    def _make_transport(self, **kwargs):
        transport = MockArcaTransport()
        transport.last_voucher = type(self)._voucher_seq
        for key, value in kwargs.items():
            setattr(transport, key, value)
        return transport

    def _mock_credentials(self):
        return ArcaCredentials(
            certificate_pem=b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
            private_key_pem=b"-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n",
        )

    def _self_signed_cert(self, key_pem, cuit):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509.oid import NameOID

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
        return cert.public_bytes(serialization.Encoding.PEM)

    def _link_user(self, user_id, cuit, pos="5"):
        from modules.arca.connections import generate_key_and_csr, store_credentials

        key_pem, _csr = generate_key_and_csr(common_name="JRH Test", cuit=cuit)
        store_credentials(
            self.org,
            user_id,
            certificate_pem=self._self_signed_cert(key_pem, cuit),
            private_key_pem=key_pem,
            connection_status="connected",
            point_of_sale=pos,
            last_verified_at=datetime.now(timezone.utc).isoformat(),
        )

    def _arca_cred_patches(self):
        return (
            patch(
                "modules.arca.wsaa.sign_tra_cms",
                return_value="MOCKCMS",
            ),
        )

    def _agent_user(self):
        return {
            "id": self.user_agent,
            "role": ROLE_AGENT,
            "agent_id": self.agent_id,
        }

    def _admin_user(self):
        return {
            "id": self.admin_id,
            "role": ROLE_ADMIN,
            "agent_id": None,
        }

    def _ready_invoice(self, *, side=SIDE_BUYER, office=False):
        op_id = add_operation(
            "28/08/2026",
            self.agent_id,
            self.property_id,
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
        )
        ensure_parties_for_operation(self.org, op_id)
        for s in (SIDE_BUYER, SIDE_SELLER):
            set_operation_party_client_fields(
                self.org,
                op_id,
                s,
                client_legal_name="Cliente Test",
                client_tax_id="20-99887766-5",
                client_tax_condition="consumidor_final",
                client_fiscal_address="Cliente 1",
            )
            set_operation_party_billing_enabled(
                self.org,
                op_id,
                s,
                enabled=True,
                by_user_id=self.admin_id,
            )
        from modules.invoicing import set_party_invoice_amount

        set_party_invoice_amount(
            self.org,
            op_id,
            side,
            "15000",
            "ARS",
            None,
            self.admin_id,
            notify=False,
        )
        if office:
            inv = create_draft_for_side(
                self.org,
                op_id,
                side,
                self._admin_user(),
                issuer_mode="office",
                issuer_profile_id=self.broker["id"],
            )
        else:
            inv = create_draft_for_side(
                self.org,
                op_id,
                side,
                self._agent_user(),
                issuer_mode="agent",
            )
        confirm_draft(self.org, inv["id"], self._admin_user())
        from modules.invoicing import get_invoice

        inv = get_invoice(self.org, inv["id"])
        profile = dict(
            self._agent_profile()
            if not office
            else self._broker_profile()
        )
        profile["arca_connection_status"] = "connected"
        profile["arca_point_of_sale"] = "5"
        profile["arca_environment"] = "homologation"
        return inv, profile

    def _agent_profile(self):
        from modules.database import get_agent_billing_profile

        p = get_agent_billing_profile(self.org, self.agent_id)
        p["issuer_key"] = f"agent:{self.agent_id}"
        p["agent_id"] = self.agent_id
        return p

    def _broker_profile(self):
        p = dict(self.broker)
        p["issuer_key"] = f"issuer:{self.broker['id']}"
        return p

    def test_auth_success_mock(self):
        transport = self._make_transport()
        client = ArcaClient(transport=transport)
        profile = self._agent_profile()
        with patch(
            "modules.arca.wsaa.sign_tra_cms",
            return_value="MOCKCMS",
        ):
            ticket = client.authenticate(
                profile,
                {"issuer_tax_id": profile["tax_id"]},
                credentials=self._mock_credentials(),
            )
        self.assertEqual(ticket.token, "MOCK-TOKEN")

    def test_auth_failure(self):
        transport = self._make_transport(auth_fail=True)
        client = ArcaClient(transport=transport)
        profile = self._agent_profile()
        with patch(
            "modules.arca.wsaa.sign_tra_cms",
            return_value="MOCKCMS",
        ):
            with self.assertRaises(ValueError):
                client.authenticate(
                    profile,
                    {"issuer_tax_id": profile["tax_id"]},
                    credentials=self._mock_credentials(),
                )

    def test_last_voucher(self):
        transport = self._make_transport()
        client = ArcaClient(transport=transport)
        profile = self._agent_profile()
        inv = {"issuer_tax_id": profile["tax_id"], "total_amount": 100}
        with patch(
            "modules.arca.wsaa.sign_tra_cms",
            return_value="MOCKCMS",
        ):
            ticket = client.authenticate(
                profile,
                inv,
                credentials=self._mock_credentials(),
            )
        from modules.arca.wsfev1 import get_last_authorized_voucher

        last = get_last_authorized_voucher(
            ticket=ticket,
            cuit="20300000003",
            point_of_sale=5,
            voucher_type=11,
            transport=transport,
        )
        self.assertEqual(last, transport.last_voucher)

    def test_issue_success_agent_buyer(self):
        self._link_user(self.user_agent, "20300000003")
        inv, _profile = self._ready_invoice(side=SIDE_BUYER)
        transport = self._make_transport()
        patches = self._arca_cred_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        issued = issue_fiscal_invoice(
            self.org,
            inv["id"],
            self._agent_user(),
            transport=transport,
        )
        self.assertEqual(issued["status"], "issued")
        self.assertEqual(issued["provider"], "arca")
        self.assertTrue(issued["cae"])

    def test_issue_success_broker_seller(self):
        self._link_user(self.admin_id, "20112233445")
        inv, _profile = self._ready_invoice(
            side=SIDE_SELLER,
            office=True,
        )
        transport = self._make_transport()
        patches = self._arca_cred_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        issued = issue_fiscal_invoice(
            self.org,
            inv["id"],
            self._admin_user(),
            transport=transport,
        )
        self.assertEqual(issued["status"], "issued")
        self.assertEqual(issued["side"], "seller")

    def test_double_submit_blocked(self):
        self._link_user(self.user_agent, "20300000003")
        inv, _ = self._ready_invoice()
        transport = self._make_transport()
        patches = self._arca_cred_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        issue_fiscal_invoice(
            self.org,
            inv["id"],
            self._agent_user(),
            transport=transport,
        )
        from modules.invoicing import InvoicingError

        with self.assertRaises(InvoicingError):
            issue_fiscal_invoice(
                self.org,
                inv["id"],
                self._agent_user(),
                transport=transport,
            )

    def test_issue_error_sets_status(self):
        self._link_user(self.user_agent, "20300000003")
        inv, _ = self._ready_invoice()
        transport = self._make_transport(auth_fail=True)
        patches = self._arca_cred_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        from modules.invoicing import InvoicingError, get_invoice

        with self.assertRaises(InvoicingError):
            issue_fiscal_invoice(
                self.org,
                inv["id"],
                self._agent_user(),
                transport=transport,
            )
        again = get_invoice(self.org, inv["id"])
        self.assertEqual(again["status"], "error")

    def test_retry_flow(self):
        self._link_user(self.user_agent, "20300000003")
        inv, _ = self._ready_invoice()
        transport = self._make_transport(auth_fail=True)
        patches = self._arca_cred_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        from modules.invoicing import InvoicingError, get_invoice

        with self.assertRaises(InvoicingError):
            issue_fiscal_invoice(
                self.org,
                inv["id"],
                self._agent_user(),
                transport=transport,
            )
        retry_error_invoice(
            self.org,
            inv["id"],
            self._agent_user(),
        )
        draft = get_invoice(self.org, inv["id"])
        self.assertEqual(draft["status"], "draft")
        confirm_draft(
            self.org,
            inv["id"],
            self._agent_user(),
        )
        transport.auth_fail = False
        issued = issue_fiscal_invoice(
            self.org,
            inv["id"],
            self._agent_user(),
            transport=transport,
        )
        self.assertEqual(issued["status"], "issued")

    def test_issue_without_connection_blocked(self):
        inv, _ = self._ready_invoice()
        from modules.invoicing import InvoicingError

        with self.assertRaises(InvoicingError) as error:
            issue_fiscal_invoice(
                self.org,
                inv["id"],
                self._agent_user(),
            )
        self.assertEqual(error.exception.message_key, "invoice_err_arca_not_linked")

    def test_agent_cannot_use_other_user_connection(self):
        self._link_user(self.admin_id, "20112233445")
        inv, _ = self._ready_invoice()
        from modules.invoicing import InvoicingError

        with self.assertRaises(InvoicingError) as error:
            issue_fiscal_invoice(
                self.org,
                inv["id"],
                self._agent_user(),
            )
        self.assertEqual(error.exception.message_key, "invoice_err_arca_not_linked")

    def test_staff_cannot_use_agent_certificate(self):
        self._link_user(self.user_agent, "20300000003")
        inv, _ = self._ready_invoice(office=True)
        from modules.invoicing import InvoicingError

        with self.assertRaises(InvoicingError) as error:
            issue_fiscal_invoice(
                self.org,
                inv["id"],
                self._admin_user(),
            )
        self.assertEqual(error.exception.message_key, "invoice_err_arca_not_linked")

    def test_cuit_mismatch_blocks(self):
        self._link_user(self.user_agent, "20112233445")
        inv, _ = self._ready_invoice()
        from modules.invoicing import InvoicingError

        with self.assertRaises(InvoicingError) as error:
            issue_fiscal_invoice(
                self.org,
                inv["id"],
                self._agent_user(),
            )
        self.assertEqual(error.exception.message_key, "arca_err_cuit_mismatch")

    def test_usd_without_rate_blocks(self):
        from modules.arca.validation import validate_fiscal_issue

        self._link_user(self.user_agent, "20300000003")
        inv, profile = self._ready_invoice()
        inv["currency"] = "USD"
        inv["exchange_rate"] = None
        from modules.database.arca_connections_repository import get_arca_connection

        connection = get_arca_connection(self.org, self.user_agent)
        result = validate_fiscal_issue(inv, profile, connection=connection)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_key, "invoice_err_arca_exchange_rate_missing")

    def test_voucher_undetermined_blocks(self):
        from modules.arca.validation import validate_fiscal_issue
        from modules.database.arca_connections_repository import get_arca_connection

        self._link_user(self.user_agent, "20300000003")
        inv, profile = self._ready_invoice()
        profile["tax_condition"] = ""
        inv["issuer_tax_condition"] = ""
        connection = get_arca_connection(self.org, self.user_agent)
        result = validate_fiscal_issue(inv, profile, connection=connection)
        self.assertFalse(result.is_valid)
        self.assertIn("invoice_err_arca_voucher_undetermined", result.missing)

    def test_ticket_cache_is_per_user(self):
        from modules.arca.connections import ta_cache_key
        from modules.database.arca_repository import store_cached_ta, get_cached_ta
        from modules.arca.wsaa import TicketAcceso

        ticket = TicketAcceso(
            token="A-TOKEN",
            sign="A-SIGN",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            service="wsfe",
            cuit="20300000003",
            environment="homologation",
        )
        store_cached_ta(ta_cache_key(self.org, self.user_agent), ticket)
        self.assertIsNotNone(get_cached_ta(ta_cache_key(self.org, self.user_agent)))
        self.assertIsNone(get_cached_ta(ta_cache_key(self.org, self.admin_id)))

    def test_pdf_uses_real_fiscal_fields_without_qr(self):
        self._link_user(self.user_agent, "20300000003")
        inv, _ = self._ready_invoice()
        transport = self._make_transport()
        patches = self._arca_cred_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        issued = issue_fiscal_invoice(
            self.org,
            inv["id"],
            self._agent_user(),
            transport=transport,
        )
        from modules.invoice_provider import ArcaInvoiceProvider

        pdf = ArcaInvoiceProvider().generate_fiscal_pdf(issued)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(issued.get("cae"))
        self.assertTrue(issued.get("issuer_tax_id"))
        self.assertNotIn("QR", ArcaInvoiceProvider.generate_fiscal_pdf.__doc__ or "")
        source = (
            Path(__file__).resolve().parents[1]
            / "modules"
            / "invoice_provider.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("qr_code", source.lower())
        self.assertEqual(issued["operation_id"], inv["operation_id"])


if __name__ == "__main__":
    unittest.main()
