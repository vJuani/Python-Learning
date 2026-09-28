"""New organizations must not inherit another office's legal identity."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_legal_identity.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.organization_settings_repository import (
    get_organization_settings,
    update_organization_marketing_fields,
)
from modules.marketing_branding import (
    branding_prompt_block,
    require_legal_identity,
    resolve_marketing_branding,
)
from modules.marketing_context import MarketingError
from modules.marketing_service import start_marketing_batch
from web_app import app

_FORBIDDEN = (
    "RE/MAX Data House",
    "Mauro Marvisi",
    "CUCICBA 1762",
    "CMCPSI 5574",
)


class LegalIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="legal-identity-test")
        create_tables()
        cls.org = add_organization("Oficina Nueva")
        cls.agent_id = add_agent("Ana López", "Asesora", cls.org)
        cls.user_id = add_user(
            "legal_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            email="ana.legal@example.com",
        )
        cls.property_id = add_property(
            "Santamarina 1335",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent_id,
            property_type="apartment",
            listing_price=100000,
            listing_purpose="sale",
        )

    def _settings(self):
        return get_organization_settings(self.org)

    def test_new_organization_has_no_foreign_legal_identity(self):
        org_id = add_organization("Oficina Sin Legales")
        create_tables()
        settings = get_organization_settings(org_id)
        blob = " ".join(
            str(settings.get(key) or "")
            for key in (
                "marketing_brand_name",
                "legal_office_name",
                "legal_broker_name",
                "legal_broker_license",
                "legal_footer_line",
                "display_name",
            )
        )
        for forbidden in _FORBIDDEN:
            self.assertNotIn(forbidden, blob)
        self.assertEqual(settings.get("display_name"), "Oficina Sin Legales")
        self.assertFalse(settings.get("legal_broker_name"))
        self.assertFalse(settings.get("legal_broker_license"))
        self.assertFalse(settings.get("legal_footer_line"))

    def test_existing_legal_data_survives_schema_migrate(self):
        org_id = add_organization("Oficina Existente")
        update_organization_marketing_fields(
            org_id,
            marketing_brand_name="Oficina Norte",
            legal_broker_name="Ana López",
            legal_broker_license="MAT 100",
            legal_footer_line="Corredor Ana López MAT 100",
        )
        create_tables()
        settings = get_organization_settings(org_id)
        self.assertEqual(settings.get("marketing_brand_name"), "Oficina Norte")
        self.assertEqual(settings.get("legal_broker_name"), "Ana López")
        self.assertEqual(settings.get("legal_broker_license"), "MAT 100")
        self.assertEqual(settings.get("legal_footer_line"), "Corredor Ana López MAT 100")
        for forbidden in _FORBIDDEN:
            self.assertNotIn(forbidden, settings.get("legal_broker_name") or "")
            self.assertNotIn(forbidden, settings.get("legal_footer_line") or "")

    def test_marketing_does_not_invent_a_broker(self):
        branding = resolve_marketing_branding(
            {"display_name": "Oficina Nueva", "organization_id": self.org},
            organization_name="Oficina Nueva",
        )
        self.assertEqual(branding["brand_name"], "Oficina Nueva")
        self.assertEqual(branding["legal_broker_name"], "")
        self.assertEqual(branding["legal_broker_license"], "")
        self.assertEqual(branding["legal_footer_line"], "")
        self.assertFalse(branding["publishable"])
        prompt = branding_prompt_block(branding, language="es")
        for forbidden in _FORBIDDEN:
            self.assertNotIn(forbidden, prompt)

    def test_publication_is_blocked_without_legal_data(self):
        update_organization_marketing_fields(
            self.org,
            legal_broker_name="",
            legal_broker_license="",
            legal_footer_line="",
        )
        user = {
            "id": self.user_id,
            "role": ROLE_AGENT,
            "agent_id": self.agent_id,
            "organization_id": self.org,
        }
        with self.assertRaises(MarketingError) as caught:
            start_marketing_batch(
                self.org,
                user,
                property_id=self.property_id,
                prompt="historia",
                formats=["story"],
                count=1,
            )
        self.assertEqual(caught.exception.message_key, "settings_legal_publish_blocked")

    def test_publication_proceeds_when_legal_data_is_present(self):
        update_organization_marketing_fields(
            self.org,
            marketing_brand_name="Oficina Norte",
            legal_broker_name="Ana López",
            legal_broker_license="MAT 100",
            legal_footer_line="Corredor Ana López MAT 100",
        )
        settings = self._settings()
        require_legal_identity(settings)
        branding = resolve_marketing_branding(
            settings,
            organization_name="Oficina Nueva",
        )
        self.assertTrue(branding["publishable"])
        self.assertEqual(branding["legal_broker_name"], "Ana López")
        self.assertIn("MAT 100", branding["legal_footer_line"])
        user = {
            "id": self.user_id,
            "role": ROLE_AGENT,
            "agent_id": self.agent_id,
            "organization_id": self.org,
        }
        with patch(
            "modules.marketing_service.parse_marketing_request",
            side_effect=RuntimeError("past-legal-guard"),
        ):
            with self.assertRaises(RuntimeError) as caught:
                start_marketing_batch(
                    self.org,
                    user,
                    property_id=self.property_id,
                    prompt="historia",
                    formats=["story"],
                    count=1,
                )
        self.assertEqual(str(caught.exception), "past-legal-guard")

    def test_settings_shows_missing_legal_data_without_placeholders(self):
        admin_id = add_user(
            "legal_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            self.org,
            first_name="Admin",
            email="admin.legal@example.com",
        )
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = admin_id
        page = client.get("/settings/organization")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Configuración legal incompleta", html)
        self.assertIn("Completar", html)
        self.assertIn('href="#settings-legal"', html)
        self.assertNotIn("Mauro Marvisi", html)
        self.assertNotIn("CUCICBA 1762", html)
        self.assertNotIn("RE/MAX Data House", html)

        saved = os.environ.get("ARCA_ENV")
        os.environ.pop("ARCA_ENV", None)
        try:
            arca = client.get("/settings/arca")
        finally:
            if saved is None:
                os.environ.pop("ARCA_ENV", None)
            else:
                os.environ["ARCA_ENV"] = saved
        self.assertEqual(arca.status_code, 200)
        arca_html = arca.get_data(as_text=True)
        self.assertIn("ARCA no está configurado", arca_html)
        self.assertNotIn("BEGIN PRIVATE KEY", arca_html)
        self.assertNotIn("BEGIN CERTIFICATE", arca_html)
