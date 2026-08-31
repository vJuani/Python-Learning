"""Branding configuration tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from modules.branding import (
    BRAND_LOGO_LIGHT_REL,
    DEFAULT_BRAND_NAME,
    get_brand_name,
    get_brand_logo_light_rel,
    get_logo_horizontal_rel,
)
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


if __name__ == "__main__":
    unittest.main()
