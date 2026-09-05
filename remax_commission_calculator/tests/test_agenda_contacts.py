"""Phase 3 Agenda ↔ Contacts."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agenda_contacts.db"
)

from modules.agenda_ai import compose_from_prompt, interpret_agenda_input
from modules.agent_tasks import AgentTaskError, complete_task, create_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.agent_tasks_repository import get_agent_task
from modules.database.contacts_repository import get_contact, list_contacts
from modules.organization_time import now_utc, organization_timezone
from web_app import app


class AgendaContactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="agenda-contacts-test")
        create_tables()

        cls.org_a = add_organization("Link Org A")
        cls.org_b = add_organization("Link Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent_a = add_agent("Link Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent("Link Agent A2", "Alto", cls.org_a)
        cls.agent_b = add_agent("Link Agent B", "Alto", cls.org_b)
        cls.admin_a = add_user(
            "link_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "link_agent_user",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.other_agent_user = add_user(
            "link_agent_other",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_other,
        )
        cls.agent_user_b = add_user(
            "link_agent_b",
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

    def _contact(self, name, **extra):
        payload = {"name": name, "status": "lead", "source": "manual"}
        payload.update(extra)
        return create_agent_contact(self.org_a, self.agent_a, payload)

    def _due_payload(self, **extra):
        tz = organization_timezone(self.org_a)
        due = (now_utc() + timedelta(hours=8)).astimezone(tz)
        payload = {
            "title": "Visita",
            "task_type": "visit",
            "due_date": due.date().isoformat(),
            "due_time": due.strftime("%H:%M"),
        }
        payload.update(extra)
        return payload

    def test_01_composer_zero_contacts_stays_unlinked(self):
        draft = compose_from_prompt(
            "Agendame una visita mañana a las 16 con Odette",
            self.org_a,
            self.agent_a,
        )
        self.assertEqual(draft["contact_match"], "none")
        self.assertFalse(draft.get("contact_id"))
        self.assertEqual(draft["item_status"], "ready")
        self.assertEqual(draft["contact_name"], "Odette")

        self._login("link_agent_user")
        page = self.client.post(
            "/agenda/compose",
            data={"prompt": "Agendame una visita mañana a las 16 con Odette"},
        )
        body = page.get_data(as_text=True)
        self.assertIn("No encontré un contacto llamado Odette", body)
        self.assertIn("Continuar sin vincular", body)
        self.assertIn("Crear contacto", body)

    def test_02_composer_clear_single_match_auto_links(self):
        contact = self._contact(
            "Valentina Ibarra",
            preferences={
                "areas": ["Núñez"],
                "rooms": 3,
                "budget": {"max": 180000, "currency": "USD"},
            },
        )
        draft = compose_from_prompt(
            "Agendame una visita mañana a las 16 con Valentina Ibarra",
            self.org_a,
            self.agent_a,
        )
        self.assertEqual(draft["contact_match"], "single")
        self.assertEqual(draft["contact_id"], contact["id"])
        self.assertEqual(draft["item_status"], "ready")

        self._login("link_agent_user")
        page = self.client.post(
            "/agenda/compose",
            data={
                "prompt": "Agendame una visita mañana a las 16 con Valentina Ibarra"
            },
        )
        body = page.get_data(as_text=True)
        self.assertIn("Valentina Ibarra", body)
        self.assertIn("3 ambientes", body)

    def test_03_composer_ambiguous_first_name_does_not_auto_match(self):
        self._contact("Carolina López")
        self._contact("Carolina Gómez")
        draft = compose_from_prompt(
            "Agendame una visita mañana a las 16 con Carolina",
            self.org_a,
            self.agent_a,
        )
        self.assertEqual(draft["contact_match"], "ambiguous")
        self.assertFalse(draft.get("contact_id"))
        self.assertEqual(draft["item_status"], "needs_attention")
        self.assertEqual(len(draft["contact_candidates"]), 2)

        self._login("link_agent_user")
        page = self.client.post(
            "/agenda/compose",
            data={"prompt": "Agendame una visita mañana a las 16 con Carolina"},
        )
        body = page.get_data(as_text=True)
        self.assertIn("Encontré 2 contactos", body)
        self.assertIn("Carolina López", body)
        self.assertIn("Carolina Gómez", body)

    def test_04_create_task_stores_contact_id_and_name(self):
        contact = self._contact("Pablo Ruiz")
        task = create_task(
            self.org_a,
            self.agent_a,
            self._due_payload(
                title="Llamar a Pablo",
                task_type="call",
                contact_name="Pablo",
                contact_id=contact["id"],
            ),
            created_by_user_id=self.agent_user,
        )
        stored = get_agent_task(task["id"], self.org_a)
        self.assertEqual(stored["contact_id"], contact["id"])
        self.assertEqual(stored["contact_name"], "Pablo Ruiz")

    def test_05_task_without_link_keeps_typed_name(self):
        task = create_task(
            self.org_a,
            self.agent_a,
            self._due_payload(
                title="Llamar a Lucía",
                task_type="call",
                contact_name="Lucía",
            ),
            created_by_user_id=self.agent_user,
        )
        stored = get_agent_task(task["id"], self.org_a)
        self.assertIsNone(stored["contact_id"])
        self.assertEqual(stored["contact_name"], "Lucía")

    def test_06_multi_event_resolves_contacts_per_item(self):
        carolina = self._contact("Carolina Núñez")
        self._contact("Pablo Ruiz")
        self._contact("Pablo Gómez")
        bundle = interpret_agenda_input(
            "Mañana a las 10 visita con Carolina Núñez, "
            "a las 12 llamada a Pablo "
            "y el viernes seguimiento con Lucía",
            self.org_a,
            self.agent_a,
        )
        items = bundle["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["contact_id"], carolina["id"])
        self.assertEqual(items[0]["item_status"], "ready")
        self.assertEqual(items[1]["contact_match"], "ambiguous")
        self.assertEqual(items[1]["item_status"], "needs_attention")
        self.assertEqual(items[2]["contact_match"], "none")
        self.assertEqual(items[2]["item_status"], "ready")

    def test_07_quick_create_from_agenda_links_draft(self):
        self._login("link_agent_user")
        preview = self.client.post(
            "/agenda/compose",
            data={"prompt": "Llamar mañana a las 11 a Ximena Ríos"},
        )
        self.assertEqual(preview.status_code, 200)
        created = self.client.post(
            "/agenda/compose",
            data={
                "prompt": "Llamar mañana a las 11 a Ximena Ríos",
                "item_count": "1",
                "create_contact_item": "0",
                "items-0-title": "Llamada",
                "items-0-task_type": "call",
                "items-0-due_date": "2030-01-10",
                "items-0-due_time": "11:00",
                "items-0-contact_name": "Ximena Ríos",
                "items-0-contact_match": "none",
                "items-0-date_found": "1",
                "items-0-time_found": "1",
                "items-0-item_status": "ready",
            },
        )
        self.assertIn("Nuevo contacto", created.get_data(as_text=True))

        linked = self.client.post(
            "/agenda/compose",
            data={
                "prompt": "Llamar mañana a las 11 a Ximena Ríos",
                "item_count": "1",
                "quick_create_contact": "0",
                "quick_name": "Ximena Ríos",
                "quick_phone": "1144001100",
                "items-0-title": "Llamada",
                "items-0-task_type": "call",
                "items-0-due_date": "2030-01-10",
                "items-0-due_time": "11:00",
                "items-0-contact_name": "Ximena Ríos",
                "items-0-contact_match": "none",
                "items-0-date_found": "1",
                "items-0-time_found": "1",
                "items-0-item_status": "ready",
            },
        )
        body = linked.get_data(as_text=True)
        self.assertIn("Ximena Ríos", body)
        created = [
            item
            for item in list_contacts(self.org_a, agent_id=self.agent_a)
            if item["name"] == "Ximena Ríos"
        ]
        self.assertTrue(created)
        self.assertEqual(created[-1]["source"], "agenda")
        self.assertIn(str(created[-1]["id"]), body)

    def test_08_compose_contact_id_is_scoped(self):
        contact = self._contact("Marina Soto")
        self._login("link_agent_user")
        own = self.client.get(f"/agenda/compose?contact_id={contact['id']}")
        self.assertEqual(own.status_code, 200)
        self.assertIn("Agendando para", own.get_data(as_text=True))
        self.assertIn("Marina Soto", own.get_data(as_text=True))

        self._login("link_agent_other")
        other = self.client.get(f"/agenda/compose?contact_id={contact['id']}")
        self.assertEqual(other.status_code, 404)

        self._login("link_agent_b")
        foreign = self.client.get(f"/agenda/compose?contact_id={contact['id']}")
        self.assertEqual(foreign.status_code, 404)

    def test_09_other_agent_cannot_link_private_contact(self):
        contact = self._contact("Nora Vega")
        with self.assertRaises(AgentTaskError):
            create_task(
                self.org_b,
                self.agent_b,
                self._due_payload(
                    title="Llamar",
                    task_type="call",
                    contact_id=contact["id"],
                    contact_name="Nora",
                ),
                created_by_user_id=self.agent_user_b,
            )
        with self.assertRaises(AgentTaskError):
            create_task(
                self.org_a,
                self.agent_other,
                self._due_payload(
                    title="Llamar",
                    task_type="call",
                    contact_id=contact["id"],
                    contact_name="Nora",
                ),
                created_by_user_id=self.other_agent_user,
            )

    def test_10_outcome_merge_is_optional(self):
        contact = self._contact(
            "Carolina López",
            preferences={
                "areas": ["Núñez"],
                "budget": {"max": 180000, "currency": "USD"},
            },
        )
        task = create_task(
            self.org_a,
            self.agent_a,
            self._due_payload(
                title="Visita Carolina",
                contact_name="Carolina López",
                contact_id=contact["id"],
            ),
            created_by_user_id=self.agent_user,
        )
        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        self._login("link_agent_user")
        saved = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "save",
                "outcome_json": json.dumps(
                    {
                        "interest": "positive",
                        "areas": ["Belgrano"],
                        "budget": {"max": 190000, "currency": "USD"},
                        "preferences": ["balcón"],
                    }
                ),
            },
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 302)
        self.assertIn("step=contact_prefs", saved.headers.get("Location", ""))

        preview = self.client.get(
            f"/agenda/{task['id']}/follow-up?step=contact_prefs"
        )
        body = preview.get_data(as_text=True)
        self.assertIn("Actualizar búsqueda de Carolina López", body)
        self.assertIn("Belgrano", body)
        self.assertIn("balcón", body)
        self.assertIn("¿Actualizar presupuesto máximo?", body)

        skipped = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={"intent": "skip_contact_prefs"},
            follow_redirects=False,
        )
        self.assertEqual(skipped.status_code, 302)
        self.assertIn("step=next", skipped.headers.get("Location", ""))
        stored_task = get_agent_task(task["id"], self.org_a)
        self.assertTrue(stored_task.get("outcome_json"))
        stored_contact = get_contact(contact["id"], self.org_a)
        prefs = json.loads(stored_contact["preferences_json"])
        self.assertEqual(prefs["areas"], ["Núñez"])
        self.assertEqual(prefs["budget"]["max"], 180000)

    def test_11_apply_merge_unions_lists_and_confirmed_budget(self):
        contact = self._contact(
            "Martín Pérez",
            preferences={
                "areas": ["Núñez"],
                "budget": {"max": 180000, "currency": "USD"},
            },
        )
        task = create_task(
            self.org_a,
            self.agent_a,
            self._due_payload(
                title="Visita Martín",
                contact_id=contact["id"],
                contact_name="Martín Pérez",
            ),
            created_by_user_id=self.agent_user,
        )
        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        self._login("link_agent_user")
        self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "save",
                "outcome_json": json.dumps(
                    {
                        "areas": ["Belgrano"],
                        "budget": {"max": 190000, "currency": "USD"},
                        "preferences": ["balcón"],
                    }
                ),
            },
        )
        applied = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "apply_contact_prefs",
                "outcome_json": json.dumps(
                    {
                        "areas": ["Belgrano"],
                        "budget": {"max": 190000, "currency": "USD"},
                        "preferences": ["balcón"],
                    }
                ),
                "accept_conflict": ["budget_max"],
            },
            follow_redirects=False,
        )
        self.assertEqual(applied.status_code, 302)
        stored = json.loads(
            get_contact(contact["id"], self.org_a)["preferences_json"]
        )
        self.assertEqual(stored["areas"], ["Núñez", "Belgrano"])
        self.assertIn("balcón", stored["features"])
        self.assertEqual(stored["budget"]["max"], 190000)

    def test_12_compose_context_binds_known_contact(self):
        contact = self._contact("Lucía Fernández")
        self._login("link_agent_user")
        page = self.client.post(
            "/agenda/compose",
            data={
                "prompt": "Visita mañana a las 16",
                "compose_contact_id": str(contact["id"]),
            },
        )
        body = page.get_data(as_text=True)
        self.assertIn("Lucía Fernández", body)
        self.assertIn("Agendando para", body)


if __name__ == "__main__":
    unittest.main()
