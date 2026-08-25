"""
Tests for Team Leader / Junior wallet ledger.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_wallet.db"
)

from modules.agent_wallet import (
    calculate_team_leader_income,
    post_wallet_for_approved_operation,
    reverse_wallet_for_operation,
)
from modules.calculations import (
    calculate_abao,
    calculate_agent_payment,
    calculate_martillero,
    calculate_total_commission,
)
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    create_tables,
    list_wallet_movements_for_agent,
    list_wallet_movements_for_operation,
    sum_wallet_by_type,
)
from modules.operations import (
    change_operation_status,
    save_calculated_operation,
)
from modules.workflow import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from web_app import app


class AgentWalletTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org = add_organization("Wallet Org")
        cls.jose = add_agent(
            "José Luis Barreiro",
            "RAPP",
            cls.org,
        )
        cls.tomas = add_agent(
            "Tomas Pasman",
            "Puro",
            cls.org,
        )
        cls.pablo = add_agent(
            "Pablo Reynals",
            "Junior",
            cls.org,
            team_leader_agent_id=cls.tomas,
        )
        cls.property_id = add_property(
            "Test Addr 100",
            "CABA",
            cls.org,
            agent_id=cls.pablo,
            status="approved",
        )

    def _operation_dict(
        self,
        *,
        sale_price=100000,
        commission_rate=7,
        was_invoiced="no",
        vat_amount=0,
        jurisdiction="CABA",
        agent_name="Pablo Reynals",
        agent_type="Junior",
    ):
        from modules.operations import calculate_operation_details

        return calculate_operation_details(
            agent_name,
            agent_type,
            "Test Addr 100",
            jurisdiction,
            sale_price,
            commission_rate,
            was_invoiced,
            vat_amount=vat_amount,
        )

    def test_team_leader_formula_matches_existing_rules(self):
        total = calculate_total_commission(100000, 7)
        self.assertAlmostEqual(total, 7000.0, places=2)

        abao = calculate_abao("no", "CABA", 0)
        base = total - abao
        breakdown = calculate_team_leader_income(base)

        junior = calculate_agent_payment("Junior", base, 0)
        martillero = calculate_martillero(base)
        puro = calculate_agent_payment("Puro", base, martillero)

        self.assertAlmostEqual(
            breakdown["junior_payment"],
            junior,
            places=2,
        )
        self.assertAlmostEqual(
            breakdown["puro_payment"],
            puro,
            places=2,
        )
        self.assertAlmostEqual(
            breakdown["team_leader_income"],
            puro - junior,
            places=2,
        )
        # Real code: 5376 - 3150 = 2226 (not 5600-3150)
        self.assertAlmostEqual(
            breakdown["team_leader_income"],
            2226.0,
            places=2,
        )

    def test_team_leader_with_abao(self):
        total = 7000
        abao = calculate_abao("yes", "PBA", 210)
        self.assertEqual(abao, 60)
        base = total - abao
        breakdown = calculate_team_leader_income(base)
        self.assertAlmostEqual(
            breakdown["team_leader_income"],
            2206.92,
            places=2,
        )

    def test_draft_pending_rejected_do_not_post(self):
        for status in (
            STATUS_DRAFT,
            STATUS_PENDING,
            STATUS_REJECTED,
        ):
            op = self._operation_dict()
            op_id, _ = save_calculated_operation(
                self.pablo,
                self.property_id,
                self.org,
                op,
                status=status,
            )
            movements = list_wallet_movements_for_operation(
                self.org,
                op_id,
            )
            self.assertEqual(movements, [])

    def test_approved_posts_own_and_team_leader_income(self):
        op = self._operation_dict()
        op_id, _ = save_calculated_operation(
            self.pablo,
            self.property_id,
            self.org,
            op,
            status=STATUS_APPROVED,
        )

        movements = list_wallet_movements_for_operation(
            self.org,
            op_id,
        )
        types = {item["movement_type"] for item in movements}
        self.assertIn("own_commission", types)
        self.assertIn("team_leader_income", types)

        own = next(
            item
            for item in movements
            if item["movement_type"] == "own_commission"
        )
        tl = next(
            item
            for item in movements
            if item["movement_type"] == "team_leader_income"
        )

        self.assertEqual(own["agent_id"], self.pablo)
        self.assertAlmostEqual(own["amount"], 3150.0, places=2)

        self.assertEqual(tl["agent_id"], self.tomas)
        self.assertEqual(tl["source_agent_id"], self.pablo)
        self.assertAlmostEqual(tl["amount"], 2226.0, places=2)

        # Idempotent second post
        post_wallet_for_approved_operation(self.org, op_id)
        again = list_wallet_movements_for_operation(
            self.org,
            op_id,
        )
        self.assertEqual(len(again), 2)

        tl_for_op = sum(
            item["amount"]
            for item in again
            if item["movement_type"] == "team_leader_income"
        )
        self.assertAlmostEqual(tl_for_op, 2226.0, places=2)

    def test_approve_via_status_change_posts(self):
        op = self._operation_dict()
        op_id, _ = save_calculated_operation(
            self.pablo,
            self.property_id,
            self.org,
            op,
            status=STATUS_PENDING,
        )
        self.assertEqual(
            list_wallet_movements_for_operation(
                self.org,
                op_id,
            ),
            [],
        )

        change_operation_status(
            op_id,
            self.org,
            STATUS_APPROVED,
        )
        movements = list_wallet_movements_for_operation(
            self.org,
            op_id,
        )
        self.assertEqual(len(movements), 2)

        # Second approve path does not duplicate
        change_operation_status(
            op_id,
            self.org,
            STATUS_APPROVED,
        )
        self.assertEqual(
            len(
                list_wallet_movements_for_operation(
                    self.org,
                    op_id,
                )
            ),
            2,
        )

    def test_reversal_keeps_history(self):
        op = self._operation_dict()
        op_id, _ = save_calculated_operation(
            self.pablo,
            self.property_id,
            self.org,
            op,
            status=STATUS_APPROVED,
        )

        reverse_wallet_for_operation(self.org, op_id)
        movements = list_wallet_movements_for_operation(
            self.org,
            op_id,
        )
        types = [item["movement_type"] for item in movements]
        self.assertEqual(types.count("own_commission"), 1)
        self.assertEqual(types.count("team_leader_income"), 1)
        self.assertEqual(types.count("reversal"), 2)

        totals = sum_wallet_by_type(self.org, self.tomas)
        # Net TL for this op should be ~0 after reverse
        # (may include prior tests' income on same Tomas)
        # Filter by summing this op movements for Tomas
        tomas_net = sum(
            item["amount"]
            for item in movements
            if item["agent_id"] == self.tomas
        )
        self.assertAlmostEqual(tomas_net, 0.0, places=2)

        # Double reverse is idempotent
        reverse_wallet_for_operation(self.org, op_id)
        self.assertEqual(
            len(
                list_wallet_movements_for_operation(
                    self.org,
                    op_id,
                )
            ),
            4,
        )

    def test_jose_rapp_without_tl_only_own(self):
        prop = add_property(
            "Jose Prop",
            "CABA",
            self.org,
            agent_id=self.jose,
            status="approved",
        )
        op = self._operation_dict(
            agent_name="José Luis Barreiro",
            agent_type="RAPP",
        )
        op_id, _ = save_calculated_operation(
            self.jose,
            prop,
            self.org,
            op,
            status=STATUS_APPROVED,
        )
        movements = list_wallet_movements_for_operation(
            self.org,
            op_id,
        )
        self.assertEqual(len(movements), 1)
        self.assertEqual(
            movements[0]["movement_type"],
            "own_commission",
        )
        self.assertEqual(movements[0]["agent_id"], self.jose)

    def test_tenant_isolation_wallet(self):
        other = add_organization("Other Wallet Org")
        self.assertEqual(
            list_wallet_movements_for_agent(other, self.tomas),
            [],
        )


if __name__ == "__main__":
    unittest.main()
