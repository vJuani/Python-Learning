"""
Tests for invoice AI intent parsing (rule-based, DB-backed resolution).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_invoice_ai.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_operation,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.properties_repository import STATUS_APPROVED
from modules.invoice_ai_service import (
    DisambiguationResult,
    INTENT_LIST_PENDING,
    MissingSideResult,
    ParsedInvoiceIntent,
    ResolvedInvoiceIntent,
    parse_invoice_intent,
    resolve_invoice_intent,
)
from modules.invoicing import SIDE_BUYER, SIDE_SELLER
from web_app import app


class InvoiceAiServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org = add_organization("AI Billing Org")
        pwd = hash_password("Password1")

        cls.agent = add_agent("Pablo Reynals", "Alto", cls.org)
        cls.other_agent = add_agent("Agent B", "Alto", cls.org)

        cls.prop = add_property(
            "Hubac 4702",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            status=STATUS_APPROVED,
            listing_price=2500000,
            external_id="HUBAC-4702",
        )
        cls.prop_paunero_a = add_property(
            "Paunero 1078",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            status=STATUS_APPROVED,
            external_id="PAUN-A",
        )
        cls.prop_paunero_b = add_property(
            "Paunero 1078 Duplex",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            status=STATUS_APPROVED,
            external_id="PAUN-B",
        )

        cls.admin = add_user(
            "ai_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
        )
        cls.agent_user = add_user(
            "ai_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )

        cls.operation_id = add_operation(
            "01/01/2026",
            cls.agent,
            cls.prop,
            "no",
            0,
            2500000,
            3,
            75000,
            75000,
            0,
            0,
            0,
            0,
            0,
            cls.org,
        )
        cls.paunero_op_a = add_operation(
            "02/01/2026",
            cls.agent,
            cls.prop_paunero_a,
            "no",
            0,
            900000,
            3,
            27000,
            27000,
            0,
            0,
            0,
            0,
            0,
            cls.org,
        )
        cls.paunero_op_b = add_operation(
            "03/01/2026",
            cls.agent,
            cls.prop_paunero_b,
            "no",
            0,
            950000,
            3,
            28500,
            28500,
            0,
            0,
            0,
            0,
            0,
            cls.org,
        )

    def test_parse_buyer_side_spanish(self):
        parsed = parse_invoice_intent(
            "Facturá Hubac 4702 al comprador"
        )
        self.assertEqual(parsed.side, SIDE_BUYER)
        self.assertEqual(parsed.intent, "create_invoice_draft")

    def test_parse_seller_side(self):
        parsed = parse_invoice_intent(
            "Prepará factura al vendedor de Hubac"
        )
        self.assertEqual(parsed.side, SIDE_SELLER)

    def test_parse_list_pending(self):
        parsed = parse_invoice_intent("Qué me falta facturar")
        self.assertEqual(parsed.intent, INTENT_LIST_PENDING)

    def test_resolve_com_id_buyer(self):
        parsed = parse_invoice_intent(
            f"Facturá COM-{self.operation_id:06d} al comprador"
        )
        user = {"role": ROLE_ADMIN, "id": self.admin}
        result = resolve_invoice_intent(
            parsed,
            self.org,
            user,
        )
        self.assertIsInstance(result, ResolvedInvoiceIntent)
        self.assertEqual(result.operation_id, self.operation_id)
        self.assertEqual(result.side, SIDE_BUYER)

    def test_resolve_property_name(self):
        parsed = parse_invoice_intent(
            "Haceme la factura de Hubac al comprador"
        )
        user = {"role": ROLE_ADMIN, "id": self.admin}
        result = resolve_invoice_intent(
            parsed,
            self.org,
            user,
        )
        self.assertIsInstance(result, ResolvedInvoiceIntent)
        self.assertEqual(result.side, SIDE_BUYER)

    def test_disambiguation_two_operations(self):
        parsed = parse_invoice_intent(
            "Facturá Paunero al comprador"
        )
        user = {"role": ROLE_ADMIN, "id": self.admin}
        result = resolve_invoice_intent(
            parsed,
            self.org,
            user,
        )
        self.assertIsInstance(result, DisambiguationResult)
        self.assertEqual(
            result.message_key,
            "billing_ai_disambiguation",
        )
        self.assertGreaterEqual(len(result.options), 2)

    def test_missing_side_question(self):
        parsed = parse_invoice_intent("Facturá Hubac 4702")
        user = {"role": ROLE_ADMIN, "id": self.admin}
        result = resolve_invoice_intent(
            parsed,
            self.org,
            user,
        )
        self.assertIsInstance(result, MissingSideResult)
        self.assertEqual(result.operation_id, self.operation_id)

    def test_follow_up_side_from_context(self):
        parsed = parse_invoice_intent(
            "al comprador",
            context={
                "operation_id": self.operation_id,
            },
        )
        user = {"role": ROLE_ADMIN, "id": self.admin}
        result = resolve_invoice_intent(
            parsed,
            self.org,
            user,
        )
        self.assertIsInstance(result, ResolvedInvoiceIntent)
        self.assertEqual(result.side, SIDE_BUYER)

    def test_agent_blocked_other_operation(self):
        other_prop = add_property(
            "Other Agent Prop",
            "CABA",
            self.org,
            agent_id=self.other_agent,
            status=STATUS_APPROVED,
        )
        other_op = add_operation(
            "04/01/2026",
            self.other_agent,
            other_prop,
            "no",
            0,
            100,
            3,
            3,
            3,
            0,
            0,
            0,
            0,
            0,
            self.org,
        )
        parsed = parse_invoice_intent(
            f"Facturá COM-{other_op:06d} al comprador"
        )
        user = {
            "role": ROLE_AGENT,
            "id": self.agent_user,
            "agent_id": self.agent,
        }
        result = resolve_invoice_intent(
            parsed,
            self.org,
            user,
        )
        self.assertIsInstance(result, DisambiguationResult)
        self.assertEqual(
            result.message_key,
            "billing_ai_operation_not_found",
        )

    def test_list_pending_intent_passthrough(self):
        parsed = parse_invoice_intent("pendientes")
        user = {"role": ROLE_ADMIN, "id": self.admin}
        result = resolve_invoice_intent(
            parsed,
            self.org,
            user,
        )
        self.assertIsInstance(result, ParsedInvoiceIntent)
        self.assertEqual(result.intent, INTENT_LIST_PENDING)

    def test_resolve_then_missing_cuit_field(self):
        from modules.database import (
            ensure_parties_for_operation,
            get_operation_party,
        )
        from modules.invoicing import (
            get_next_missing_client_field,
            set_party_invoice_amount,
        )

        ensure_parties_for_operation(self.org, self.operation_id)
        set_party_invoice_amount(
            self.org,
            self.operation_id,
            SIDE_BUYER,
            "2500000",
            "ARS",
            None,
            self.admin,
            enable_billing=True,
        )
        party = get_operation_party(
            self.org,
            self.operation_id,
            SIDE_BUYER,
        )
        missing = get_next_missing_client_field(party)
        self.assertEqual(missing, "client_tax_id")


if __name__ == "__main__":
    unittest.main()
