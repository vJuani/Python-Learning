"""FASE 4H — ACM V3: explanation, sources, connectors, simulator."""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_acm_v3.db")
os.environ.pop("DATABASE_URL", None)
os.environ["JRH_AI_PROVIDER"] = "mock"

from modules.acm_connectors import (  # noqa: E402
    PropertySourceConnector,
    SourceNotConnectedError,
    ZonapropConnector,
    connector_registry,
)
from modules.acm_dedupe import deduplicate_comparable_candidates  # noqa: E402
from modules.acm_engine import compute_price_scenario  # noqa: E402
from modules.acm_explain import (  # noqa: E402
    build_acm_facts,
    explain_acm,
    explanation_uses_only_fact_numbers,
    fallback_market_highlights,
)
from modules.acm_sources import format_diff_label  # noqa: E402
from modules.database import create_tables  # noqa: E402
from modules.database.property_sync_repository import (  # noqa: E402
    upsert_property_external_identity,
)
from modules.formatting import format_money  # noqa: E402


class AcmV3UnitTests(unittest.TestCase):
    def test_human_diff_not_technical(self):
        label = format_diff_label(["area_delta", "-19.8"], language="es")
        self.assertIn("menos superficie", label)
        self.assertNotIn("acm_diff_", label)
        self.assertNotIn("[", label)

    def test_price_scenario_deterministic(self):
        scenario = compute_price_scenario(360000, 390000, 366000, 413000)
        self.assertEqual(scenario["band"], "below")
        self.assertFalse(scenario["in_range"])
        self.assertEqual(scenario["delta_vs_market_pct"], Decimal("-7.7"))

    def test_explanation_fallback_uses_facts(self):
        facts = {
            "estimated": "389724",
            "estimated_label": "USD 389.724,00",
            "positioning_band": "below",
            "positioning_delta_pct": "-17.9",
            "median_ppm2_label": "USD 2.975,00",
            "closings": 2,
            "confidence": "medium",
        }
        explained = explain_acm(
            facts,
            narrative="El valor es USD 999.999 porque sí",
            language="es",
        )
        self.assertEqual(explained["source"], "fallback")
        self.assertNotIn("999.999", explained["text"])
        self.assertTrue(
            explanation_uses_only_fact_numbers(explained["text"], facts)
        )
        bullets = fallback_market_highlights(facts, language="es")
        self.assertTrue(any("17,9" in item or "17.9" in item for item in bullets))

    def test_connector_interface_does_not_touch_db(self):
        connector = ZonapropConnector()
        self.assertFalse(connector.is_connected())
        with self.assertRaises(SourceNotConnectedError):
            connector.search_comparables({"neighborhood": "Martínez"})
        self.assertIsInstance(connector, PropertySourceConnector)
        self.assertIn("zonaprop", connector_registry())

    def test_dedupe_identity_only(self):
        rows = [
            {"organization_id": 1, "source_type": "zonaprop", "external_id": "A1"},
            {"organization_id": 1, "source_type": "zonaprop", "external_id": "A1"},
            {"organization_id": 1, "property_id": 9},
            {"organization_id": 1, "property_id": 9},
            {"address": "unique loose row"},
        ]
        unique = deduplicate_comparable_candidates(rows)
        self.assertEqual(len(unique), 3)

    def test_money_es(self):
        self.assertEqual(
            format_money(389724, currency="USD", language="es"),
            "USD 389.724,00",
        )


class AcmV3SyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_tables()
        from modules.database import add_organization, add_property

        cls.org = add_organization("Sync Org")
        cls.prop = add_property("Italia 1341", "CABA", cls.org)

    def test_external_identity_is_idempotent(self):
        first = upsert_property_external_identity(
            self.org,
            self.prop,
            external_source="remax_web",
            external_id="MLS-100",
        )
        second = upsert_property_external_identity(
            self.org,
            self.prop,
            external_source="remax_web",
            external_id="MLS-100",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["external_id"], "MLS-100")
        from modules.database import add_property as _add

        other_id = _add("Otra 1", "CABA", self.org)
        reused = upsert_property_external_identity(
            self.org,
            other_id,
            external_source="remax_web",
            external_id="MLS-100",
        )
        self.assertEqual(reused["id"], first["id"])


if __name__ == "__main__":
    unittest.main()
