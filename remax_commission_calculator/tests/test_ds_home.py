"""Design System V2 home: admin uses DS chrome, agent home stays intact."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_ds_home.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from web_app import app


class DesignSystemHomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="ds-home-tests")
        create_tables()
        cls.org = add_organization("DS Home Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("DS Agent", "Alto", cls.org)
        cls.admin_id = add_user("ds_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_user_id = add_user(
            "ds_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )

    def _login(self, user_id, role):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = self.org
        return client

    def test_admin_home_uses_design_system(self):
        page = self._login(self.admin_id, ROLE_ADMIN).get("/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("ds-page-header", body)
        self.assertIn("ds-metric-card", body)
        self.assertIn("ds-ai-card", body)
        self.assertIn("ds-empty", body)
        self.assertIn("ds-home__layout", body)
        self.assertIn("ds-home__main", body)
        self.assertIn("ds-home__aside", body)
        self.assertNotIn("home-mobile-quick", body)
        self.assertNotIn("jrh-hero", body)

    def test_agent_home_is_unchanged(self):
        page = self._login(self.agent_user_id, ROLE_AGENT).get("/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("jrh-hero", body)
        self.assertNotIn("ds-page-header", body)


if __name__ == "__main__":
    unittest.main()
