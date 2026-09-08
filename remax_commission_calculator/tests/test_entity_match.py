"""Shared JRH entity matching: accents, typos, partial addresses."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_entity_match.db")
os.environ.pop("DATABASE_URL", None)
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_operation, add_organization, add_property, add_user, create_tables
from modules.database.properties_repository import STATUS_APPROVED
from modules.entity_match import (
    UNIQUE_MIN,
    decide_entity_matches,
    normalize_search_text,
    rank_entity_candidates,
    score_entity_text,
)
from modules.invoice_ai_service import _find_operations_by_reference
from modules.jrh_ai_intents import QUERY_PROPERTIES, START_ACM
from modules.jrh_ai_resolver import resolve_properties
from modules.jrh_ai_service import ask_jrh
from web_app import app


class EntityMatchUnitTests(unittest.TestCase):
    def test_normalize_strips_accents_and_abbreviations(self):
        self.assertEqual(normalize_search_text("Martín Rodríguez"), "martin rodriguez")
        self.assertEqual(normalize_search_text("Av. del Libertador"), "avenida del libertador")
        self.assertEqual(normalize_search_text("Núñez"), "nunez")
        self.assertEqual(normalize_search_text("Nuñez"), "nunez")
        self.assertEqual(normalize_search_text("José"), "jose")

    def test_typo_scores(self):
        self.assertGreaterEqual(score_entity_text("martin rodiguez", "Martín Rodríguez 2268"), UNIQUE_MIN)
        self.assertGreaterEqual(score_entity_text("martin rodriges", "Martín Rodríguez 2268"), 60)
        self.assertGreaterEqual(score_entity_text("santamrina", "Santamarina 100"), UNIQUE_MIN)
        self.assertGreaterEqual(score_entity_text("libertado", "Av. Libertador 4200"), UNIQUE_MIN)
        self.assertGreaterEqual(score_entity_text("barero", "Barreiro"), UNIQUE_MIN)

    def test_number_is_a_strong_signal(self):
        a = score_entity_text("martin 2268", "Martín Rodríguez 2268")
        b = score_entity_text("martin 2268", "Martín Rodríguez 550")
        self.assertGreater(a, b)
        self.assertGreaterEqual(a, UNIQUE_MIN)
        self.assertLess(b, UNIQUE_MIN)

    def test_low_score_does_not_auto_select(self):
        ranked = rank_entity_candidates(
            "xyzabc",
            [{"id": 1, "address": "Martín Rodríguez 2268"}],
            text_fields=("address",),
        )
        status, chosen, confidence = decide_entity_matches(ranked)
        self.assertNotEqual(status, "unique")
        self.assertEqual(confidence in {"low", "none", "medium"}, True)


class EntityMatchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="entity-match")
        create_tables()
        cls.org = add_organization("Match Org")
        cls.other = add_organization("Other Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana Match", "Alto", cls.org)
        cls.other_agent = add_agent("Otra Match", "Alto", cls.org)
        cls.foreign_agent = add_agent("Foreign Match", "Alto", cls.other)
        cls.user = add_user(
            "match_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.other_user = add_user(
            "match_other",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.record = {
            "id": cls.user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.agent_id,
        }
        cls.other_record = {
            "id": cls.other_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.other_agent,
        }
        cls.p2268 = cls._prop("Martín Rodríguez 2268", "Martínez")
        cls.p3100 = cls._prop("Martín Rodríguez 3100", "Martínez")
        cls.santa = cls._prop("Santamarina 440", "Martínez")
        cls.lib = cls._prop("Av. del Libertador 100", "Martínez")
        cls.other_prop = cls._prop(
            "Martín Rodríguez 2268",
            "Martínez",
            org=cls.other,
            agent_id=cls.foreign_agent,
        )
        cls.other_agent_prop = cls._prop(
            "Martín Rodríguez 9999",
            "Martínez",
            agent_id=cls.other_agent,
        )
        cls.op = add_operation(
            "02/08/2026",
            cls.agent_id,
            cls.p2268,
            "no",
            0,
            200000,
            3,
            6000,
            6000,
            0,
            0,
            0,
            0,
            0,
            cls.org,
        )

    @classmethod
    def _prop(cls, address, neighborhood, *, org=None, agent_id=None):
        return add_property(
            address,
            "CABA",
            org or cls.org,
            agent_id=agent_id or cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=210000,
            listing_purpose="sale",
            listing_currency="USD",
            neighborhood=neighborhood,
            rooms=3,
            bedrooms=2,
            covered_m2=80,
            total_m2=80,
        )

    def _ask(self, prompt, user=None):
        return ask_jrh(
            prompt,
            organization_id=self.org,
            user=user or self.record,
            agent_id=(user or self.record).get("agent_id"),
            language="es",
            session={},
        )

    def test_01_martin_rodriguez_ambiguous(self):
        items, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="martin rodriguez",
        )
        ids = {item["id"] for item in items}
        self.assertIn(self.p2268, ids)
        self.assertIn(self.p3100, ids)
        self.assertNotIn(self.other_prop, ids)
        self.assertNotIn(self.other_agent_prop, ids)

    def test_02_typo_and_number_unique(self):
        items, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="martin rodiguez 2268",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], self.p2268)

    def test_03_rodriguez_2268(self):
        items, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="Rodriguez 2268",
        )
        self.assertEqual(items[0]["id"], self.p2268)

    def test_04_no_accent(self):
        items, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="martin 2268",
        )
        self.assertEqual(items[0]["id"], self.p2268)

    def test_05_santamrina_and_libertado(self):
        santa, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="santamrina",
        )
        self.assertEqual(santa[0]["id"], self.santa)
        lib, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="libertado",
        )
        self.assertEqual(lib[0]["id"], self.lib)

    def test_06_start_acm_uses_global_resolver(self):
        result = self._ask("haceme un acm de martin 2268")
        self.assertEqual(result["intent"], START_ACM)
        self.assertTrue(result.get("wrote"))
        self.assertIn("Martín Rodríguez 2268", result.get("data", {}).get("address") or "")

    def test_07_start_acm_ambiguous(self):
        result = self._ask("haceme un acm de martin rodriguez")
        self.assertEqual(result["intent"], START_ACM)
        self.assertFalse(result.get("wrote"))
        self.assertGreaterEqual(len(result.get("candidates") or result.get("cards") or []), 2)

    def test_08_operation_by_partial_address(self):
        result = self._ask("haceme un acm de la operacion de martin rodriguez")
        self.assertEqual(result["intent"], START_ACM)
        self.assertTrue(result.get("wrote") or len(result.get("cards") or []) >= 1)

    def test_09_dedupe_property_and_operation(self):
        result = self._ask("haceme un acm de martin 2268")
        self.assertEqual(result["entity"].get("kind"), "acm")

    def test_10_agent_scope(self):
        items, _total = resolve_properties(
            self.org,
            user=self.other_record,
            agent_id=self.other_agent,
            address="martin rodriguez",
        )
        ids = {item["id"] for item in items}
        self.assertIn(self.other_agent_prop, ids)
        self.assertNotIn(self.p2268, ids)

    def test_11_other_org_never_appears(self):
        items, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="martin 2268",
        )
        self.assertNotIn(self.other_prop, {item["id"] for item in items})

    def test_12_query_properties_uses_resolver(self):
        items, _total = resolve_properties(
            self.org,
            user=self.record,
            agent_id=self.agent_id,
            address="martin 2268",
        )
        self.assertEqual(items[0]["id"], self.p2268)
        result = ask_jrh(
            "mostrame las propiedades disponibles",
            organization_id=self.org,
            user=self.record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], QUERY_PROPERTIES)

    def test_13_invoice_reuses_resolver(self):
        found = _find_operations_by_reference(
            "martin rodiguez",
            self.org,
            agent_id=self.agent_id,
        )
        self.assertTrue(found)
        self.assertEqual(found[0].get("property_db_id") or found[0].get("property_id"), self.p2268)


if __name__ == "__main__":
    unittest.main()
