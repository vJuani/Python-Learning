"""Production startup fails closed. Optional integrations stay visible, not secret."""

from __future__ import annotations

import os
import unittest

from modules.branding import get_app_base_url
from modules.config import ProductionConfigError, validate_production_startup
from modules.integration_status import describe_integrations


class ProductionConfigTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "APP_ENV",
                "SECRET_KEY",
                "APP_BASE_URL",
                "DATABASE_URL",
                "DATABASE_PATH",
                "OPENAI_API_KEY",
                "RESEND_API_KEY",
                "EMAIL_BACKEND",
            )
        }

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_production_refuses_to_start_without_critical_settings(self):
        os.environ["APP_ENV"] = "production"
        os.environ.pop("SECRET_KEY", None)
        os.environ.pop("APP_BASE_URL", None)
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("DATABASE_PATH", None)
        with self.assertRaises(ProductionConfigError) as caught:
            validate_production_startup()
        self.assertEqual(
            caught.exception.missing,
            ["SECRET_KEY", "APP_BASE_URL", "DATABASE_URL"],
        )

    def test_production_accepts_https_and_an_explicit_database(self):
        os.environ["APP_ENV"] = "production"
        os.environ["SECRET_KEY"] = "production-secret-value"
        os.environ["APP_BASE_URL"] = "https://app.example.com"
        os.environ["DATABASE_URL"] = "postgresql://user:secret@db/app"
        validate_production_startup()
        self.assertEqual(get_app_base_url(), "https://app.example.com")

    def test_production_does_not_fall_back_to_localhost(self):
        os.environ["APP_ENV"] = "production"
        os.environ["APP_BASE_URL"] = "http://127.0.0.1:5000"
        with self.assertRaises(RuntimeError):
            get_app_base_url()

    def test_development_keeps_the_local_base_url(self):
        os.environ["APP_ENV"] = "development"
        os.environ.pop("APP_BASE_URL", None)
        self.assertEqual(get_app_base_url(), "http://127.0.0.1:5000")

    def test_integration_status_hides_secrets(self):
        os.environ["APP_ENV"] = "development"
        os.environ["OPENAI_API_KEY"] = "sk-secret-value"
        os.environ["EMAIL_BACKEND"] = "resend"
        os.environ["RESEND_API_KEY"] = "re_secret_value"
        os.environ.pop("ARCA_ENV", None)
        status = describe_integrations()
        dumped = str(status)
        self.assertNotIn("sk-secret-value", dumped)
        self.assertNotIn("re_secret_value", dumped)
        by_key = {item["key"]: item["configured"] for item in status}
        self.assertTrue(by_key["openai"])
        self.assertTrue(by_key["mail"])
        self.assertIn("arca", by_key)
        self.assertFalse(by_key["arca"])
