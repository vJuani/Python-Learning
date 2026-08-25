"""
Tests for team report / profile presentation layer.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_team_reports.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.excel_team_report import build_team_report_xlsx
from modules.operations import (
    calculate_operation_details,
    save_calculated_operation,
)
from modules.pdf_team_report import build_team_report_pdf
from modules.team_reports import (
    agent_is_team_leader,
    build_agent_profile_view,
    build_dashboard_team_block,
    load_team_report,
)
from modules.workflow import STATUS_APPROVED, STATUS_DRAFT
from web_app import app


class TeamReportsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        create_tables()

        cls.org = add_organization("Team Report Org")
        cls.other = add_organization("Other Team Org")
        cls.tomas = add_agent("Tomas Pasman", "Puro", cls.org)
        cls.pablo = add_agent(
            "Pablo Reynals",
            "Junior",
            cls.org,
            team_leader_agent_id=cls.tomas,
        )
        cls.jose = add_agent(
            "José Luis Barreiro",
            "RAPP",
            cls.org,
        )
        cls.prop = add_property(
            "Team Prop",
            "CABA",
            cls.org,
            agent_id=cls.pablo,
            status="approved",
        )
        pwd = hash_password("Password1")
        cls.admin_id = add_user(
            "team_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="team_admin@example.com",
        )
        cls.tomas_user = add_user(
            "tomas_user",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.tomas,
            email="tomas@example.com",
        )
        cls.pablo_user = add_user(
            "pablo_user",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.pablo,
            email="pablo@example.com",
        )

        op = calculate_operation_details(
            "Pablo Reynals",
            "Junior",
            "Team Prop",
            "CABA",
            100000,
            7,
            "no",
        )
        save_calculated_operation(
            cls.pablo,
            cls.prop,
            cls.org,
            op,
            status=STATUS_APPROVED,
        )

    def test_agent_is_team_leader(self):
        self.assertTrue(
            agent_is_team_leader(self.org, self.tomas)
        )
        self.assertFalse(
            agent_is_team_leader(self.org, self.pablo)
        )

    def test_profile_team_leader_has_junior_rows(self):
        view = build_agent_profile_view(self.org, self.tomas)
        self.assertTrue(view["is_team_leader"])
        self.assertEqual(len(view["junior_rows"]), 1)
        row = view["junior_rows"][0]
        self.assertEqual(row["agent"]["id"], self.pablo)
        self.assertEqual(row["operations_count"], 1)
        self.assertAlmostEqual(row["production"], 7000.0, places=2)
        self.assertAlmostEqual(
            row["team_leader_income"],
            2226.0,
            places=2,
        )

    def test_profile_junior_has_leader_and_yield(self):
        view = build_agent_profile_view(self.org, self.pablo)
        self.assertFalse(view["is_team_leader"])
        self.assertEqual(view["team_leader"]["id"], self.tomas)
        self.assertEqual(view["own_stats"]["operations_count"], 1)
        self.assertAlmostEqual(
            view["own_stats"]["agent_yield"],
            3150.0,
            places=2,
        )

    def test_team_report_and_exports(self):
        report = load_team_report(
            self.org,
            self.tomas,
            {"period_mode": "all"},
            language="es",
        )
        self.assertIsNotNone(report)
        self.assertGreater(report["metrics"]["team_production"], 0)
        self.assertAlmostEqual(
            report["metrics"]["juniors_income_to_leader"],
            2226.0,
            places=2,
        )

        pdf = build_team_report_pdf(report)
        xlsx = build_team_report_xlsx(report)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(xlsx), 100)

    def test_dashboard_block(self):
        block = build_dashboard_team_block(
            self.org,
            self.tomas,
            language="es",
        )
        self.assertEqual(block["juniors_active"], 1)
        self.assertIn("labels", block)

    def test_http_scopes(self):
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["user_id"] = self.pablo_user
        # Junior cannot open Tomas wallet/profile
        denied = client.get(f"/agents/{self.tomas}")
        self.assertEqual(denied.status_code, 302)

        # Junior can open own profile
        own = client.get(f"/agents/{self.pablo}")
        self.assertEqual(own.status_code, 200)

        # Junior cannot open team report as Tomas
        forbidden = client.get(f"/reports/team/{self.tomas}")
        self.assertEqual(forbidden.status_code, 302)

        with client.session_transaction() as sess:
            sess["user_id"] = self.tomas_user
        # TL can open junior profile
        junior = client.get(f"/agents/{self.pablo}")
        self.assertEqual(junior.status_code, 200)
        report = client.get(f"/reports/team/{self.tomas}")
        self.assertEqual(report.status_code, 200)

        with client.session_transaction() as sess:
            sess["user_id"] = self.admin_id
        admin_report = client.get(f"/reports/team/{self.tomas}")
        self.assertEqual(admin_report.status_code, 200)
        pdf = client.get(f"/reports/team/{self.tomas}/pdf")
        self.assertEqual(pdf.status_code, 200)


if __name__ == "__main__":
    unittest.main()
