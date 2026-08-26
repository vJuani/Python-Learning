"""
Tests for office cash / treasury module.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_cash_treasury.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.cash_treasury import (
    CashTreasuryError,
    confirm_movement,
    get_balances,
    reverse_movement,
    set_opening_balances,
    validate_movement_payload,
)
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_cash_movement,
    list_cash_movements,
)
from web_app import app


class CashTreasuryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-cash"
        create_tables()

        cls.org_a = add_organization("Cash Org A")
        cls.org_b = add_organization("Cash Org B")
        pwd = hash_password("Password1")

        cls.agent_record = add_agent(
            "Cash Agent",
            "Alto",
            cls.org_a,
        )
        cls.admin_a = add_user(
            "cash_admin_a",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_a = add_user(
            "cash_agent_a",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_record,
        )
        cls.admin_b = add_user(
            "cash_admin_b",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.password = "Password1"

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        return self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def _payload(
        self,
        *,
        movement_type="income",
        currency="ARS",
        amount="1000",
        category=None,
        description="Test movement",
        payment_method="cash",
        movement_date="2026-08-01",
        notes="",
    ):
        if category is None:
            category = (
                "other_income"
                if movement_type == "income"
                else "other_expense"
            )

        return {
            "movement_type": movement_type,
            "currency": currency,
            "amount": amount,
            "category": category,
            "description": description,
            "payment_method": payment_method,
            "movement_date": movement_date,
            "notes": notes,
        }

    def test_validate_requires_fields(self):
        errors, _ = validate_movement_payload(
            {
                "movement_type": "",
                "currency": "EUR",
                "amount": "-1",
                "category": "x",
                "description": "",
                "payment_method": "x",
                "movement_date": "2026-08-01",
            }
        )
        self.assertIn("cash_err_type_required", errors)
        self.assertIn("cash_err_currency_invalid", errors)
        self.assertIn("cash_err_amount_invalid", errors)
        self.assertIn(
            "cash_err_description_required",
            errors,
        )

    def test_opening_and_income_expense_balances(self):
        set_opening_balances(
            self.org_a,
            amounts_by_currency={
                "ARS": "10000",
                "USD": "500",
            },
            user_id=self.admin_a,
        )
        balances = get_balances(self.org_a)
        self.assertAlmostEqual(balances["ARS"], 10000)
        self.assertAlmostEqual(balances["USD"], 500)

        income_values = validate_movement_payload(
            self._payload(amount="2000", currency="ARS")
        )[1]
        confirm_movement(
            self.org_a,
            income_values,
            user_id=self.admin_a,
        )

        expense_values = validate_movement_payload(
            self._payload(
                movement_type="expense",
                amount="1500",
                currency="ARS",
                category="office_supplies",
            )
        )[1]
        confirm_movement(
            self.org_a,
            expense_values,
            user_id=self.admin_a,
        )

        usd_income = validate_movement_payload(
            self._payload(
                amount="50",
                currency="USD",
            )
        )[1]
        confirm_movement(
            self.org_a,
            usd_income,
            user_id=self.admin_a,
        )

        balances = get_balances(self.org_a)
        self.assertAlmostEqual(balances["ARS"], 10500)
        self.assertAlmostEqual(balances["USD"], 550)

    def test_blocks_negative_expense(self):
        set_opening_balances(
            self.org_b,
            amounts_by_currency={"ARS": "100"},
            user_id=self.admin_b,
        )
        values = validate_movement_payload(
            self._payload(
                movement_type="expense",
                amount="150",
                currency="ARS",
                category="taxes",
                description="Too much",
            )
        )[1]

        with self.assertRaises(CashTreasuryError) as ctx:
            confirm_movement(
                self.org_b,
                values,
                user_id=self.admin_b,
            )

        self.assertEqual(
            ctx.exception.message_key,
            "cash_err_insufficient_balance",
        )
        self.assertAlmostEqual(
            get_balances(self.org_b)["ARS"],
            100,
        )

    def test_reverse_restores_balance(self):
        org = add_organization("Cash Reverse Org")
        admin = add_user(
            "cash_rev_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        set_opening_balances(
            org,
            amounts_by_currency={"USD": "1000"},
            user_id=admin,
        )
        values = validate_movement_payload(
            self._payload(
                amount="200",
                currency="USD",
            )
        )[1]
        movement = confirm_movement(
            org,
            values,
            user_id=admin,
        )
        reverse_movement(
            org,
            movement["id"],
            user_id=admin,
            reason="Correction",
        )
        original = get_cash_movement(movement["id"], org)
        self.assertEqual(original["status"], "reversed")
        self.assertAlmostEqual(
            get_balances(org)["USD"],
            1000,
        )

        with self.assertRaises(CashTreasuryError):
            reverse_movement(
                org,
                movement["id"],
                user_id=admin,
                reason="Again",
            )

    def test_opening_cannot_repeat(self):
        org = add_organization("Cash Opening Once")
        admin = add_user(
            "cash_open_once",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        set_opening_balances(
            org,
            amounts_by_currency={"ARS": "50"},
            user_id=admin,
        )

        with self.assertRaises(CashTreasuryError) as ctx:
            set_opening_balances(
                org,
                amounts_by_currency={"ARS": "10"},
                user_id=admin,
            )

        self.assertEqual(
            ctx.exception.message_key,
            "cash_err_opening_already_set",
        )

    def test_org_isolation(self):
        org1 = add_organization("Cash Iso 1")
        org2 = add_organization("Cash Iso 2")
        admin1 = add_user(
            "cash_iso_1",
            hash_password("Password1"),
            ROLE_ADMIN,
            org1,
        )
        admin2 = add_user(
            "cash_iso_2",
            hash_password("Password1"),
            ROLE_ADMIN,
            org2,
        )
        set_opening_balances(
            org1,
            amounts_by_currency={"ARS": "300"},
            user_id=admin1,
        )
        set_opening_balances(
            org2,
            amounts_by_currency={"ARS": "900"},
            user_id=admin2,
        )
        self.assertAlmostEqual(
            get_balances(org1)["ARS"],
            300,
        )
        self.assertAlmostEqual(
            get_balances(org2)["ARS"],
            900,
        )
        movs1 = list_cash_movements(org1)
        movs2 = list_cash_movements(org2)
        self.assertTrue(movs1)
        self.assertTrue(movs2)
        self.assertNotEqual(
            movs1[0]["id"],
            movs2[0]["id"],
        )
        self.assertIsNone(
            get_cash_movement(movs1[0]["id"], org2)
        )

    def test_admin_can_access_cash_routes(self):
        self._login("cash_admin_a")
        response = self.client.get("/cash")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Caja", response.data)

        response = self.client.get("/cash/new")
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            "/cash/opening-balance"
        )
        self.assertEqual(response.status_code, 200)

    def test_agent_forbidden(self):
        self._login("cash_agent_a")
        response = self.client.get(
            "/cash",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith("/")
            or "dashboard" in response.headers["Location"]
        )

        response = self.client.get(
            "/cash/new",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            "/cash/opening-balance",
            data={"amount_ars": "10"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_create_via_form_preview_and_save(self):
        org = add_organization("Cash Form Org")
        add_user(
            "cash_form_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        self._login("cash_form_admin")

        preview = self.client.post(
            "/cash/new",
            data={
                **self._payload(
                    amount="250",
                    currency="ARS",
                    description="Form income",
                ),
                "action": "preview",
            },
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"250", preview.data)

        save = self.client.post(
            "/cash/new",
            data={
                **self._payload(
                    amount="250",
                    currency="ARS",
                    description="Form income",
                ),
                "action": "save",
            },
            follow_redirects=False,
        )
        self.assertEqual(save.status_code, 302)
        balances = get_balances(org)
        self.assertAlmostEqual(balances["ARS"], 250)


if __name__ == "__main__":
    unittest.main()
