"""Tests for conditional commission lines and multi 'other' docs."""

from __future__ import annotations

import unittest

from modules.operation_summary import build_commission_lines
from modules.database.operation_documents_repository import (
    DOC_TYPE_OTHER,
    allows_multiple_documents,
)


class CommissionLinesTests(unittest.TestCase):
    def test_hides_abao_when_zero(self):
        lines = build_commission_lines(
            {
                "commission_rate": 3,
                "total_commission": 1000,
                "abao": 0,
                "commission_after_abao": 1000,
                "martillero": 0,
                "agent_payment": 600,
                "office_payment": 400,
                "office_total": 400,
            },
            "es",
        )
        keys = [line["key"] for line in lines]
        self.assertIn("commission", keys)
        self.assertNotIn("abao", keys)
        self.assertNotIn("commission_after_abao", keys)
        self.assertNotIn("martillero", keys)

    def test_shows_abao_chain_when_positive(self):
        lines = build_commission_lines(
            {
                "commission_rate": 3,
                "total_commission": 1000,
                "abao": 50,
                "commission_after_abao": 950,
                "martillero": 38,
                "agent_payment": 547.2,
                "office_payment": 364.8,
                "office_total": 464.8,
            },
            "es",
        )
        keys = [line["key"] for line in lines]
        self.assertEqual(
            keys[:4],
            [
                "commission_rate",
                "total_commission",
                "abao",
                "commission_after_abao",
            ],
        )
        self.assertIn("martillero", keys)


class OtherDocTypeTests(unittest.TestCase):
    def test_other_allows_multiple(self):
        self.assertTrue(allows_multiple_documents(DOC_TYPE_OTHER))
        self.assertFalse(
            allows_multiple_documents("martillero_client")
        )


if __name__ == "__main__":
    unittest.main()
