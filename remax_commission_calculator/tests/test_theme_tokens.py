"""Light theme tokens must govern chrome; org branding cannot overwrite --accent."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_theme_tokens.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_AGENT, hash_password
from modules.config import BASE_DIR, apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    update_organization_settings,
)
from web_app import app


class ThemeTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="theme-token-tests")
        create_tables()
        cls.org = add_organization("Theme Token Org")
        update_organization_settings(
            cls.org,
            display_name="Theme Token Org",
            default_language="es",
            default_currency="USD",
            timezone="America/Argentina/Buenos_Aires",
            logo_path=None,
            accent_color="#0d47ff",
        )
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Theme Agent", "Alto", cls.org)
        cls.agent_user_id = add_user(
            "theme_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )

    def test_light_tokens_define_semantic_green_system(self):
        css = (BASE_DIR / "static" / "css" / "tokens.css").read_text(encoding="utf-8")
        self.assertIn(":root,", css)
        self.assertIn('[data-theme="light"]', css)
        self.assertIn("--color-brand: #156A4A;", css)
        self.assertIn("--color-brand-hover: #0E4F38;", css)
        self.assertIn("--color-bg: #F6F8F6;", css)
        self.assertIn("--color-surface: #FFFFFF;", css)
        self.assertIn("--accent: var(--color-brand);", css)
        self.assertIn("--ia-accent: var(--color-brand);", css)
        self.assertIn("--btn-primary-bg: var(--color-brand);", css)
        self.assertIn('[data-theme="dark"]', css)
        self.assertIn("--color-brand: #7C5CFF;", css)

    def test_base_html_does_not_let_org_branding_overwrite_accent(self):
        html = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("--org-accent:", html)
        self.assertNotIn("--accent: {{ organization_branding_css.accent }}", html)
        self.assertIn('setAttribute("data-theme", "light")', html)

    def test_agent_home_keeps_product_accent_when_org_is_blue(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.agent_user_id
            session["role"] = ROLE_AGENT
            session["organization_id"] = self.org
        page = client.get("/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("jrh-hero", body)
        self.assertIn("--org-accent:", body)
        self.assertIn("#0d47ff", body.lower())
        self.assertIsNone(re.search(r"--accent:\s*#0d47ff", body, re.I))
        self.assertIn("css/home-v2.css", body)

    def test_service_worker_busts_css_cache(self):
        body = (BASE_DIR / "static" / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn("jrh-one-static-v7", body)
        self.assertIn('url.pathname.indexOf("/static/css/") === 0', body)
        self.assertNotIn("jrh-one-static-v6", body)

    def test_hero_buttons_share_height_token_consumers(self):
        css = (BASE_DIR / "static" / "css" / "home-v2.css").read_text(encoding="utf-8")
        self.assertIn("background: var(--color-brand);", css)
        self.assertIn(".jrh-hero__mic {", css)
        self.assertIn("height: 40px;", css)
        self.assertIn(".jrh-hero__ask {", css)
        self.assertIn("background: var(--color-brand-soft);", css)
        self.assertNotIn("border-color: var(--ia-accent);", css)


if __name__ == "__main__":
    unittest.main()
