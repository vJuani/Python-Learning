"""
UX tests for agent account: payment allocation, operation search, detail view.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_account_ux.db"
)

from modules.auth import ROLE_ADMIN, hash_password
from modules.agent_account import (
    AgentAccountError,
    create_movement,
)
from modules.agent_account_presentation import (
    build_movement_detail_display,
    enrich_movement_for_display,
    format_pending_charge_option,
)
from modules.agent_account_charges import VAT_MODE_ADD
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_account_movement,
    list_agent_account_movements,
)
from modules.database.agent_account_repository import (
    list_pending_charges,
)
from modules.database.connection import get_connection
from modules.database.operations_repository import (
    search_operations_for_agent_account,
)
from web_app import app


class AgentAccountUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-agent-account-ux"
        create_tables()

        cls.org_a = add_organization("AA UX Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("UX Agent", "Alto", cls.org_a)
        cls.other_agent_id = add_agent("Other UX Agent", "Alto", cls.org_a)
        cls.admin_a = add_user(
            "aa_ux_admin",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.password = "Password1"

    def setUp(self):
        self.client = app.test_client()

    def _login(self):
        self.client.get("/logout", follow_redirects=True)
        self.client.post(
            "/login",
            data={
                "username": "aa_ux_admin",
                "password": self.password,
            },
            follow_redirects=True,
        )

    def _create_charge(self, agent_id=None, **payload):
        agent_id = agent_id or self.agent_id
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

    def test_pending_charges_and_payment_allocation(self):
        agent_id = add_agent("Pending Pay Agent", "Alto", self.org_a)
        charge = self._create_charge(agent_id=agent_id)
        pending = list_pending_charges(
            self.org_a,
            agent_id,
            "USD",
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], charge["id"])
        option = format_pending_charge_option(
            pending[0],
            language="es",
        )
        self.assertIn("Fee · Septiembre 2026", option["label"])
        self.assertIn("pendiente", option["label"])

        payment = create_movement(
            self.org_a,
            agent_id,
            {
                "movement_type": "payment",
                "currency": "USD",
                "amount": "78,65",
                "movement_date": "2026-09-02",
                "applied_to_movement_id": str(charge["id"]),
            },
            created_by_user_id=self.admin_a,
        )
        self.assertEqual(payment["source_id"], charge["id"])
        self.assertEqual(
            list_pending_charges(
                self.org_a,
                agent_id,
                "USD",
            ),
            [],
        )

    def test_payment_cannot_apply_cross_currency(self):
        agent_id = add_agent("Cross Currency Agent", "Alto", self.org_a)
        charge = self._create_charge(agent_id=agent_id, currency="USD")
        with self.assertRaises(AgentAccountError) as ctx:
            create_movement(
                self.org_a,
                agent_id,
                {
                    "movement_type": "payment",
                    "currency": "ARS",
                    "amount": "1000",
                    "movement_date": "2026-09-02",
                    "applied_to_movement_id": str(charge["id"]),
                },
                created_by_user_id=self.admin_a,
            )
        self.assertEqual(
            ctx.exception.message_key,
            "agent_account_err_invalid_applied_charge",
        )

    def test_pending_charges_api_filters_by_currency(self):
        agent_id = add_agent("API Pending Agent", "Alto", self.org_a)
        self._create_charge(agent_id=agent_id, currency="USD")
        self._create_charge(
            agent_id=agent_id,
            charge_category="mainstreet",
            currency="ARS",
            amount="1000",
            vat_mode="none",
        )
        self._login()
        response = self.client.get(
            f"/agent-accounts/{agent_id}/pending-charges?currency=USD"
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(len(payload["charges"]), 1)
        self.assertEqual(payload["charges"][0]["currency"], "USD")

    def test_commission_operation_search_is_agent_scoped(self):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO properties (
                organization_id, address, jurisdiction, external_id
            ) VALUES (?, ?, ?, ?)
            """,
            (
                self.org_a,
                "Calle UX 123",
                "CABA",
                "UX-PROP-1",
            ),
        )
        property_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO operations (
                organization_id,
                agent_id,
                property_id,
                operation_date,
                was_invoiced,
                vat_amount,
                sale_price,
                commission_rate,
                total_commission,
                commission_after_abao,
                abao,
                martillero,
                agent_payment,
                office_payment,
                office_total,
                currency,
                status,
                created_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.org_a,
                self.agent_id,
                property_id,
                "01/09/2026",
                "no",
                0,
                100000,
                3,
                3000,
                3000,
                0,
                0,
                1500,
                1500,
                1500,
                "USD",
                "approved",
                self.admin_a,
            ),
        )
        own_operation_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO operations (
                organization_id,
                agent_id,
                property_id,
                operation_date,
                was_invoiced,
                vat_amount,
                sale_price,
                commission_rate,
                total_commission,
                commission_after_abao,
                abao,
                martillero,
                agent_payment,
                office_payment,
                office_total,
                currency,
                status,
                created_by_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.org_a,
                self.other_agent_id,
                property_id,
                "02/09/2026",
                "no",
                0,
                200000,
                3,
                6000,
                6000,
                0,
                0,
                3000,
                3000,
                3000,
                "USD",
                "approved",
                self.admin_a,
            ),
        )
        connection.commit()
        connection.close()

        results = search_operations_for_agent_account(
            self.org_a,
            self.agent_id,
            f"COM-{own_operation_id:06d}",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["db_id"], own_operation_id)

        commission = create_movement(
            self.org_a,
            self.agent_id,
            {
                "movement_type": "commission",
                "currency": "USD",
                "amount": "1500",
                "movement_date": "2026-09-03",
                "operation_id": str(own_operation_id),
            },
            created_by_user_id=self.admin_a,
        )
        self.assertEqual(commission["source_id"], own_operation_id)

        with self.assertRaises(AgentAccountError):
            create_movement(
                self.org_a,
                self.agent_id,
                {
                    "movement_type": "commission",
                    "currency": "USD",
                    "amount": "100",
                    "movement_date": "2026-09-03",
                    "operation_id": str(own_operation_id + 9999),
                },
                created_by_user_id=self.admin_a,
            )

    def test_movement_detail_display_includes_vat_rows(self):
        agent_id = add_agent("Detail Agent", "Alto", self.org_a)
        charge = self._create_charge(agent_id=agent_id)
        movement = get_agent_account_movement(
            charge["id"],
            self.org_a,
        )
        lookup = {movement["id"]: movement}
        detail = build_movement_detail_display(
            movement,
            language="es",
            movement_lookup=lookup,
        )
        labels = [row["label"] for row in detail]
        self.assertIn("Neto", labels)
        self.assertIn("Total a cargar", labels)

        enriched = enrich_movement_for_display(
            movement,
            language="es",
            movement_lookup=lookup,
        )
        self.assertTrue(enriched["display_detail"])


if __name__ == "__main__":
    unittest.main()
