"""Branding configuration tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from modules.branding import (
    BRAND_CHROME_ICONS,
    BRAND_LOGO_LIGHT_REL,
    BRAND_MARKS,
    DEFAULT_BRAND_NAME,
    get_brand_logo_light_rel,
    get_brand_mark_rel,
    get_brand_name,
    get_logo_horizontal_rel,
    get_web_manifest_payload,
    iter_brand_mark_rels,
)
from modules.config import BASE_DIR
from modules.i18n import translate


class BrandingTests(unittest.TestCase):
    def test_default_brand_name(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_brand_name(), DEFAULT_BRAND_NAME)

    def test_brand_name_from_env(self):
        with patch.dict(
            os.environ,
            {"APP_BRAND_NAME": "Custom Brand"},
            clear=False,
        ):
            self.assertEqual(get_brand_name(), "Custom Brand")

    def test_translate_app_title_uses_brand(self):
        with patch.dict(
            os.environ,
            {"APP_BRAND_NAME": "JRH One"},
            clear=False,
        ):
            self.assertEqual(translate("app_title", "es"), "JRH One")

    def test_default_logo_paths(self):
        self.assertEqual(
            get_logo_horizontal_rel(),
            get_brand_logo_light_rel(),
        )
        self.assertEqual(
            get_brand_logo_light_rel(),
            BRAND_LOGO_LIGHT_REL,
        )

    def test_canonical_mark_files_exist(self):
        for rel in BRAND_MARKS.values():
            path = BASE_DIR / "static" / rel
            self.assertTrue(path.is_file(), msg=rel)
        for rel in BRAND_CHROME_ICONS.values():
            path = BASE_DIR / "static" / rel
            self.assertTrue(path.is_file(), msg=rel)

    def test_mark_catalog_covers_identity_slots(self):
        expected = {
            "primary_light",
            "horizontal_light",
            "isotype_light",
            "app_icon",
            "primary_dark_green",
            "primary_dark_violet",
            "horizontal_dark_green",
            "horizontal_dark_violet",
            "isotype_dark_green",
            "isotype_dark_violet",
            "favicon",
            "apple_touch",
            "pwa_192",
            "pwa_512",
            "pwa_maskable",
        }
        names = {name for name, _rel in iter_brand_mark_rels()}
        self.assertTrue(expected.issubset(names))
        self.assertEqual(
            get_brand_mark_rel("primary_light"),
            "brand/logo-primary-light.png",
        )

    def test_web_manifest_uses_app_icon_derivatives(self):
        payload = get_web_manifest_payload(lambda rel: f"/static/{rel}")
        self.assertEqual(payload["theme_color"], "#0E4F38")
        srcs = [item["src"] for item in payload["icons"]]
        self.assertTrue(any("icon-192.png" in src for src in srcs))
        self.assertTrue(any("icon-maskable-512.png" in src for src in srcs))


class BrandingTemplateTests(unittest.TestCase):
    def test_login_template_uses_central_brand_marks(self):
        login = (BASE_DIR / "templates" / "auth" / "login.html").read_text(
            encoding="utf-8"
        )
        marketing = (
            BASE_DIR / "templates" / "auth" / "_login_marketing.html"
        ).read_text(encoding="utf-8")
        base = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("brand_slot='login-primary'", login)
        self.assertIn("brand_slot='login-panel'", marketing)
        self.assertIn("brand_slot='sidebar-icon'", base)
        self.assertIn("brand_slot='sidebar-lockup'", base)
        self.assertIn("brand_slot='navbar'", base)
        self.assertIn("brand_marks.favicon", base)
        self.assertIn("css/brand-logo.css", base)
        self.assertNotIn("jrh-one-logo-horizontal.jpg", base)
        self.assertNotIn("jrh-one-logo-full.jpg", login)
        self.assertNotIn("images/logo-horizontal.png", base)

    def test_brand_partial_is_slot_based(self):
        html = (BASE_DIR / "templates" / "_brand_logo.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("brand_slot", html)
        self.assertIn("brand_marks.primary_light", html)
        self.assertIn("brand_marks.horizontal_light", html)
        self.assertIn("brand_marks.isotype_dark_green", html)
        css = (BASE_DIR / "static" / "css" / "brand-logo.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".brand-logo--accent-violet", css)
        self.assertIn("jrh-sidebar", css)


if __name__ == "__main__":
    unittest.main()
