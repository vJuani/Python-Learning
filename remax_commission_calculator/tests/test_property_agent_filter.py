"""
Tests for property list agent_id filter.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_property_agent_filter.db"
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
from modules.database.properties_repository import STATUS_APPROVED
from modules.filter_helpers import parse_filter_agent_id
from modules.properties import get_filtered_properties
from web_app import app


class PropertyAgentFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-property-agent-filter"
        create_tables()

        cls.org_a = add_organization("Filter Org A")
        cls.org_b = add_organization("Filter Org B")
        pwd = hash_password("Password1")

        cls.agent_jose = add_agent(
            "Jose Luis Barreiro",
            "Alto",
            cls.org_a,
        )
        cls.agent_other = add_agent(
            "Otro Agente",
            "Alto",
            cls.org_a,
        )
        cls.agent_b = add_agent(
            "Agent B",
            "Alto",
            cls.org_b,
        )

        cls.admin_a = add_user(
            "prop_filter_admin",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.user_jose = add_user(
            "prop_filter_jose",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_jose,
        )

        cls.prop_jose = add_property(
            "Calle Jose 1",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_jose,
            status=STATUS_APPROVED,
        )
        cls.prop_other = add_property(
            "Calle Otro 2",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_other,
            status=STATUS_APPROVED,
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

    def test_filter_by_valid_agent_id(self):
        errors, props = get_filtered_properties(
            {
                "property_id": "",
                "address": "",
                "jurisdiction": "",
                "agent_id": str(self.agent_jose),
                "min_price": "",
                "max_price": "",
            },
            self.org_a,
        )
        self.assertEqual(errors, [])
        ids = {p["id"] for p in props}
        self.assertEqual(ids, {self.prop_jose})

    def test_filter_agent_with_no_properties_returns_empty(self):
        empty_agent = add_agent(
            "Sin Props",
            "Alto",
            self.org_a,
        )
        errors, props = get_filtered_properties(
            {
                "property_id": "",
                "address": "",
                "jurisdiction": "",
                "agent_id": str(empty_agent),
                "min_price": "",
                "max_price": "",
            },
            self.org_a,
        )
        self.assertEqual(errors, [])
        self.assertEqual(props, [])

    def test_invalid_agent_id_does_not_raise(self):
        errors, props = get_filtered_properties(
            {
                "property_id": "",
                "address": "",
                "jurisdiction": "",
                "agent_id": "not-a-number",
                "min_price": "",
                "max_price": "",
            },
            self.org_a,
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(props), 2)

    def test_other_org_agent_id_is_ignored(self):
        errors, props = get_filtered_properties(
            {
                "property_id": "",
                "address": "",
                "jurisdiction": "",
                "agent_id": str(self.agent_b),
                "min_price": "",
                "max_price": "",
            },
            self.org_a,
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(props), 2)

    def test_parse_filter_agent_id_accepts_name_value_safely(self):
        self.assertIsNone(
            parse_filter_agent_id(
                "Jose Luis Barreiro",
                self.org_a,
            )
        )

    def test_web_filter_by_agent_id_returns_200(self):
        self._login("prop_filter_admin")
        response = self.client.get(
            f"/properties?agent_id={self.agent_jose}"
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Calle Jose 1", body)
        self.assertNotIn("Calle Otro 2", body)

    def test_web_filter_keeps_selected_agent(self):
        self._login("prop_filter_admin")
        response = self.client.get(
            f"/properties?agent_id={self.agent_jose}"
        )
        body = response.get_data(as_text=True)
        self.assertIn(f'value="{self.agent_jose}"', body)
        self.assertRegex(
            body,
            rf'value="{self.agent_jose}"[^>]*selected',
        )

    def test_agent_scope_blocks_other_agent_filter(self):
        self._login("prop_filter_jose")
        response = self.client.get(
            f"/properties?agent_id={self.agent_other}"
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Calle Jose 1", body)
        self.assertNotIn("Calle Otro 2", body)


if __name__ == "__main__":
    unittest.main()
