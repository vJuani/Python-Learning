"""ARCA_ENV must be explicit and must not mix homologation with production."""

from __future__ import annotations

import os
import unittest

from modules.arca.config import (
    ArcaEnvironmentError,
    describe_arca_environment,
    get_arca_environment,
    get_wsaa_url,
    get_wsfe_url,
    is_arca_fiscal_enabled,
)
from modules.arca.validation import validate_fiscal_issue


class ArcaEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("ARCA_ENV")
        self._provider = os.environ.get("INVOICE_PROVIDER")
        os.environ["INVOICE_PROVIDER"] = "arca"

    def tearDown(self):
        if self._env is None:
            os.environ.pop("ARCA_ENV", None)
        else:
            os.environ["ARCA_ENV"] = self._env
        if self._provider is None:
            os.environ.pop("INVOICE_PROVIDER", None)
        else:
            os.environ["INVOICE_PROVIDER"] = self._provider

    def test_homologation_and_test_alias_use_homo_endpoints(self):
        for raw in ("homologation", "test", "testing"):
            os.environ["ARCA_ENV"] = raw
            self.assertEqual(get_arca_environment(), "homologation")
            wsaa = get_wsaa_url()
            wsfe = get_wsfe_url()
            self.assertIn("wsaahomo.afip.gov.ar", wsaa)
            self.assertIn("wswhomo.afip.gov.ar", wsfe)
            self.assertNotIn("servicios1.afip.gov.ar", wsfe)
            self.assertTrue(is_arca_fiscal_enabled())

    def test_production_uses_production_endpoints_only(self):
        os.environ["ARCA_ENV"] = "production"
        self.assertEqual(get_arca_environment(), "production")
        wsaa = get_wsaa_url()
        wsfe = get_wsfe_url()
        self.assertEqual(wsaa, "https://wsaa.afip.gov.ar/ws/services/LoginCms")
        self.assertEqual(
            wsfe,
            "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
        )
        self.assertNotIn("homo", wsaa.lower())
        self.assertNotIn("homo", wsfe.lower())
        self.assertNotEqual(get_wsfe_url("production"), get_wsfe_url("homologation"))
        self.assertNotEqual(get_wsaa_url("production"), get_wsaa_url("homologation"))
        self.assertTrue(is_arca_fiscal_enabled())

    def test_prod_alias(self):
        os.environ["ARCA_ENV"] = "prod"
        self.assertEqual(get_arca_environment(), "production")
        self.assertNotIn("homo", get_wsfe_url().lower())

    def test_missing_env_fails_closed(self):
        os.environ.pop("ARCA_ENV", None)
        with self.assertRaises(ArcaEnvironmentError) as caught:
            get_arca_environment()
        self.assertEqual(caught.exception.reason, "missing")
        self.assertFalse(is_arca_fiscal_enabled())
        status = describe_arca_environment(cuit="")
        self.assertFalse(status["environment_configured"])
        self.assertIsNone(status["environment"])
        self.assertIsNone(status["wsfe_endpoint"])
        self.assertFalse(status["is_production"])
        self.assertFalse(status["is_homologation"])

    def test_invalid_env_does_not_become_homologation(self):
        os.environ["ARCA_ENV"] = "staging"
        with self.assertRaises(ArcaEnvironmentError) as caught:
            get_arca_environment()
        self.assertEqual(caught.exception.reason, "invalid")
        self.assertFalse(is_arca_fiscal_enabled())
        with self.assertRaises(ArcaEnvironmentError):
            get_wsfe_url()

    def test_missing_credentials_do_not_issue(self):
        os.environ["ARCA_ENV"] = "production"
        result = validate_fiscal_issue(
            {"status": "ready_to_issue", "currency": "ARS"},
            {"tax_id": "20111111112"},
            connection={"connection_status": "connected"},
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_key, "invoice_err_arca_credentials_missing")

    def test_missing_env_does_not_issue(self):
        os.environ.pop("ARCA_ENV", None)
        result = validate_fiscal_issue(
            {"status": "ready_to_issue", "currency": "ARS"},
            {"tax_id": "20111111112"},
            connection={"connection_status": "connected"},
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.error_key, "invoice_err_fiscal_issue_unavailable")

    def test_diagnostic_hides_secrets(self):
        os.environ["ARCA_ENV"] = "homologation"
        status = describe_arca_environment(
            connection={
                "certificate_encrypted": "cipher-not-a-pem",
                "private_key_encrypted": "cipher-not-a-key",
            },
            cuit="20-1",
        )
        dumped = str(status)
        self.assertNotIn("cipher-not-a-pem", dumped)
        self.assertNotIn("cipher-not-a-key", dumped)
        self.assertTrue(status["certificates_present"])
        self.assertFalse(status["cuit_configured"])
        complete = describe_arca_environment(cuit="20-11111111-2")
        self.assertTrue(complete["cuit_configured"])
        self.assertTrue(status["is_homologation"])
        self.assertIn("wswhomo.afip.gov.ar", status["wsfe_endpoint"])
