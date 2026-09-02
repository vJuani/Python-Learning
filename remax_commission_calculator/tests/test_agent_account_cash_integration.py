"""
Phase 3A: Agent account payments integrated with cash treasury.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_account_cash.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.agent_account import (
    AgentAccountError,
    cancel_movement,
    create_movement,
)
from modules.agent_account_charges import VAT_MODE_ADD, VAT_MODE_NONE
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_balances,
)
from modules.database.agent_account_payment_repository import (
    CASH_SOURCE_AGENT_ACCOUNT_PAYMENT,
    list_payment_allocations,
)
from modules.database.agent_account_repository import (
    SOURCE_CASH,
    list_pending_charges,
)
from modules.database.cash_treasury_repository import (
    get_cash_account,
    list_cash_movements,
)
from modules.database.connection import get_connection
from modules.database.schema import (
    _column_exists,
    _migrate_agent_account,
    _migrate_agent_account_v4,
    _table_exists,
)
from web_app import app


class AgentAccountCashIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-agent-account-cash"
        create_tables()

        cls.org_a = add_organization("Cash Int Org A")
        cls.org_b = add_organization("Cash Int Org B")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Cash Agent", "Alto", cls.org_a)
        cls.admin_a = add_user(
            "cash_int_admin",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "cash_int_agent",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_id,
        )
        cls.admin_b = add_user(
            "cash_int_admin_b",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.password = "Password1"
        cls._agent_counter = 0

    def setUp(self):
        self.client = app.test_client()
        AgentAccountCashIntegrationTests._agent_counter += 1
        suffix = AgentAccountCashIntegrationTests._agent_counter
        self.agent_id = add_agent(
            f"Cash Agent {suffix}",
            "Alto",
            self.org_a,
        )

    def _login(self, username="cash_int_admin"):
        self.client.get("/logout", follow_redirects=True)
        self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def _create_charge(self, agent_id=None, **payload):
        agent_id = agent_id or self.agent_id
        base = {
            "charge_category": "fee",
            "currency": "USD",
            "amount": "100",
            "vat_mode": VAT_MODE_NONE,
            "billing_period": "Septiembre 2026",
            "movement_date": "2026-09-01",
        }
        base.update(payload)
        return create_movement(
            self.org_a,
            agent_id,
            base,
            created_by_user_id=self.admin_a,
        )

    def _pay(self, agent_id=None, **payload):
        agent_id = agent_id or self.agent_id
        base = {
            "movement_type": "payment",
            "currency": "USD",
            "amount": "40",
            "movement_date": "2026-09-02",
            "payment_method": "transfer",
        }
        base.update(payload)
        return create_movement(
            self.org_a,
            agent_id,
            base,
            created_by_user_id=self.admin_a,
            idempotency_key=payload.get("idempotency_key"),
        )

    def test_payment_usd_creates_agent_and_cash_movements(self):
        payment = self._pay(amount="50")
        self.assertEqual(payment["source_type"], SOURCE_CASH)
        self.assertIsNotNone(payment["source_id"])

        cash_rows = [
            row
            for row in list_cash_movements(self.org_a, currency="USD")
            if row["source_reference"] == str(payment["id"])
        ]
        self.assertEqual(len(cash_rows), 1)
        self.assertEqual(cash_rows[0]["movement_type"], "income")
        self.assertEqual(cash_rows[0]["source"], CASH_SOURCE_AGENT_ACCOUNT_PAYMENT)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            50.0,
        )

    def test_payment_ars_only_affects_ars_cash(self):
        usd_before = get_cash_account(self.org_a, "USD")["cached_balance"]
        payment = self._pay(currency="ARS", amount="1000")
        cash_row = next(
            row
            for row in list_cash_movements(self.org_a, currency="ARS")
            if row["source_reference"] == str(payment["id"])
        )
        self.assertAlmostEqual(cash_row["amount"], 1000.0)
        self.assertAlmostEqual(
            get_cash_account(self.org_a, "USD")["cached_balance"],
            usd_before,
        )

    def test_usd_payment_does_not_modify_ars_cash(self):
        ars_account = get_cash_account(self.org_a, "ARS") or {}
        ars_before = ars_account.get("cached_balance") or 0.0
        self._pay(currency="USD", amount="25")
        self.assertAlmostEqual(
            get_cash_account(self.org_a, "ARS")["cached_balance"]
            if get_cash_account(self.org_a, "ARS")
            else 0.0,
            ars_before,
        )

    def test_exchange_rate_is_informational_only(self):
        ars_account = get_cash_account(self.org_a, "ARS") or {}
        ars_before = ars_account.get("cached_balance") or 0.0
        payment = self._pay(
            currency="USD",
            amount="100",
            exchange_rate="1480",
        )
        cash_row = next(
            row
            for row in list_cash_movements(self.org_a, currency="USD")
            if row["source_reference"] == str(payment["id"])
        )
        self.assertAlmostEqual(cash_row["amount"], 100.0)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            100.0,
        )
        self.assertAlmostEqual(
            get_cash_account(self.org_a, "ARS")["cached_balance"]
            if get_cash_account(self.org_a, "ARS")
            else 0.0,
            ars_before,
        )

    def test_idempotency_prevents_duplicate_payment_and_cash(self):
        key = f"pay-idem-{self.agent_id}"
        cash_before = len(list_cash_movements(self.org_a))
        first = self._pay(amount="30", idempotency_key=key)
        second = self._pay(amount="30", idempotency_key=key)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(list_cash_movements(self.org_a)), cash_before + 1)

    def test_cash_failure_rolls_back_agent_payment(self):
        cash_before = len(list_cash_movements(self.org_a))
        with patch(
            "modules.database.agent_account_payment_repository.create_cash_movement_atomic",
            side_effect=ValueError("cash_failed"),
        ):
            with self.assertRaises(AgentAccountError):
                self._pay(amount="20")

        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            0.0,
        )
        self.assertEqual(len(list_cash_movements(self.org_a)), cash_before)

    def test_payment_applied_to_charge_creates_allocation(self):
        charge = self._create_charge(amount="100")
        payment = self._pay(
            amount="40",
            applied_to_movement_id=str(charge["id"]),
        )
        allocations = list_payment_allocations(
            self.org_a,
            payment["id"],
        )
        self.assertEqual(len(allocations), 1)
        self.assertEqual(
            allocations[0]["charge_movement_id"],
            charge["id"],
        )
        self.assertAlmostEqual(allocations[0]["amount"], 40.0)

    def test_partial_payment_keeps_charge_pending(self):
        charge = self._create_charge(amount="100")
        self._pay(
            amount="40",
            applied_to_movement_id=str(charge["id"]),
        )
        pending = list_pending_charges(
            self.org_a,
            self.agent_id,
            "USD",
        )
        self.assertEqual(len(pending), 1)
        self.assertAlmostEqual(pending[0]["pending_amount"], 60.0)
        self.assertEqual(
            pending[0]["payment_status"],
            "partially_paid",
        )

    def test_overpayment_leaves_credit_balance(self):
        charge = self._create_charge(amount="78.65", vat_mode=VAT_MODE_NONE)
        payment = self._pay(
            amount="100",
            applied_to_movement_id=str(charge["id"]),
        )
        allocations = list_payment_allocations(
            self.org_a,
            payment["id"],
        )
        self.assertAlmostEqual(allocations[0]["amount"], 78.65)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            21.35,
        )

    def test_full_payment_removes_charge_from_pending(self):
        charge = self._create_charge(amount="50")
        self._pay(
            amount="50",
            applied_to_movement_id=str(charge["id"]),
        )
        self.assertEqual(
            list_pending_charges(
                self.org_a,
                self.agent_id,
                "USD",
            ),
            [],
        )

    def test_cancel_payment_reverses_both_sides(self):
        payment = self._pay(amount="45")
        cash_id = payment["source_id"]
        cancel_movement(
            self.org_a,
            payment["id"],
            created_by_user_id=self.admin_a,
            reason="Error de carga",
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            0.0,
        )
        cash_rows = list_cash_movements(self.org_a)
        original = next(
            row for row in cash_rows if row["id"] == cash_id
        )
        self.assertEqual(original["status"], "reversed")

    def test_cancel_restores_charge_allocation(self):
        charge = self._create_charge(amount="100")
        payment = self._pay(
            amount="40",
            applied_to_movement_id=str(charge["id"]),
        )
        cancel_movement(
            self.org_a,
            payment["id"],
            created_by_user_id=self.admin_a,
            reason="Corrección",
        )
        pending = list_pending_charges(
            self.org_a,
            self.agent_id,
            "USD",
        )
        self.assertEqual(len(pending), 1)
        self.assertAlmostEqual(pending[0]["pending_amount"], 100.0)

    def test_agent_cannot_register_payment(self):
        self._login("cash_int_agent")
        response = self.client.post(
            f"/agent-accounts/{self.agent_id}/movements",
            data={
                "movement_type": "payment",
                "currency": "USD",
                "amount": "10",
                "payment_method": "transfer",
                "movement_date": "2026-09-02",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_other_organization_cannot_access(self):
        self._pay(amount="10")
        self._login("cash_int_admin_b")
        response = self.client.get(
            f"/agent-accounts/{self.agent_id}",
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 404))

    def test_agent_payment_failure_does_not_modify_cash(self):
        cash_before = len(list_cash_movements(self.org_a))
        with patch(
            "modules.database.agent_account_payment_repository.execute_insert",
            side_effect=RuntimeError("agent_insert_failed"),
        ):
            with self.assertRaises(RuntimeError):
                self._pay(amount="15")

        self.assertEqual(len(list_cash_movements(self.org_a)), cash_before)

    def test_cash_and_agent_movements_are_linked(self):
        payment = self._pay(amount="33")
        cash_row = next(
            row
            for row in list_cash_movements(self.org_a)
            if row["source_reference"] == str(payment["id"])
        )
        self.assertEqual(payment["source_id"], cash_row["id"])
        self.assertEqual(
            cash_row["source_reference"],
            str(payment["id"]),
        )

    def test_balances_match_after_operations(self):
        self._create_charge(amount="100")
        first_payment = self._pay(amount="40")
        second_payment = self._pay(amount="40")
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            -20.0,
        )
        payment_ids = {
            str(first_payment["id"]),
            str(second_payment["id"]),
        }
        payment_total = sum(
            row["amount"]
            for row in list_cash_movements(self.org_a, currency="USD")
            if row["source_reference"] in payment_ids
        )
        self.assertAlmostEqual(payment_total, 80.0)

    def test_migration_v4_idempotent(self):
        connection = get_connection()
        cursor = connection.cursor()
        try:
            _migrate_agent_account(cursor)
            connection.commit()
            self.assertTrue(
                _table_exists(
                    cursor,
                    "agent_account_payment_allocations",
                )
            )
            _migrate_agent_account_v4(cursor)
            connection.commit()
            self.assertTrue(
                _column_exists(
                    cursor,
                    "agent_account_payment_allocations",
                    "amount",
                )
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
