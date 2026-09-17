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
        cls.maria = add_agent("Maria Leader", "Puro", cls.org)
        cls.maria_junior = add_agent(
            "Maria Junior",
            "Junior",
            cls.org,
            team_leader_agent_id=cls.maria,
        )
        cls.maria_user = add_user(
            "maria_user",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.maria,
            email="maria@example.com",
        )
        cls.foreign_leader = add_agent("Foreign Leader", "Puro", cls.other)
        cls.foreign_junior = add_agent(
            "Foreign Junior",
            "Junior",
            cls.other,
            team_leader_agent_id=cls.foreign_leader,
        )
        cls.foreign_user = add_user(
            "foreign_tl",
            pwd,
            ROLE_AGENT,
            cls.other,
            agent_id=cls.foreign_leader,
            email="foreign.tl@example.com",
        )
        cls.foreign_admin = add_user(
            "foreign_admin",
            pwd,
            ROLE_ADMIN,
            cls.other,
            email="foreign.admin@example.com",
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

    def _login(self, user_id, role, organization_id, agent_id=None):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = organization_id
            if agent_id is not None:
                sess["agent_id"] = agent_id
        return client

    def test_http_scopes(self):
        junior = self._login(
            self.pablo_user, ROLE_AGENT, self.org, self.pablo
        )
        denied_profile = junior.get(f"/agents/{self.tomas}")
        self.assertIn(denied_profile.status_code, (302, 403))
        own = junior.get(f"/agents/{self.pablo}")
        self.assertEqual(own.status_code, 200)
        self.assertIn(junior.get(f"/reports/team/{self.tomas}").status_code, (302, 403))
        self.assertIn(junior.get(f"/reports/team/{self.pablo}").status_code, (302, 403))
        self.assertIn(junior.get("/reports/team").status_code, (302, 403))

        leader = self._login(
            self.tomas_user, ROLE_AGENT, self.org, self.tomas
        )
        junior_profile = leader.get(f"/agents/{self.pablo}")
        self.assertEqual(junior_profile.status_code, 200)
        own_team = leader.get(f"/reports/team/{self.tomas}")
        self.assertEqual(own_team.status_code, 200)
        self.assertIn("Resumen de Team", own_team.get_data(as_text=True))
        self.assertIn(leader.get(f"/reports/team/{self.maria}").status_code, (302, 403))
        self.assertEqual(
            leader.get(f"/reports/team/{self.tomas}?team_leader_id={self.maria}").status_code,
            200,
        )
        switched = leader.get(
            f"/reports/team/{self.tomas}?team_leader_id={self.maria}"
        )
        self.assertNotIn("Maria Leader", switched.get_data(as_text=True))
        self.assertEqual(leader.get(f"/reports/team/{self.tomas}/pdf").status_code, 200)

        other_leader = self._login(
            self.maria_user, ROLE_AGENT, self.org, self.maria
        )
        self.assertIn(other_leader.get(f"/reports/team/{self.tomas}").status_code, (302, 403))
        self.assertEqual(other_leader.get(f"/reports/team/{self.maria}").status_code, 200)

        admin = self._login(self.admin_id, ROLE_ADMIN, self.org)
        self.assertIn(admin.get(f"/reports/team/{self.tomas}").status_code, (302, 403))
        self.assertIn(admin.get("/reports/team").status_code, (302, 403))
        self.assertIn(admin.get(f"/reports/team/{self.tomas}/pdf").status_code, (302, 403))

        foreign = self._login(
            self.foreign_user, ROLE_AGENT, self.other, self.foreign_leader
        )
        self.assertIn(foreign.get(f"/reports/team/{self.tomas}").status_code, (302, 403))
        self.assertEqual(
            foreign.get(f"/reports/team/{self.foreign_leader}").status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
