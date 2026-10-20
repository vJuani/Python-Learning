"""
Tests for office cash / treasury module.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_cash_treasury.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.cash_treasury import (
    CashTreasuryError,
    build_cash_kpis,
    build_operation_collection_status,
    confirm_movement,
    describe_side_collection,
    get_balances,
    reverse_movement,
    set_account_opening_balance,
    set_opening_balances,
    validate_movement_payload,
)
from modules.database.cash_treasury_repository import (
    create_cash_movement_atomic,
    get_operation_side_collection,
)
from modules.database.treasury_accounts_repository import (
    create_treasury_account,
    get_default_treasury_account,
    get_treasury_account,
)
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_operation,
    add_organization,
    add_property,
    add_user,
    create_parties_for_new_operation,
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
        treasury_account_id=None,
        linked_operation_id=None,
        linked_party_role=None,
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
            "treasury_account_id": treasury_account_id or "",
            "linked_operation_id": (
                ""
                if linked_operation_id is None
                else str(linked_operation_id)
            ),
            "linked_party_role": linked_party_role or "",
        }

    def _commission_operation(
        self,
        organization_id,
        agent_id,
        address,
        *,
        currency="USD",
        buyer_amount=100,
        seller_amount=0,
        buyer_active=True,
        seller_active=False,
        status="approved",
    ):
        property_id = add_property(
            address,
            "CABA",
            organization_id,
            agent_id=agent_id,
        )
        operation_id = add_operation(
            "01/10/2026",
            agent_id,
            property_id,
            "no",
            0,
            100000,
            3,
            buyer_amount + seller_amount,
            buyer_amount + seller_amount,
            0,
            0,
            0,
            0,
            0,
            organization_id,
            currency=currency,
            status=status,
        )
        create_parties_for_new_operation(
            organization_id,
            operation_id,
            seller_active=seller_active,
            buyer_active=buyer_active,
            seller_commission_percent=3 if seller_active else 0,
            buyer_commission_percent=3 if buyer_active else 0,
            seller_commission_amount=seller_amount,
            buyer_commission_amount=buyer_amount,
        )
        return operation_id

    def _collection_org(self, name):
        organization_id = add_organization(name)
        agent_id = add_agent(name + " Agent", "Alto", organization_id)
        admin_id = add_user(
            name.lower().replace(" ", "_"),
            hash_password("Password1"),
            ROLE_ADMIN,
            organization_id,
        )
        return organization_id, admin_id, agent_id

    def _collect(
        self,
        organization_id,
        operation_id,
        amount,
        role="buyer",
        currency="USD",
        user_id=None,
    ):
        _errors, values = validate_movement_payload(
            self._payload(
                movement_type="income",
                currency=currency,
                amount=str(amount),
                category="commission_collection",
                description="Cobro de comisión",
                movement_date="2026-10-15",
                linked_operation_id=operation_id,
                linked_party_role=role,
            )
        )
        self.assertEqual(_errors, [])
        return confirm_movement(
            organization_id,
            values,
            user_id=user_id or self.admin_a,
        )

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

    def test_opening_balance_is_scoped_to_one_account(self):
        org = add_organization("Cash Two USD")
        admin = add_user(
            "cash_two_usd",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        general = get_default_treasury_account(org, "USD")
        bank = create_treasury_account(
            org,
            name="Banco QA USD",
            account_type="bank",
            currency="USD",
            is_default=False,
            created_by_user_id=admin,
        )
        movement = set_account_opening_balance(
            org,
            treasury_account_id=bank["id"],
            amount="10000",
            user_id=admin,
        )

        self.assertEqual(movement["movement_type"], "opening_balance")
        self.assertEqual(movement["treasury_account_id"], bank["id"])
        self.assertEqual(movement["currency"], "USD")
        self.assertAlmostEqual(movement["amount"], 10000)
        self.assertAlmostEqual(
            get_treasury_account(bank["id"], org)["cached_balance"],
            10000,
        )
        self.assertAlmostEqual(
            get_treasury_account(general["id"], org)["cached_balance"],
            0,
        )
        self.assertEqual(
            list_cash_movements(
                org,
                treasury_account_id=general["id"],
                movement_type="opening_balance",
                status="confirmed",
            ),
            [],
        )

        with self.assertRaises(CashTreasuryError) as ctx:
            set_account_opening_balance(
                org,
                treasury_account_id=bank["id"],
                amount="1",
                user_id=admin,
            )
        self.assertEqual(
            ctx.exception.message_key,
            "cash_err_opening_already_set",
        )
        self.assertAlmostEqual(
            get_treasury_account(bank["id"], org)["cached_balance"],
            10000,
        )

        set_account_opening_balance(
            org,
            treasury_account_id=general["id"],
            amount="25",
            user_id=admin,
        )
        self.assertAlmostEqual(
            get_treasury_account(general["id"], org)["cached_balance"],
            25,
        )
        self.assertAlmostEqual(
            get_treasury_account(bank["id"], org)["cached_balance"],
            10000,
        )

        other = add_organization("Cash Two USD Other")
        with self.assertRaises(CashTreasuryError) as isolated:
            set_account_opening_balance(
                other,
                treasury_account_id=bank["id"],
                amount="50",
                user_id=admin,
            )
        self.assertEqual(
            isolated.exception.message_key,
            "cash_err_treasury_account_invalid",
        )

    def test_today_usd_expense_is_reported_separately(self):
        from datetime import date

        from modules.cash_ai_directory import build_cash_ai_workspace

        org = add_organization("Cash USD Day")
        admin = add_user(
            "cash_usd_day",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        set_opening_balances(
            org,
            amounts_by_currency={"USD": "10000"},
            user_id=admin,
        )
        today = date.today().isoformat()
        values = validate_movement_payload(
            self._payload(
                movement_type="expense",
                amount="4320",
                currency="USD",
                category="other_expense",
                description="QA USD expense",
                movement_date=today,
            )
        )[1]
        confirm_movement(
            org,
            values,
            user_id=admin,
        )

        balances = get_balances(org)
        self.assertAlmostEqual(balances["USD"], 5680)
        self.assertAlmostEqual(balances["ARS"], 0)

        panel = build_cash_ai_workspace(
            org,
            language="es",
            today=date.today(),
        )
        self.assertAlmostEqual(panel["kpis"]["expense_today"], 0)
        self.assertAlmostEqual(panel["kpis"]["expense_today_usd"], 4320)
        self.assertAlmostEqual(panel["kpis"]["income_today"], 0)
        self.assertAlmostEqual(panel["kpis"]["income_today_usd"], 0)
        self.assertEqual(panel["kpis"]["income_count"], 0)

        from modules.cash_treasury import build_cash_kpis

        kpis = build_cash_kpis(org, today=date.today())
        self.assertAlmostEqual(kpis["balances"]["USD"], 5680)
        self.assertAlmostEqual(kpis["income_month"]["USD"], 0)
        self.assertAlmostEqual(kpis["expense_month"]["USD"], 4320)
        self.assertAlmostEqual(kpis["income_month"]["ARS"], 0)
        self.assertAlmostEqual(kpis["expense_month"]["ARS"], 0)
        descriptions = [
            row["description"] for row in panel["recent_movements"]
        ]
        self.assertIn("Saldo inicial", descriptions)
        self.assertNotIn("Opening balance", descriptions)

        self._login("cash_usd_day")
        page = self.client.get("/cash")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("USD 4.320,00", body)
        self.assertIn("Saldo inicial", body)
        self.assertNotIn("Opening balance", body)

    def test_operating_totals_exclude_opening_and_transfers(self):
        from datetime import date

        from modules.cash_ai_directory import (
            _balance_evolution,
            build_cash_ai_workspace,
        )
        from modules.cash_treasury import (
            build_cash_kpis,
            create_internal_transfer,
        )
        from modules.reports_directory import _cash_flow_series

        org = add_organization("Cash Operating Totals")
        admin = add_user(
            "cash_operating_totals",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        general_usd = get_default_treasury_account(org, "USD")
        general_ars = get_default_treasury_account(org, "ARS")
        bank = create_treasury_account(
            org,
            name="Banco QA USD",
            account_type="bank",
            currency="USD",
            is_default=False,
            created_by_user_id=admin,
        )
        today = date.today().isoformat()
        set_account_opening_balance(
            org,
            treasury_account_id=bank["id"],
            amount="10000",
            user_id=admin,
            movement_date=today,
        )
        set_account_opening_balance(
            org,
            treasury_account_id=general_ars["id"],
            amount="1000",
            user_id=admin,
            movement_date=today,
        )
        confirm_movement(
            org,
            validate_movement_payload(
                self._payload(
                    movement_type="income",
                    amount="500",
                    currency="USD",
                    description="Real USD income",
                    movement_date=today,
                    treasury_account_id=str(general_usd["id"]),
                )
            )[1],
            user_id=admin,
        )
        confirm_movement(
            org,
            validate_movement_payload(
                self._payload(
                    movement_type="expense",
                    amount="4320",
                    currency="USD",
                    category="other_expense",
                    description="Real USD expense",
                    movement_date=today,
                    treasury_account_id=str(bank["id"]),
                )
            )[1],
            user_id=admin,
        )
        confirm_movement(
            org,
            validate_movement_payload(
                self._payload(
                    movement_type="income",
                    amount="200",
                    currency="ARS",
                    description="Real ARS income",
                    movement_date=today,
                    treasury_account_id=str(general_ars["id"]),
                )
            )[1],
            user_id=admin,
        )
        confirm_movement(
            org,
            validate_movement_payload(
                self._payload(
                    movement_type="expense",
                    amount="50",
                    currency="ARS",
                    category="other_expense",
                    description="Real ARS expense",
                    movement_date=today,
                    treasury_account_id=str(general_ars["id"]),
                )
            )[1],
            user_id=admin,
        )
        balance_before_transfer = get_balances(org)["USD"]
        create_internal_transfer(
            org,
            from_account_id=bank["id"],
            to_account_id=general_usd["id"],
            amount="100",
            movement_date=today,
            user_id=admin,
            idempotency_key="operating-totals-transfer",
        )

        self.assertAlmostEqual(balance_before_transfer, 6180)
        self.assertAlmostEqual(get_balances(org)["USD"], 6180)
        self.assertAlmostEqual(get_balances(org)["ARS"], 1150)
        self.assertAlmostEqual(
            get_treasury_account(bank["id"], org)["cached_balance"],
            5580,
        )
        self.assertAlmostEqual(
            get_treasury_account(general_usd["id"], org)["cached_balance"],
            600,
        )

        panel = build_cash_ai_workspace(
            org,
            language="es",
            today=date.today(),
        )
        self.assertAlmostEqual(panel["kpis"]["income_today"], 200)
        self.assertAlmostEqual(panel["kpis"]["income_today_usd"], 500)
        self.assertAlmostEqual(panel["kpis"]["expense_today"], 50)
        self.assertAlmostEqual(panel["kpis"]["expense_today_usd"], 4320)
        self.assertEqual(panel["kpis"]["income_count"], 2)

        kpis = build_cash_kpis(org, today=date.today())
        self.assertAlmostEqual(kpis["income_month"]["USD"], 500)
        self.assertAlmostEqual(kpis["expense_month"]["USD"], 4320)
        self.assertAlmostEqual(kpis["income_month"]["ARS"], 200)
        self.assertAlmostEqual(kpis["expense_month"]["ARS"], 50)

        _, usd_evolution, _ = _balance_evolution(
            org,
            date.today(),
            currency="USD",
        )
        self.assertAlmostEqual(usd_evolution[-1], 6180)
        self.assertAlmostEqual(usd_evolution[-2], 0)

        series = _cash_flow_series(
            org,
            date.today().strftime("%Y%m%d"),
            date.today().strftime("%Y%m%d"),
        )
        self.assertAlmostEqual(series["totals"]["inflow"], 200)
        self.assertAlmostEqual(series["totals"]["outflow"], 50)
        self.assertAlmostEqual(series["totals"]["net"], 150)

        descriptions = [
            row["description"] for row in panel["recent_movements"]
        ]
        self.assertIn("Saldo inicial", descriptions)
        stored = list_cash_movements(
            org,
            movement_type="opening_balance",
            status="confirmed",
        )
        self.assertEqual(len(stored), 2)
        self.assertTrue(
            all(row["description"] == "Opening balance" for row in stored)
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
        self.assertIn("Tesorería".encode(), response.data)

        response = self.client.get("/cash/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Registrar movimiento".encode(), response.data)
        self.assertNotIn(b'value="preview"', response.data)

        summary = self.client.get("/cash")
        self.assertIn(b"cash-new-dialog", summary.data)
        self.assertIn("Registrar movimiento".encode(), summary.data)
        self.assertIn("Cobro de comisión".encode(), summary.data)
        self.assertIn(
            b'data-cash-origin-categories="commission_collection"',
            summary.data,
        )
        self.assertIn("Origen comercial".encode(), summary.data)

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

    def test_generic_income_stays_unlinked(self):
        org = add_organization("Cash Generic Org")
        admin = add_user(
            "cash_generic_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        _errors, values = validate_movement_payload(
            self._payload(
                amount="80",
                currency="ARS",
                category="other_income",
                description="Ingreso genérico",
            )
        )
        movement = confirm_movement(
            org,
            values,
            user_id=admin,
        )
        self.assertIsNone(movement["linked_operation_id"])
        self.assertIsNone(movement["linked_party_role"])

        errors, _values = validate_movement_payload(
            self._payload(
                category="operating_income",
                description="Ingreso operativo",
                linked_operation_id=1,
                linked_party_role="buyer",
            )
        )
        self.assertIn("cash_err_operation_category", errors)

    def test_full_commission_collection_counts_as_income(self):
        org, admin, agent = self._collection_org("Cash Full")
        operation_id = self._commission_operation(
            org,
            agent,
            "Calle Cobro Total 8",
            buyer_amount=100,
        )
        movement = self._collect(org, operation_id, 100, user_id=admin)
        self.assertEqual(movement["category"], "commission_collection")
        self.assertEqual(movement["linked_operation_id"], operation_id)
        self.assertEqual(movement["linked_party_role"], "buyer")
        self.assertAlmostEqual(get_balances(org)["USD"], 100)
        kpis = build_cash_kpis(org, today=date(2026, 10, 16))
        self.assertAlmostEqual(kpis["income_month"]["USD"], 100)

        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 100)
        self.assertAlmostEqual(state["remaining"], 0)
        self.assertEqual(state["count"], 1)

        with self.assertRaises(CashTreasuryError) as settled:
            self._collect(org, operation_id, 1, user_id=admin)
        self.assertEqual(
            settled.exception.message_key,
            "cash_err_operation_side_settled",
        )

    def test_operation_collection_tracks_partials_and_reversal(self):
        org, admin, agent = self._collection_org("Cash Partial")
        operation_id = self._commission_operation(
            org,
            agent,
            "Av. Preview Factura 200",
            buyer_amount=100,
        )
        first = self._collect(org, operation_id, 40, user_id=admin)
        self.assertEqual(first["linked_operation_id"], operation_id)
        self.assertEqual(first["linked_party_role"], "buyer")
        self.assertIn("COM-", first["linked_operation_label"])
        self.assertIn(
            "Av. Preview Factura 200",
            first["linked_operation_label"],
        )

        state = get_operation_side_collection(
            org,
            operation_id,
            "buyer",
        )
        self.assertAlmostEqual(state["expected"], 100)
        self.assertAlmostEqual(state["collected"], 40)
        self.assertAlmostEqual(state["remaining"], 60)
        self.assertEqual(state["count"], 1)

        with self.assertRaises(CashTreasuryError) as exceeded:
            self._collect(org, operation_id, 70, user_id=admin)
        self.assertEqual(
            exceeded.exception.message_key,
            "cash_err_operation_side_exceeds",
        )
        state = get_operation_side_collection(
            org,
            operation_id,
            "buyer",
        )
        self.assertAlmostEqual(state["collected"], 40)

        second = self._collect(org, operation_id, 60, user_id=admin)
        state = get_operation_side_collection(
            org,
            operation_id,
            "buyer",
        )
        self.assertAlmostEqual(state["collected"], 100)
        self.assertAlmostEqual(state["remaining"], 0)
        self.assertEqual(state["count"], 2)

        with self.assertRaises(CashTreasuryError) as settled:
            self._collect(org, operation_id, 1, user_id=admin)
        self.assertEqual(
            settled.exception.message_key,
            "cash_err_operation_side_settled",
        )

        reverse_movement(
            org,
            second["id"],
            user_id=admin,
            reason="Ajuste de cobro",
        )
        state = get_operation_side_collection(
            org,
            operation_id,
            "buyer",
        )
        self.assertAlmostEqual(state["collected"], 40)
        self.assertEqual(state["count"], 1)
        replacement = self._collect(org, operation_id, 60, user_id=admin)
        self.assertEqual(replacement["status"], "confirmed")

    def test_non_participating_side_is_rejected(self):
        org, admin, agent = self._collection_org("Cash Side")
        operation_id = self._commission_operation(
            org,
            agent,
            "Calle Solo Vendedora 10",
            buyer_amount=0,
            seller_amount=80,
            buyer_active=False,
            seller_active=True,
        )
        with self.assertRaises(CashTreasuryError) as rejected:
            self._collect(
                org,
                operation_id,
                80,
                role="buyer",
                user_id=admin,
            )
        self.assertEqual(
            rejected.exception.message_key,
            "cash_err_operation_side_invalid",
        )
        collected = self._collect(
            org,
            operation_id,
            80,
            role="seller",
            user_id=admin,
        )
        self.assertEqual(collected["linked_party_role"], "seller")

    def test_other_organization_cannot_link_operation(self):
        other_agent = add_agent(
            "Cash Agent B",
            "Alto",
            self.org_b,
        )
        operation_id = self._commission_operation(
            self.org_b,
            other_agent,
            "Operación ajena 1",
            buyer_amount=50,
        )
        with self.assertRaises(CashTreasuryError) as rejected:
            self._collect(self.org_a, operation_id, 50)
        self.assertEqual(
            rejected.exception.message_key,
            "cash_err_operation_invalid",
        )

    def test_collection_currency_must_match_commission(self):
        org, admin, agent = self._collection_org("Cash Currency")
        operation_id = self._commission_operation(
            org,
            agent,
            "Comisión en dólares 1",
            buyer_amount=25,
            currency="USD",
        )
        with self.assertRaises(CashTreasuryError) as rejected:
            self._collect(
                org,
                operation_id,
                25,
                currency="ARS",
                user_id=admin,
            )
        self.assertEqual(
            rejected.exception.message_key,
            "cash_err_operation_currency",
        )

    def test_collection_link_is_listed_and_isolated(self):
        org, admin, agent = self._collection_org("Cash Listed")
        operation_id = self._commission_operation(
            org,
            agent,
            "Av. Listado Cobro 15",
            buyer_amount=30,
            seller_amount=10,
            seller_active=False,
        )
        movement = self._collect(
            org,
            operation_id,
            30,
            user_id=admin,
        )
        self._login("cash_listed")
        page = self.client.get("/cash?tab=movements")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        code = f"COM-{operation_id:06d}"
        self.assertIn(code, body)
        self.assertIn("Av. Listado Cobro 15", body)
        self.assertIn(f"/operations/{operation_id}", body)
        self.assertIn("Punta compradora", body)

        detail = self.client.get(f"/cash/{movement['id']}")
        self.assertIn(
            f"/operations/{operation_id}",
            detail.get_data(as_text=True),
        )

        options = self.client.get(
            "/cash/operation-collections.json"
        )
        payload = options.get_json()
        match = next(
            item
            for item in payload["operations"]
            if item["id"] == operation_id
        )
        roles = [side["role"] for side in match["sides"]]
        self.assertEqual(roles, ["buyer"])
        self.assertAlmostEqual(match["sides"][0]["collected"], 30)
        self.assertAlmostEqual(match["sides"][0]["remaining"], 0)

        self.client.get("/logout")
        self._login("cash_admin_b")
        foreign = self.client.get(
            "/cash/operation-collections.json"
        )
        foreign_ids = [
            item["id"]
            for item in foreign.get_json()["operations"]
        ]
        self.assertNotIn(operation_id, foreign_ids)

    def test_collection_status_never_goes_negative(self):
        settled = describe_side_collection(100, 100)
        self.assertEqual(settled["status"], "collected")
        self.assertAlmostEqual(settled["pending"], 0)
        self.assertAlmostEqual(settled["surplus"], 0)

        over = describe_side_collection(100, 130)
        self.assertEqual(over["status"], "collected")
        self.assertAlmostEqual(over["pending"], 0)
        self.assertAlmostEqual(over["surplus"], 30)

        empty = describe_side_collection(100, 0)
        self.assertEqual(empty["status"], "unpaid")
        self.assertAlmostEqual(empty["pending"], 100)

    def test_operation_detail_shows_full_and_unpaid_sides(self):
        org, admin, agent = self._collection_org("Cash Detail Full")
        operation_id = self._commission_operation(
            org,
            agent,
            "Av. Estado Cobro 2",
            buyer_amount=3000,
            seller_amount=4500,
            seller_active=True,
            status="closed",
        )
        self._collect(
            org,
            operation_id,
            4500,
            role="seller",
            user_id=admin,
        )
        status = build_operation_collection_status(org, operation_id)
        self.assertEqual(status["seller"]["status"], "collected")
        self.assertAlmostEqual(status["seller"]["collected"], 4500)
        self.assertAlmostEqual(status["seller"]["pending"], 0)
        self.assertEqual(status["buyer"]["status"], "unpaid")
        self.assertAlmostEqual(status["buyer"]["collected"], 0)
        self.assertAlmostEqual(status["buyer"]["pending"], 3000)
        self.assertAlmostEqual(status["collected_total"], 4500)
        self.assertAlmostEqual(status["pending_total"], 3000)

        self._login("cash_detail_full")
        page = self.client.get(f"/operations/{operation_id}")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Punta vendedora", body)
        self.assertIn("Punta compradora", body)
        self.assertIn("Sin cobrar", body)
        self.assertIn("Cobrado total", body)
        self.assertIn("Pendiente de cobro", body)
        self.assertIn("4.500,00", body)
        self.assertIn("3.000,00", body)
        self.assertIn("op-collect-badge--collected", body)
        self.assertIn("op-collect-badge--unpaid", body)

    def test_partial_and_multiple_collections_sum_per_side(self):
        org, admin, agent = self._collection_org("Cash Detail Partial")
        operation_id = self._commission_operation(
            org,
            agent,
            "Av. Parciales 9",
            buyer_amount=3000,
            seller_amount=4500,
            seller_active=True,
        )
        self._collect(org, operation_id, 1000, role="seller", user_id=admin)
        partial = build_operation_collection_status(org, operation_id)
        self.assertEqual(partial["seller"]["status"], "partial")
        self.assertAlmostEqual(partial["seller"]["collected"], 1000)
        self.assertAlmostEqual(partial["seller"]["pending"], 3500)
        self.assertEqual(partial["seller"]["count"], 1)
        self.assertEqual(partial["buyer"]["status"], "unpaid")

        self._collect(org, operation_id, 1500, role="seller", user_id=admin)
        self._collect(org, operation_id, 500, role="buyer", user_id=admin)
        combined = build_operation_collection_status(org, operation_id)
        self.assertEqual(combined["seller"]["status"], "partial")
        self.assertAlmostEqual(combined["seller"]["collected"], 2500)
        self.assertAlmostEqual(combined["seller"]["pending"], 2000)
        self.assertEqual(combined["seller"]["count"], 2)
        self.assertEqual(combined["buyer"]["status"], "partial")
        self.assertAlmostEqual(combined["buyer"]["collected"], 500)
        self.assertAlmostEqual(combined["buyer"]["pending"], 2500)
        self.assertAlmostEqual(combined["collected_total"], 3000)
        self.assertAlmostEqual(combined["pending_total"], 4500)

    def test_reversal_reopens_collection_status(self):
        org, admin, agent = self._collection_org("Cash Detail Reverse")
        operation_id = self._commission_operation(
            org,
            agent,
            "Av. Reversion 4",
            buyer_amount=0,
            seller_amount=4500,
            buyer_active=False,
            seller_active=True,
        )
        movement = self._collect(
            org,
            operation_id,
            4500,
            role="seller",
            user_id=admin,
        )
        before = build_operation_collection_status(org, operation_id)
        self.assertEqual(before["seller"]["status"], "collected")
        self.assertIsNone(before["buyer"])

        reverse_movement(
            org,
            movement["id"],
            user_id=admin,
            reason="Cobro anulado",
        )
        after = build_operation_collection_status(org, operation_id)
        self.assertEqual(after["seller"]["status"], "unpaid")
        self.assertAlmostEqual(after["seller"]["collected"], 0)
        self.assertAlmostEqual(after["seller"]["pending"], 4500)
        self.assertEqual(after["seller"]["count"], 0)

    def test_collection_status_is_isolated_by_organization(self):
        org_a, admin_a, agent_a = self._collection_org("Cash Detail A")
        org_b, admin_b, agent_b = self._collection_org("Cash Detail B")
        operation_a = self._commission_operation(
            org_a,
            agent_a,
            "Operacion A cobro",
            buyer_amount=3000,
            seller_amount=4500,
            seller_active=True,
            status="closed",
        )
        operation_b = self._commission_operation(
            org_b,
            agent_b,
            "Operacion B sin cobro",
            buyer_amount=3000,
            seller_amount=4500,
            seller_active=True,
            status="closed",
        )
        self._collect(
            org_a,
            operation_a,
            4500,
            role="seller",
            user_id=admin_a,
        )
        foreign = build_operation_collection_status(org_b, operation_a)
        self.assertIsNone(foreign["buyer"])
        self.assertIsNone(foreign["seller"])
        self.assertAlmostEqual(foreign["collected_total"], 0)

        own = build_operation_collection_status(org_b, operation_b)
        self.assertEqual(own["seller"]["status"], "unpaid")
        self.assertAlmostEqual(own["seller"]["collected"], 0)

        self._login("cash_detail_b")
        denied = self.client.get(f"/operations/{operation_a}")
        self.assertEqual(denied.status_code, 404)
        visible = self.client.get(f"/operations/{operation_b}")
        self.assertEqual(visible.status_code, 200)
        body = visible.get_data(as_text=True)
        self.assertIn("Sin cobrar", body)
        self.assertNotIn("op-collect-badge--collected", body)

    def _pending_two_thousand(self, name):
        org, admin, agent = self._collection_org(name)
        operation_id = self._commission_operation(
            org,
            agent,
            name + " 10",
            buyer_amount=3000,
        )
        first = self._collect(org, operation_id, 1000, user_id=admin)
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["remaining"], 2000)
        return org, admin, operation_id, first

    def test_collection_above_pending_is_rejected(self):
        org, admin, operation_id, _first = self._pending_two_thousand(
            "Cash Cap Over"
        )
        with self.assertRaises(CashTreasuryError) as exceeded:
            self._collect(org, operation_id, 2500, user_id=admin)
        self.assertEqual(
            exceeded.exception.message_key,
            "cash_err_operation_side_exceeds",
        )
        self.assertAlmostEqual(exceeded.exception.kwargs["pending"], 2000)
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 1000)
        self.assertAlmostEqual(state["remaining"], 2000)

    def test_collection_equal_to_pending_is_accepted(self):
        org, admin, operation_id, _first = self._pending_two_thousand(
            "Cash Cap Exact"
        )
        movement = self._collect(org, operation_id, 2000, user_id=admin)
        self.assertEqual(movement["status"], "confirmed")
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 3000)
        self.assertAlmostEqual(state["remaining"], 0)

        with self.assertRaises(CashTreasuryError) as settled:
            self._collect(org, operation_id, 1, user_id=admin)
        self.assertEqual(
            settled.exception.message_key,
            "cash_err_operation_side_settled",
        )

    def test_partial_collection_leaves_the_rest_pending(self):
        org, admin, operation_id, _first = self._pending_two_thousand(
            "Cash Cap Partial"
        )
        self._collect(org, operation_id, 1000, user_id=admin)
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 2000)
        self.assertAlmostEqual(state["remaining"], 1000)
        self.assertEqual(state["count"], 2)

    def test_reversed_collection_increases_pending_again(self):
        org, admin, operation_id, first = self._pending_two_thousand(
            "Cash Cap Reverse"
        )
        reverse_movement(
            org,
            first["id"],
            user_id=admin,
            reason="Cobro anulado",
        )
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 0)
        self.assertAlmostEqual(state["remaining"], 3000)
        self.assertEqual(state["count"], 0)

        self._collect(org, operation_id, 2500, user_id=admin)
        reopened = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(reopened["collected"], 2500)
        self.assertAlmostEqual(reopened["remaining"], 500)

    def test_http_post_rejects_collection_above_pending(self):
        org, admin, operation_id, _first = self._pending_two_thousand(
            "Cash Cap Http"
        )
        self._login("cash_cap_http")
        response = self.client.post(
            "/cash/new",
            data={
                **self._payload(
                    movement_type="income",
                    currency="USD",
                    amount="2500",
                    category="commission_collection",
                    description="Cobro de comisión",
                    movement_date="2026-10-15",
                    linked_operation_id=operation_id,
                    linked_party_role="buyer",
                ),
                "action": "save",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(
            "El importe supera el saldo pendiente de USD 2.000,00.",
            body,
        )
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 1000)
        self.assertAlmostEqual(state["remaining"], 2000)

    def test_atomic_insert_rejects_collection_above_pending(self):
        org, admin, operation_id, _first = self._pending_two_thousand(
            "Cash Cap Atomic"
        )
        with self.assertRaises(ValueError) as exceeded:
            create_cash_movement_atomic(
                org,
                movement_type="income",
                currency="USD",
                amount=2500,
                category="commission_collection",
                description="Cobro directo",
                payment_method="cash",
                movement_date="2026-10-15",
                created_by_user_id=admin,
                linked_operation_id=operation_id,
                linked_party_role="buyer",
            )
        self.assertEqual(str(exceeded.exception), "operation_side_exceeded")
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 1000)
        self.assertAlmostEqual(state["remaining"], 2000)

    def test_ai_confirm_rejects_linked_collection_above_pending(self):
        from modules.cash_ai_service import confirm_ai_draft
        from modules.database.cash_ai_drafts_repository import (
            STATUS_REVIEW,
            create_cash_ai_draft,
            update_cash_ai_draft,
        )

        org, admin, operation_id, _first = self._pending_two_thousand(
            "Cash Cap AI"
        )
        draft_id = create_cash_ai_draft(
            org,
            created_by_user_id=admin,
            confirm_token="tok-cap-1",
            status=STATUS_REVIEW,
        )
        update_cash_ai_draft(
            draft_id,
            org,
            draft_payload={
                "movement_type": "income",
                "currency": "USD",
                "amount": 2500,
                "movement_date": "2026-10-15",
                "category": "commission_collection",
                "description": "Cobro de comisión",
                "payment_method": "cash",
                "notes": "",
                "linked_operation_id": operation_id,
                "linked_party_role": "buyer",
                "confidence": "high",
                "fields_needing_review": [],
            },
            fields_needing_review=[],
            confidence="high",
        )
        with self.assertRaises(CashTreasuryError) as exceeded:
            confirm_ai_draft(
                org,
                draft_id,
                user_id=admin,
                confirm_token="tok-cap-1",
            )
        self.assertEqual(
            exceeded.exception.message_key,
            "cash_err_operation_side_exceeds",
        )
        self.assertAlmostEqual(exceeded.exception.kwargs["pending"], 2000)
        state = get_operation_side_collection(org, operation_id, "buyer")
        self.assertAlmostEqual(state["collected"], 1000)
        self.assertAlmostEqual(state["remaining"], 2000)

    def _collection_catalog(self, username, operation_id):
        self._login(username)
        payload = self.client.get(
            "/cash/operation-collections.json"
        ).get_json()
        return next(
            item
            for item in payload["operations"]
            if item["id"] == operation_id
        )

    def test_open_side_is_selectable_and_settled_side_is_not(self):
        org, admin, agent = self._collection_org("Cash Side Choice")
        operation_id = self._commission_operation(
            org,
            agent,
            "Calle Puntas 20",
            buyer_amount=3000,
            seller_amount=4500,
            seller_active=True,
        )
        self._collect(org, operation_id, 1000, role="buyer", user_id=admin)
        self._collect(org, operation_id, 4500, role="seller", user_id=admin)

        match = self._collection_catalog("cash_side_choice", operation_id)
        buyer = next(side for side in match["sides"] if side["role"] == "buyer")
        seller = next(side for side in match["sides"] if side["role"] == "seller")

        self.assertTrue(buyer["selectable"])
        self.assertAlmostEqual(buyer["expected"], 3000)
        self.assertAlmostEqual(buyer["collected"], 1000)
        self.assertAlmostEqual(buyer["remaining"], 2000)
        self.assertFalse(seller["selectable"])
        self.assertAlmostEqual(seller["expected"], 4500)
        self.assertAlmostEqual(seller["collected"], 4500)
        self.assertAlmostEqual(seller["remaining"], 0)

        page = self.client.get("/cash")
        body = page.get_data(as_text=True)
        self.assertIn("{side} — Cobrada", body)
        self.assertIn("Operación totalmente cobrada", body)

    def test_fully_collected_operation_rejects_another_collection(self):
        org, admin, agent = self._collection_org("Cash All Settled")
        operation_id = self._commission_operation(
            org,
            agent,
            "Calle Cobrada 21",
            buyer_amount=3000,
            seller_amount=4500,
            seller_active=True,
        )
        self._collect(org, operation_id, 3000, role="buyer", user_id=admin)
        self._collect(org, operation_id, 4500, role="seller", user_id=admin)

        match = self._collection_catalog("cash_all_settled", operation_id)
        self.assertTrue(match["sides"])
        self.assertTrue(all(not side["selectable"] for side in match["sides"]))
        for side in match["sides"]:
            self.assertIn("expected", side)
            self.assertIn("collected", side)
            self.assertAlmostEqual(side["remaining"], 0)

        with self.assertRaises(CashTreasuryError) as settled:
            self._collect(org, operation_id, 1, role="buyer", user_id=admin)
        self.assertEqual(
            settled.exception.message_key,
            "cash_err_operation_side_settled",
        )

        response = self.client.post(
            "/cash/new",
            data={
                **self._payload(
                    movement_type="income",
                    currency="USD",
                    amount="500",
                    category="commission_collection",
                    description="Cobro de comisión",
                    movement_date="2026-10-15",
                    linked_operation_id=operation_id,
                    linked_party_role="seller",
                ),
                "action": "save",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Esa punta ya está cobrada al 100%.",
            response.get_data(as_text=True),
        )

        with self.assertRaises(ValueError) as direct:
            create_cash_movement_atomic(
                org,
                movement_type="income",
                currency="USD",
                amount=500,
                category="commission_collection",
                description="Cobro directo",
                payment_method="cash",
                movement_date="2026-10-15",
                created_by_user_id=admin,
                linked_operation_id=operation_id,
                linked_party_role="buyer",
            )
        self.assertEqual(str(direct.exception), "operation_side_settled")
        buyer = get_operation_side_collection(org, operation_id, "buyer")
        seller = get_operation_side_collection(org, operation_id, "seller")
        self.assertAlmostEqual(buyer["collected"], 3000)
        self.assertAlmostEqual(seller["collected"], 4500)

    def _billing_ready(self, organization_id, operation_id, role):
        from modules.database import get_operation_party
        from modules.invoicing import side_ready_for_billing

        party = get_operation_party(organization_id, operation_id, role)
        return side_ready_for_billing(
            organization_id,
            operation_id,
            party,
            operation_status="approved",
        )

    def test_billing_follows_confirmed_commission_collections(self):
        org, admin, agent = self._collection_org("Cash Bill Gate")
        operation_id = self._commission_operation(
            org,
            agent,
            "Calle Facturar 30",
            buyer_amount=3000,
            seller_amount=4500,
            seller_active=True,
        )
        self.assertFalse(self._billing_ready(org, operation_id, "buyer"))
        self.assertFalse(self._billing_ready(org, operation_id, "seller"))

        self._collect(org, operation_id, 1000, role="buyer", user_id=admin)
        self.assertFalse(self._billing_ready(org, operation_id, "buyer"))
        self.assertFalse(self._billing_ready(org, operation_id, "seller"))

        buyer_full = self._collect(
            org,
            operation_id,
            2000,
            role="buyer",
            user_id=admin,
        )
        self.assertTrue(self._billing_ready(org, operation_id, "buyer"))
        self.assertFalse(self._billing_ready(org, operation_id, "seller"))

        reverse_movement(
            org,
            buyer_full["id"],
            user_id=admin,
            reason="Cobro anulado",
        )
        self.assertFalse(self._billing_ready(org, operation_id, "buyer"))

        self._collect(org, operation_id, 2000, role="buyer", user_id=admin)
        seller = self._collect(
            org,
            operation_id,
            4500,
            role="seller",
            user_id=admin,
        )
        self.assertTrue(self._billing_ready(org, operation_id, "buyer"))
        self.assertTrue(self._billing_ready(org, operation_id, "seller"))

        reverse_movement(
            org,
            seller["id"],
            user_id=admin,
            reason="Cobro anulado",
        )
        self.assertTrue(self._billing_ready(org, operation_id, "buyer"))
        self.assertFalse(self._billing_ready(org, operation_id, "seller"))

        other_org, other_admin, other_agent = self._collection_org(
            "Cash Bill Other"
        )
        other_operation = self._commission_operation(
            other_org,
            other_agent,
            "Calle Ajena 31",
            buyer_amount=3000,
        )
        self._collect(
            other_org,
            other_operation,
            3000,
            role="buyer",
            user_id=other_admin,
        )
        self.assertTrue(
            self._billing_ready(other_org, other_operation, "buyer")
        )
        self.assertFalse(self._billing_ready(org, operation_id, "seller"))

        from modules.auth import ROLE_ADMIN
        from modules.invoicing import (
            InvoicingError,
            build_draft_preview_for_side,
        )

        user = {"id": admin, "role": ROLE_ADMIN, "agent_id": None}
        with self.assertRaises(InvoicingError) as blocked:
            build_draft_preview_for_side(
                org,
                operation_id,
                "seller",
                user,
                issuer_mode="office",
            )
        self.assertEqual(
            blocked.exception.message_key,
            "invoice_err_side_billing_disabled",
        )
        with self.assertRaises(InvoicingError) as allowed:
            build_draft_preview_for_side(
                org,
                operation_id,
                "buyer",
                user,
                issuer_mode="office",
            )
        self.assertNotEqual(
            allowed.exception.message_key,
            "invoice_err_side_billing_disabled",
        )

        self._login("cash_bill_gate")
        detail = self.client.get(f"/operations/{operation_id}")
        detail_body = detail.get_data(as_text=True)
        self.assertIn("Habilitada para facturar", detail_body)
        self.assertIn("No habilitada para facturar", detail_body)


if __name__ == "__main__":
    unittest.main()
