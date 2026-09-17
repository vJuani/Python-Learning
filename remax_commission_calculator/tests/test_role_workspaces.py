"""Role workspaces: agent tools vs staff vs team-leader capability."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_role_workspaces.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ["MARKETING_AI_PROVIDER"] = "mock"

from modules.app_nav import visible_endpoints
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from web_app import app


class RoleWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="role-workspace-tests")
        create_tables()
        pwd = hash_password("Password1")
        cls.org = add_organization("Workspace Org")
        cls.other = add_organization("Other Workspace Org")
        cls.agent_id = add_agent("Ana Agent", "Alto", cls.org)
        cls.leader_id = add_agent("Luis Leader", "Puro", cls.org)
        cls.junior_id = add_agent(
            "Pablo Junior",
            "Junior",
            cls.org,
            team_leader_agent_id=cls.leader_id,
        )
        cls.other_leader_id = add_agent("Maria Leader", "Puro", cls.org)
        add_agent(
            "Maria Junior",
            "Junior",
            cls.org,
            team_leader_agent_id=cls.other_leader_id,
        )
        cls.foreign_leader_id = add_agent("Foreign Leader", "Puro", cls.other)
        add_agent(
            "Foreign Junior",
            "Junior",
            cls.other,
            team_leader_agent_id=cls.foreign_leader_id,
        )
        cls.admin_id = add_user("ws_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_user = add_user(
            "ws_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.leader_user = add_user(
            "ws_leader",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.leader_id,
        )
        cls.other_leader_user = add_user(
            "ws_other_leader",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_leader_id,
        )
        cls.foreign_leader_user = add_user(
            "ws_foreign",
            pwd,
            ROLE_AGENT,
            cls.other,
            agent_id=cls.foreign_leader_id,
        )
        cls.foreign_admin = add_user(
            "ws_foreign_admin",
            pwd,
            ROLE_ADMIN,
            cls.other,
        )

    def _login(self, user_id, role, organization_id, agent_id=None):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = organization_id
            if agent_id is not None:
                session["agent_id"] = agent_id
        return client

    def _nav_keys(self, user_id, role, organization_id, agent_id=None):
        user = {
            "id": user_id,
            "role": role,
            "organization_id": organization_id,
            "agent_id": agent_id,
        }
        with app.test_request_context("/"):
            return visible_endpoints(user)

    def test_marketing_agent_allowed_admin_and_guest_blocked(self):
        agent = self._login(self.agent_user, ROLE_AGENT, self.org, self.agent_id)
        self.assertEqual(agent.get("/marketing").status_code, 200)
        self.assertEqual(agent.get("/marketing/generations").status_code, 200)
        self.assertEqual(agent.get("/marketing/brand-kit").status_code, 200)
        admin = self._login(self.admin_id, ROLE_ADMIN, self.org)
        self.assertEqual(admin.get("/marketing").status_code, 403)
        self.assertEqual(admin.get("/marketing/generations").status_code, 403)
        self.assertEqual(admin.get("/marketing/brand-kit").status_code, 403)
        self.assertEqual(admin.get("/marketing/templates").status_code, 403)
        guest = app.test_client()
        self.assertEqual(guest.get("/marketing").status_code, 403)

    def test_contacts_agent_allowed_admin_and_guest_blocked(self):
        agent = self._login(self.agent_user, ROLE_AGENT, self.org, self.agent_id)
        self.assertEqual(agent.get("/contacts").status_code, 200)
        admin = self._login(self.admin_id, ROLE_ADMIN, self.org)
        self.assertIn(admin.get("/contacts").status_code, (302, 403))
        self.assertIn(admin.get("/contacts/new").status_code, (302, 403))
        guest = app.test_client()
        self.assertIn(guest.get("/contacts").status_code, (302, 403))

    def test_agenda_agent_allowed_admin_and_guest_blocked(self):
        agent = self._login(self.agent_user, ROLE_AGENT, self.org, self.agent_id)
        self.assertEqual(agent.get("/agenda").status_code, 200)
        self.assertEqual(agent.get("/agenda/new").status_code, 200)
        admin = self._login(self.admin_id, ROLE_ADMIN, self.org)
        self.assertIn(admin.get("/agenda").status_code, (302, 403))
        self.assertIn(admin.get("/agenda/new").status_code, (302, 403))
        guest = app.test_client()
        self.assertIn(guest.get("/agenda").status_code, (302, 403))

    def test_team_summary_only_for_own_team_leader(self):
        agent = self._login(self.agent_user, ROLE_AGENT, self.org, self.agent_id)
        self.assertIn(agent.get("/reports/team").status_code, (302, 403))
        self.assertIn(agent.get(f"/reports/team/{self.leader_id}").status_code, (302, 403))

        leader = self._login(self.leader_user, ROLE_AGENT, self.org, self.leader_id)
        own = leader.get(f"/reports/team/{self.leader_id}")
        self.assertEqual(own.status_code, 200)
        self.assertIn(
            leader.get(f"/reports/team/{self.other_leader_id}").status_code,
            (302, 403),
        )
        switched = leader.get(
            f"/reports/team/{self.leader_id}?team_leader_id={self.other_leader_id}"
        )
        self.assertEqual(switched.status_code, 200)
        self.assertNotIn("Maria Leader", switched.get_data(as_text=True))

        admin = self._login(self.admin_id, ROLE_ADMIN, self.org)
        self.assertIn(admin.get("/reports/team").status_code, (302, 403))
        self.assertIn(admin.get(f"/reports/team/{self.leader_id}").status_code, (302, 403))

        foreign = self._login(
            self.foreign_leader_user,
            ROLE_AGENT,
            self.other,
            self.foreign_leader_id,
        )
        self.assertIn(foreign.get(f"/reports/team/{self.leader_id}").status_code, (302, 403))
        self.assertEqual(
            foreign.get(f"/reports/team/{self.foreign_leader_id}").status_code,
            200,
        )

    def test_navigation_matches_role(self):
        agent_keys = self._nav_keys(
            self.agent_user, ROLE_AGENT, self.org, self.agent_id
        )
        self.assertIn("agenda_index", agent_keys)
        self.assertIn("contacts_index", agent_keys)
        self.assertIn("marketing_home", agent_keys)
        self.assertNotIn("team_report", agent_keys)
        self.assertNotIn("cash_list", agent_keys)

        leader_keys = self._nav_keys(
            self.leader_user, ROLE_AGENT, self.org, self.leader_id
        )
        self.assertIn("agenda_index", leader_keys)
        self.assertIn("contacts_index", leader_keys)
        self.assertIn("marketing_home", leader_keys)
        self.assertIn("team_report", leader_keys)

        admin_keys = self._nav_keys(self.admin_id, ROLE_ADMIN, self.org)
        self.assertIn("cash_list", admin_keys)
        self.assertIn("users_list", admin_keys)
        self.assertNotIn("agenda_index", admin_keys)
        self.assertNotIn("contacts_index", admin_keys)
        self.assertNotIn("marketing_home", admin_keys)
        self.assertNotIn("team_report", admin_keys)

        agent = self._login(self.agent_user, ROLE_AGENT, self.org, self.agent_id)
        agent_home = agent.get("/").get_data(as_text=True)
        self.assertIn('href="/contacts"', agent_home)
        self.assertIn('href="/agenda"', agent_home)
        self.assertIn('href="/marketing"', agent_home)
        self.assertNotIn('href="/reports/team/', agent_home)

        leader = self._login(self.leader_user, ROLE_AGENT, self.org, self.leader_id)
        leader_home = leader.get("/").get_data(as_text=True)
        self.assertIn('href="/contacts"', leader_home)
        self.assertIn('href="/agenda"', leader_home)
        self.assertIn('href="/marketing"', leader_home)
        self.assertIn(f'href="/reports/team/{self.leader_id}"', leader_home)

        admin = self._login(self.admin_id, ROLE_ADMIN, self.org)
        admin_home = admin.get("/").get_data(as_text=True)
        self.assertIn('href="/cash"', admin_home)
        self.assertNotIn('href="/contacts"', admin_home)
        self.assertNotIn('href="/agenda"', admin_home)
        self.assertNotIn('href="/marketing"', admin_home)
        self.assertNotIn('href="/reports/team"', admin_home)
        self.assertIn("m-home", admin_home)
        self.assertIn("mobile-bottom-nav", admin_home)
        self.assertNotIn("mobile-bottom-nav-label\">Agenda", admin_home)
        self.assertNotIn("mobile-bottom-nav-label\">Contactos", admin_home)


if __name__ == "__main__":
    unittest.main()
