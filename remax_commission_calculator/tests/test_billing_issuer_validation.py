"""
Tests for unified billing issuer validation and ARCA prep.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_billing_issuer_validation.db"
)

from modules.auth import ROLE_ADMIN, hash_password
from modules.billing_issuer_validation import (
    list_usable_issuer_profiles,
    normalize_cuit,
    resolve_office_issuer_profile_id,
    validate_billing_issuer_profile,
    validate_cuit,
)
from modules.config import apply_config
from modules.database import (
    add_organization,
    add_user,
    create_tables,
    deactivate_billing_issuer_profile,
    get_organization_settings,
    set_default_billing_issuer_profile,
    upsert_billing_issuer_profile,
)
from modules.invoice_provider import (
    InternalInvoiceProvider,
    MockArcaInvoiceProvider,
    get_invoice_provider,
)
from modules.invoicing import (
    InvoicingError,
    ISSUER_MODE_OFFICE,
    default_issuer_mode_for_user,
)
from web_app import app


def _broker_profile(**overrides):
    base = {
        "issuer_type": "broker",
        "display_name": "Martillero",
        "legal_name": "Juan Perez Martillero",
        "tax_id": "20-11223344-5",
        "tax_condition": "responsable_inscripto",
        "fiscal_address": "Calle 123",
        "email": "martillero@example.com",
        "is_active": True,
        "is_default": False,
    }
    base.update(overrides)
    return base


class BillingIssuerValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-billing-issuer-validation"
        create_tables()

        cls.org_a = add_organization("Issuer Validation Org")
        pwd = hash_password("Password1")
        cls.admin_a = add_user(
            "issuer_admin",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )

    def test_broker_complete_and_active(self):
        result = validate_billing_issuer_profile(
            _broker_profile(),
        )
        self.assertTrue(result["is_valid"])
        self.assertEqual(result["missing_fields"], [])
        self.assertIsNone(result["error_key"])

    def test_cuit_with_and_without_dashes(self):
        self.assertTrue(validate_cuit("20-11223344-5"))
        self.assertTrue(validate_cuit("20112233445"))
        self.assertEqual(
            normalize_cuit("20112233445"),
            "20-11223344-5",
        )
        self.assertEqual(
            normalize_cuit(20112233445),
            "20-11223344-5",
        )

    def test_missing_fields_exact(self):
        result = validate_billing_issuer_profile(
            _broker_profile(
                legal_name="",
                tax_id="bad",
                tax_condition="",
                fiscal_address="",
            )
        )
        self.assertFalse(result["is_valid"])
        self.assertEqual(
            result["missing_fields"],
            [
                "legal_name",
                "tax_id",
                "tax_condition",
                "fiscal_address",
            ],
        )
        self.assertEqual(
            result["error_key"],
            "invoice_err_issuer_missing_legal_name",
        )

    def test_broker_without_default_single_usable(self):
        org = add_organization("Single Broker Org")
        profile = upsert_billing_issuer_profile(
            org,
            issuer_type="broker",
            display_name="Broker Solo",
            legal_name="Broker Solo SA",
            tax_id="20-99887766-5",
            tax_condition="monotributo",
            fiscal_address="Dir 1",
            is_default=False,
        )
        settings = get_organization_settings(org)
        self.assertIsNone(
            settings.get("default_issuer_profile_id")
        )

        resolved = resolve_office_issuer_profile_id(
            org,
            settings=settings,
        )
        self.assertEqual(resolved, profile["id"])

        admin_user = {
            "id": self.admin_a,
            "role": ROLE_ADMIN,
            "agent_id": None,
        }
        mode = default_issuer_mode_for_user(
            admin_user,
            settings,
            org,
        )
        self.assertEqual(mode, ISSUER_MODE_OFFICE)

    def test_multiple_issuers_without_default(self):
        org = add_organization("Multi Issuer Org")
        upsert_billing_issuer_profile(
            org,
            issuer_type="broker",
            display_name="Broker A",
            legal_name="Broker A",
            tax_id="20-11111111-1",
            tax_condition="monotributo",
            fiscal_address="A",
            is_default=False,
        )
        upsert_billing_issuer_profile(
            org,
            issuer_type="organization",
            display_name="Org B",
            legal_name="Org B",
            tax_id="20-22222222-2",
            tax_condition="monotributo",
            fiscal_address="B",
            is_default=False,
        )

        with self.assertRaises(InvoicingError) as ctx:
            resolve_office_issuer_profile_id(org)
        self.assertEqual(
            ctx.exception.message_key,
            "invoice_err_issuer_default_required",
        )

    def test_set_default_syncs_organization_settings(self):
        org = add_organization("Default Sync Org")
        profile = upsert_billing_issuer_profile(
            org,
            issuer_type="broker",
            display_name="Default Broker",
            legal_name="Default Broker",
            tax_id="20-33333333-3",
            tax_condition="monotributo",
            fiscal_address="C",
            is_default=False,
        )
        set_default_billing_issuer_profile(
            org,
            profile["id"],
        )
        settings = get_organization_settings(org)
        self.assertEqual(
            settings.get("default_issuer_profile_id"),
            profile["id"],
        )

    def test_inactive_issuer(self):
        org = add_organization("Inactive Issuer Org")
        profile = upsert_billing_issuer_profile(
            org,
            issuer_type="broker",
            display_name="Inactive",
            legal_name="Inactive",
            tax_id="20-44444444-4",
            tax_condition="monotributo",
            fiscal_address="D",
            is_default=True,
        )
        deactivate_billing_issuer_profile(org, profile["id"])
        deactivated = {
            **profile,
            "is_active": False,
        }
        result = validate_billing_issuer_profile(
            deactivated,
            require_active=True,
        )
        self.assertFalse(result["is_valid"])
        self.assertEqual(
            result["error_key"],
            "invoice_err_issuer_inactive",
        )
        self.assertEqual(
            list_usable_issuer_profiles(org),
            [],
        )

    def test_broker_issuer_type_valid(self):
        result = validate_billing_issuer_profile(
            _broker_profile(issuer_type="broker")
        )
        self.assertTrue(result["is_valid"])

    def test_internal_provider_still_works(self):
        provider = get_invoice_provider()
        self.assertIsInstance(provider, InternalInvoiceProvider)
        self.assertFalse(provider.can_issue_fiscal())

    def test_mock_arca_provider_returns_cae(self):
        provider = MockArcaInvoiceProvider()
        invoice = {
            "issuer_name": "Broker",
            "issuer_tax_id": "20-11223344-5",
            "issuer_tax_condition": "monotributo",
            "issuer_address": "Dir",
            "recipient_name": "Cliente",
            "recipient_tax_id": "20-99887766-5",
            "recipient_tax_condition": "consumidor_final",
            "recipient_address": "Cliente 1",
            "total_amount": 1000,
            "currency": "ARS",
            "issue_date": "2026-08-28",
        }
        issuer_profile = {
            "arca_connection_status": "connected",
            "arca_point_of_sale": "0005",
        }
        result = provider.issue_invoice(
            invoice,
            issuer_profile=issuer_profile,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.cae, "MOCK-CAE-12345678")
        self.assertIn("issuer_tax_id", result.snapshot)

    def test_mock_arca_not_configured_status(self):
        provider = MockArcaInvoiceProvider()
        status = provider.validate_issuer_configuration(
            {"arca_connection_status": "not_configured"}
        )
        self.assertFalse(status.is_ready)


if __name__ == "__main__":
    unittest.main()
