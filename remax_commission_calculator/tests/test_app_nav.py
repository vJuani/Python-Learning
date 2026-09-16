"""Sidebar nav config: roles, real routes, active state, no cross-role links."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_app_nav.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("APP_NAV_LEGACY", None)

from modules.app_nav import (
    NAV_GROUPS,
    all_configured_endpoints,
    build_app_nav,
    item_is_active,
    use_legacy_nav,
    viewer_role,
    visible_endpoints,
)
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from web_app import app


class AppNavTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="app-nav-tests")
        create_tables()
        cls.org = add_organization("Nav Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana Nav", "Alto", cls.org)
        cls.admin_id = add_user("admin_nav", pwd, ROLE_ADMIN, cls.org)
        cls.agent_user_id = add_user(
            "agent_nav",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Nav",
        )

    def _nav(self, user, endpoint=None, unread=0):
        with app.test_request_context("/"):
            return build_app_nav(
                user,
                endpoint=endpoint,
                unread_notifications=unread,
            )

    def _user(self, user_id, role, agent_id=None):
        return {
            "id": user_id,
            "role": role,
            "organization_id": self.org,
            "agent_id": agent_id,
        }

    def test_configured_endpoints_exist(self):
        with app.app_context():
            registered = {rule.endpoint for rule in app.url_map.iter_rules()}
        for name in all_configured_endpoints():
            self.assertIn(name, registered, name)

    def test_admin_sees_treasury_and_users_not_acm(self):
        nav = self._nav(self._user(self.admin_id, ROLE_ADMIN))
        with app.test_request_context("/"):
            keys = visible_endpoints(self._user(self.admin_id, ROLE_ADMIN))
        self.assertIn("cash_list", keys)
        self.assertIn("users_list", keys)
        self.assertIn("organization_settings", keys)
        self.assertNotIn("acm_list", keys)
        self.assertNotIn("productivity_home", keys)
        self.assertIn("jrh_ask", keys)
        self.assertIn("marketing_new", keys)
        self.assertEqual(nav["role"], "admin")

    def test_agent_sees_crm_agenda_not_cash(self):
        user = self._user(self.agent_user_id, ROLE_AGENT, self.agent_id)
        with app.test_request_context("/"):
            keys = visible_endpoints(user)
        self.assertIn("contacts_index", keys)
        self.assertIn("agenda_index", keys)
        self.assertIn("acm_list", keys)
        self.assertIn("my_agent_account", keys)
        self.assertIn("billing_list", keys)
        self.assertIn("settings_notifications", keys)
        self.assertNotIn("cash_list", keys)
        self.assertNotIn("users_list", keys)
        self.assertNotIn("agent_account_index", keys)

    def test_guest_sees_only_public_modules(self):
        with app.test_request_context("/"):
            from unittest.mock import patch

            with patch("modules.app_nav.is_guest_session", return_value=True):
                keys = visible_endpoints(None)
        self.assertIn("dashboard", keys)
        self.assertIn("properties_list", keys)
        self.assertIn("operations_list", keys)
        self.assertIn("reports_index", keys)
        self.assertNotIn("contacts_index", keys)
        self.assertNotIn("agenda_index", keys)
        self.assertNotIn("jrh_ask", keys)
        self.assertNotIn("cash_list", keys)

    def test_active_state_uses_prefixes(self):
        self.assertTrue(
            item_is_active(
                {"endpoint": "properties_list", "active_prefixes": ("properties_",)},
                "properties_detail",
            )
        )
        self.assertTrue(
            item_is_active(
                {"endpoint": "operations_list", "active_prefixes": ("operations_",)},
                "operations_detail",
            )
        )
        self.assertTrue(
            item_is_active(
                {
                    "endpoint": "settings_notifications",
                    "active_prefixes": ("settings_notifications", "notifications_"),
                },
                "notifications_list",
            )
        )
        self.assertFalse(
            item_is_active(
                {"endpoint": "dashboard", "exact": True, "active_prefixes": ()},
                "properties_list",
            )
        )
        self.assertFalse(
            item_is_active(
                {
                    "endpoint": "cash_list",
                    "active_prefixes": ("cash_",),
                    "exclude_prefixes": ("cash_ai",),
                },
                "cash_ai_new",
            )
        )
        self.assertTrue(
            item_is_active(
                {
                    "endpoint": "cash_list",
                    "active_prefixes": ("cash_",),
                    "exclude_prefixes": ("cash_ai",),
                },
                "cash_detail",
            )
        )

    def test_property_detail_keeps_properties_active(self):
        user = self._user(self.agent_user_id, ROLE_AGENT, self.agent_id)
        nav = self._nav(user, endpoint="properties_detail")
        properties = next(group for group in nav["groups"] if group["key"] == "properties")
        self.assertTrue(properties["active"])
        self.assertTrue(properties["expanded"])
        child = next(item for item in properties["children"] if item["key"] == "properties_list")
        self.assertTrue(child["active"])

    def test_unread_badge_on_notifications(self):
        user = self._user(self.agent_user_id, ROLE_AGENT, self.agent_id)
        nav = self._nav(user, unread=4)
        settings = next(group for group in nav["groups"] if group["key"] == "settings")
        item = next(child for child in settings["children"] if child["key"] == "notifications")
        self.assertEqual(item["badge"], 4)
        self.assertEqual(settings["badge"], 4)

    def test_http_agent_page_hides_admin_links(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.agent_user_id
            session["role"] = ROLE_AGENT
            session["organization_id"] = self.org
        page = client.get("/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('href="/contacts"', body)
        self.assertIn('href="/jrh"', body)
        self.assertIn('href="/marketing/new"', body)
        self.assertNotIn('href="/cash"', body)
        self.assertNotIn('href="/users"', body)
        self.assertIn('aria-label="', body)
        self.assertIn("jrh-sidebar", body)
        self.assertIn("jrh-sidebar__logo--full", body)
        self.assertIn("logo-horizontal-dark-green.png", body)
        self.assertIn("isotype-dark-green.png", body)

    def test_http_admin_page_shows_treasury(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.admin_id
            session["role"] = ROLE_ADMIN
            session["organization_id"] = self.org
        page = client.get("/")
        body = page.get_data(as_text=True)
        self.assertIn('href="/cash"', body)
        self.assertIn('href="/users"', body)
        self.assertNotIn('href="/acm"', body)

    def test_nav_groups_have_unique_keys(self):
        keys = [group["key"] for group in NAV_GROUPS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_legacy_flag_defaults_off(self):
        self.assertFalse(use_legacy_nav())

    def test_viewer_role_admin_agent(self):
        self.assertEqual(
            viewer_role(self._user(self.admin_id, ROLE_ADMIN)),
            "admin",
        )
        self.assertEqual(
            viewer_role(self._user(self.agent_user_id, ROLE_AGENT, self.agent_id)),
            "agent",
        )


if __name__ == "__main__":
    unittest.main()
