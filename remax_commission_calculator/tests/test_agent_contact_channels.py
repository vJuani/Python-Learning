"""Agent personal WhatsApp / Instagram channels. Isolated temp DB."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_agent_contact.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)

from modules.agent_branding import get_agent_branding
from modules.agent_contact_channels import (
    AgentContactError,
    link_agent_instagram,
    link_agent_whatsapp,
    publishable_agent_contacts,
    set_instagram_marketing_enabled,
    set_whatsapp_marketing_enabled,
    unlink_agent_instagram,
    unlink_agent_whatsapp,
)
from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.marketing_copy import summarize_listing_copy
from modules.openai_image_service import build_marketing_image_prompt
from web_app import app


class AgentContactChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="agent-contact-tests")
        create_tables()
        cls.org = add_organization("Contact Org")
        cls.agent_id = add_agent("José Barreiro", "Alto", cls.org)
        cls.user_id = add_user(
            "jose_contact",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="José",
            last_name="Barreiro",
            phone="1140000000",
            email="jose.contact@example.com",
        )

    def setUp(self):
        unlink_agent_whatsapp(self.agent_id, self.org)
        unlink_agent_instagram(self.agent_id, self.org)

    def test_users_phone_is_not_publishable(self):
        branding = get_agent_branding(self.agent_id, self.org, agent_login_only=True)
        self.assertEqual(branding["phone"], "1140000000")
        self.assertFalse(branding["whatsapp_enabled"])
        self.assertIsNone(branding["whatsapp"])
        self.assertFalse(branding["instagram_enabled"])
        self.assertIsNone(branding["instagram"])

    def test_case_a_both_on(self):
        link_agent_whatsapp(self.agent_id, self.org, "1131704333", enable_marketing=True)
        link_agent_instagram(self.agent_id, self.org, "josebarreiro", enable_marketing=True)
        branding = get_agent_branding(self.agent_id, self.org)
        self.assertEqual(branding["whatsapp"], "+54 9 11 3170 4333")
        self.assertTrue(branding["whatsapp_enabled"])
        self.assertEqual(branding["instagram"], "@josebarreiro")
        self.assertTrue(branding["instagram_enabled"])
        copy = summarize_listing_copy({}, branding, language="es")
        self.assertEqual(copy["agent_whatsapp"], "+54 9 11 3170 4333")
        self.assertEqual(copy["agent_instagram"], "@josebarreiro")

    def test_case_b_whatsapp_on_instagram_off(self):
        link_agent_whatsapp(self.agent_id, self.org, "+54 9 11 3170 4333", enable_marketing=True)
        link_agent_instagram(self.agent_id, self.org, "josebarreiro", enable_marketing=False)
        branding = get_agent_branding(self.agent_id, self.org)
        self.assertEqual(branding["whatsapp"], "+54 9 11 3170 4333")
        self.assertIsNone(branding["instagram"])
        self.assertFalse(branding["instagram_enabled"])

    def test_case_c_whatsapp_off_instagram_on(self):
        link_agent_whatsapp(self.agent_id, self.org, "1131704333", enable_marketing=False)
        link_agent_instagram(self.agent_id, self.org, "@josebarreiro", enable_marketing=True)
        branding = get_agent_branding(self.agent_id, self.org)
        self.assertIsNone(branding["whatsapp"])
        self.assertFalse(branding["whatsapp_enabled"])
        self.assertEqual(branding["instagram"], "@josebarreiro")

    def test_case_d_both_off(self):
        link_agent_whatsapp(self.agent_id, self.org, "1131704333", enable_marketing=False)
        link_agent_instagram(self.agent_id, self.org, "josebarreiro", enable_marketing=False)
        branding = get_agent_branding(self.agent_id, self.org)
        self.assertIsNone(branding["whatsapp"])
        self.assertIsNone(branding["instagram"])
        copy = summarize_listing_copy({}, branding, language="es")
        self.assertEqual(copy["agent_name"], "José Barreiro")
        self.assertEqual(copy["agent_title"], "Agente inmobiliario")
        self.assertEqual(copy["agent_whatsapp"], "")
        self.assertEqual(copy["agent_instagram"], "")

    def _prompt(self):
        return build_marketing_image_prompt(
            {
                "facts": {"title": "Santamarina 1335", "locality": "Victoria"},
                "agent": get_agent_branding(self.agent_id, self.org),
                "photos": [],
                "language": "es",
            },
            "story",
            include_agent=True,
            language="es",
        )

    def test_prompt_case_b_hides_instagram(self):
        link_agent_whatsapp(self.agent_id, self.org, "1131704333", enable_marketing=True)
        link_agent_instagram(self.agent_id, self.org, "josebarreiro", enable_marketing=False)
        copy = summarize_listing_copy({}, get_agent_branding(self.agent_id, self.org), language="es")
        self.assertEqual(copy["agent_whatsapp"], "+54 9 11 3170 4333")
        self.assertEqual(copy["agent_instagram"], "")
        prompt = self._prompt()
        self.assertIn("VISUAL-ONLY MODE", prompt)
        self.assertNotIn("WhatsApp +54 9 11 3170 4333", prompt)
        self.assertNotIn("@josebarreiro", prompt)

    def test_prompt_case_d_name_and_title_only(self):
        link_agent_whatsapp(self.agent_id, self.org, "1131704333", enable_marketing=False)
        link_agent_instagram(self.agent_id, self.org, "josebarreiro", enable_marketing=False)
        copy = summarize_listing_copy({}, get_agent_branding(self.agent_id, self.org), language="es")
        self.assertEqual(copy["agent_name"], "José Barreiro")
        self.assertEqual(copy["agent_whatsapp"], "")
        self.assertEqual(copy["agent_instagram"], "")
        prompt = self._prompt()
        self.assertIn("VISUAL-ONLY MODE", prompt)
        self.assertNotIn("+54 9 11 3170 4333", prompt)
        self.assertNotIn("@josebarreiro", prompt)

    def test_unlink_hides_channel(self):
        link_agent_whatsapp(self.agent_id, self.org, "1131704333", enable_marketing=True)
        unlink_agent_whatsapp(self.agent_id, self.org)
        self.assertIsNone(publishable_agent_contacts(self.agent_id, self.org)["whatsapp"])

    def test_cannot_enable_without_number(self):
        unlink_agent_whatsapp(self.agent_id, self.org)
        with self.assertRaises(AgentContactError):
            set_whatsapp_marketing_enabled(self.agent_id, self.org, True)
        unlink_agent_instagram(self.agent_id, self.org)
        with self.assertRaises(AgentContactError):
            set_instagram_marketing_enabled(self.agent_id, self.org, True)

    def test_agent_can_open_and_link_from_settings(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.user_id
            session["role"] = ROLE_AGENT
            session["organization_id"] = self.org
        page = client.get("/settings/contact")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"WhatsApp", page.data)
        self.assertIn("Canales de contacto".encode("utf-8"), page.data)
        saved = client.post(
            "/settings/contact",
            data={
                "action": "link_whatsapp",
                "whatsapp_number": "1131704333",
                "use_in_marketing": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        branding = get_agent_branding(self.agent_id, self.org)
        self.assertEqual(branding["whatsapp"], "+54 9 11 3170 4333")


if __name__ == "__main__":
    unittest.main()
