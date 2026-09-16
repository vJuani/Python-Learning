"""Mobile shell: staff/agent home, Más panel, JRH IA, property filters."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_mobile_shell.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from web_app import app


class MobileShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="mobile-shell-tests")
        create_tables()
        cls.org = add_organization("Mobile Shell Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Shell Agent", "Alto", cls.org)
        cls.admin_id = add_user("shell_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_user_id = add_user(
            "shell_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Shell",
        )

    def _login(self, user_id, role):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = self.org
            if role == ROLE_AGENT:
                session["agent_id"] = self.agent_id
        return client

    def test_staff_home_uses_mobile_architecture(self):
        body = self._login(self.admin_id, ROLE_ADMIN).get("/").get_data(as_text=True)
        home = body.split('class="m-home hide-desktop"', 1)[1].split('class="hide-mobile"', 1)[0]
        self.assertIn("m-hello", home)
        self.assertIn("m-kpi-grid", home)
        self.assertIn("Acá tenés el resumen de la oficina.", home)
        self.assertNotIn("home-panel__period-select", home)
        self.assertNotIn("mobile-fab--center", body)
        self.assertNotIn("home-mobile-greeting", home)

    def test_agent_home_follows_wireframe_order(self):
        body = self._login(self.agent_user_id, ROLE_AGENT).get("/").get_data(as_text=True)
        home = body.split('class="m-home hide-desktop"', 1)[1].split('class="hide-mobile"', 1)[0]
        self.assertIn("Hola, Ana", home)
        self.assertIn("Acá tenés tu día en JRH One.", home)
        self.assertLess(home.find("m-today"), home.find("m-jrh-card"))
        self.assertLess(home.find("m-jrh-card"), home.find("m-tile-grid"))
        self.assertIn("m-composer__send", home)
        self.assertIn("m-composer__mic", home)
        self.assertNotIn("mobile-fab--center", body)

    def test_more_panel_is_not_the_desktop_drawer(self):
        body = self._login(self.admin_id, ROLE_ADMIN).get("/").get_data(as_text=True)
        start = body.index('id="mobile-more"')
        end = body.index("m-more__logout", start)
        more = body[start:end]
        self.assertIn("Módulos", more)
        self.assertIn("Preferencias", more)
        self.assertIn("data-open-sheet=\"language\"", more)
        self.assertIn("data-open-sheet=\"appearance\"", more)
        self.assertIn("m-account", more)
        self.assertIn('href="/logout"', more)
        self.assertNotIn("Navegación", more)
        self.assertNotIn("No tenés notificaciones", more)
        self.assertNotIn("m-drawer-close", more)
        self.assertNotIn("filters-toggle", more)

    def test_agent_bottom_nav_keeps_agenda_and_contacts(self):
        body = self._login(self.agent_user_id, ROLE_AGENT).get("/").get_data(as_text=True)
        nav = body.split('class="mobile-bottom-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('href="/agenda"', nav)
        self.assertIn('href="/contacts"', nav)
        self.assertNotIn('href="/reports"', nav)

    def test_staff_bottom_nav_keeps_reports_and_operations(self):
        body = self._login(self.admin_id, ROLE_ADMIN).get("/").get_data(as_text=True)
        nav = body.split('class="mobile-bottom-nav"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('href="/reports"', nav)
        self.assertIn('href="/operations"', nav)
        self.assertNotIn('href="/agenda"', nav)

    def test_jrh_ask_has_compact_empty_and_send_icon(self):
        body = self._login(self.admin_id, ROLE_ADMIN).get("/jrh").get_data(as_text=True)
        self.assertIn("jrh-studio__ask-icon", body)
        self.assertIn("¿En qué te ayudo?", body)
        self.assertIn("Podés buscar propiedades, agendar, consultar contactos o crear un ACM.", body)
        self.assertIn("Preguntame algo...", body)
        self.assertIn("Tu asistente para trabajar más rápido.", body)

    def test_property_filters_sheet_hooks_exist(self):
        page = self._login(self.admin_id, ROLE_ADMIN).get("/properties")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("data-open-property-filters", body)
        self.assertIn("data-filter-sheet", body)
        self.assertIn("data-sheet-panel", body)
        self.assertIn("data-close-property-filters", body)
        self.assertIn('id="m-sheet-dynamic"', body)
        self.assertIn("data-m-sheet-backdrop", body)
        self.assertIn("m-sheet-actions", body)

    def test_language_and_appearance_sheets_exist(self):
        body = self._login(self.admin_id, ROLE_ADMIN).get("/").get_data(as_text=True)
        self.assertIn('id="m-sheet-language"', body)
        self.assertIn('id="m-sheet-appearance"', body)
        self.assertIn('id="m-voice"', body)
        self.assertIn("data-set-theme", body)
        self.assertIn('data-set-theme="system"', body)
        self.assertIn("Claro", body)
        self.assertIn("Oscuro", body)
        self.assertIn("Sistema", body)
        self.assertIn('id="m-sheet-dynamic"', body)

    def test_notifications_page_has_mobile_title(self):
        page = self._login(self.admin_id, ROLE_ADMIN).get("/notifications")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("is-notifications-page", body)
        self.assertIn("No tenés notificaciones.", body)
        self.assertIn("Estás al día.", body)


if __name__ == "__main__":
    unittest.main()
