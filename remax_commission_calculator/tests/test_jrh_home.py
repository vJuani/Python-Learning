"""Home V2 + global JRH IA intent router tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_jrh_home.db")

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
)
from modules.database.connection import get_connection
from modules.jrh_intent import (
    INTENT_AGENDA,
    INTENT_CONTACT,
    INTENT_INVOICE,
    INTENT_PENDING,
    INTENT_PROPERTY_SEARCH,
    INTENT_UNKNOWN,
    STATUS_NEEDS_ATTENTION,
    STATUS_READY,
    STATUS_UNSUPPORTED,
    classify_jrh_segment,
    interpret_jrh_request,
    split_jrh_segments,
)
from web_app import app


def _task_count(organization_id):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM agent_tasks WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()[0]
    finally:
        connection.close()


class JrhHomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="jrh-home-test")
        create_tables()
        cls.org = add_organization("JRH Home Org")
        cls.other_org = add_organization("JRH Other Org")
        cls.password = "Password1"
        pwd = hash_password(cls.password)
        cls.agent_id = add_agent("Home Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Home Other Agent", "Alto", cls.org)
        cls.foreign_agent = add_agent("Foreign Agent", "Alto", cls.other_org)
        cls.admin = add_user("jrh_admin", pwd, ROLE_ADMIN, cls.org, email="jrh_admin@example.com")
        cls.agent_user = add_user(
            "jrh_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            email="jrh_agent@example.com",
        )
        cls.other_user = add_user(
            "jrh_other_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
            email="jrh_other@example.com",
        )
        cls.foreign_user = add_user(
            "jrh_foreign",
            pwd,
            ROLE_AGENT,
            cls.other_org,
            agent_id=cls.foreign_agent,
            email="jrh_foreign@example.com",
        )
        cls.carolina = create_agent_contact(
            cls.org,
            cls.agent_id,
            {"name": "Carolina López", "phone": "5491111111111"},
        )
        cls.martin = create_agent_contact(
            cls.org,
            cls.agent_id,
            {"name": "Martín Pérez", "phone": "5491122222222"},
        )

    def _login(self, username, role=ROLE_AGENT, org=None, user_id=None):
        client = app.test_client()
        mapping = {
            "jrh_admin": (self.admin, ROLE_ADMIN, self.org),
            "jrh_agent": (self.agent_user, ROLE_AGENT, self.org),
            "jrh_other_agent": (self.other_user, ROLE_AGENT, self.org),
            "jrh_foreign": (self.foreign_user, ROLE_AGENT, self.other_org),
        }
        uid, mapped_role, mapped_org = mapping[username]
        with client.session_transaction() as sess:
            sess["user_id"] = user_id or uid
            sess["role"] = role or mapped_role
            sess["organization_id"] = org or mapped_org
        return client

    def test_home_agent_shows_jrh_hero(self):
        client = self._login("jrh_agent")
        page = client.get("/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("jrh-hero", body)
        self.assertIn("¿Qué necesitás hacer?", body)
        self.assertIn("Hablar con JRH", body)
        self.assertIn("data-jrh-voice-unsupported", body)
        agenda_voice = client.get("/agenda")
        self.assertIn(
            "data-jrh-voice-unsupported",
            agenda_voice.get_data(as_text=True),
        )
        self.assertIn("agenda-block", body)
        self.assertIn("Para vos", body)
        self.assertIn("Google Calendar", body)

    def test_home_staff_keeps_executive_dashboard(self):
        client = self._login("jrh_admin", role=ROLE_ADMIN)
        page = client.get("/")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Panel ejecutivo", body)
        self.assertNotIn("jrh-hero", body)
        self.assertIn("Requiere tu atención", body)

    def test_intent_agenda(self):
        result = interpret_jrh_request(
            "Agendame mañana a las 15 una visita con Carolina",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        self.assertEqual(len(result["intents"]), 1)
        intent = result["intents"][0]
        self.assertEqual(intent["type"], INTENT_AGENDA)
        self.assertTrue(intent["confirm_required"])
        self.assertIn(intent["status"], (STATUS_READY, STATUS_NEEDS_ATTENTION))
        self.assertIn("Carolina", intent["summary"])

    def test_intent_property_search(self):
        result = interpret_jrh_request(
            "Buscame propiedades para Carolina",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        intent = result["intents"][0]
        self.assertEqual(intent["type"], INTENT_PROPERTY_SEARCH)
        self.assertEqual(intent["status"], STATUS_READY)
        self.assertFalse(intent["confirm_required"])
        self.assertEqual(intent["data"]["contact_id"], self.carolina["id"])

    def test_intent_pending(self):
        result = interpret_jrh_request(
            "¿Qué tengo pendiente hoy?",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        intent = result["intents"][0]
        self.assertEqual(intent["type"], INTENT_PENDING)
        self.assertEqual(intent["status"], STATUS_READY)
        self.assertFalse(intent["confirm_required"])

    def test_intent_contact_no_next(self):
        result = interpret_jrh_request(
            "Mostrame clientes sin próxima acción",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        intent = result["intents"][0]
        self.assertEqual(intent["type"], INTENT_CONTACT)
        self.assertEqual(intent["status"], STATUS_READY)

    def test_intent_invoice_does_not_create(self):
        before = _task_count(self.org)
        result = interpret_jrh_request(
            "Haceme la factura de la operación de Libertador",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        intent = result["intents"][0]
        self.assertEqual(intent["type"], INTENT_INVOICE)
        self.assertFalse(intent["confirm_required"])
        self.assertFalse(result["wrote"])
        self.assertEqual(_task_count(self.org), before)

    def test_multi_intent(self):
        prompt = (
            "Mañana agendame visita con Carolina a las 10, "
            "buscame propiedades para Martín "
            "y decime qué tengo pendiente"
        )
        self.assertEqual(len(split_jrh_segments(prompt)), 3)
        result = interpret_jrh_request(
            prompt,
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        types = [item["type"] for item in result["intents"]]
        self.assertEqual(result["understood_count"], 3)
        self.assertEqual(types, [INTENT_AGENDA, INTENT_PROPERTY_SEARCH, INTENT_PENDING])

    def test_unknown_unsupported(self):
        result = interpret_jrh_request(
            "Mandale un mail masivo a toda la base",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        intent = result["intents"][0]
        self.assertEqual(intent["type"], INTENT_UNKNOWN)
        self.assertEqual(intent["status"], STATUS_UNSUPPORTED)

    def test_needs_attention_which_contact(self):
        result = interpret_jrh_request(
            "Buscame propiedades",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        intent = result["intents"][0]
        self.assertEqual(intent["status"], STATUS_NEEDS_ATTENTION)
        self.assertEqual(intent["message_key"], "jrh_msg_which_contact")

    def test_no_write_without_confirmation(self):
        before = _task_count(self.org)
        client = self._login("jrh_agent")
        response = client.post(
            "/jrh/interpret",
            data={"prompt": "Agendame mañana a las 16 una visita con Carolina"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Entendí 1 cosa", body)
        self.assertIn("Revisar", body)
        self.assertEqual(_task_count(self.org), before)

    def test_voice_uses_same_router(self):
        self.assertEqual(
            classify_jrh_segment("Buscame propiedades para Carolina"),
            INTENT_PROPERTY_SEARCH,
        )
        spoken = interpret_jrh_request(
            "Buscame propiedades para Carolina",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        typed = interpret_jrh_request(
            "Buscame propiedades para Carolina",
            organization_id=self.org,
            agent_id=self.agent_id,
            user_id=self.agent_user,
        )
        self.assertEqual(spoken["intents"][0]["type"], typed["intents"][0]["type"])
        self.assertEqual(
            spoken["intents"][0]["data"]["contact_id"],
            typed["intents"][0]["data"]["contact_id"],
        )

    def test_google_disconnected_on_home(self):
        client = self._login("jrh_agent")
        body = client.get("/").get_data(as_text=True)
        self.assertIn("Google Calendar", body)
        self.assertTrue(
            "Conectar" in body or "No configurado" in body or "unconfigured" in body
        )

    def test_other_agent_cannot_see_private_contact(self):
        result = interpret_jrh_request(
            "Buscame propiedades para Carolina",
            organization_id=self.org,
            agent_id=self.other_agent,
            user_id=self.other_user,
        )
        intent = result["intents"][0]
        self.assertEqual(intent["status"], STATUS_NEEDS_ATTENTION)
        self.assertIsNone(intent["data"].get("contact_id"))

    def test_other_org_forbidden(self):
        client = self._login("jrh_foreign")
        result = interpret_jrh_request(
            "Buscame propiedades para Carolina",
            organization_id=self.other_org,
            agent_id=self.foreign_agent,
            user_id=self.foreign_user,
        )
        self.assertIsNone(result["intents"][0]["data"].get("contact_id"))
        forbidden = client.post(
            "/jrh/interpret",
            data={"prompt": "Buscame propiedades para Carolina"},
        )
        self.assertEqual(forbidden.status_code, 200)
        self.assertNotIn(f"/contacts/{self.carolina['id']}/property-matches", forbidden.get_data(as_text=True))

    def test_guest_cannot_interpret(self):
        client = app.test_client()
        response = client.post(
            "/jrh/interpret",
            data={"prompt": "¿Qué tengo pendiente?"},
        )
        self.assertIn(response.status_code, (302, 401, 403))

    def test_staff_cannot_interpret(self):
        client = self._login("jrh_admin", role=ROLE_ADMIN)
        response = client.post(
            "/jrh/interpret",
            data={"prompt": "¿Qué tengo pendiente?"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Panel ejecutivo", body)
        self.assertNotIn("jrh-result", body)
        self.assertNotIn("Entendí", body)

    def test_voice_stt_stays_on_browser(self):
        from modules.voice_stt import (
            PROVIDER_BROWSER,
            VoiceSttClientRequired,
            VoiceSttNotImplemented,
            current_stt_provider,
            transcribe_audio,
        )

        self.assertEqual(current_stt_provider(), PROVIDER_BROWSER)
        with self.assertRaises(VoiceSttClientRequired):
            transcribe_audio(b"fake-audio")
        with self.assertRaises(VoiceSttNotImplemented):
            transcribe_audio(b"fake-audio", provider="server")

    def test_google_connected_on_home(self):
        from datetime import timedelta

        from modules.database.google_calendar_repository import (
            upsert_calendar_connection,
        )
        from modules.google_calendar import encrypt_token
        from modules.organization_time import now_utc

        previous_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID")
        previous_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET")
        os.environ["GOOGLE_CALENDAR_CLIENT_ID"] = "test-google-client"
        os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"] = "test-google-secret"
        upsert_calendar_connection(
            self.org,
            self.agent_user,
            google_email="juan@example.com",
            refresh_token_encrypted=encrypt_token("refresh-token"),
            access_token_encrypted=encrypt_token("access-token"),
            access_expires_at=(
                now_utc() + timedelta(hours=1)
            ).replace(tzinfo=None).isoformat(),
        )
        try:
            client = self._login("jrh_agent")
            body = client.get("/").get_data(as_text=True)
            self.assertIn("juan@example.com", body)
            self.assertIn("Conectado", body)
        finally:
            if previous_id is None:
                os.environ.pop("GOOGLE_CALENDAR_CLIENT_ID", None)
            else:
                os.environ["GOOGLE_CALENDAR_CLIENT_ID"] = previous_id
            if previous_secret is None:
                os.environ.pop("GOOGLE_CALENDAR_CLIENT_SECRET", None)
            else:
                os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"] = previous_secret

    def test_http_multi_intent_cards(self):
        client = self._login("jrh_agent")
        page = client.post(
            "/jrh/interpret",
            data={
                "prompt": (
                    "Agendame mañana a las 10 una visita con Carolina, "
                    "buscame propiedades para Martín "
                    "y decime qué tengo pendiente"
                )
            },
        )
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Entendí 3 cosas", body)
        self.assertIn("Buscar propiedades", body)
        self.assertIn("Pendientes", body)


if __name__ == "__main__":
    unittest.main()
