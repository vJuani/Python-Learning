"""FASE 4F — flexible invoice intent + entity resolution."""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_invoice_ai_v2.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_account import create_movement
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
    ExistingInvoiceResult,
    MissingSideResult,
    ResolvedChargeIntent,
    ResolvedInvoiceIntent,
    _amount_close,
    parse_invoice_intent,
    resolve_invoice_intent,
)
from modules.jrh_ai_context import SESSION_KEY as JRH_CONTEXT_KEY
from modules.invoicing import SIDE_BUYER, SIDE_SELLER, set_party_invoice_amount
from modules.jrh_ai_classify import (
    ORIGIN_CHARGE,
    ORIGIN_OPERATION,
    normalize_billing_period,
)
from modules.jrh_ai_intents import START_INVOICE
from modules.jrh_ai_service import ask_jrh
from modules.search import search_agents_flexible, token_edit_distance
from web_app import app


class InvoiceAiV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="invoice-ai-v2")
        create_tables()
        cls.org = add_organization("Invoice V2 Org")
        cls.other_org = add_organization("Invoice V2 Other")
        pwd = hash_password("Password1")
        cls.barreiro = add_agent("José Luis Barreiro", "Alto", cls.org)
        cls.jose_martinez = add_agent("José Martínez", "Alto", cls.org)
        cls.pablo = add_agent("Pablo Gómez", "Alto", cls.org)
        cls.foreign = add_agent("José Luis Barreiro", "Alto", cls.other_org)
        cls.admin = add_user("inv_v2_admin", pwd, ROLE_ADMIN, cls.org)
        cls.barreiro_user = add_user(
            "inv_v2_barreiro",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.barreiro,
        )
        cls.pablo_user = add_user(
            "inv_v2_pablo",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.pablo,
        )
        cls.admin_record = {
            "id": cls.admin,
            "role": ROLE_ADMIN,
            "organization_id": cls.org,
        }
        cls.barreiro_record = {
            "id": cls.barreiro_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.barreiro,
        }
        cls.pablo_record = {
            "id": cls.pablo_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.pablo,
        }
        cls.fee = cls._charge(cls.barreiro, "Fee septiembre", "fee", "65")
        cls.jrh_fee = cls._charge(
            cls.barreiro,
            "JRH One septiembre",
            "jrh_subscription",
            "10",
        )
        cls.pablo_fee = cls._charge(cls.pablo, "Fee septiembre", "fee", "65")
        cls._charge(cls.foreign, "Fee extranjera", "fee", "99", org=cls.other_org)

        cls.prop_lib_a = add_property(
            "Av. Libertador 4200",
            "CABA",
            cls.org,
            agent_id=cls.barreiro,
            status=STATUS_APPROVED,
        )
        cls.prop_lib_b = add_property(
            "Av. Libertador 6200",
            "CABA",
            cls.org,
            agent_id=cls.pablo,
            status=STATUS_APPROVED,
        )
        cls.op_lib_a = cls._operation(cls.barreiro, cls.prop_lib_a)
        cls.op_lib_b = cls._operation(cls.pablo, cls.prop_lib_b)
        set_party_invoice_amount(
            cls.org,
            cls.op_lib_a,
            SIDE_BUYER,
            "1000",
            "USD",
            None,
            cls.admin,
            enable_billing=True,
        )

    @classmethod
    def _charge(cls, agent_id, description, category, amount, *, org=None):
        return create_movement(
            org or cls.org,
            agent_id,
            {
                "movement_type": "charge",
                "charge_category": category,
                "currency": "USD",
                "amount": amount,
                "vat_mode": "add_vat",
                "vat_rate": "21",
                "description": description,
                "movement_date": "2026-09-02",
                "period_label": "Septiembre 2026",
            },
            created_by_user_id=cls.admin,
        )

    @classmethod
    def _operation(cls, agent_id, property_id):
        return add_operation(
            "01/09/2026",
            agent_id,
            property_id,
            "no",
            0,
            250000,
            3,
            7500,
            7500,
            0,
            0,
            0,
            0,
            0,
            cls.org,
        )

    def _resolve(self, prompt, *, user=None, agent_id=None, context=None):
        parsed = parse_invoice_intent(prompt, context=context)
        return parsed, resolve_invoice_intent(
            parsed,
            self.org,
            user
            or {
                "id": self.admin,
                "role": ROLE_ADMIN,
                "agent_id": None,
            },
            agent_scope=agent_id,
        )

    def _ask(self, prompt, *, user=None, agent_id=None, session=None):
        return ask_jrh(
            prompt,
            organization_id=self.org,
            user=user or self.admin_record,
            agent_id=agent_id,
            language="es",
            session=session if session is not None else {},
        )

    def test_01_fee_barreiro_resolves_charge(self):
        parsed, result = self._resolve("haceme la factura del fee de Barreiro")
        self.assertEqual(parsed.origin_type, ORIGIN_CHARGE)
        self.assertIsInstance(result, ResolvedChargeIntent)
        self.assertEqual(result.charge_id, self.fee["id"])
        self.assertFalse(getattr(result, "wrote", False))

    def test_02_jose_september_needs_selection(self):
        parsed, result = self._resolve("facturale a José lo de septiembre")
        self.assertEqual(parsed.entities.get("agent_name"), "José")
        self.assertIsInstance(result, DisambiguationResult)
        self.assertGreaterEqual(len(result.options), 2)

    def test_03_several_jose_clarifies(self):
        _, result = self._resolve("facturale a José")
        self.assertIsInstance(result, DisambiguationResult)
        names = " ".join(item.get("name") or "" for item in result.options)
        self.assertIn("Barreiro", names)
        self.assertIn("Martínez", names)

    def test_04_several_september_charges(self):
        _, result = self._resolve("facturame lo de Barreiro de septiembre")
        self.assertIsInstance(result, DisambiguationResult)
        self.assertGreaterEqual(len(result.options), 2)

    def test_05_agent_one_charge_preview(self):
        result = self._ask(
            "facturame lo que tengo",
            user=self.pablo_record,
            agent_id=self.pablo,
        )
        self.assertEqual(result["intent"], START_INVOICE)
        self.assertTrue(result["confirm_required"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["entity"].get("id"), self.pablo_fee["id"])

    def test_06_agent_several_charges_options(self):
        result = self._ask(
            "facturame lo que tengo",
            user=self.barreiro_record,
            agent_id=self.barreiro,
        )
        self.assertEqual(result["intent"], START_INVOICE)
        self.assertEqual(result["status"], "needs_attention")
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertFalse(result["wrote"])

    def test_07_agent_without_charges(self):
        empty_agent = add_agent("Sin Cargos V2", "Alto", self.org)
        empty_user = add_user(
            "inv_v2_empty",
            hash_password("Password1"),
            ROLE_AGENT,
            self.org,
            agent_id=empty_agent,
        )
        result = self._ask(
            "facturame lo que tengo",
            user={
                "id": empty_user,
                "role": ROLE_AGENT,
                "organization_id": self.org,
                "agent_id": empty_agent,
            },
            agent_id=empty_agent,
        )
        self.assertEqual(result["intent"], START_INVOICE)
        self.assertIn("cargos", result["message"].lower())

    def test_08_libertador_operation(self):
        parsed, result = self._resolve(
            "haceme la factura de la operación de Libertador 4200 al comprador"
        )
        self.assertEqual(parsed.origin_type, ORIGIN_OPERATION)
        self.assertIsInstance(result, ResolvedInvoiceIntent)
        self.assertEqual(result.operation_id, self.op_lib_a)
        self.assertEqual(result.side, SIDE_BUYER)

    def test_09_several_libertador_clarifies(self):
        _, result = self._resolve("haceme la factura de Libertador")
        self.assertIsInstance(result, DisambiguationResult)
        self.assertGreaterEqual(len(result.options), 2)

    def test_10_buyer_from_operation_context(self):
        parsed, result = self._resolve(
            "haceme la del comprador",
            context={
                "operation_id": self.op_lib_a,
                "last_entity": {"kind": "operation", "id": self.op_lib_a},
            },
        )
        self.assertIsInstance(result, ResolvedInvoiceIntent)
        self.assertEqual(result.side, SIDE_BUYER)
        self.assertEqual(result.operation_id, self.op_lib_a)

    def test_11_both_sides_ask(self):
        _, result = self._resolve("haceme la factura de Libertador 6200")
        self.assertIsInstance(result, MissingSideResult)

    def test_12_only_buyer_does_not_ask_side(self):
        _, result = self._resolve("haceme la factura de Libertador 4200")
        self.assertIsInstance(result, ResolvedInvoiceIntent)
        self.assertEqual(result.side, SIDE_BUYER)

    def test_13_context_facturame_eso(self):
        session = {}
        first = self._ask("qué debe Barreiro de fee?", session=session)
        self.assertEqual(first["entity"].get("id"), self.fee["id"])
        second = self._ask("facturame eso", session=session)
        self.assertEqual(second["intent"], START_INVOICE)
        self.assertEqual(second["entity"].get("id"), self.fee["id"])
        self.assertFalse(second["wrote"])

    def test_14_operation_context_seller(self):
        session = {}
        first = self._ask("buscame la operación de Libertador 6200", session=session)
        self.assertEqual(first["entity"].get("kind"), "operation")
        second = self._ask("haceme la del vendedor", session=session)
        self.assertEqual(second["intent"], START_INVOICE)
        self.assertEqual(second.get("data", {}).get("side") or second["actions"][0]["href_args"].get("side"), SIDE_SELLER)

    def test_15_fuzzy_barero(self):
        matches = search_agents_flexible("Barero", self.org)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], self.barreiro)
        _, result = self._resolve("facturame el fee de Barero")
        self.assertIsInstance(result, ResolvedChargeIntent)
        self.assertEqual(result.charge_id, self.fee["id"])

    def test_16_fuzzy_ambiguous(self):
        add_agent("Barerón Extra", "Alto", self.org)
        matches = search_agents_flexible("Barero", self.org)
        self.assertGreaterEqual(len(matches), 1)

    def test_17_other_org_never_appears(self):
        _, result = self._resolve("facturame el fee de Barreiro")
        self.assertIsInstance(result, ResolvedChargeIntent)
        self.assertEqual(result.agent_id, self.barreiro)

    def test_18_agent_cannot_see_other_agent(self):
        _, result = self._resolve(
            "facturame el fee de Barreiro",
            user=self.pablo_record,
            agent_id=self.pablo,
        )
        self.assertIsInstance(result, DisambiguationResult)

    def test_19_preview_does_not_insert(self):
        before = self.fee["id"]
        result = self._ask("haceme la factura del fee de Barreiro")
        self.assertTrue(result["confirm_required"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["entity"].get("id"), before)

    def test_20_amount_decimal_exact(self):
        self.assertTrue(_amount_close("78.65", Decimal("78.65")))
        self.assertTrue(_amount_close("78,65", "78.65"))
        self.assertFalse(_amount_close("78.65", "12.10"))
        parsed = parse_invoice_intent("la de USD 78,65 de José")
        self.assertEqual(parsed.entities.get("currency"), "USD")
        self.assertAlmostEqual(parsed.entities.get("amount"), 78.65, places=2)

    def test_21_period_normalization(self):
        self.assertEqual(normalize_billing_period("septiembre 2026"), "2026-09")
        self.assertEqual(normalize_billing_period("09/2026"), "2026-09")

    def test_22_existing_invoice_does_not_duplicate(self):
        from modules.invoice_ai_service import get_charge_invoice_context as real_ctx

        class _Fake:
            def get(self, key, default=None):
                return {"id": 99} if key == "active_invoice" else default

        def fake_context(*_args, **_kwargs):
            return {"active_invoice": {"id": 99}, "movement": {"id": self.fee["id"], "agent_id": self.barreiro}}

        from modules import invoice_ai_service as service

        original = service.get_charge_invoice_context
        service.get_charge_invoice_context = fake_context
        try:
            _, result = self._resolve("haceme la factura del fee de Barreiro")
            self.assertIsInstance(result, ExistingInvoiceResult)
            self.assertEqual(result.invoice_id, 99)
        finally:
            service.get_charge_invoice_context = original
        self.assertTrue(callable(real_ctx))

    def test_23_fuzzy_distance_helper(self):
        self.assertLessEqual(token_edit_distance("barero", "barreiro"), 2)
        self.assertLessEqual(token_edit_distance("libertado", "libertador"), 1)

    def test_24_fee_chip_then_jose_is_agent_not_operation(self):
        session = {}
        first = self._ask("Fee de un agente", session=session)
        self.assertEqual(first["intent"], START_INVOICE)
        self.assertNotIn("operación", (first.get("message") or "").lower())
        pending = (session.get(JRH_CONTEXT_KEY) or {}).get("pending_invoice") or {}
        self.assertEqual(pending.get("expected_entity"), "agent")
        self.assertEqual(pending.get("origin_type"), ORIGIN_CHARGE)
        self.assertEqual((pending.get("charge_category") or "").lower(), "fee")
        second = self._ask("jose", session=session)
        self.assertEqual(second["intent"], START_INVOICE)
        self.assertNotIn("operación", (second.get("message") or "").lower())
        kinds = {item.get("kind") for item in second.get("candidates") or []}
        self.assertTrue(
            second.get("entity", {}).get("kind") == "charge"
            or kinds <= {"agent", "charge"}
            or "agente" in (second.get("message") or "").lower()
        )
        self.assertNotEqual(second.get("message_key"), "billing_ai_operation_not_found")

    def test_25_jose_barreiro_resolves_full_name(self):
        parsed, result = self._resolve("facturame el fee de jose barreiro")
        self.assertEqual(parsed.origin_type, ORIGIN_CHARGE)
        self.assertIsInstance(result, ResolvedChargeIntent)
        self.assertEqual(result.agent_id, self.barreiro)

    def test_26_barreiro_alone_resolves_unique(self):
        _, result = self._resolve("facturame el fee de barreiro")
        self.assertIsInstance(result, ResolvedChargeIntent)
        self.assertEqual(result.agent_id, self.barreiro)
        self.assertEqual(result.charge_id, self.fee["id"])

    def test_27_jose_with_two_matches_asks(self):
        _, result = self._resolve("facturame el fee de jose")
        self.assertIsInstance(result, DisambiguationResult)
        names = " ".join(item.get("name") or "" for item in result.options)
        self.assertIn("Barreiro", names)
        self.assertIn("Martínez", names)
        self.assertEqual(result.expected_entity, "agent")

    def test_28_unknown_agent_message_is_agent_not_operation(self):
        _, result = self._resolve("facturame el fee de ZetaInexistente")
        self.assertIsInstance(result, DisambiguationResult)
        self.assertEqual(result.message_key, "billing_ai_agent_not_found")
        self.assertNotEqual(result.message_key, "billing_ai_operation_not_found")
        message = self._ask("facturame el fee de ZetaInexistente")
        self.assertIn("agente", message["message"].lower())
        self.assertNotIn("operación", message["message"].lower())

    def test_29_los_agentes_is_not_a_name(self):
        parsed, result = self._resolve("facturame el fee de los agentes")
        self.assertTrue(parsed.entities.get("generic_agents"))
        self.assertFalse(parsed.entities.get("agent_name"))
        self.assertIsInstance(result, DisambiguationResult)
        self.assertEqual(result.expected_entity, "agent")
        names = " ".join(item.get("name") or "" for item in result.options).lower()
        self.assertNotIn("los agentes", names)
        self.assertIn("barreiro", names)

    def test_30_fee_quick_action_keeps_category(self):
        parsed = parse_invoice_intent("Fee de un agente")
        self.assertEqual(parsed.origin_type, ORIGIN_CHARGE)
        self.assertEqual((parsed.entities.get("charge_category") or "").lower(), "fee")
        session = {}
        result = self._ask("Fee de un agente", session=session)
        pending = (session.get(JRH_CONTEXT_KEY) or {}).get("pending_invoice") or {}
        self.assertEqual((pending.get("charge_category") or "").lower(), "fee")
        self.assertEqual(result["intent"], START_INVOICE)

    def test_31_side_slot_fills_buyer(self):
        parsed, result = self._resolve(
            "comprador",
            context={
                "pending_invoice": {
                    "intent": START_INVOICE,
                    "origin_type": ORIGIN_OPERATION,
                    "expected_entity": "side",
                    "operation_id": self.op_lib_a,
                    "entities": {"origin_type": ORIGIN_OPERATION},
                },
                "operation_id": self.op_lib_a,
            },
        )
        self.assertIsInstance(result, ResolvedInvoiceIntent)
        self.assertEqual(result.side, SIDE_BUYER)
        self.assertEqual(result.operation_id, self.op_lib_a)

    def test_32_properties_intent_abandons_agent_slot(self):
        session = {}
        first = self._ask("Fee de un agente", session=session)
        self.assertEqual(first["pending_invoice"].get("expected_entity"), "agent")
        second = self._ask("mejor mostrame mis propiedades", session=session)
        self.assertEqual(second["intent"], "QUERY_PROPERTIES")
        pending = (session.get(JRH_CONTEXT_KEY) or {}).get("pending_invoice") or {}
        self.assertFalse(pending.get("expected_entity"))

    def test_33_context_survives_consecutive_posts(self):
        session = {}
        self._ask("Fee de un agente", session=session)
        first_pending = dict(
            (session.get(JRH_CONTEXT_KEY) or {}).get("pending_invoice") or {}
        )
        self.assertEqual(first_pending.get("expected_entity"), "agent")
        self._ask("jose", session=session)
        second_ctx = session.get(JRH_CONTEXT_KEY) or {}
        self.assertTrue(
            second_ctx.get("pending_invoice")
            or second_ctx.get("last_entity")
        )
        self.assertEqual(second_ctx.get("last_intent"), START_INVOICE)

    def test_34_other_org_never_in_jose_candidates(self):
        _, result = self._resolve("facturame el fee de jose")
        ids = {item.get("id") for item in result.options}
        self.assertNotIn(self.foreign, ids)

    def test_35_agent_scope_own_records_only(self):
        _, result = self._resolve(
            "facturame el fee de jose",
            user=self.pablo_record,
            agent_id=self.pablo,
        )
        self.assertIsInstance(result, DisambiguationResult)
        ids = {item.get("id") for item in result.options}
        self.assertNotIn(self.barreiro, ids)
        self.assertNotIn(self.jose_martinez, ids)
        self.assertEqual(result.message_key, "billing_ai_agent_not_found")
        own = self._ask(
            "Fee de un agente",
            user=self.pablo_record,
            agent_id=self.pablo,
        )
        self.assertEqual(own["intent"], START_INVOICE)
        self.assertNotIn(self.barreiro, {
            item.get("id") for item in own.get("candidates") or []
        })

    def test_36_selected_agent_lists_real_charges(self):
        _, result = self._resolve("facturame lo de Barreiro")
        self.assertIsInstance(result, DisambiguationResult)
        charge_ids = {
            item.get("id") or item.get("charge_id") for item in result.options
        }
        self.assertIn(self.fee["id"], charge_ids)
        self.assertIn(self.jrh_fee["id"], charge_ids)
        self.assertNotIn(self.pablo_fee["id"], charge_ids)

    def test_37_single_fee_preview(self):
        _, result = self._resolve("facturame el fee de barreiro")
        self.assertIsInstance(result, ResolvedChargeIntent)
        self.assertEqual(result.charge_id, self.fee["id"])

    def test_38_several_fees_need_selection(self):
        extra = self._charge(self.barreiro, "Fee extra", "fee", "20")
        _, result = self._resolve("facturame el fee de barreiro")
        self.assertIsInstance(result, DisambiguationResult)
        charge_ids = {
            item.get("id") or item.get("charge_id") for item in result.options
        }
        self.assertIn(self.fee["id"], charge_ids)
        self.assertIn(extra["id"], charge_ids)


if __name__ == "__main__":
    unittest.main()
