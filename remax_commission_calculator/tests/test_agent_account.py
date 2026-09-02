"""
Tests for agent current account (cuenta corriente) module.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_account.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.agent_account import (
    AgentAccountError,
    create_movement,
    reverse_movement,
)
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_account_movement,
    get_agent_balances,
    migrate_schema,
)
from modules.database.connection import get_connection
from modules.database.schema import _migrate_agent_account
from web_app import app


class AgentAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-agent-account"
        create_tables()

        cls.org_a = add_organization("Agent Account Org A")
        cls.org_b = add_organization("Agent Account Org B")
        pwd = hash_password("Password1")

        cls.agent_a_id = add_agent(
            "Account Agent A",
            "Alto",
            cls.org_a,
        )
        cls.agent_b_id = add_agent(
            "Account Agent B",
            "Alto",
            cls.org_b,
        )
        cls.admin_a = add_user(
            "aa_admin_a",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user_a = add_user(
            "aa_agent_a",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a_id,
        )
        cls.admin_b = add_user(
            "aa_admin_b",
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

    def test_admin_creates_movement_for_org_agent(self):
        movement = create_movement(
            self.org_a,
            self.agent_a_id,
            {
                "movement_type": "commission",
                "currency": "USD",
                "amount": "150",
                "description": "Manual commission",
                "movement_date": "2026-01-15",
            },
            created_by_user_id=self.admin_a,
        )
        self.assertEqual(movement["status"], "confirmed")
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_a_id)["USD"],
            150.0,
        )

    def test_admin_cannot_access_other_org_agent(self):
        self._login("aa_admin_a")
        response = self.client.get(
            f"/agent-accounts/{self.agent_b_id}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            f"/agent-accounts/{self.agent_b_id}/movements",
            data={
                "movement_type": "credit",
                "currency": "ARS",
                "amount": "100",
                "description": "Cross org",
                "movement_date": "2026-01-15",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

    def test_agent_only_sees_own_account(self):
        create_movement(
            self.org_a,
            self.agent_a_id,
            {
                "movement_type": "credit",
                "currency": "ARS",
                "amount": "500",
                "description": "Own credit",
                "movement_date": "2026-02-01",
            },
            created_by_user_id=self.admin_a,
        )
        self._login("aa_agent_a")
        response = self.client.get("/my-account")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Own credit", response.data)

        response = self.client.get(
            f"/agent-accounts/{self.agent_a_id}",
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/my-account", response.headers.get("Location", ""))

    def test_agent_cannot_create_movement(self):
        agent_id = add_agent(
            "No Create Agent",
            "Alto",
            self.org_a,
        )
        before = get_agent_balances(self.org_a, agent_id)
        self._login("aa_agent_a")
        response = self.client.post(
            f"/agent-accounts/{agent_id}/movements",
            data={
                "movement_type": "credit",
                "currency": "USD",
                "amount": "50",
                "description": "Agent attempt",
                "movement_date": "2026-02-01",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            response.headers.get("Location", ""),
            ("/", "http://localhost/"),
        )
        after = get_agent_balances(self.org_a, agent_id)
        self.assertAlmostEqual(after["USD"], before["USD"])
        self.assertAlmostEqual(after["ARS"], before["ARS"])

    def test_ars_and_usd_balances_are_independent(self):
        agent_id = add_agent(
            "Dual Currency Agent",
            "Medio",
            self.org_a,
        )
        create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "commission",
                "currency": "USD",
                "amount": "200",
                "description": "USD commission",
                "movement_date": "2026-03-01",
            },
            created_by_user_id=self.admin_a,
        )
        create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "charge",
                "currency": "ARS",
                "amount": "1000",
                "description": "ARS charge",
                "movement_date": "2026-03-02",
            },
            created_by_user_id=self.admin_a,
        )
        balances = get_agent_balances(self.org_a, agent_id)
        self.assertAlmostEqual(balances["USD"], 200.0)
        self.assertAlmostEqual(balances["ARS"], -1000.0)

    def test_reversal_keeps_audit_trail(self):
        agent_id = add_agent(
            "Reversal Agent",
            "Alto",
            self.org_a,
        )
        movement = create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "fee",
                "currency": "USD",
                "amount": "75",
                "description": "Office fee",
                "movement_date": "2026-04-01",
            },
            created_by_user_id=self.admin_a,
        )
        reversal = reverse_movement(
            self.org_a,
            movement["id"],
            created_by_user_id=self.admin_a,
            reason="Billing error",
        )
        original = get_agent_account_movement(
            movement["id"],
            self.org_a,
        )
        self.assertEqual(original["status"], "reversed")
        self.assertEqual(
            reversal["reversed_movement_id"],
            movement["id"],
        )
        self.assertEqual(reversal["reversal_reason"], "Billing error")
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            0.0,
        )

    def test_idempotency_prevents_duplicate_submit(self):
        agent_id = add_agent(
            "Idempotent Agent",
            "Alto",
            self.org_a,
        )
        payload = {
            "movement_type": "credit",
            "currency": "USD",
            "amount": "42",
            "description": "Once only",
            "movement_date": "2026-05-01",
        }
        first = create_movement(
            self.org_a,
            agent_id,
            payload,
            created_by_user_id=self.admin_a,
            idempotency_key="idem-aa-1",
        )
        second = create_movement(
            self.org_a,
            agent_id,
            payload,
            created_by_user_id=self.admin_a,
            idempotency_key="idem-aa-1",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            42.0,
        )

    def test_migration_is_idempotent(self):
        connection = get_connection()
        cursor = connection.cursor()
        try:
            _migrate_agent_account(cursor)
            _migrate_agent_account(cursor)
            connection.commit()
            cursor.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                    AND name = 'agent_account_movements'
                """
            )
            self.assertIsNotNone(cursor.fetchone())
        finally:
            connection.close()

    def test_empty_db_returns_zero_balance(self):
        agent_id = add_agent(
            "Empty Agent",
            "Bajo",
            self.org_a,
        )
        balances = get_agent_balances(self.org_a, agent_id)
        self.assertAlmostEqual(balances["ARS"], 0.0)
        self.assertAlmostEqual(balances["USD"], 0.0)

    def test_mobile_template_renders_without_error(self):
        self._login("aa_admin_a")
        response = self.client.get(
            "/agent-accounts",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 "
                    "like Mac OS X) AppleWebKit/605.1.15"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"aa-agent-cards",
            response.data,
        )
        self._login("aa_agent_a")
        response = self.client.get(
            "/my-account",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 "
                    "like Mac OS X) AppleWebKit/605.1.15"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"aa-move-cards",
            response.data,
        )


if __name__ == "__main__":
    unittest.main()
