"""Office onboarding stays invite-gated, transactional, and tenant-isolated."""

from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_onboarding.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
os.environ["APP_ENV"] = "development"
os.environ["JRH_ONBOARDING_INVITE_CODE"] = "achard-invite"

from modules.auth import authenticate_user
from modules.config import apply_config
from modules.database import create_tables
from modules.database.connection import get_connection
from modules.database.organization_settings_repository import (
    get_organization_settings,
)
from modules.database.organizations_repository import (
    execute_insert,
    get_organization_by_id,
    get_organizations,
    provision_organization,
)
from modules.database.treasury_accounts_repository import list_treasury_accounts
from modules.database.users_repository import get_user_by_id
from modules.onboarding import (
    EVENT_INVITE_FAILED,
    _record_event,
)
from web_app import app

INVITE = "achard-invite"


def _counts():
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM organizations")
    organizations = int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM users")
    users = int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM treasury_accounts")
    boxes = int(cursor.fetchone()[0])
    connection.close()
    return organizations, users, boxes


def _payload(**overrides):
    data = {
        "invite_code": INVITE,
        "name": "Achard Propiedades",
        "country": "Uruguay",
        "region": "Montevideo",
        "city": "Montevideo",
        "timezone": "America/Montevideo",
        "currency": "USD",
        "language": "es",
        "first_name": "Ana",
        "last_name": "Achard",
        "email": "ana@achard.test",
        "password": "Achard2026",
        "confirm_password": "Achard2026",
        "commercial_name": "Achard Propiedades",
        "marketing_phone": "1112345678",
        "marketing_email": "hola@achard.test",
        "marketing_website": "https://achard.test",
        "accent_color": "#112233",
        "company_website": "",
    }
    data.update(overrides)
    return data


class OnboardingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        create_tables()

    def setUp(self):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM onboarding_rate_events")
        connection.commit()
        connection.close()
        self.client = app.test_client()

    def test_invalid_invite_creates_nothing_and_lists_no_offices(self):
        before = _counts()
        response = self.client.post(
            "/onboarding",
            data=_payload(invite_code="wrong-code"),
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn("No se pudo completar el alta.", body)
        self.assertNotIn("Inmobiliaria Principal", body)
        self.assertNotIn("name=\"name\" value=\"Achard Propiedades\"", body)
        self.assertEqual(_counts(), before)

    def test_missing_invite_configuration_fails_closed(self):
        before = _counts()
        with patch.dict(os.environ, {"JRH_ONBOARDING_INVITE_CODE": ""}):
            response = self.client.post("/onboarding", data=_payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn("No se pudo completar el alta.", response.get_data(as_text=True))
        self.assertEqual(_counts(), before)

    def test_honeypot_matches_the_generic_failure(self):
        before = _counts()
        response = self.client.post(
            "/onboarding",
            data=_payload(company_website="https://spam.test"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No se pudo completar el alta.", response.get_data(as_text=True))
        self.assertEqual(_counts(), before)

    def test_failed_invite_rate_limit_is_persistent(self):
        for _ in range(10):
            _record_event(EVENT_INVITE_FAILED)
        before = _counts()
        response = self.client.post("/onboarding", data=_payload())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_counts(), before)
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM onboarding_rate_events")
        self.assertGreaterEqual(int(cursor.fetchone()[0]), 10)
        connection.close()

    def test_a_second_office_is_not_blocked_by_the_first(self):
        first = self.client.post(
            "/onboarding",
            data=_payload(email="uno@achard.test", name="Oficina Uno"),
        )
        second = self.client.post(
            "/onboarding",
            data=_payload(email="dos@achard.test", name="Oficina Dos"),
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)

    def test_created_cap_can_still_be_configured(self):
        self.client.post(
            "/onboarding",
            data=_payload(email="cap1@achard.test", name="Tope Uno"),
        )
        with patch("modules.onboarding.CREATED_LIMIT", 1):
            blocked = self.client.post(
                "/onboarding",
                data=_payload(email="cap2@achard.test", name="Tope Dos"),
            )
        self.assertEqual(blocked.status_code, 400)
        names = [item["name"] for item in get_organizations()]
        self.assertIn("Tope Uno", names)
        self.assertNotIn("Tope Dos", names)

    def test_invalid_email_password_and_long_username(self):
        before = _counts()
        invalid_email = self.client.post(
            "/onboarding",
            data=_payload(email="not-an-email"),
        )
        invalid_password = self.client.post(
            "/onboarding",
            data=_payload(email="corta@achard.test", password="password", confirm_password="password"),
        )
        long_email = "a" * 60 + "@achard.test"
        long_username = self.client.post(
            "/onboarding",
            data=_payload(email=long_email),
        )
        self.assertEqual(invalid_email.status_code, 400)
        self.assertIn("email no es válido", invalid_email.get_data(as_text=True))
        self.assertEqual(invalid_password.status_code, 400)
        self.assertEqual(long_username.status_code, 400)
        self.assertIn("64", long_username.get_data(as_text=True))
        self.assertEqual(_counts(), before)
        self.assertGreater(len(long_email), 64)

    def test_duplicate_names_are_allowed_and_codes_differ(self):
        self.client.post("/onboarding", data=_payload(email="dup1@achard.test"))
        self.client.get("/logout")
        self.client.post("/onboarding", data=_payload(email="dup2@achard.test"))
        matches = [
            item for item in get_organizations()
            if item["name"] == "Achard Propiedades"
        ]
        self.assertGreaterEqual(len(matches), 2)
        hashes = {
            get_organization_settings(item["id"])["registration_code_hash"]
            for item in matches
        }
        self.assertEqual(len(hashes), len(matches))
        self.assertNotIn(None, hashes)

    def test_empty_legal_identity_is_stored_blank(self):
        self.client.post(
            "/onboarding",
            data=_payload(email="legal@achard.test", name="Sin Legales"),
        )
        created = next(
            item for item in get_organizations() if item["name"] == "Sin Legales"
        )
        settings = get_organization_settings(created["id"])
        self.assertEqual(settings["legal_broker_name"], "")
        self.assertEqual(settings["legal_broker_license"], "")
        self.assertEqual(settings["legal_office_name"], "")
        self.assertEqual(settings["legal_footer_line"], "")

    def test_provision_does_not_stamp_data_house_or_organization_one(self):
        with patch("modules.auth.authenticate_user", side_effect=AssertionError("lookup")):
            response = self.client.post(
                "/onboarding",
                data=_payload(
                    email="ana.principal@achard.test",
                    name="Achard Propiedades",
                    organization_id="1",
                ),
            )
        self.assertEqual(response.status_code, 302)
        ready = self.client.get("/onboarding/ready")
        body = ready.get_data(as_text=True)
        main = body.split('<main class="onboarding">', 1)[-1].split("</main>", 1)[0]
        self.assertNotIn("Data House", main)
        self.assertNotIn("RE/MAX", main)
        created = [
            item for item in get_organizations()
            if item["name"] == "Achard Propiedades"
        ][-1]
        self.assertNotEqual(created["id"], 1)
        principal = get_organization_by_id(1)
        self.assertEqual(principal["name"], "Inmobiliaria Principal")
        settings = get_organization_settings(created["id"])
        self.assertNotIn("Data House", settings["display_name"])
        self.assertEqual(settings["country"], "Uruguay")
        self.assertEqual(settings["timezone"], "America/Montevideo")
        self.assertEqual(settings["marketing_website"], "https://achard.test")
        self.assertEqual(settings["marketing_email"], "hola@achard.test")
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, role, account_status, email, first_name
            FROM users
            WHERE organization_id = ? AND email = ?
            """,
            (created["id"], "ana.principal@achard.test"),
        )
        row = cursor.fetchone()
        connection.close()
        self.assertEqual(row[1], "admin")
        self.assertEqual(row[2], "active")
        self.assertEqual(row[4], "Ana")
        loaded = get_user_by_id(row[0])
        with self.client.session_transaction() as current:
            self.assertEqual(int(current["organization_id"]), created["id"])
            self.assertEqual(int(current["user_id"]), loaded["id"])
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        home_body = home.get_data(as_text=True)
        self.assertIn("Cargá tu primera propiedad", home_body)
        self.assertIn("Logo pendiente", home_body)
        self.assertIn("Identidad legal incompleta", home_body)
        self.assertNotIn("Data House", home_body)
        agents = self.client.get("/agents")
        self.assertEqual(agents.status_code, 200)
        self.assertIn("Agregá tu primer agente", agents.get_data(as_text=True))
        boxes = list_treasury_accounts(created["id"])
        self.assertEqual(
            {item["currency"] for item in boxes},
            {"ARS", "USD"},
        )
        self.assertTrue(all(item["organization_id"] == created["id"] for item in boxes))

    def test_failed_logo_keeps_the_organization(self):
        before = _counts()[0]
        data = _payload(email="logo@achard.test", name="Logo Pendiente")
        data["logo"] = (io.BytesIO(b"png"), "logo.png")
        with patch("web_app.save_organization_logo", side_effect=RuntimeError("disk")):
            response = self.client.post(
                "/onboarding",
                data=data,
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        created = next(item for item in get_organizations() if item["name"] == "Logo Pendiente")
        settings = get_organization_settings(created["id"])
        self.assertFalse(settings["logo_path"])
        self.assertGreater(_counts()[0], before)
        ready = self.client.get("/onboarding/ready")
        self.assertIn("logo no se pudo guardar", ready.get_data(as_text=True))
        user, error = authenticate_user("logo@achard.test", "Achard2026")
        self.assertIsNone(error)
        self.assertEqual(user["organization_id"], created["id"])

    def test_checklist_stays_until_the_office_is_complete(self):
        self.client.post(
            "/onboarding",
            data=_payload(email="check@achard.test", name="Checklist Office"),
        )
        created = next(item for item in get_organizations() if item["name"] == "Checklist Office")
        dismissed = self.client.post("/onboarding/checklist/dismiss")
        self.assertEqual(dismissed.status_code, 302)
        settings = get_organization_settings(created["id"])
        self.assertFalse(settings["onboarding_checklist_dismissed_at"])
        home = self.client.get("/")
        self.assertIn("Primeros pasos", home.get_data(as_text=True))

    def _raise_on_users(self, cursor, sql, params=None):
        if "INSERT INTO users" in sql:
            raise RuntimeError("admin failed")
        return execute_insert(cursor, sql, params)

    def test_admin_failure_rolls_back_the_organization(self):
        before = _counts()
        with patch(
            "modules.database.organizations_repository.execute_insert",
            side_effect=self._raise_on_users,
        ):
            with self.assertRaises(Exception):
                provision_organization(
                    name="Rollback Admin",
                    display_name="Rollback Admin",
                    default_language="es",
                    default_currency="USD",
                    timezone="America/Montevideo",
                    admin_username="rollback-admin@achard.test",
                    admin_password_hash="hash",
                    admin_role="admin",
                    registration_code_hash="hash-admin",
                )
        self.assertEqual(_counts(), before)

    def test_settings_failure_rolls_back_the_organization(self):
        before = _counts()
        real_get = get_connection

        class ConnectionProxy:
            def __init__(self, inner):
                self.inner = inner

            def cursor(self):
                return CursorProxy(self.inner.cursor())

            def commit(self):
                return self.inner.commit()

            def rollback(self):
                return self.inner.rollback()

            def close(self):
                return self.inner.close()

        class CursorProxy:
            def __init__(self, inner):
                self.inner = inner

            def execute(self, sql, params=()):
                if "INSERT INTO organization_settings" in sql:
                    raise RuntimeError("settings failed")
                return self.inner.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with patch(
            "modules.database.organizations_repository.get_connection",
            side_effect=lambda: ConnectionProxy(real_get()),
        ):
            with self.assertRaises(Exception):
                provision_organization(
                    name="Rollback Settings",
                    display_name="Rollback Settings",
                    default_language="es",
                    default_currency="USD",
                    timezone="America/Montevideo",
                    admin_username="rollback-settings@achard.test",
                    admin_password_hash="hash",
                    admin_role="admin",
                    registration_code_hash="hash-settings",
                )
        self.assertEqual(_counts(), before)

    def test_treasury_failure_rolls_back_the_organization(self):
        before = _counts()
        with patch(
            "modules.database.treasury_accounts_repository.ensure_legacy_default_accounts",
            side_effect=RuntimeError("boxes failed"),
        ):
            with self.assertRaises(Exception):
                provision_organization(
                    name="Rollback Boxes",
                    display_name="Rollback Boxes",
                    default_language="es",
                    default_currency="USD",
                    timezone="America/Montevideo",
                    admin_username="rollback-boxes@achard.test",
                    admin_password_hash="hash",
                    admin_role="admin",
                    registration_code_hash="hash-boxes",
                )
        self.assertEqual(_counts(), before)
        names = [item["name"] for item in get_organizations()]
        self.assertNotIn("Rollback Boxes", names)

    def test_two_offices_stay_isolated_through_the_ui(self):
        achard_code = self._onboard_and_read_code(
            email="admin.achard@achard.test",
            name="Achard Propiedades",
            commercial_name="Achard Comercial",
            currency="USD",
            timezone="America/Montevideo",
            marketing_phone="1111111111",
            country="Uruguay",
        )
        self.client.get("/logout")
        norte_code = self._onboard_and_read_code(
            email="admin.norte@norte.test",
            name="Norte Propiedades",
            commercial_name="Norte Comercial",
            currency="ARS",
            timezone="America/Santiago",
            marketing_phone="2222222222",
            country="Chile",
            first_name="Luis",
            last_name="Norte",
        )
        self.assertNotEqual(achard_code, norte_code)
        achard = {"id": self._organization_for_email("admin.achard@achard.test")}
        norte = {"id": self._organization_for_email("admin.norte@norte.test")}
        achard_settings = get_organization_settings(achard["id"])
        norte_settings = get_organization_settings(norte["id"])
        self.assertNotEqual(achard_settings["display_name"], norte_settings["display_name"])
        self.assertNotEqual(achard_settings["default_currency"], norte_settings["default_currency"])
        self.assertNotEqual(achard_settings["timezone"], norte_settings["timezone"])
        self.assertNotEqual(
            achard_settings["registration_code_hash"],
            norte_settings["registration_code_hash"],
        )
        achard_boxes = {item["id"] for item in list_treasury_accounts(achard["id"])}
        norte_boxes = {item["id"] for item in list_treasury_accounts(norte["id"])}
        self.assertTrue(achard_boxes)
        self.assertTrue(norte_boxes)
        self.assertTrue(achard_boxes.isdisjoint(norte_boxes))

        self.client.get("/logout")
        achard_agent = self._register_and_approve(
            achard_code,
            "agente.achard@achard.test",
            "Agente Achard",
        )
        norte_agent = self._register_and_approve(
            norte_code,
            "agente.norte@norte.test",
            "Agente Norte",
        )
        self.assertNotEqual(achard_agent, norte_agent)

        self._login("agente.achard@achard.test", "Agente2026")
        achard_property = self._create_property("Calle Achard 100")
        self.client.get("/logout")
        self._login("agente.norte@norte.test", "Agente2026")
        norte_property = self._create_property("Calle Norte 200")
        self.client.get("/logout")
        self._login("admin.achard@achard.test", "Achard2026")
        approved = self.client.post(f"/approvals/properties/{achard_property}/approve")
        self.assertIn(approved.status_code, (302, 303))
        self.client.get("/logout")
        self._login("admin.norte@norte.test", "Achard2026")
        approved_norte = self.client.post(f"/approvals/properties/{norte_property}/approve")
        self.assertIn(approved_norte.status_code, (302, 303))

        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT organization_id, address FROM properties WHERE id IN (?, ?)",
            (achard_property, norte_property),
        )
        located = {row[1]: row[0] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT organization_id FROM agents WHERE id = ?",
            (achard_agent,),
        )
        achard_agent_org = cursor.fetchone()[0]
        cursor.execute(
            "SELECT organization_id FROM agents WHERE id = ?",
            (norte_agent,),
        )
        norte_agent_org = cursor.fetchone()[0]
        connection.close()
        self.assertEqual(located["Calle Achard 100"], achard["id"])
        self.assertEqual(located["Calle Norte 200"], norte["id"])
        self.assertEqual(achard_agent_org, achard["id"])
        self.assertEqual(norte_agent_org, norte["id"])

        self.client.get("/logout")
        self._login("agente.achard@achard.test", "Agente2026")
        token = self._publish_property(achard_property)
        public_page = self.client.get(f"/p/{token}")
        self.assertEqual(public_page.status_code, 200)
        public_body = public_page.get_data(as_text=True)
        self.assertIn("Calle Achard 100", public_body)
        self.assertNotIn("Calle Norte 200", public_body)
        self.assertNotIn("Data House", public_body)

    def _organization_for_email(self, email):
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT organization_id FROM users WHERE email = ?",
            (email,),
        )
        row = cursor.fetchone()
        connection.close()
        self.assertIsNotNone(row)
        return row[0]

    def _onboard_and_read_code(self, **overrides):
        response = self.client.post("/onboarding", data=_payload(**overrides))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        ready = self.client.get("/onboarding/ready")
        self.assertEqual(ready.status_code, 200)
        match = re.search(
            r'id="onboarding-registration-code"[^>]*>([^<]+)',
            ready.get_data(as_text=True),
        )
        self.assertIsNotNone(match)
        return match.group(1).strip()

    def _login(self, email, password):
        self.client.get("/logout")
        response = self.client.post(
            "/login",
            data={"username": email, "password": password},
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))

    def _register_and_approve(self, code, email, full_name):
        first_name, last_name = full_name.split(" ", 1)
        captured = {}

        def capture(to_email, raw_code, language="es"):
            captured["code"] = raw_code
            return True

        with patch(
            "modules.registration.send_verification_code_email",
            side_effect=capture,
        ):
            registered = self.client.post(
                "/register",
                data={
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "phone": "1112345678",
                    "organization_code": code,
                    "password": "Agente2026",
                    "confirm_password": "Agente2026",
                },
            )
        self.assertEqual(registered.status_code, 302, registered.get_data(as_text=True))
        self.assertIn("code", captured)
        digits = {
            f"digit{index + 1}": character
            for index, character in enumerate(captured["code"])
        }
        verified = self.client.post("/verify-email", data=digits)
        self.assertIn(verified.status_code, (200, 302))
        self.client.get("/logout")
        admin_email = (
            "admin.achard@achard.test"
            if "achard" in email
            else "admin.norte@norte.test"
        )
        self._login(admin_email, "Achard2026")
        listing = self.client.get("/access-requests")
        self.assertEqual(listing.status_code, 200)
        request_ids = re.findall(r"/access-requests/(\d+)", listing.get_data(as_text=True))
        self.assertTrue(request_ids)
        approved = self.client.post(
            f"/access-requests/{request_ids[0]}",
            data={"action": "approve", "agent_mode": "create", "agent_type": "Alto"},
        )
        self.assertEqual(approved.status_code, 302, approved.get_data(as_text=True))
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT agent_id
            FROM users
            WHERE email = ? AND account_status = 'active'
            """,
            (email,),
        )
        row = cursor.fetchone()
        connection.close()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])
        return row[0]

    def _create_property(self, address):
        created = self.client.post(
            "/properties/new",
            data={
                "address": address,
                "jurisdiction": "CABA",
                "property_type": "apartment",
                "listing_purpose": "sale",
                "listing_currency": "USD",
                "listing_price": "100000",
                "commercial_status": "available",
            },
        )
        self.assertEqual(created.status_code, 302, created.get_data(as_text=True))
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT id FROM properties WHERE address = ? ORDER BY id DESC LIMIT 1",
            (address,),
        )
        row = cursor.fetchone()
        connection.close()
        self.assertIsNotNone(row)
        return row[0]

    def _publish_property(self, property_id):
        contact = self.client.post(
            "/contacts/new",
            data={"name": "Cliente Achard", "phone": "1112345678"},
        )
        self.assertEqual(contact.status_code, 302, contact.get_data(as_text=True))
        contact_id = re.search(r"/contacts/(\d+)", contact.headers["Location"]).group(1)
        shared = self.client.post(
            f"/contacts/{contact_id}/shortlist",
            data={"property_id": str(property_id)},
        )
        self.assertEqual(shared.status_code, 302, shared.get_data(as_text=True))
        page = self.client.get(f"/contacts/{contact_id}/shortlist")
        self.assertEqual(page.status_code, 200, page.get_data(as_text=True))
        match = re.search(r"/p/([A-Za-z0-9_-]+)", page.get_data(as_text=True))
        self.assertIsNotNone(match, page.get_data(as_text=True)[:1500])
        return match.group(1)
