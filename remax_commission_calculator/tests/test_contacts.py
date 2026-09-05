"""Light CRM contacts for JRH One agents."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_contacts.db")

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import (
    apply_preference_update,
    build_contact_summary,
    create_agent_contact,
    decorate_contact,
    diff_preference_update,
    link_task_to_contact,
    list_contact_cards,
    match_contacts,
    merge_contact_preferences,
    normalize_preferences,
    preferences_from_outcome,
)
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.contacts_repository import get_contact
from modules.agent_tasks import create_task
from modules.organization_time import now_utc, organization_timezone
from web_app import app


class ContactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="contacts-test")
        create_tables()

        cls.org_a = add_organization("Contacts Org A")
        cls.org_b = add_organization("Contacts Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent_a = add_agent("Contact Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent("Contact Agent A2", "Alto", cls.org_a)
        cls.agent_b = add_agent("Contact Agent B", "Alto", cls.org_b)
        cls.admin_a = add_user(
            "contacts_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "contacts_agent_user",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.other_agent_user = add_user(
            "contacts_agent_other",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_other,
        )
        cls.admin_b = add_user(
            "contacts_admin_b",
            password_hash,
            ROLE_ADMIN,
            cls.org_b,
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

    def _create(self, **extra):
        payload = {
            "name": "Carolina López",
            "status": "lead",
            "source": "manual",
        }
        payload.update(extra)
        return create_agent_contact(self.org_a, self.agent_a, payload)

    def test_01_create_contact_with_nullable_fields(self):
        contact = self._create()
        self.assertEqual(contact["name"], "Carolina López")
        self.assertEqual(contact["status"], "lead")
        self.assertEqual(contact["phone"], "")
        self.assertEqual(contact["email"], "")
        self.assertEqual(contact["visibility"], "private")
        self.assertIsNotNone(contact["created_at"])

    def test_02_edit_status_and_preferences(self):
        self._login("contacts_agent_user")
        created = self.client.post(
            "/contacts/new",
            data={"name": "Martín Pérez", "status": "lead"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 302)
        contact_id = int(created.headers["Location"].rstrip("/").split("/")[-1])

        updated = self.client.post(
            f"/contacts/{contact_id}/edit",
            data={
                "name": "Martín Pérez",
                "status": "active",
                "phone": "1144556677",
                "email": "martin@example.com",
                "areas": "Núñez, Belgrano",
                "budget_min": "150000",
                "budget_max": "180000",
                "budget_currency": "USD",
                "rooms": "3",
                "bedrooms": "2",
                "features": "balcón",
                "property_types": "departamento",
            },
        )
        self.assertEqual(updated.status_code, 302)
        stored = get_contact(contact_id, self.org_a)
        self.assertEqual(stored["status"], "active")
        self.assertEqual(stored["phone"], "1144556677")
        self.assertEqual(stored["email"], "martin@example.com")
        prefs = normalize_preferences(stored["preferences_json"])
        self.assertEqual(prefs["areas"], ["Núñez", "Belgrano"])
        self.assertEqual(prefs["budget"]["max"], 180000)
        self.assertEqual(prefs["rooms"], 3)
        self.assertIn("balcón", prefs["features"])

    def test_03_agent_only_sees_own_contacts(self):
        self._create(name="Carolina López")
        other = create_agent_contact(
            self.org_a,
            self.agent_other,
            {"name": "Lucía Ajena"},
        )
        self._login("contacts_agent_user")
        page = self.client.get("/contacts")
        body = page.get_data(as_text=True)
        self.assertIn("Carolina López", body)
        self.assertNotIn("Lucía Ajena", body)

        blocked = self.client.get(f"/contacts/{other['id']}")
        self.assertEqual(blocked.status_code, 404)

    def test_04_other_organization_cannot_read(self):
        contact = self._create(name="Privada Org A")
        self._login("contacts_admin_b")
        response = self.client.get(f"/contacts/{contact['id']}")
        self.assertEqual(response.status_code, 404)

    def test_05_staff_reads_org_and_cannot_create(self):
        self._create(name="Visible para staff")
        self._login("contacts_admin_a")
        page = self.client.get("/contacts")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Visible para staff", page.get_data(as_text=True))
        created = self.client.post(
            "/contacts/new",
            data={"name": "Staff no crea"},
        )
        self.assertIn(created.status_code, (302, 403))
        self.assertIsNone(
            next(
                (
                    card
                    for card in list_contact_cards(self.org_a)
                    if card["name"] == "Staff no crea"
                ),
                None,
            )
        )

    def test_06_search_and_status_filters(self):
        self._create(name="Carolina López", status="lead")
        self._create(name="Martín Activo", status="active")
        self._login("contacts_agent_user")
        leads = self.client.get("/contacts?filter=leads")
        self.assertIn("Carolina López", leads.get_data(as_text=True))
        self.assertNotIn("Martín Activo", leads.get_data(as_text=True))
        search = self.client.get("/contacts?q=Martín")
        self.assertIn("Martín Activo", search.get_data(as_text=True))
        self.assertNotIn("Carolina López", search.get_data(as_text=True))

    def test_07_factual_summary_does_not_invent(self):
        contact = self._create(
            name="Carolina López",
            preferences={
                "areas": ["Núñez", "Belgrano"],
                "budget": {"max": 180000, "currency": "USD"},
                "rooms": 3,
                "features": ["balcón"],
            },
        )
        summary = build_contact_summary(contact, language="es")
        self.assertIn("Carolina", summary)
        self.assertIn("3 ambientes", summary)
        self.assertIn("Núñez", summary)
        self.assertIn("Belgrano", summary)
        self.assertIn("180.000", summary)
        self.assertIn("balcón", summary)
        self.assertNotIn("muy interesada", summary)

        empty = self._create(name="Sin datos")
        self.assertEqual(build_contact_summary(empty, language="es"), "")

    def test_08_next_action_is_derived_only_when_linked(self):
        contact = self._create(name="Carolina López")
        card = decorate_contact(contact, organization_id=self.org_a)
        self.assertFalse(card["has_next_action"])

        tz = organization_timezone(self.org_a)
        due = (now_utc() + timedelta(hours=20)).astimezone(tz)
        task = create_task(
            self.org_a,
            self.agent_a,
            {
                "title": "Llamar a Carolina",
                "task_type": "call",
                "due_date": due.date().isoformat(),
                "due_time": due.strftime("%H:%M"),
                "contact_name": "Carolina",
            },
            created_by_user_id=self.agent_user,
        )
        still = decorate_contact(contact, organization_id=self.org_a)
        self.assertFalse(still["has_next_action"])

        link_task_to_contact(
            self.org_a,
            task["id"],
            contact["id"],
            agent_id=self.agent_a,
        )
        linked = decorate_contact(
            get_contact(contact["id"], self.org_a),
            organization_id=self.org_a,
        )
        self.assertTrue(linked["has_next_action"])
        self.assertEqual(linked["next_task"]["id"], task["id"])

    def test_09_merge_preferences_is_not_destructive(self):
        merged = merge_contact_preferences(
            {
                "areas": ["Núñez"],
                "budget": {"max": 180000, "currency": "USD"},
            },
            {
                "areas": ["Belgrano"],
                "features": ["balcón"],
                "budget": {"max": 200000, "currency": "USD"},
            },
        )
        self.assertEqual(merged["areas"], ["Núñez", "Belgrano"])
        self.assertEqual(merged["budget"]["max"], 180000)
        self.assertIn("balcón", merged["features"])

    def test_10_migration_is_idempotent(self):
        from modules.database.schema import create_tables as migrate_again
        from modules.database.connection import get_connection

        migrate_again(create_backup=False)
        migrate_again(create_backup=False)
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'contacts'
            """
        )
        self.assertIsNotNone(cursor.fetchone())
        cursor.execute("PRAGMA table_info(contacts)")
        columns = {row[1] for row in cursor.fetchall()}
        cursor.execute("PRAGMA table_info(agent_tasks)")
        task_columns = {row[1] for row in cursor.fetchall()}
        connection.close()
        self.assertIn("visibility", columns)
        self.assertIn("preferences_json", columns)
        self.assertIn("contact_id", task_columns)

    def test_11_nav_and_templates_render(self):
        self._create(name="Carolina López")
        self._login("contacts_agent_user")
        home = self.client.get("/contacts")
        body = home.get_data(as_text=True)
        self.assertEqual(home.status_code, 200)
        self.assertIn("Contactos", body)
        self.assertIn("Buscar contacto", body)
        self.assertIn("Sin próxima acción", body)
        form = self.client.get("/contacts/new")
        self.assertEqual(form.status_code, 200)
        self.assertIn("Nombre", form.get_data(as_text=True))

    def test_12_match_zero_one_many_is_conservative(self):
        self._create(name="Berenice Soler")
        self._create(name="Berenice Vidal")
        none = match_contacts(self.org_a, self.agent_a, "Odette")
        self.assertEqual(none["status"], "none")
        self.assertFalse(none["clear"])

        many = match_contacts(self.org_a, self.agent_a, "Berenice")
        self.assertEqual(many["status"], "ambiguous")
        self.assertEqual(len(many["candidates"]), 2)
        self.assertIsNone(many["contact"])

        one = match_contacts(self.org_a, self.agent_a, "Berenice Soler")
        self.assertEqual(one["status"], "single")
        self.assertTrue(one["clear"])
        self.assertEqual(one["contact"]["name"], "Berenice Soler")

    def test_13_first_name_only_is_not_a_clear_match(self):
        self._create(name="Lucía Fernández")
        matched = match_contacts(self.org_a, self.agent_a, "Lucía")
        self.assertEqual(matched["status"], "single")
        self.assertFalse(matched["clear"])

    def test_14_merge_conflict_requires_confirmation(self):
        existing = {
            "areas": ["Núñez"],
            "budget": {"max": 180000, "currency": "USD"},
        }
        incoming = preferences_from_outcome(
            {
                "areas": ["Belgrano"],
                "budget": {"max": 190000, "currency": "USD"},
                "preferences": ["balcón"],
            }
        )
        preview = diff_preference_update(existing, incoming)
        self.assertTrue(preview["has_changes"])
        self.assertEqual(
            [item["value"] for item in preview["additions"]],
            ["Belgrano", "balcón"],
        )
        self.assertTrue(
            any(item["key"] == "budget_max" for item in preview["conflicts"])
        )

        silent = apply_preference_update(existing, incoming)
        self.assertEqual(silent["budget"]["max"], 180000)
        self.assertEqual(silent["areas"], ["Núñez", "Belgrano"])
        self.assertIn("balcón", silent["features"])

        accepted = apply_preference_update(
            existing,
            incoming,
            accepted_conflicts=["budget_max"],
        )
        self.assertEqual(accepted["budget"]["max"], 190000)

    def test_15_history_and_views_use_contact_id_only(self):
        from modules.database import add_property
        from modules.agent_tasks import complete_task

        contact = self._create(name="Carolina López")
        property_id = add_property(
            "Libertador 3200",
            "CABA",
            self.org_a,
            agent_id=self.agent_a,
            status="approved",
        )
        tz = organization_timezone(self.org_a)
        due = (now_utc() + timedelta(hours=2)).astimezone(tz)
        payload = {
            "title": "Visita Libertador",
            "task_type": "visit",
            "due_date": due.date().isoformat(),
            "due_time": due.strftime("%H:%M"),
            "contact_name": "Carolina",
            "property_id": property_id,
        }
        unlinked = create_task(
            self.org_a,
            self.agent_a,
            payload,
            created_by_user_id=self.agent_user,
        )
        linked = create_task(
            self.org_a,
            self.agent_a,
            {**payload, "contact_id": contact["id"], "title": "Visita vinculada"},
            created_by_user_id=self.agent_user,
        )
        complete_task(
            self.org_a,
            linked["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        card = decorate_contact(
            get_contact(contact["id"], self.org_a),
            organization_id=self.org_a,
        )
        details = " ".join(
            event.get("detail") or ""
            for group in card["history"]
            for event in group["events"]
        )
        self.assertIn("Libertador 3200", details)
        self.assertEqual(card["viewed_count"], 1)
        self.assertEqual(card["viewed_properties"][0]["id"], property_id)
        self.assertTrue(card["last_interaction_label"])
        self.assertIsNotNone(card["recommendation"])
        self.assertNotEqual(unlinked.get("contact_id"), contact["id"])


if __name__ == "__main__":
    unittest.main()
