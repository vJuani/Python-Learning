"""Phase 3D recurring agent charge integration tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_recurring_agent_charges.db"
)

from modules.agent_account import cancel_movement
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_balances,
    list_agent_account_movements,
)
from modules.database.recurring_charges_repository import (
    get_recurring_charge,
    list_recurring_charges,
)
from modules.invoicing import list_billable_agent_charges
from modules.recurring_agent_charges import (
    create_recurring_charge,
    end_recurring_charge,
    generate_due_recurring_charges,
    pause_recurring_charge,
    resume_recurring_charge,
    update_recurring_charge,
)
from web_app import app


class RecurringAgentChargesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(
            TESTING=True,
            SECRET_KEY="test-recurring-charges",
        )
        create_tables()
        cls.org_a = add_organization("Recurring Org A")
        cls.org_b = add_organization("Recurring Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent_a = add_agent(
            "Recurring Agent A",
            "Alto",
            cls.org_a,
        )
        cls.agent_b = add_agent(
            "Recurring Agent B",
            "Alto",
            cls.org_b,
        )
        cls.admin_a = add_user(
            "recurring_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.admin_b = add_user(
            "recurring_admin_b",
            password_hash,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.agent_user = add_user(
            "recurring_agent_user",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )

    def setUp(self):
        self.client = app.test_client()
        self.created = []

    def tearDown(self):
        for recurring_id in self.created:
            recurring = get_recurring_charge(
                self.org_a,
                recurring_id,
            )
            if recurring and recurring["status"] != "ended":
                end_recurring_charge(
                    self.org_a,
                    recurring_id,
                    actor_user_id=self.admin_a,
                )

    def _payload(self, **overrides):
        payload = {
            "charge_category": "fee",
            "currency": "USD",
            "amount": "65",
            "vat_mode": "add_vat",
            "vat_rate": "21",
            "recurrence_type": "monthly",
            "billing_day": "1",
            "start_date": "2026-10-01",
            "end_date": "",
            "description": "",
        }
        payload.update(overrides)
        return payload

    def _create(self, **overrides):
        recurring = create_recurring_charge(
            self.org_a,
            self.agent_a,
            self._payload(**overrides),
            actor_user_id=self.admin_a,
        )
        self.created.append(recurring["id"])
        return recurring

    def _generate(self, as_of="2026-10-03", **kwargs):
        return generate_due_recurring_charges(
            self.org_a,
            as_of=as_of,
            actor_user_id=self.admin_a,
            **kwargs,
        )

    def _login(self, username):
        self.client.get("/logout", follow_redirects=True)
        return self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def test_01_monthly_generates_correct_charge(self):
        recurring = self._create()
        result = self._generate()
        movement = next(
            row for row in result["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(movement["movement_type"], "charge")
        self.assertEqual(movement["description"], "Fee · Octubre 2026")
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_a)["USD"],
            -78.65,
        )

    def test_02_annual_generates_correct_charge(self):
        recurring = self._create(
            recurrence_type="annual",
            start_date="2026-10-02",
            billing_day="",
        )
        result = self._generate(as_of="2026-10-02")
        movement = next(
            row for row in result["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(movement["billing_period"], "2026")

    def test_03_usd_stays_usd(self):
        recurring = self._create()
        result = self._generate()
        movement = next(
            row for row in result["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(movement["currency"], "USD")

    def test_04_ars_stays_ars(self):
        recurring = self._create(currency="ARS")
        result = self._generate()
        movement = next(
            row for row in result["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(movement["currency"], "ARS")

    def test_05_vat_snapshot_is_copied(self):
        recurring = self._create()
        movement = next(
            row for row in self._generate()["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(movement["net_amount"], 65.0)
        self.assertEqual(movement["vat_rate"], 0.21)
        self.assertEqual(movement["vat_amount"], 13.65)
        self.assertEqual(movement["gross_amount"], 78.65)

    def test_06_decimal_65_plus_21_is_exact(self):
        recurring = self._create()
        self.assertEqual(
            Decimal(str(recurring["gross_amount"])),
            Decimal("78.65"),
        )

    def test_07_same_period_does_not_duplicate(self):
        recurring = self._create()
        self._generate()
        self._generate()
        rows = [
            row for row in list_agent_account_movements(
                self.org_a,
                agent_id=self.agent_a,
            )
            if row["source_type"] == "recurring_charge"
            and row["source_id"] == recurring["id"]
            and row["billing_period"] == "2026-10"
        ]
        self.assertEqual(len(rows), 1)

    def test_08_double_job_execution_does_not_duplicate(self):
        recurring = self._create()
        first = self._generate()
        second = self._generate()
        self.assertTrue(any(
            row["source_id"] == recurring["id"]
            for row in first["generated"]
        ))
        self.assertFalse(any(
            row["source_id"] == recurring["id"]
            for row in second["generated"]
        ))

    def test_09_concurrent_workers_do_not_duplicate(self):
        recurring = self._create()

        def run():
            return self._generate()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: run(), range(2)))
        rows = [
            row for row in list_agent_account_movements(
                self.org_a,
                agent_id=self.agent_a,
            )
            if row["source_id"] == recurring["id"]
            and row["billing_period"] == "2026-10"
        ]
        self.assertEqual(len(rows), 1)

    def test_10_pause_prevents_generation(self):
        recurring = self._create()
        pause_recurring_charge(
            self.org_a,
            recurring["id"],
            actor_user_id=self.admin_a,
        )
        result = self._generate()
        self.assertFalse(any(
            row["source_id"] == recurring["id"]
            for row in result["generated"]
        ))

    def test_11_resume_skips_paused_periods(self):
        recurring = self._create()
        pause_recurring_charge(
            self.org_a,
            recurring["id"],
            actor_user_id=self.admin_a,
        )
        resumed = resume_recurring_charge(
            self.org_a,
            recurring["id"],
            actor_user_id=self.admin_a,
            as_of=date(2026, 12, 15),
        )
        self.assertEqual(resumed["next_run_date"], "2027-01-01")

    def test_12_end_prevents_future_generation(self):
        recurring = self._create()
        end_recurring_charge(
            self.org_a,
            recurring["id"],
            actor_user_id=self.admin_a,
        )
        result = self._generate()
        self.assertFalse(any(
            row["source_id"] == recurring["id"]
            for row in result["generated"]
        ))

    def test_13_edit_does_not_change_history(self):
        recurring = self._create()
        first = next(
            row for row in self._generate()["generated"]
            if row["source_id"] == recurring["id"]
        )
        update_recurring_charge(
            self.org_a,
            recurring["id"],
            self._payload(amount="70", start_date="2026-11-01"),
            actor_user_id=self.admin_a,
            as_of=date(2026, 11, 1),
        )
        second = next(
            row for row in self._generate(as_of="2026-11-01")["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(first["gross_amount"], 78.65)
        self.assertEqual(second["gross_amount"], 84.7)

    def test_14_source_fields_are_correct(self):
        recurring = self._create()
        movement = next(
            row for row in self._generate()["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(movement["source_type"], "recurring_charge")
        self.assertEqual(movement["source_id"], recurring["id"])

    def test_15_billing_period_is_canonical(self):
        recurring = self._create()
        movement = next(
            row for row in self._generate()["generated"]
            if row["source_id"] == recurring["id"]
        )
        self.assertEqual(movement["billing_period"], "2026-10")
        self.assertEqual(movement["period_label"], "Octubre 2026")

    def test_16_agent_cannot_create_or_edit(self):
        recurring = self._create()
        self._login("recurring_agent_user")
        create_response = self.client.post(
            f"/agent-accounts/{self.agent_a}/recurring-charges/new",
            data=self._payload(),
        )
        edit_response = self.client.post(
            f"/agent-accounts/recurring-charges/{recurring['id']}/edit",
            data=self._payload(),
        )
        self.assertIn(create_response.status_code, (302, 403))
        self.assertIn(edit_response.status_code, (302, 403))

    def test_17_other_organization_is_rejected(self):
        recurring = self._create()
        self._login("recurring_admin_b")
        response = self.client.get(
            f"/agent-accounts/recurring-charges/{recurring['id']}/edit"
        )
        self.assertEqual(response.status_code, 404)

    def test_18_dry_run_does_not_modify_database(self):
        recurring = self._create()
        result = self._generate(dry_run=True)
        self.assertTrue(any(
            item["recurring_charge"]["id"] == recurring["id"]
            for item in result["preview"]
        ))
        self.assertFalse(any(
            row["source_id"] == recurring["id"]
            for row in list_agent_account_movements(
                self.org_a,
                agent_id=self.agent_a,
            )
        ))

    def test_19_generated_charge_is_billable(self):
        recurring = self._create()
        movement = next(
            row for row in self._generate()["generated"]
            if row["source_id"] == recurring["id"]
        )
        billable = list_billable_agent_charges(
            self.org_a,
            agent_id=self.agent_a,
        )
        self.assertTrue(any(
            row["id"] == movement["id"] for row in billable
        ))

    def test_20_cancelling_charge_keeps_recurrence(self):
        recurring = self._create()
        movement = next(
            row for row in self._generate()["generated"]
            if row["source_id"] == recurring["id"]
        )
        cancel_movement(
            self.org_a,
            movement["id"],
            created_by_user_id=self.admin_a,
            reason="Configuration correction",
        )
        self.assertEqual(
            get_recurring_charge(
                self.org_a,
                recurring["id"],
            )["status"],
            "active",
        )

    def test_21_cancelled_period_is_not_recreated(self):
        recurring = self._create()
        movement = next(
            row for row in self._generate()["generated"]
            if row["source_id"] == recurring["id"]
        )
        cancel_movement(
            self.org_a,
            movement["id"],
            created_by_user_id=self.admin_a,
            reason="Configuration correction",
        )
        self._generate(as_of="2026-10-31")
        october = [
            row for row in list_agent_account_movements(
                self.org_a,
                agent_id=self.agent_a,
            )
            if row["source_id"] == recurring["id"]
            and row["billing_period"] == "2026-10"
        ]
        self.assertEqual(len(october), 1)

    def test_22_migration_is_idempotent(self):
        from modules.database.recurring_charges_migration import (
            migrate_recurring_charges_sqlite,
        )

        migrate_recurring_charges_sqlite()
        migrate_recurring_charges_sqlite()

    def test_23_create_tables_twice(self):
        create_tables()
        create_tables()

    def test_24_account_pages_render(self):
        self._create()
        self._login("recurring_admin_a")
        staff = self.client.get(
            f"/agent-accounts/{self.agent_a}"
        )
        self.assertEqual(staff.status_code, 200)
        self.assertIn(b"Cargos recurrentes", staff.data)
        self._login("recurring_agent_user")
        agent = self.client.get("/my-account")
        self.assertEqual(agent.status_code, 200)
        self.assertIn(b"Cargos recurrentes", agent.data)

    def test_25_decimal_snapshot_remains_exact(self):
        recurring = self._create(
            amount="78,65",
            vat_mode="gross_includes_vat",
        )
        self.assertEqual(
            Decimal(str(recurring["net_amount"])),
            Decimal("65.00"),
        )
        self.assertEqual(
            Decimal(str(recurring["vat_amount"])),
            Decimal("13.65"),
        )


if __name__ == "__main__":
    unittest.main()
