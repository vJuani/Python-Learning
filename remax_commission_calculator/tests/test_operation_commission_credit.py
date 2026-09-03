"""Phase 3B: operation commission credits in agent current account."""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_operation_commission_credit.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
    get_agent_balances,
)
from modules.database.agent_account_repository import (
    list_agent_account_movements,
    list_operation_commission_movements,
)
from modules.database.cash_treasury_repository import (
    list_cash_movements,
)
from modules.database.connection import get_connection
from modules.database.schema import (
    _column_exists,
    _migrate_agent_account,
)
from modules.operation_commission_credit import (
    OperationCommissionError,
    build_operation_commission_state,
    credit_operation_commission,
    reverse_operation_commission,
)
from modules.operations import (
    calculate_operation_details,
    save_calculated_operation,
)
from web_app import app


class OperationCommissionCreditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "operation-commission-test"
        create_tables()

        cls.org_a = add_organization("Commission Credit Org A")
        cls.org_b = add_organization("Commission Credit Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.admin_a = add_user(
            "commission_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.admin_b = add_user(
            "commission_admin_b",
            password_hash,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.agent_id = add_agent(
            "José Luis Barreiro",
            "Alto",
            cls.org_a,
        )
        cls.agent_user = add_user(
            "commission_agent",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_id,
        )
        cls._counter = 0

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username="commission_admin_a"):
        self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def _operation(
        self,
        *,
        side="buyer",
        currency="USD",
        status="approved",
        was_invoiced="yes",
        agent_payment=None,
        agent_id=None,
        organization_id=None,
    ):
        organization_id = organization_id or self.org_a
        agent_id = agent_id or self.agent_id
        OperationCommissionCreditTests._counter += 1
        number = OperationCommissionCreditTests._counter
        property_id = add_property(
            f"Luis García {650 + number}",
            "CABA",
            organization_id,
            agent_id=agent_id,
            status="approved",
        )
        operation = calculate_operation_details(
            "José Luis Barreiro",
            "Alto",
            f"Luis García {650 + number}",
            "CABA",
            100000,
            3,
            was_invoiced,
            vat_amount=0,
        )
        operation["currency"] = currency
        operation["original_amount"] = (
            100000 if currency == "USD" else 150000000
        )
        operation["exchange_rate"] = (
            1 if currency == "USD" else 1500
        )
        if agent_payment is not None:
            operation["agent_payment"] = agent_payment

        seller = side in ("seller", "both")
        buyer = side in ("buyer", "both")
        operation["side_data"] = {
            "seller_active": seller,
            "buyer_active": buyer,
            "seller_commission_percent": 3 if seller else 0,
            "buyer_commission_percent": 3 if buyer else 0,
            "seller_commission_amount": 3000 if seller else 0,
            "buyer_commission_amount": 3000 if buyer else 0,
        }
        operation_id, _ = save_calculated_operation(
            agent_id,
            property_id,
            organization_id,
            operation,
            status=status,
            created_by_user_id=self.admin_a,
        )
        return operation_id

    def _credit(
        self,
        operation_id,
        *,
        amount="1728",
        currency="USD",
        organization_id=None,
    ):
        return credit_operation_commission(
            organization_id or self.org_a,
            operation_id,
            amount=amount,
            currency=currency,
            created_by_user_id=self.admin_a,
        )

    # 1, 4
    def test_valid_operation_creates_linked_account_commission(self):
        operation_id = self._operation()
        movement = self._credit(operation_id)

        self.assertEqual(movement["movement_type"], "commission")
        self.assertEqual(movement["source_type"], "operation")
        self.assertEqual(movement["source_id"], operation_id)
        self.assertEqual(movement["commission_purpose"], "own_commission")
        self.assertEqual(movement["commission_source_currency"], "USD")
        self.assertAlmostEqual(
            movement["commission_source_amount"],
            1728.0,
        )
        self.assertEqual(
            build_operation_commission_state(
                self.org_a,
                operation_id,
            )["state"],
            "credited",
        )

    # 2
    def test_usd_commission_only_affects_usd_balance(self):
        operation_id = self._operation()
        before = get_agent_balances(self.org_a, self.agent_id)
        self._credit(operation_id, amount="1250.25", currency="USD")
        after = get_agent_balances(self.org_a, self.agent_id)

        self.assertAlmostEqual(
            after["USD"] - before["USD"],
            1250.25,
        )
        self.assertAlmostEqual(after["ARS"], before["ARS"])

    # 3
    def test_ars_commission_only_affects_ars_balance(self):
        operation_id = self._operation(currency="ARS")
        before = get_agent_balances(self.org_a, self.agent_id)
        self._credit(
            operation_id,
            amount="125000,50",
            currency="ARS",
        )
        after = get_agent_balances(self.org_a, self.agent_id)

        self.assertAlmostEqual(
            after["ARS"] - before["ARS"],
            125000.5,
        )
        self.assertAlmostEqual(after["USD"], before["USD"])

    # 5
    def test_double_confirm_is_idempotent(self):
        operation_id = self._operation()
        before_count = len(
            list_agent_account_movements(
                self.org_a,
                self.agent_id,
            )
        )
        first = self._credit(operation_id, amount="100")
        second = self._credit(operation_id, amount="100")

        self.assertEqual(first["id"], second["id"])
        after = list_agent_account_movements(
            self.org_a,
            self.agent_id,
        )
        self.assertEqual(len(after), before_count + 1)

    # 6
    def test_other_organization_is_rejected(self):
        operation_id = self._operation()
        with self.assertRaises(OperationCommissionError) as ctx:
            self._credit(
                operation_id,
                organization_id=self.org_b,
            )
        self.assertEqual(
            ctx.exception.message_key,
            "operation_commission_err_not_found",
        )

    # 7
    def test_agent_cannot_credit(self):
        operation_id = self._operation()
        self._login("commission_agent")
        response = self.client.post(
            f"/operations/{operation_id}/commission/credit",
            data={"amount": "100", "currency": "USD"},
        )
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(
            build_operation_commission_state(
                self.org_a,
                operation_id,
            )["state"],
            "ready",
        )

    # 8
    def test_seller_side_snapshot(self):
        operation_id = self._operation(side="seller")
        state = build_operation_commission_state(
            self.org_a,
            operation_id,
        )
        movement = self._credit(operation_id)
        self.assertEqual(state["commission_side"], "seller")
        self.assertEqual(movement["commission_side"], "seller")

    # 9
    def test_buyer_side_snapshot(self):
        operation_id = self._operation(side="buyer")
        movement = self._credit(operation_id)
        self.assertEqual(movement["commission_side"], "buyer")

    # 10
    def test_both_sides_are_one_consolidated_credit(self):
        operation_id = self._operation(side="both")
        movement = self._credit(operation_id)
        history = list_operation_commission_movements(
            self.org_a,
            operation_id,
            self.agent_id,
            "both",
        )
        self.assertEqual(movement["commission_side"], "both")
        self.assertEqual(len(history), 1)

    # 11, 12
    def test_reverse_offsets_balance_and_returns_to_ready(self):
        operation_id = self._operation()
        before = get_agent_balances(self.org_a, self.agent_id)["USD"]
        self._credit(operation_id, amount="875.25")
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            before + 875.25,
        )
        reverse_operation_commission(
            self.org_a,
            operation_id,
            created_by_user_id=self.admin_a,
            reason="Importe incorrecto",
        )

        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            before,
        )
        state = build_operation_commission_state(
            self.org_a,
            operation_id,
        )
        self.assertEqual(state["state"], "ready")
        self.assertIsNotNone(state["last_reversed_movement"])
        technical = [
            row
            for row in list_agent_account_movements(
                self.org_a,
                self.agent_id,
                include_internal_reversals=True,
            )
            if row.get("is_internal_reversal")
        ]
        self.assertTrue(technical)

    # 13
    def test_operation_without_valid_agent_is_not_ready(self):
        operation_id = self._operation()
        with patch(
            "modules.operation_commission_credit.get_agent_record",
            return_value=None,
        ):
            state = build_operation_commission_state(
                self.org_a,
                operation_id,
            )
            self.assertEqual(state["state"], "not_ready")
            self.assertIn(
                "operation_commission_agent_missing",
                state["reasons"],
            )

    # 14
    def test_operation_without_valid_amount_is_not_ready(self):
        operation_id = self._operation(agent_payment=0)
        state = build_operation_commission_state(
            self.org_a,
            operation_id,
        )
        self.assertEqual(state["state"], "not_ready")
        self.assertIn(
            "operation_commission_amount_missing",
            state["reasons"],
        )

    # 15
    def test_missing_operation_currency_is_rejected(self):
        operation_id = self._operation()
        with self.assertRaises(OperationCommissionError) as ctx:
            self._credit(operation_id, currency="")
        self.assertEqual(
            ctx.exception.message_key,
            "operation_commission_err_currency_missing",
        )

    # 16
    def test_decimal_amount_is_preserved(self):
        operation_id = self._operation()
        movement = self._credit(
            operation_id,
            amount=Decimal("1250.37"),
        )
        self.assertAlmostEqual(movement["amount"], 1250.37)

    # 17
    def test_migration_is_idempotent(self):
        connection = get_connection()
        cursor = connection.cursor()
        try:
            _migrate_agent_account(cursor)
            _migrate_agent_account(cursor)
            connection.commit()
            for column in (
                "commission_side",
                "commission_purpose",
                "commission_source_amount",
                "commission_source_currency",
            ):
                self.assertTrue(
                    _column_exists(
                        cursor,
                        "agent_account_movements",
                        column,
                    )
                )
        finally:
            connection.close()

    # 18
    def test_operation_detail_renders_commission_block(self):
        operation_id = self._operation()
        self._login()
        response = self.client.get(f"/operations/{operation_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Comisi".encode(),
            response.data,
        )

    # 19
    def test_current_account_shows_operation_link(self):
        operation_id = self._operation()
        self._credit(operation_id)
        self._login()
        response = self.client.get(
            f"/agent-accounts/{self.agent_id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"/operations/{operation_id}".encode(),
            response.data,
        )

    def test_not_closed_operation_is_not_ready(self):
        operation_id = self._operation(was_invoiced="no")
        state = build_operation_commission_state(
            self.org_a,
            operation_id,
        )
        self.assertEqual(state["state"], "not_ready")
        self.assertIn(
            "operation_commission_not_closed",
            state["reasons"],
        )

    def test_commission_does_not_touch_cash(self):
        operation_id = self._operation()
        before = len(list_cash_movements(self.org_a))
        self._credit(operation_id)
        self.assertEqual(len(list_cash_movements(self.org_a)), before)

    def test_recredit_after_reversal_creates_new_audit_row(self):
        operation_id = self._operation()
        first = self._credit(operation_id)
        reverse_operation_commission(
            self.org_a,
            operation_id,
            created_by_user_id=self.admin_a,
            reason="Corrección",
        )
        second = self._credit(operation_id, amount="900")
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(second["idempotency_key"].endswith(":v2"))


if __name__ == "__main__":
    unittest.main()
