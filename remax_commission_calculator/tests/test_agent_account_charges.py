"""
Tests for agent account charge registration (VAT, categories, migration).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_account_charges.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.agent_account import (
    AgentAccountError,
    create_movement,
)
from modules.agent_account_charges import (
    CHARGE_CATEGORY_JRH_SUBSCRIPTION,
    CHARGE_CATEGORY_OTHER,
    DEFAULT_VAT_RATE,
    VAT_MODE_ADD,
    VAT_MODE_GROSS_INCLUDES,
    VAT_MODE_NONE,
    compute_vat_amounts,
    validate_charge_payload,
)
from modules.agent_account_presentation import (
    enrich_movement_for_display,
    movement_display_amount,
)
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_balances,
    list_agent_account_movements,
)
from modules.database.connection import get_connection
from modules.database.schema import (
    _column_exists,
    _migrate_agent_account,
    _migrate_agent_account_v3,
)
from web_app import app


class AgentAccountChargesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-agent-account-charges"
        create_tables()

        cls.org_a = add_organization("AA Charges Org A")
        cls.org_b = add_organization("AA Charges Org B")
        pwd = hash_password("Password1")

        cls.agent_a_id = add_agent("Agent Charges A", "Alto", cls.org_a)
        cls.agent_b_id = add_agent("Agent Charges B", "Alto", cls.org_b)
        cls.admin_a = add_user(
            "aa_charges_admin_a",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user_a = add_user(
            "aa_charges_agent_a",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a_id,
        )
        cls.admin_b = add_user(
            "aa_charges_admin_b",
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

    def _create_charge(self, agent_id, **payload):
        base = {
            "charge_category": "fee",
            "currency": "USD",
            "amount": "65",
            "vat_mode": VAT_MODE_ADD,
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

    def test_fee_usd_65_plus_vat_21_equals_7865(self):
        breakdown = compute_vat_amounts(
            vat_mode=VAT_MODE_ADD,
            input_amount=Decimal("65"),
            vat_rate=DEFAULT_VAT_RATE,
        )
        self.assertEqual(breakdown["net_amount"], Decimal("65.00"))
        self.assertEqual(breakdown["vat_amount"], Decimal("13.65"))
        self.assertEqual(breakdown["gross_amount"], Decimal("78.65"))

        agent_id = add_agent("VAT Fee Agent", "Alto", self.org_a)
        movement = self._create_charge(agent_id)
        self.assertAlmostEqual(movement["gross_amount"], 78.65)
        self.assertAlmostEqual(movement["net_amount"], 65.0)
        self.assertAlmostEqual(movement["vat_amount"], 13.65)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            -78.65,
        )
        self.assertIn("Fee · Septiembre 2026", movement["description"])

    def test_charge_without_vat(self):
        agent_id = add_agent("No VAT Agent", "Alto", self.org_a)
        movement = self._create_charge(
            agent_id,
            amount="50",
            vat_mode=VAT_MODE_NONE,
        )
        self.assertAlmostEqual(movement["gross_amount"], 50.0)
        self.assertAlmostEqual(movement["vat_amount"], 0.0)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            -50.0,
        )

    def test_gross_includes_vat_mode(self):
        breakdown = compute_vat_amounts(
            vat_mode=VAT_MODE_GROSS_INCLUDES,
            input_amount=Decimal("78.65"),
            vat_rate=DEFAULT_VAT_RATE,
        )
        self.assertEqual(breakdown["gross_amount"], Decimal("78.65"))
        self.assertEqual(breakdown["net_amount"], Decimal("65.00"))
        self.assertEqual(breakdown["vat_amount"], Decimal("13.65"))

        agent_id = add_agent("Gross VAT Agent", "Alto", self.org_a)
        movement = self._create_charge(
            agent_id,
            amount="78,65",
            vat_mode=VAT_MODE_GROSS_INCLUDES,
        )
        self.assertAlmostEqual(movement["gross_amount"], 78.65)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            -78.65,
        )

    def test_gross_amount_updates_balance(self):
        agent_id = add_agent("Gross Balance Agent", "Alto", self.org_a)
        self._create_charge(agent_id)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            -78.65,
        )
        display = movement_display_amount(
            list_agent_account_movements(self.org_a, agent_id)[0]
        )
        self.assertAlmostEqual(display["amount"], 78.65)

    def test_payment_reduces_debt(self):
        agent_id = add_agent("Pay After Charge", "Alto", self.org_a)
        self._create_charge(agent_id)
        create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "78,65",
                "description": "Pago recibido",
                "movement_date": "2026-09-02",
                "payment_method": "transfer",
            },
            created_by_user_id=self.admin_a,
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            0.0,
        )

    def test_ars_usd_balances_independent(self):
        agent_id = add_agent("FX Independent Agent", "Alto", self.org_a)
        self._create_charge(agent_id, currency="USD", amount="65")
        create_movement(
            self.org_a,
            agent_id,
            {
                "charge_category": "mainstreet",
                "currency": "ARS",
                "amount": "1000",
                "vat_mode": VAT_MODE_NONE,
                "movement_date": "2026-09-01",
            },
            created_by_user_id=self.admin_a,
        )
        balances = get_agent_balances(self.org_a, agent_id)
        self.assertAlmostEqual(balances["USD"], -78.65)
        self.assertAlmostEqual(balances["ARS"], -1000.0)

    def test_jrh_subscription_manual_charge(self):
        agent_id = add_agent("JRH Agent", "Alto", self.org_a)
        movement = create_movement(
            self.org_a,
            agent_id,
            {
                "charge_category": CHARGE_CATEGORY_JRH_SUBSCRIPTION,
                "currency": "USD",
                "amount": "20",
                "vat_mode": VAT_MODE_NONE,
                "billing_period": "Septiembre 2026",
                "movement_date": "2026-09-01",
                "recurring": "1",
                "recurrence_type": "monthly",
            },
            created_by_user_id=self.admin_a,
        )
        self.assertEqual(
            movement["charge_category"],
            CHARGE_CATEGORY_JRH_SUBSCRIPTION,
        )
        self.assertEqual(movement["billing_period"], "Septiembre 2026")
        self.assertEqual(movement["recurrence_type"], "monthly")
        self.assertEqual(movement["recurring"], 1)
        self.assertIn("Suscripción JRH One", movement["description"])

    def test_other_category_requires_description(self):
        with self.assertRaises(AgentAccountError) as ctx:
            validate_charge_payload(
                {
                    "charge_category": CHARGE_CATEGORY_OTHER,
                    "currency": "USD",
                    "amount": "10",
                    "vat_mode": VAT_MODE_NONE,
                    "movement_date": "2026-09-01",
                }
            )
        self.assertEqual(
            ctx.exception.message_key,
            "agent_account_err_description_required",
        )

    def test_exchange_rate_does_not_alter_balances(self):
        agent_id = add_agent("TC Agent", "Alto", self.org_a)
        self._create_charge(
            agent_id,
            exchange_rate="1480",
            exchange_rate_date="2026-09-01",
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["USD"],
            -78.65,
        )
        movement = list_agent_account_movements(
            self.org_a,
            agent_id,
        )[0]
        self.assertIsNotNone(movement["equivalent_amount_ars"])
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, agent_id)["ARS"],
            0.0,
        )

    def test_decimal_rounding_half_up(self):
        breakdown = compute_vat_amounts(
            vat_mode=VAT_MODE_ADD,
            input_amount=Decimal("65.005"),
            vat_rate=DEFAULT_VAT_RATE,
        )
        self.assertEqual(breakdown["net_amount"], Decimal("65.01"))
        self.assertEqual(breakdown["vat_amount"], Decimal("13.65"))
        self.assertEqual(breakdown["gross_amount"], Decimal("78.66"))

    def test_migration_v3_idempotent(self):
        connection = get_connection()
        cursor = connection.cursor()
        try:
            _migrate_agent_account(cursor)
            connection.commit()
            for column in (
                "charge_category",
                "net_amount",
                "vat_rate",
                "vat_amount",
                "gross_amount",
                "billing_period",
                "recurring",
                "recurrence_type",
            ):
                self.assertTrue(
                    _column_exists(
                        cursor,
                        "agent_account_movements",
                        column,
                    )
                )
            _migrate_agent_account_v3(cursor)
            connection.commit()
            for column in (
                "charge_category",
                "net_amount",
                "vat_rate",
                "vat_amount",
                "gross_amount",
                "billing_period",
                "recurring",
                "recurrence_type",
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

    def test_permissions_organization_and_agent_intact(self):
        agent_id = add_agent("Perm Agent", "Alto", self.org_a)
        self._create_charge(agent_id)

        self._login("aa_charges_agent_a")
        response = self.client.get(
            f"/agent-accounts/{agent_id}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        self._login("aa_charges_admin_a")
        response = self.client.get(f"/agent-accounts/{agent_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Fee", response.data)

        self._login("aa_charges_admin_b")
        response = self.client.get(
            f"/agent-accounts/{agent_id}",
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (404, 302))

    def test_payment_display_positive_in_history(self):
        agent_id = add_agent("Display Agent", "Alto", self.org_a)
        payment = create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "78,65",
                "description": "Pago recibido",
                "movement_date": "2026-09-02",
                "payment_method": "transfer",
            },
            created_by_user_id=self.admin_a,
        )
        enriched = enrich_movement_for_display(payment)
        self.assertTrue(
            enriched["display_amount"]["formatted"].startswith("+")
        )


if __name__ == "__main__":
    unittest.main()
