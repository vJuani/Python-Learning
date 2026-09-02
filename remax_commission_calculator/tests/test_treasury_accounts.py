"""
Tests for treasury accounts and multi-account cash.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_treasury_accounts.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.agent_account import create_movement
from modules.cash_treasury import (
    CashTreasuryError,
    confirm_movement,
    create_internal_transfer,
    get_balances,
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
from modules.database.connection import get_connection
from modules.database.schema import (
    _migrate_treasury_accounts,
    _table_exists,
)
from modules.database.treasury_accounts_repository import (
    LEGACY_DEFAULT_NAMES,
    count_treasury_account_movements,
    create_treasury_account,
    get_default_treasury_account,
    get_treasury_account,
    list_treasury_accounts,
    suggest_treasury_account_for_payment,
)
from web_app import app


class TreasuryAccountsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-treasury"
        create_tables()

        cls.org_a = add_organization("Treasury Org A")
        cls.org_b = add_organization("Treasury Org B")
        pwd = hash_password("Password1")
        cls.admin_a = add_user(
            "treasury_admin_a",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "treasury_agent",
            pwd,
            ROLE_AGENT,
            cls.org_a,
        )
        cls.admin_b = add_user(
            "treasury_admin_b",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.agent_id = add_agent(
            "Treasury Agent",
            "Alto",
            cls.org_a,
        )
        cls.password = "Password1"

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username="treasury_admin_a"):
        self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def _create_cash_ars_account(self, name="Caja física ARS"):
        return create_treasury_account(
            self.org_a,
            name=name,
            account_type="cash",
            currency="ARS",
            created_by_user_id=self.admin_a,
        )

    def _create_bank_usd_account(self, name="Banco Galicia USD"):
        return create_treasury_account(
            self.org_a,
            name=name,
            account_type="bank",
            currency="USD",
            bank_name="Galicia",
            created_by_user_id=self.admin_a,
        )

    def test_create_cash_ars_account(self):
        account = self._create_cash_ars_account()
        self.assertEqual(account["currency"], "ARS")
        self.assertEqual(account["account_type"], "cash")

    def test_create_bank_usd_account(self):
        account = self._create_bank_usd_account()
        self.assertEqual(account["currency"], "USD")
        self.assertEqual(account["account_type"], "bank")

    def test_ars_movement_rejects_usd_account(self):
        usd_account = self._create_bank_usd_account()
        errors, values = validate_movement_payload(
            {
                "movement_type": "income",
                "currency": "ARS",
                "amount": "100",
                "category": "operating_income",
                "description": "Test",
                "payment_method": "cash",
                "movement_date": "2026-09-02",
                "treasury_account_id": str(usd_account["id"]),
            }
        )
        self.assertEqual(errors, [])
        with self.assertRaises(CashTreasuryError):
            confirm_movement(
                self.org_a,
                values,
                user_id=self.admin_a,
            )

    def test_usd_movement_rejects_ars_account(self):
        ars_account = self._create_cash_ars_account()
        errors, values = validate_movement_payload(
            {
                "movement_type": "income",
                "currency": "USD",
                "amount": "50",
                "category": "operating_income",
                "description": "Test USD",
                "payment_method": "transfer",
                "movement_date": "2026-09-02",
                "treasury_account_id": str(ars_account["id"]),
            }
        )
        with self.assertRaises(CashTreasuryError):
            confirm_movement(
                self.org_a,
                values,
                user_id=self.admin_a,
            )

    def test_agent_payment_lands_in_selected_bank_usd(self):
        bank = self._create_bank_usd_account()
        payment = create_movement(
            self.org_a,
            self.agent_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "78.65",
                "movement_date": "2026-09-02",
                "payment_method": "transfer",
                "treasury_account_id": str(bank["id"]),
            },
            created_by_user_id=self.admin_a,
        )
        cash = get_cash_movement(
            payment["source_id"],
            self.org_a,
        )
        self.assertEqual(cash["treasury_account_id"], bank["id"])

    def test_cash_payment_suggests_cash_account(self):
        cash = self._create_cash_ars_account("Caja ARS default")
        suggested = suggest_treasury_account_for_payment(
            self.org_a,
            "ARS",
            "cash",
        )
        self.assertIsNotNone(suggested)
        self.assertEqual(suggested["account_type"], "cash")

    def test_transfer_keeps_consolidated_usd_balance(self):
        cash = self._create_bank_usd_account("Caja USD")
        bank = create_treasury_account(
            self.org_a,
            name="Banco USD",
            account_type="bank",
            currency="USD",
            created_by_user_id=self.admin_a,
        )
        confirm_movement(
            self.org_a,
            validate_movement_payload(
                {
                    "movement_type": "income",
                    "currency": "USD",
                    "amount": "500",
                    "category": "operating_income",
                    "description": "Seed",
                    "payment_method": "cash",
                    "movement_date": "2026-09-02",
                    "treasury_account_id": str(cash["id"]),
                }
            )[1],
            user_id=self.admin_a,
        )
        before = get_balances(self.org_a)["USD"]
        create_internal_transfer(
            self.org_a,
            from_account_id=cash["id"],
            to_account_id=bank["id"],
            amount="200",
            movement_date="2026-09-02",
            user_id=self.admin_a,
            idempotency_key="transfer-001",
        )
        after = get_balances(self.org_a)["USD"]
        self.assertAlmostEqual(before, after)

    def test_transfer_not_counted_as_income(self):
        org_id = add_organization("Transfer Income Org")
        admin_id = add_user(
            "transfer_income_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            org_id,
        )
        cash = create_treasury_account(
            org_id,
            name="Caja USD 2",
            account_type="bank",
            currency="USD",
            created_by_user_id=admin_id,
        )
        bank = create_treasury_account(
            org_id,
            name="Banco USD 2",
            account_type="bank",
            currency="USD",
            created_by_user_id=admin_id,
        )
        confirm_movement(
            org_id,
            validate_movement_payload(
                {
                    "movement_type": "income",
                    "currency": "USD",
                    "amount": "300",
                    "category": "operating_income",
                    "description": "Seed 2",
                    "payment_method": "cash",
                    "movement_date": "2026-09-02",
                    "treasury_account_id": str(cash["id"]),
                }
            )[1],
            user_id=admin_id,
        )
        create_internal_transfer(
            org_id,
            from_account_id=cash["id"],
            to_account_id=bank["id"],
            amount="100",
            movement_date="2026-09-02",
            user_id=admin_id,
            idempotency_key="transfer-002",
        )
        income_rows = [
            row
            for row in list_cash_movements(
                org_id,
                movement_type="income",
            )
            if row.get("source") != "internal_transfer"
        ]
        self.assertEqual(len(income_rows), 1)

    def test_cross_currency_transfer_rejected(self):
        ars = self._create_cash_ars_account()
        usd = self._create_bank_usd_account()
        with self.assertRaises(CashTreasuryError):
            create_internal_transfer(
                self.org_a,
                from_account_id=ars["id"],
                to_account_id=usd["id"],
                amount="100",
                movement_date="2026-09-02",
                user_id=self.admin_a,
            )

    def test_other_organization_account_rejected(self):
        foreign = create_treasury_account(
            self.org_b,
            name="Foreign USD",
            account_type="bank",
            currency="USD",
            created_by_user_id=self.admin_b,
        )
        with self.assertRaises(CashTreasuryError):
            confirm_movement(
                self.org_a,
                validate_movement_payload(
                    {
                        "movement_type": "income",
                        "currency": "USD",
                        "amount": "10",
                        "category": "operating_income",
                        "description": "Hack",
                        "payment_method": "cash",
                        "movement_date": "2026-09-02",
                        "treasury_account_id": str(
                            foreign["id"]
                        ),
                    }
                )[1],
                user_id=self.admin_a,
            )

    def test_account_with_movements_cannot_be_deleted(self):
        account = self._create_cash_ars_account("Cuenta con movs")
        confirm_movement(
            self.org_a,
            validate_movement_payload(
                {
                    "movement_type": "income",
                    "currency": "ARS",
                    "amount": "100",
                    "category": "operating_income",
                    "description": "Mov",
                    "payment_method": "cash",
                    "movement_date": "2026-09-02",
                    "treasury_account_id": str(account["id"]),
                }
            )[1],
            user_id=self.admin_a,
        )
        connection = get_connection()
        cursor = connection.cursor()
        try:
            count = count_treasury_account_movements(
                cursor,
                self.org_a,
                account["id"],
            )
            self.assertGreater(count, 0)
        finally:
            connection.close()

    def test_legacy_migration_assigns_default_accounts(self):
        defaults = list_treasury_accounts(self.org_a)
        names = {item["name"] for item in defaults}
        self.assertIn(LEGACY_DEFAULT_NAMES["ARS"], names)
        self.assertIn(LEGACY_DEFAULT_NAMES["USD"], names)

    def test_migration_idempotent(self):
        connection = get_connection()
        cursor = connection.cursor()
        try:
            _migrate_treasury_accounts(cursor)
            connection.commit()
            self.assertTrue(
                _table_exists(cursor, "treasury_accounts")
            )
            _migrate_treasury_accounts(cursor)
            connection.commit()
        finally:
            connection.close()

    def test_agent_cannot_manage_treasury_accounts(self):
        self._login("treasury_agent")
        response = self.client.get(
            "/cash/treasury-accounts",
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 403))

    def test_balances_per_account_and_consolidated(self):
        org_id = add_organization("Balance Split Org")
        admin_id = add_user(
            "balance_split_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            org_id,
        )
        cash = create_treasury_account(
            org_id,
            name="Caja ARS A",
            account_type="cash",
            currency="ARS",
            created_by_user_id=admin_id,
        )
        bank = create_treasury_account(
            org_id,
            name="Banco ARS A",
            account_type="bank",
            currency="ARS",
            created_by_user_id=admin_id,
        )
        confirm_movement(
            org_id,
            validate_movement_payload(
                {
                    "movement_type": "income",
                    "currency": "ARS",
                    "amount": "1000",
                    "category": "operating_income",
                    "description": "A",
                    "payment_method": "cash",
                    "movement_date": "2026-09-02",
                    "treasury_account_id": str(cash["id"]),
                }
            )[1],
            user_id=admin_id,
        )
        confirm_movement(
            org_id,
            validate_movement_payload(
                {
                    "movement_type": "income",
                    "currency": "ARS",
                    "amount": "500",
                    "category": "operating_income",
                    "description": "B",
                    "payment_method": "transfer",
                    "movement_date": "2026-09-02",
                    "treasury_account_id": str(bank["id"]),
                }
            )[1],
            user_id=admin_id,
        )
        self.assertAlmostEqual(
            get_treasury_account(
                cash["id"],
                org_id,
            )["cached_balance"],
            1000.0,
        )
        self.assertAlmostEqual(
            get_treasury_account(
                bank["id"],
                org_id,
            )["cached_balance"],
            500.0,
        )
        self.assertAlmostEqual(
            get_balances(org_id)["ARS"],
            1500.0,
        )


if __name__ == "__main__":
    unittest.main()
