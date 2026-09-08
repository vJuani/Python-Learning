"""Contactos V2: import, ownership, history, JRH, needs."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_contacts_v2.db")

from modules.agent_productivity import (
    confirm_logged_activity,
    enrich_logged_contacts,
    propose_logged_activity,
)
from modules.agent_tasks import create_task
from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contact_import import (
    confirm_import,
    merge_import_fields,
    preview_import,
)
from modules.contact_normalize import normalize_email, normalize_phone
from modules.contacts import (
    archive_agent_contact,
    create_agent_contact,
    decorate_contact,
    list_contact_cards,
    load_contact,
    match_contacts,
    normalize_preferences,
    save_contact_need,
)
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.contacts_repository import (
    get_contact,
    list_contacts,
    record_property_interaction,
)
from modules.jrh_ai_service import ask_jrh, confirm_jrh_action
from web_app import app


class ContactsV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="contacts-v2-test")
        create_tables()
        cls.org_a = add_organization("Contacts V2 Org A")
        cls.org_b = add_organization("Contacts V2 Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent_a = add_agent("V2 Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent("V2 Agent A2", "Alto", cls.org_a)
        cls.agent_b = add_agent("V2 Agent B", "Alto", cls.org_b)
        cls.user_a = add_user(
            "contacts_v2_agent",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.user_other = add_user(
            "contacts_v2_other",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_other,
        )
        cls.user_b = add_user(
            "contacts_v2_org_b",
            password_hash,
            ROLE_AGENT,
            cls.org_b,
            agent_id=cls.agent_b,
        )

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        self.client.get("/logout", follow_redirects=True)
        return self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def _user(self, user_id, agent_id, organization_id):
        return {
            "id": user_id,
            "agent_id": agent_id,
            "organization_id": organization_id,
            "role": ROLE_AGENT,
        }

    def _create(self, **extra):
        payload = {
            "name": extra.pop("name", "Martín López"),
            "phone": extra.pop("phone", "+54 9 11 1234-5678"),
            "email": extra.pop("email", "martin@example.com"),
            "status": "lead",
            "source": extra.pop("source", "manual"),
        }
        payload.update(extra)
        return create_agent_contact(self.org_a, self.agent_a, payload)

    def test_01_agent_creates_contact(self):
        contact = self._create()
        self.assertEqual(contact["name"], "Martín López")
        self.assertEqual(contact["agent_id"], self.agent_a)
        self.assertEqual(contact["organization_id"], self.org_a)

    def test_02_agent_sees_own_only(self):
        mine = self._create(name="Own Contact")
        other = create_agent_contact(
            self.org_a,
            self.agent_other,
            {"name": "Other Agent Contact"},
        )
        cards = list_contact_cards(self.org_a, agent_id=self.agent_a)
        names = [card["name"] for card in cards]
        self.assertIn(mine["name"], names)
        self.assertNotIn(other["name"], names)

    def test_03_other_org_blocked(self):
        contact = self._create(name="Org A Only")
        with self.assertRaises(Exception):
            load_contact(self.org_b, contact["id"], agent_id=self.agent_b)
        self.assertIsNone(get_contact(contact["id"], self.org_b))

    def test_04_phone_normalized(self):
        self.assertEqual(normalize_phone("+54 9 11 1234-5678"), "5491112345678")
        self.assertEqual(normalize_phone("5491112345678"), "5491112345678")
        self.assertEqual(normalize_phone("11 1234 5678"), "1112345678")
        contact = self._create(phone="+54 9 11 1234-5678")
        self.assertEqual(contact["phone"], "+54 9 11 1234-5678")
        self.assertEqual(contact["phone_normalized"], "5491112345678")

    def test_05_email_normalized(self):
        self.assertEqual(normalize_email("  Martin@Example.com "), "martin@example.com")
        contact = self._create(email="  Martin@Example.com ")
        self.assertEqual(contact["email"], "Martin@Example.com")
        self.assertEqual(contact["email_normalized"], "martin@example.com")

    def test_06_duplicate_phone_detected(self):
        existing = self._create(
            name="Phone Dup Martín",
            phone="+54 9 11 4444-5555",
            email="",
        )
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "Martin Lopez", "phone": "5491144445555", "email": ""}],
        )
        self.assertEqual(preview["items"][0]["status"], "exists")
        self.assertEqual(preview["items"][0]["match"]["id"], existing["id"])
        self.assertFalse(preview["persisted"])

    def test_07_duplicate_email_detected(self):
        existing = self._create(name="Mail Match", phone="", email="dup@example.com")
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "Other Name", "phone": "", "email": "DUP@example.com"}],
        )
        self.assertEqual(preview["items"][0]["status"], "exists")
        self.assertEqual(preview["items"][0]["match"]["id"], existing["id"])

    def test_08_fuzzy_name_does_not_auto_merge(self):
        self._create(name="Martín López", phone="", email="")
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "Martin Lopez", "phone": "", "email": ""}],
        )
        self.assertEqual(preview["items"][0]["status"], "possible_duplicate")
        self.assertNotEqual(preview["items"][0]["suggested_action"], "import")
        before = len(list_contacts(self.org_a, agent_id=self.agent_a))
        self.assertGreaterEqual(before, 1)

    def test_09_import_preview_does_not_save(self):
        before = len(list_contacts(self.org_a, agent_id=self.agent_a))
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "Preview Only", "phone": "1144444444", "email": "prev@example.com"}],
        )
        after = len(list_contacts(self.org_a, agent_id=self.agent_a))
        self.assertEqual(before, after)
        self.assertFalse(preview["persisted"])
        self.assertTrue(preview["import_token"])

    def test_10_confirm_import_saves(self):
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "Imported One", "phone": "1155555555", "email": "imp@example.com"}],
            source_type="phone_import",
        )
        result = confirm_import(self.org_a, self.agent_a, preview)
        self.assertEqual(result["created_count"], 1)
        created = result["created"][0]
        self.assertEqual(created["source_type"], "phone_import")
        self.assertEqual(created["name"], "Imported One")

    def test_11_double_confirm_does_not_duplicate(self):
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "Idempotent One", "phone": "1166666666", "email": "idemp@example.com"}],
        )
        first = confirm_import(self.org_a, self.agent_a, preview)
        second = confirm_import(self.org_a, self.agent_a, preview)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        matches = [
            item
            for item in list_contacts(self.org_a, agent_id=self.agent_a, search="Idempotent One")
            if item["name"] == "Idempotent One"
        ]
        self.assertEqual(len(matches), 1)

    def test_12_update_existing_keeps_filled_fields(self):
        existing = {
            "name": "Rocío Garay",
            "phone": "+54 9 11 1111-1111",
            "email": "ro@example.com",
            "notes": "keep me",
            "company": "Consultora",
        }
        incoming = {
            "name": "Rocio Garay",
            "phone": "",
            "email": "new@example.com",
            "notes": "",
            "company": "",
        }
        merged = merge_import_fields(existing, incoming)
        self.assertEqual(merged["phone"], "+54 9 11 1111-1111")
        self.assertEqual(merged["notes"], "keep me")
        self.assertEqual(merged["company"], "Consultora")
        self.assertEqual(merged["email"], "new@example.com")

    def test_13_import_source_saved(self):
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "VCard Source", "phone": "1177777777", "email": "vcf@example.com"}],
            source_type="vcard",
        )
        result = confirm_import(self.org_a, self.agent_a, preview)
        self.assertEqual(result["created"][0]["source_type"], "vcard")

    def test_14_activity_appears_in_history(self):
        contact = self._create(name="HistoryMartín", phone="1110000001", email="hist@example.com")
        user = self._user(self.user_a, self.agent_a, self.org_a)
        proposals = enrich_logged_contacts(
            self.org_a,
            self.agent_a,
            propose_logged_activity("Hoy llamé a HistoryMartín para hacer seguimiento."),
        )
        self.assertEqual(proposals[0]["contact_id"], contact["id"])
        created = confirm_logged_activity(
            self.org_a,
            user=user,
            proposals=proposals,
        )
        self.assertEqual(created[0]["contact_id"], contact["id"])
        card = decorate_contact(contact, organization_id=self.org_a)
        kinds = [event["kind"] for group in card["history"] for event in group["events"]]
        self.assertIn("call", kinds)

    def test_15_agenda_task_appears_in_history(self):
        contact = self._create(name="Agenda Link")
        create_task(
            self.org_a,
            self.agent_a,
            {
                "title": "Llamar",
                "task_type": "call",
                "due_date": "2026-09-09",
                "due_time": "10:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
            },
            created_by_user_id=self.user_a,
        )
        card = decorate_contact(
            get_contact(contact["id"], self.org_a),
            organization_id=self.org_a,
        )
        self.assertTrue(card["has_next_action"])
        self.assertGreaterEqual(card["linked_task_count"], 1)

    def test_16_need_contact_id_works(self):
        contact = self._create(name="Need Owner")
        updated = save_contact_need(
            self.org_a,
            contact["id"],
            {
                "areas": ["Núñez"],
                "rooms": 3,
                "budget": {"max": 250000, "currency": "USD"},
            },
            agent_id=self.agent_a,
        )
        prefs = normalize_preferences(updated["preferences_json"])
        self.assertEqual(prefs["rooms"], 3)
        self.assertEqual(prefs["client_name"], "Need Owner")
        self.assertEqual(updated["id"], contact["id"])

    def test_17_need_legacy_client_name_kept(self):
        contact = self._create(
            name="Legacy Client",
            preferences={"areas": ["Belgrano"], "client_name": "Laura Legacy"},
        )
        prefs = normalize_preferences(contact["preferences_json"])
        self.assertEqual(prefs["client_name"], "Laura Legacy")
        updated = save_contact_need(
            self.org_a,
            contact["id"],
            {"areas": ["Belgrano", "Núñez"], "client_name": "Laura Legacy"},
            agent_id=self.agent_a,
            client_name="Laura Legacy",
        )
        kept = normalize_preferences(updated["preferences_json"])
        self.assertEqual(kept["client_name"], "Laura Legacy")

    def test_18_jrh_fuzzy_contact(self):
        contact = self._create(name="Martín Rodríguez")
        user = self._user(self.user_a, self.agent_a, self.org_a)
        result = ask_jrh(
            "buscame a martin rodriges",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session={},
        )
        self.assertEqual(result["intent"], "QUERY_CONTACT")
        self.assertEqual(result["entity"].get("id"), contact["id"])

    def test_19_jrh_ambiguity_asks(self):
        self._create(name="Martín Uno", phone="1188880001", email="m1@example.com")
        self._create(name="Martín Dos", phone="1188880002", email="m2@example.com")
        user = self._user(self.user_a, self.agent_a, self.org_a)
        result = ask_jrh(
            "buscame a Martín",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session={},
        )
        self.assertEqual(result["status"], "needs_attention")
        self.assertGreaterEqual(len(result.get("candidates") or []), 2)

    def test_20_jrh_history_uses_activities(self):
        contact = self._create(name="RoHistory", phone="1110000002", email="rohist@example.com")
        user = self._user(self.user_a, self.agent_a, self.org_a)
        proposals = enrich_logged_contacts(
            self.org_a,
            self.agent_a,
            propose_logged_activity("Hoy llamé a RoHistory para hacer seguimiento."),
        )
        self.assertEqual(proposals[0].get("contact_id"), contact["id"])
        confirm_logged_activity(self.org_a, user=user, proposals=proposals)
        session = {}
        result = ask_jrh(
            "qué hice con RoHistory hoy?",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session=session,
        )
        self.assertEqual(result["intent"], "QUERY_CONTACT_HISTORY")
        self.assertEqual(result["entity"].get("id"), contact["id"])
        self.assertIn("Llamad", result["summary"])

    def test_21_other_org_never_in_jrh(self):
        create_agent_contact(
            self.org_b,
            self.agent_b,
            {"name": "Foreign Martín", "phone": "1199999999"},
        )
        user = self._user(self.user_a, self.agent_a, self.org_a)
        result = ask_jrh(
            "buscame a Foreign Martín",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session={},
        )
        ids = [item.get("id") for item in (result.get("candidates") or [])]
        if result.get("entity"):
            ids.append(result["entity"].get("id"))
        foreign = list_contacts(self.org_b, agent_id=self.agent_b, search="Foreign Martín")
        self.assertTrue(foreign)
        self.assertNotIn(foreign[0]["id"], ids)

    def test_22_archived_keeps_history(self):
        contact = self._create(name="Archive Me")
        create_task(
            self.org_a,
            self.agent_a,
            {
                "title": "Seguimiento",
                "task_type": "call",
                "due_date": "2026-09-08",
                "due_time": "10:00",
                "contact_id": contact["id"],
            },
            created_by_user_id=self.user_a,
        )
        archived = archive_agent_contact(
            self.org_a,
            contact["id"],
            agent_id=self.agent_a,
        )
        self.assertTrue(archived.get("archived_at"))
        listed = list_contacts(self.org_a, agent_id=self.agent_a)
        self.assertNotIn(contact["id"], [item["id"] for item in listed])
        stored = get_contact(contact["id"], self.org_a)
        card = decorate_contact(stored, organization_id=self.org_a)
        self.assertGreaterEqual(card["linked_task_count"], 1)

    def test_23_mobile_render_200(self):
        self._login("contacts_v2_agent")
        page = self.client.get(
            "/contacts",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
        )
        self.assertEqual(page.status_code, 200)

    def test_24_unsupported_contact_api_shows_fallback(self):
        self._login("contacts_v2_agent")
        page = self.client.get("/contacts/import")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("no permite seleccionar contactos", body.lower())

    def test_25_no_silent_contact_access(self):
        before = len(list_contacts(self.org_a, agent_id=self.agent_a))
        self._login("contacts_v2_agent")
        self.client.get("/contacts/import")
        after = len(list_contacts(self.org_a, agent_id=self.agent_a))
        self.assertEqual(before, after)

    def test_26_import_idempotent_http(self):
        preview = preview_import(
            self.org_a,
            self.agent_a,
            [{"name": "Token Twice", "phone": "1133333333", "email": "token@example.com"}],
        )
        first = confirm_import(
            self.org_a,
            self.agent_a,
            preview,
            import_token=preview["import_token"],
        )
        second = confirm_import(
            self.org_a,
            self.agent_a,
            preview,
            import_token=preview["import_token"],
        )
        self.assertEqual(first["created_count"], 1)
        self.assertTrue(second["idempotent"])

    def test_27_shared_properties_structured_only(self):
        contact = self._create(name="Shared Pablo")
        record_property_interaction(
            self.org_a,
            self.agent_a,
            contact_id=contact["id"],
            property_id=None,
            interaction_type="shared",
            label="Libertador 4200",
        )
        user = self._user(self.user_a, self.agent_a, self.org_a)
        result = ask_jrh(
            "qué propiedades le mandé a Pablo?",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session={},
        )
        self.assertEqual(result["intent"], "QUERY_CONTACT_PROPERTIES")
        self.assertIn("Libertador 4200", result["summary"])

    def test_28_jrh_schedule_preview_uses_contact(self):
        contact = self._create(name="Call Martín")
        user = self._user(self.user_a, self.agent_a, self.org_a)
        session = {}
        first = ask_jrh(
            "buscame a Call Martín",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session=session,
        )
        self.assertEqual(first["entity"].get("id"), contact["id"])
        second = ask_jrh(
            "agendame llamarlo mañana a las 10",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session=session,
        )
        self.assertEqual(second["intent"], "CREATE_TASK")
        self.assertTrue(second.get("confirm_required"))
        self.assertEqual(str(second["data"]["draft"].get("contact_id")), str(contact["id"]))

    def test_29_need_create_from_jrh(self):
        contact = self._create(name="Laura Need")
        user = self._user(self.user_a, self.agent_a, self.org_a)
        session = {}
        result = ask_jrh(
            "creame una búsqueda para Laura: 3 ambientes en Núñez hasta USD 250.000",
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session=session,
        )
        self.assertEqual(result["intent"], "START_CONTACT_NEED")
        confirmed = confirm_jrh_action(
            organization_id=self.org_a,
            user=user,
            agent_id=self.agent_a,
            session=session,
            language="es",
        )
        self.assertTrue(confirmed.get("wrote"))
        stored = get_contact(contact["id"], self.org_a)
        prefs = normalize_preferences(stored["preferences_json"])
        self.assertEqual(prefs.get("rooms"), 3)
        self.assertIn("Núñez", prefs.get("areas") or [])


if __name__ == "__main__":
    unittest.main()
