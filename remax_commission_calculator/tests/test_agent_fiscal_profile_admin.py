"""Blocking fix: Staff manages the agent fiscal profile used by billing."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_fiscal_profile_admin.db"
)

from modules.agent_account import create_movement
from modules.agent_account_charges import VAT_MODE_ADD
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_billing_profile,
    upsert_agent_billing_profile,
    upsert_billing_issuer_profile,
)
from modules.invoicing import (
    ISSUER_MODE_OFFICE,
    create_draft_for_charge,
    get_invoice,
)
from web_app import app


class AgentFiscalProfileAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "agent-fiscal-admin-secret"
        create_tables()

        cls.org = add_organization("Fiscal Profile Org")
        cls.other_org = add_organization("Other Fiscal Org")
        cls.agent = add_agent("José Fiscal", "Alto", cls.org)
        cls.incomplete_agent = add_agent(
            "Agente Sin Perfil", "Junior", cls.org
        )
        cls.other_agent = add_agent(
            "Other Organization Agent", "Alto", cls.other_org
        )
        password = hash_password("Password1")
        cls.admin = add_user(
            "fiscal_admin", password, ROLE_ADMIN, cls.org
        )
        cls.agent_user = add_user(
            "fiscal_agent",
            password,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
            email="user-prefill@example.com",
        )
        cls.other_admin = add_user(
            "fiscal_other_admin",
            password,
            ROLE_ADMIN,
            cls.other_org,
        )
        cls.other_agent_user = add_user(
            "fiscal_other_agent",
            password,
            ROLE_AGENT,
            cls.other_org,
            agent_id=cls.other_agent,
        )
        cls.password = "Password1"
        cls.issuer = upsert_billing_issuer_profile(
            cls.org,
            issuer_type="organization",
            display_name="Fiscal Office",
            legal_name="Fiscal Office SA",
            tax_id="30-71234567-8",
            tax_condition="responsable_inscripto",
            fiscal_address="Oficina Fiscal 123",
            email="office@example.com",
            is_default=True,
        )

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username="fiscal_admin"):
        self.client.get("/logout", follow_redirects=True)
        return self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def _profile_payload(self, **overrides):
        payload = {
            "legal_name": "José Fiscal Servicios",
            "tax_id": "20301234567",
            "tax_condition": "monotributo",
            "fiscal_address": "Calle Fiscal 456",
            "email": "billing-agent@example.com",
        }
        payload.update(overrides)
        return payload

    def _charge(self, agent_id=None):
        return create_movement(
            self.org,
            agent_id or self.agent,
            {
                "charge_category": "fee",
                "currency": "USD",
                "amount": "65",
                "vat_mode": VAT_MODE_ADD,
                "vat_rate": "21",
                "billing_period": "Septiembre 2026",
                "movement_date": "2026-09-03",
            },
            created_by_user_id=self.admin,
        )

    def test_staff_can_edit_agent_fiscal_data(self):
        self._login()
        response = self.client.post(
            f"/billing/agent-profile/{self.agent}",
            data=self._profile_payload(),
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        profile = get_agent_billing_profile(self.org, self.agent)
        self.assertEqual(profile["legal_name"], "José Fiscal Servicios")
        self.assertEqual(profile["email"], "billing-agent@example.com")

    def test_other_organization_agent_is_not_accessible(self):
        self._login("fiscal_other_agent")
        response = self.client.get(
            f"/billing/agent-profile/{self.agent}"
        )
        self.assertIn(response.status_code, (302, 403, 404))

    def test_other_organization_staff_cannot_edit_agent(self):
        self._login("fiscal_other_admin")
        response = self.client.post(
            f"/billing/agent-profile/{self.agent}",
            data=self._profile_payload(),
        )
        self.assertEqual(response.status_code, 404)

    def test_cuit_is_normalized(self):
        self._login()
        self.client.post(
            f"/billing/agent-profile/{self.agent}",
            data=self._profile_payload(tax_id="20301234567"),
        )
        profile = get_agent_billing_profile(self.org, self.agent)
        self.assertEqual(profile["tax_id"], "20-30123456-7")

    def test_invoice_works_after_completing_profile(self):
        charge = self._charge()
        self._login()
        self.client.post(
            f"/billing/agent-profile/{self.agent}",
            data=self._profile_payload(),
        )
        invoice = create_draft_for_charge(
            self.org,
            charge["id"],
            {"id": self.admin, "role": ROLE_ADMIN},
            issuer_mode=ISSUER_MODE_OFFICE,
            issuer_profile_id=self.issuer["id"],
        )
        self.assertEqual(invoice["recipient_name"], "José Fiscal Servicios")

    def test_compact_alert_lists_missing_fields_once(self):
        charge = self._charge(self.incomplete_agent)
        self._login()
        response = self.client.get(
            f"/billing/agent-account-charges/{charge['id']}/prepare",
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        self.assertIn("Perfil fiscal incompleto. Faltan:", body)
        self.assertEqual(body.count("Perfil fiscal incompleto. Faltan:"), 1)
        self.assertIn("Razón social", body)
        self.assertIn("CUIT", body)
        self.assertIn("Email", body)

    def test_compact_alert_cta_targets_correct_agent(self):
        charge = self._charge(self.incomplete_agent)
        self._login()
        response = self.client.get(
            f"/billing/agent-account-charges/{charge['id']}/prepare",
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        self.assertIn(
            f"/billing/agent-profile/{self.incomplete_agent}",
            body,
        )
        self.assertIn("#datos-fiscales", body)

    def test_invoice_keeps_snapshot_after_profile_change(self):
        upsert_agent_billing_profile(
            self.org,
            self.agent,
            legal_name="Snapshot Original",
            tax_id="20-30123456-7",
            tax_condition="monotributo",
            fiscal_address="Original 123",
            email="original@example.com",
        )
        invoice = create_draft_for_charge(
            self.org,
            self._charge()["id"],
            {"id": self.admin, "role": ROLE_ADMIN},
            issuer_mode=ISSUER_MODE_OFFICE,
            issuer_profile_id=self.issuer["id"],
        )
        upsert_agent_billing_profile(
            self.org,
            self.agent,
            legal_name="Snapshot Changed",
            tax_id="27-11223344-9",
            tax_condition="responsable_inscripto",
            fiscal_address="Changed 999",
            email="changed@example.com",
        )
        stored = get_invoice(self.org, invoice["id"])
        self.assertEqual(stored["recipient_name"], "Snapshot Original")
        self.assertEqual(stored["recipient_tax_id"], "20-30123456-7")
        self.assertEqual(stored["recipient_address"], "Original 123")

    def test_agent_detail_renders_fiscal_section(self):
        self._login()
        response = self.client.get(f"/agents/{self.agent}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Datos fiscales".encode(), response.data)
        self.assertIn(b"datos-fiscales", response.data)

    def test_create_tables_remains_idempotent(self):
        create_tables(create_backup=False)
        create_tables(create_backup=False)

    def test_agent_can_view_but_cannot_edit_profile(self):
        self._login("fiscal_agent")
        self.assertEqual(
            self.client.get(
                f"/billing/agent-profile/{self.agent}"
            ).status_code,
            200,
        )
        self.assertIn(
            self.client.post(
                f"/billing/agent-profile/{self.agent}",
                data=self._profile_payload(),
            ).status_code,
            (302, 403),
        )

    def test_empty_profile_prefills_agent_name_and_user_email(self):
        fresh_agent = add_agent("Prefill Agent", "Alto", self.org)
        add_user(
            f"prefill_agent_{fresh_agent}",
            hash_password(self.password),
            ROLE_AGENT,
            self.org,
            agent_id=fresh_agent,
            email="prefill@example.com",
        )
        self._login()
        response = self.client.get(
            f"/billing/agent-profile/{fresh_agent}"
        )
        body = response.get_data(as_text=True)
        self.assertIn('value="Prefill Agent"', body)
        self.assertIn('value="prefill@example.com"', body)

    def test_invalid_billing_email_is_rejected(self):
        fresh_agent = add_agent("Invalid Email Agent", "Alto", self.org)
        self._login()
        response = self.client.post(
            f"/billing/agent-profile/{fresh_agent}",
            data=self._profile_payload(email="not-an-email"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(
            get_agent_billing_profile(self.org, fresh_agent)
        )
        self.assertIn(
            "Falta el email del agente".encode(),
            response.data,
        )

    def test_profile_next_rejects_backslash_redirect(self):
        fresh_agent = add_agent("Safe Redirect Agent", "Alto", self.org)
        self._login()
        response = self.client.post(
            f"/billing/agent-profile/{fresh_agent}",
            query_string={"next": r"/\evil.example"},
            data=self._profile_payload(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f"/billing/agent-profile/{fresh_agent}",
            response.headers["Location"],
        )


if __name__ == "__main__":
    unittest.main()
