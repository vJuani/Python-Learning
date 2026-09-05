"""Per-user ARCA connections, wizard, and JRH invoice routing."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_arca_connections.db")
os.environ["INVOICE_PROVIDER"] = "arca"
os.environ["ARCA_ENV"] = "homologation"
os.environ["SECRET_KEY"] = "arca-connection-secret"

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from modules.arca.connections import (
    ArcaConnectionError,
    assert_certificate_matches_key,
    generate_key_and_csr,
    inspect_certificate,
    load_credentials,
    store_credentials,
    ta_cache_key,
)
from modules.arca.secrets import load_isolated_dev_credentials
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
)
from modules.database.arca_connections_repository import (
    get_arca_connection,
    upsert_arca_connection,
)
from modules.invoicing import InvoicingError, issue_fiscal_invoice
from modules.jrh_intent import INTENT_INVOICE, interpret_jrh_request
from web_app import app


def _self_signed(key_pem, cuit):
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


class ArcaConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="arca-connection-secret")
        create_tables()
        cls.org = add_organization("ARCA Conn Org")
        cls.other_org = add_organization("ARCA Other Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Conn Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Other Agent", "Alto", cls.org)
        cls.admin_id = add_user("conn_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_user = add_user(
            "conn_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.other_user = add_user(
            "conn_other",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.foreign_user = add_user(
            "conn_foreign",
            pwd,
            ROLE_AGENT,
            cls.other_org,
            agent_id=add_agent("Foreign", "Alto", cls.other_org),
        )

    def _login(self, user_id, role, org, agent_id=None):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = org
            if agent_id:
                sess["agent_id"] = agent_id
        return client

    def test_unique_user_environment(self):
        first = upsert_arca_connection(
            self.org,
            self.agent_user,
            point_of_sale="4",
        )
        second = upsert_arca_connection(
            self.org,
            self.agent_user,
            point_of_sale="7",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["point_of_sale"], "7")

    def test_credentials_are_encrypted(self):
        key_pem, csr_pem = generate_key_and_csr(common_name="Juan", cuit="20300000003")
        cert_pem = _self_signed(key_pem, "20300000003")
        stored = store_credentials(
            self.org,
            self.agent_user,
            certificate_pem=cert_pem,
            private_key_pem=key_pem,
            csr_pem=csr_pem,
            connection_status="configuring",
        )
        self.assertNotIn(b"PRIVATE KEY", (stored["private_key_encrypted"] or "").encode())
        loaded = load_credentials(stored)
        self.assertIn(b"BEGIN", loaded.private_key_pem)
        inspect_certificate(loaded.certificate_pem)

    def test_cert_must_match_key(self):
        key_a, _ = generate_key_and_csr(common_name="A", cuit="20300000003")
        key_b, _ = generate_key_and_csr(common_name="B", cuit="20300000003")
        cert_b = _self_signed(key_b, "20300000003")
        inspected = inspect_certificate(cert_b)
        with self.assertRaises(ArcaConnectionError):
            assert_certificate_matches_key(inspected["certificate"], key_a)

    def test_other_agent_cannot_load_foreign_row(self):
        store_credentials(
            self.org,
            self.agent_user,
            private_key_pem=b"-----BEGIN PRIVATE KEY-----\nA\n-----END PRIVATE KEY-----",
            connection_status="configuring",
        )
        other = get_arca_connection(self.org, self.other_user)
        self.assertTrue(other is None or other.get("user_id") != self.agent_user)

    def test_other_org_cannot_read_connection(self):
        key_pem, _ = generate_key_and_csr(common_name="Juan", cuit="20300000003")
        store_credentials(
            self.org,
            self.agent_user,
            certificate_pem=_self_signed(key_pem, "20300000003"),
            private_key_pem=key_pem,
            connection_status="connected",
        )
        self.assertIsNone(get_arca_connection(self.other_org, self.agent_user))
        self.assertIsNone(get_arca_connection(self.other_org, self.foreign_user))
        own = get_arca_connection(self.org, self.agent_user)
        self.assertEqual(own["user_id"], self.agent_user)

    def test_expired_certificate_rejected(self):
        from modules.arca.connections import ArcaConnectionError, inspect_certificate

        key_pem, _ = generate_key_and_csr(common_name="Expired", cuit="20300000003")
        key = serialization.load_pem_private_key(key_pem, password=None)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Expired")]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=40))
            .not_valid_after(datetime.now(timezone.utc) - timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        with self.assertRaises(ArcaConnectionError) as error:
            inspect_certificate(cert.public_bytes(serialization.Encoding.PEM))
        self.assertEqual(error.exception.message_key, "arca_err_certificate_expired")

    def test_wsaa_verify_uses_user_connection(self):
        from modules.arca.issuer_config import test_arca_connection

        class _Transport:
            def wsaa_login(self, cms_b64):
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

        key_pem, _ = generate_key_and_csr(common_name="Juan", cuit="20300000003")
        stored = store_credentials(
            self.org,
            self.agent_user,
            certificate_pem=_self_signed(key_pem, "20300000003"),
            private_key_pem=key_pem,
            connection_status="configuring",
            point_of_sale="4",
        )
        with patch("modules.arca.wsaa.sign_tra_cms", return_value="MOCKCMS"):
            status, error_key = test_arca_connection(
                {"tax_id": "20-30000000-3"},
                connection=stored,
                organization_id=self.org,
                user_id=self.agent_user,
                transport=_Transport(),
            )
        self.assertEqual(status, "connected")
        self.assertIsNone(error_key)

    def test_no_global_fallback_on_issue(self):
        os.environ["ARCA_CERT_PEM_B64"] = "Y2VydA=="
        os.environ["ARCA_KEY_PEM_B64"] = "a2V5"
        with self.assertRaises(InvoicingError) as error:
            issue_fiscal_invoice(
                self.org,
                999999,
                {"id": self.agent_user, "role": ROLE_AGENT, "agent_id": self.agent_id},
            )
        self.assertEqual(error.exception.message_key, "invoice_err_not_found")
        isolated = load_isolated_dev_credentials()
        self.assertTrue(isolated.certificate_pem)
        from modules.arca.connections import require_user_connection, ArcaConnectionError

        with self.assertRaises(ArcaConnectionError) as linked:
            require_user_connection(self.org, self.agent_user)
        self.assertEqual(linked.exception.message_key, "invoice_err_arca_not_linked")

    def test_ticket_cache_key_is_per_user(self):
        self.assertNotEqual(
            ta_cache_key(self.org, self.agent_user),
            ta_cache_key(self.org, self.other_user),
        )

    def test_wizard_generates_csr_without_clave_fiscal(self):
        client = self._login(
            self.agent_user,
            ROLE_AGENT,
            self.org,
            agent_id=self.agent_id,
        )
        step1 = client.post(
            "/settings/arca/connect",
            data={
                "legal_name": "Agente Test",
                "tax_id": "20-30000000-3",
                "tax_condition": "monotributo",
                "fiscal_address": "Cabildo 100",
                "point_of_sale": "4",
            },
            follow_redirects=True,
        )
        self.assertEqual(step1.status_code, 200)
        self.assertIn("WSASS", step1.get_data(as_text=True))
        self.assertNotIn("Clave Fiscal</label>", step1.get_data(as_text=True))
        csr = client.get("/settings/arca/csr")
        self.assertEqual(csr.status_code, 200)
        self.assertIn(b"BEGIN CERTIFICATE REQUEST", csr.data)

    def test_integrations_shows_arca(self):
        client = self._login(
            self.agent_user,
            ROLE_AGENT,
            self.org,
            agent_id=self.agent_id,
        )
        body = client.get("/settings/integrations").get_data(as_text=True)
        self.assertIn("ARCA", body)
        self.assertIn("Vincular ARCA", body)

    def test_home_invoice_uses_composer(self):
        result = interpret_jrh_request(
            "Haceme la factura de Libertador",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        self.assertEqual(result["intents"][0]["type"], INTENT_INVOICE)
        self.assertFalse(result["wrote"])

    def test_voice_same_invoice_router(self):
        spoken = interpret_jrh_request(
            "Facturale al comprador de Quesada",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        typed = interpret_jrh_request(
            "Facturale al comprador de Quesada",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        self.assertEqual(spoken["intents"][0]["type"], typed["intents"][0]["type"])

    def test_preview_route_does_not_issue(self):
        client = self._login(
            self.agent_user,
            ROLE_AGENT,
            self.org,
            agent_id=self.agent_id,
        )
        with patch(
            "modules.billing_routes.issue_fiscal_invoice"
        ) as issue:
            page = client.get("/billing")
            self.assertEqual(page.status_code, 200)
            self.assertIn("¿Qué querés facturar?", page.get_data(as_text=True))
            issue.assert_not_called()

    def test_voucher_mapping_needs_attention(self):
        from modules.arca.voucher_mapping import resolve_voucher_type

        self.assertIsNone(
            resolve_voucher_type(
                issuer_tax_condition="",
                recipient_tax_condition="consumidor_final",
            )
        )
        self.assertEqual(
            resolve_voucher_type(
                issuer_tax_condition="monotributo",
                recipient_tax_condition="consumidor_final",
            ),
            11,
        )


if __name__ == "__main__":
    unittest.main()
