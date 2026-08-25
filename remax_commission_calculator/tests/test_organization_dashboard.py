"""Tests for executive dashboard period presets and scoping."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_organization_dashboard.db"
)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(
    Path(_TEST_TMP.name) / "uploads"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.organizations_repository import add_organization
from modules.organization_dashboard import (
    PERIOD_CUSTOM,
    PERIOD_THIS_MONTH,
    load_organization_dashboard,
    resolve_dashboard_period,
)
from web_app import app


class OrganizationDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org = add_organization("Dash Org")
        cls.agent_a = add_agent("Dash Agent A", "Alto", cls.org)
        cls.agent_b = add_agent("Dash Agent B", "Puro", cls.org)
        pwd = hash_password("Password1")
        cls.admin = add_user(
            "dash_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="dash_admin@example.com",
        )
        add_user(
            "dash_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_a,
            email="dash_agent@example.com",
        )

        cls.today = date(2026, 8, 24)
        cls.prop_a = cls._insert_property(cls.org, cls.agent_a, "Dash A")
        cls.prop_b = cls._insert_property(cls.org, cls.agent_b, "Dash B")

        cls._insert_operation(
            cls.org,
            cls.agent_a,
            cls.prop_a,
            operation_date="10/08/2026",
            was_invoiced="yes",
            status="approved",
            sale_price=100000,
            total_commission=3000,
            agent_payment=1800,
            office_payment=1200,
        )
        cls._insert_operation(
            cls.org,
            cls.agent_a,
            cls.prop_a,
            operation_date="12/08/2026",
            was_invoiced="no",
            status="approved",
            sale_price=50000,
            total_commission=1500,
            agent_payment=900,
            office_payment=600,
        )
        cls._insert_operation(
            cls.org,
            cls.agent_b,
            cls.prop_b,
            operation_date="05/07/2026",
            was_invoiced="yes",
            status="approved",
            sale_price=80000,
            total_commission=2400,
            agent_payment=1440,
            office_payment=960,
        )
        cls._insert_operation(
            cls.org,
            cls.agent_a,
            cls.prop_a,
            operation_date="01/08/2026",
            was_invoiced="no",
            status="pending",
            sale_price=20000,
            total_commission=600,
            agent_payment=360,
            office_payment=240,
        )

        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        _TEST_TMP.cleanup()

    @classmethod
    def _insert_property(cls, org_id, agent_id, address):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO properties (
                address, jurisdiction, organization_id, agent_id, status
            )
            VALUES (?, 'CABA', ?, ?, 'approved')
            """,
            (address, org_id, agent_id),
        )
        property_id = cursor.lastrowid
        connection.commit()
        connection.close()
        return property_id

    @classmethod
    def _insert_operation(
        cls,
        org_id,
        agent_id,
        property_id,
        *,
        operation_date,
        was_invoiced,
        status,
        sale_price,
        total_commission,
        agent_payment,
        office_payment,
    ):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO operations (
                operation_date, agent_id, property_id, organization_id,
                was_invoiced, vat_amount, sale_price, commission_rate,
                total_commission, commission_after_abao, abao, martillero,
                agent_payment, office_payment, office_total, currency,
                original_amount, exchange_rate, status, created_by_user_id
            )
            VALUES (
                ?, ?, ?, ?, ?, 0, ?, 3, ?, ?, 0, 0,
                ?, ?, ?, 'USD', ?, 1, ?, ?
            )
            """,
            (
                operation_date,
                agent_id,
                property_id,
                org_id,
                was_invoiced,
                sale_price,
                total_commission,
                total_commission,
                agent_payment,
                office_payment,
                office_payment,
                sale_price,
                status,
                cls.admin,
            ),
        )
        connection.commit()
        connection.close()

    def test_resolve_this_month(self):
        errors, period, date_from, date_to = resolve_dashboard_period(
            {"period": "this_month"},
            today=self.today,
        )
        self.assertEqual(errors, [])
        self.assertEqual(period, PERIOD_THIS_MONTH)
        self.assertEqual(date_from, "01/08/2026")
        self.assertEqual(date_to, "31/08/2026")

    def test_resolve_previous_month(self):
        errors, period, date_from, date_to = resolve_dashboard_period(
            {"period": "previous_month"},
            today=self.today,
        )
        self.assertEqual(errors, [])
        self.assertEqual(date_from, "01/07/2026")
        self.assertEqual(date_to, "31/07/2026")

    def test_resolve_custom(self):
        errors, period, date_from, date_to = resolve_dashboard_period(
            {
                "period": "custom",
                "date_from": "01/07/2026",
                "date_to": "15/08/2026",
            },
            today=self.today,
        )
        self.assertEqual(errors, [])
        self.assertEqual(period, PERIOD_CUSTOM)
        self.assertEqual(date_from, "01/07/2026")
        self.assertEqual(date_to, "15/08/2026")

    def test_admin_this_month_metrics(self):
        dashboard = load_organization_dashboard(
            self.org,
            {
                "period": "custom",
                "date_from": "01/08/2026",
                "date_to": "31/08/2026",
            },
            language="es",
            role="admin",
            can_write=True,
            can_manage_approvals=True,
            can_create_operations=True,
        )
        self.assertEqual(dashboard["metrics"]["approved_operations"], 2)
        self.assertEqual(dashboard["metrics"]["invoiced_count"], 1)
        self.assertEqual(dashboard["metrics"]["not_invoiced_count"], 1)
        self.assertEqual(
            dashboard["metrics"]["total_commission"],
            4500,
        )
        self.assertTrue(dashboard["show_ranking"])
        self.assertTrue(dashboard["has_period_data"])
        self.assertGreaterEqual(len(dashboard["agent_ranking"]), 1)

    def test_agent_scope_hides_global_ranking(self):
        dashboard = load_organization_dashboard(
            self.org,
            {
                "period": "custom",
                "date_from": "01/08/2026",
                "date_to": "31/08/2026",
            },
            language="es",
            scoped_agent_id=self.agent_a,
            role="agent",
            can_write=True,
            can_manage_approvals=False,
            can_create_operations=True,
        )
        self.assertFalse(dashboard["show_ranking"])
        self.assertEqual(dashboard["agent_ranking"], [])
        self.assertEqual(dashboard["metrics"]["approved_operations"], 2)
        self.assertTrue(dashboard["show_workflow"])
        self.assertEqual(dashboard["workflow_counts"]["pending"], 1)

    def test_empty_period(self):
        dashboard = load_organization_dashboard(
            self.org,
            {
                "period": "custom",
                "date_from": "01/01/2020",
                "date_to": "31/01/2020",
            },
            language="es",
            role="admin",
            can_write=True,
            can_manage_approvals=True,
            can_create_operations=True,
        )
        self.assertFalse(dashboard["has_period_data"])
        self.assertEqual(dashboard["metrics"]["approved_operations"], 0)

    def test_dashboard_route(self):
        self.client.post(
            "/login",
            data={
                "username": "dash_admin",
                "password": "Password1",
            },
            follow_redirects=True,
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Bienvenido", response.data)


if __name__ == "__main__":
    unittest.main()
