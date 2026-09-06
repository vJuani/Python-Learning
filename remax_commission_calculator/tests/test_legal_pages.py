"""Public privacy and terms pages must be reachable without login."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_legal_pages.db")

from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from web_app import app


class LegalPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()
        cls.org = add_organization("Legal Org")
        cls.agent_id = add_agent("Legal Agent", "Alto", cls.org)
        cls.user_id = add_user(
            "legal_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )

    def test_privacy_is_public(self):
        client = app.test_client()
        response = client.get("/privacy", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Política de privacidad", body)
        self.assertIn("Google Calendar", body)
        self.assertIn("no vende datos personales", body)
        self.assertIn("Clave Fiscal", body)
        self.assertIn("soporte@jrhone.com", body)
        self.assertIn("/terms", body)
        self.assertNotIn("/login?next=", body)

    def test_terms_is_public(self):
        client = app.test_client()
        response = client.get("/terms", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Términos y condiciones", body)
        self.assertIn("Google Calendar", body)
        self.assertIn("Clave Fiscal", body)
        self.assertIn("/privacy", body)
        self.assertNotIn("/login?next=", body)

    def test_login_footer_points_to_legal_pages(self):
        client = app.test_client()
        body = client.get("/login").get_data(as_text=True)
        self.assertIn('href="/terms"', body)
        self.assertIn('href="/privacy"', body)

    def test_logged_in_agent_can_still_read_them(self):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["role"] = ROLE_AGENT
            sess["organization_id"] = self.org
            sess["agent_id"] = self.agent_id
        privacy = client.get("/privacy", follow_redirects=False)
        terms = client.get("/terms", follow_redirects=False)
        self.assertEqual(privacy.status_code, 200)
        self.assertEqual(terms.status_code, 200)


if __name__ == "__main__":
    unittest.main()
