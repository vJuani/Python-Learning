"""
Tests for agent current account UX/logic (phase 1+2).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_account_v2.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.agent_account import (
    AgentAccountError,
    cancel_movement,
    create_movement,
)
from modules.agent_account_presentation import (
    enrich_movement_for_display,
    filter_movements_for_display,
    human_balance,
    movement_display_amount,
    movement_is_internal_reversal,
)
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_account_movement,
    get_agent_balances,
    list_agent_account_movements,
)
from modules.database.connection import get_connection
from modules.database.schema import (
    _migrate_agent_account,
)
from web_app import app


class AgentAccountV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-agent-account-v2"
        create_tables()

        cls.org_a = add_organization("AA V2 Org A")
        cls.org_b = add_organization("AA V2 Org B")
        pwd = hash_password("Password1")

        cls.agent_a_id = add_agent("Agent V2 A", "Alto", cls.org_a)
        cls.agent_b_id = add_agent("Agent V2 B", "Alto", cls.org_b)
        cls.admin_a = add_user(
            "aa_v2_admin_a",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user_a = add_user(
            "aa_v2_agent_a",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a_id,
        )
        cls.admin_b = add_user(
            "aa_v2_admin_b",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.password = "Password1"

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        self.client.get("/logout", follow_redirects=True)
        return self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def test_fee_increases_pending_balance(self):
        agent_id = add_agent("Fee Agent", "Alto", self.org_a)
        create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "fee",
                "currency": "USD",
                "amount": "78",
                "description": "Fee mensual",
                "movement_date": "2026-09-01",
            },
            created_by_user_id=self.admin_a,
        )
        balance = get_agent_balances(self.org_a, agent_id)["USD"]
        self.assertAlmostEqual(balance, -78.0)
        display = human_balance(balance, currency="USD")
        self.assertEqual(display["label_key"], "agent_account_balance_pending")
        self.assertAlmostEqual(float(display["amount"]), 78.0)

    def test_payment_reduces_pending_balance(self):
        agent_id = add_agent("Payment Agent", "Alto", self.org_a)
        create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "fee",
                "currency": "USD",
                "amount": "78",
                "description": "Fee",
                "movement_date": "2026-09-01",
            },
            created_by_user_id=self.admin_a,
        )
        create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "75",
                "description": "Pago recibido",
                "movement_date": "2026-09-02",
                "payment_method": "transfer",
            },
            created_by_user_id=self.admin_a,
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            -3.0,
        )

    def test_payment_display_is_positive(self):
        movement = create_movement(
            self.org_a,
            self.agent_a_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "75",
                "description": "Pago fee",
                "movement_date": "2026-09-02",
                "payment_method": "transfer",
            },
            created_by_user_id=self.admin_a,
        )
        display = movement_display_amount(movement)
        self.assertEqual(display["tone"], "credit")
        self.assertTrue(display["formatted"].startswith("+"))

    def test_balance_in_favor_display(self):
        agent_id = add_agent("Credit Agent", "Alto", self.org_a)
        create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "commission",
                "currency": "USD",
                "amount": "120",
                "description": "Comisión",
                "movement_date": "2026-09-03",
            },
            created_by_user_id=self.admin_a,
        )
        display = human_balance(120.0, currency="USD")
        self.assertEqual(display["label_key"], "agent_account_balance_in_favor")
        self.assertEqual(display["tone"], "credit")

    def test_exchange_rate_saved_without_mixing_balances(self):
        agent_id = add_agent("FX Agent", "Alto", self.org_a)
        movement = create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "75",
                "description": "Pago USD",
                "movement_date": "2026-09-02",
                "payment_method": "transfer",
                "exchange_rate": "1480",
            },
            created_by_user_id=self.admin_a,
        )
        self.assertAlmostEqual(movement["exchange_rate"], 1480.0)
        self.assertAlmostEqual(
            movement["equivalent_amount_ars"],
            75 * 1480.0,
        )
        balances = get_agent_balances(self.org_a, agent_id)
        self.assertAlmostEqual(balances["ARS"], 0.0)
        self.assertAlmostEqual(balances["USD"], 75.0)

    def test_cancel_requires_reason(self):
        agent_id = add_agent("Cancel Agent", "Alto", self.org_a)
        movement = create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "fee",
                "currency": "USD",
                "amount": "50",
                "description": "Fee",
                "movement_date": "2026-09-04",
            },
            created_by_user_id=self.admin_a,
        )
        with self.assertRaises(AgentAccountError) as ctx:
            cancel_movement(
                self.org_a,
                movement["id"],
                created_by_user_id=self.admin_a,
                reason="",
            )
        self.assertEqual(
            ctx.exception.message_key,
            "agent_account_err_cancel_reason_required",
        )

    def test_cancel_keeps_audit_and_hides_internal_row(self):
        agent_id = add_agent("Audit Agent", "Alto", self.org_a)
        movement = create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "fee",
                "currency": "USD",
                "amount": "80",
                "description": "Fee mensual",
                "movement_date": "2026-09-05",
            },
            created_by_user_id=self.admin_a,
        )
        cancel_movement(
            self.org_a,
            movement["id"],
            created_by_user_id=self.admin_a,
            reason="Moneda incorrecta",
        )
        original = get_agent_account_movement(
            movement["id"],
            self.org_a,
        )
        self.assertEqual(original["status"], "reversed")
        self.assertEqual(
            original["cancellation_reason"],
            "Moneda incorrecta",
        )

        all_rows = list_agent_account_movements(
            self.org_a,
            agent_id,
            include_internal_reversals=True,
        )
        internal = [
            row
            for row in all_rows
            if movement_is_internal_reversal(row)
        ]
        self.assertEqual(len(internal), 1)

        visible = filter_movements_for_display(
            all_rows,
            show_cancelled=True,
        )
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["id"], movement["id"])

    def test_agent_cannot_cancel(self):
        agent_id = add_agent("No Cancel Agent", "Alto", self.org_a)
        movement = create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "fee",
                "currency": "USD",
                "amount": "20",
                "description": "Fee",
                "movement_date": "2026-09-06",
            },
            created_by_user_id=self.admin_a,
        )
        self._login("aa_v2_agent_a")
        response = self.client.post(
            f"/agent-accounts/movements/{movement['id']}/cancel",
            data={"cancellation_reason": "hack"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_admin_other_org_forbidden(self):
        self._login("aa_v2_admin_a")
        response = self.client.get(
            f"/agent-accounts/{self.agent_b_id}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

    def test_templates_render_mobile_and_i18n(self):
        self._login("aa_v2_admin_a")
        response = self.client.get(
            "/agent-accounts",
            headers={"User-Agent": "Mozilla/5.0 (iPhone)"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"aa-agent-cards", response.data)
        self.assertIn(
            "Total por cobrar".encode("utf-8"),
            response.data,
        )

        response = self.client.get(
            f"/agent-accounts/{self.agent_a_id}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Importe", response.data)

        self._login("aa_v2_agent_a")
        response = self.client.get(
            "/my-account",
            headers={"User-Agent": "Mozilla/5.0 (iPhone)"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"aa-move-cards", response.data)

    def test_migration_idempotent_twice(self):
        connection = get_connection()
        cursor = connection.cursor()
        try:
            _migrate_agent_account(cursor)
            _migrate_agent_account(cursor)
            connection.commit()
            cursor.execute(
                """
                SELECT cancellation_reason
                FROM agent_account_movements
                LIMIT 1
                """
            )
        finally:
            connection.close()

    def test_enriched_movement_for_ui(self):
        movement = create_movement(
            self.org_a,
            self.agent_a_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "75",
                "description": "Pago",
                "movement_date": "2026-09-07",
                "payment_method": "transfer",
            },
            created_by_user_id=self.admin_a,
        )
        enriched = enrich_movement_for_display(movement)
        self.assertIn("display_amount", enriched)
        self.assertIn("display_balance_after", enriched)


if __name__ == "__main__":
    unittest.main()
