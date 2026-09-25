"""Contact need reads and CRM preview/reschedule on the canonical JRH stack."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_jrh_contact_crm.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_tasks import create_task
from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact, decorate_contact
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.agent_tasks_repository import (
    get_agent_task,
    list_agent_tasks,
    set_google_event_id,
)
from modules.database.contacts_repository import get_contact, update_contact
from modules.jrh_ai_intents import LOG_CONTACT_FOLLOW_UP, QUERY_CONTACT_NEED, RESCHEDULE_TASK
from modules.jrh_ai_provider import MockAIIntentProvider
from modules.jrh_ai_service import SESSION_DRAFT_KEY, ask_jrh, confirm_jrh_action
from modules.organization_time import UTC, organization_timezone
from web_app import app


NEED = {
    "purpose": "sale",
    "property_types": ["departamento"],
    "areas": ["Martínez", "Olivos"],
    "budget": {"min": 150000, "max": 250000, "currency": "USD"},
    "rooms": 3,
    "bedrooms": 2,
    "bathrooms": 2,
    "features": ["cochera"],
    "observations": "Quiere luz y patio.",
}


class JrhContactCrmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="jrh-crm-test")
        create_tables()
        cls.org = add_organization("CRM Org")
        cls.other_org = add_organization("Other CRM Org")
        cls.password = "Password1"
        cls.agent = add_agent("Owner Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Other Agent", "Alto", cls.org)
        cls.foreign_agent = add_agent("Foreign Agent", "Alto", cls.other_org)
        cls.user = add_user(
            "crm_agent",
            hash_password(cls.password),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.tz = organization_timezone(cls.org)
        cls.viewer = {
            "id": cls.user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.agent,
        }

    def _now(self):
        return datetime(2026, 9, 24, 15, 0, tzinfo=self.tz).astimezone(UTC)

    def _contact(self, name, *, agent=None, org=None, need=None):
        contact = create_agent_contact(
            org or self.org,
            agent or self.agent,
            {"name": name, "phone": "1155550000", "status": "lead", "source": "manual"},
        )
        if need is not None:
            contact = update_contact(
                contact["id"],
                org or self.org,
                preferences_json=json.dumps(need, ensure_ascii=False),
            )
        return contact

    def _ask(self, prompt, *, session=None):
        return ask_jrh(
            prompt,
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            language="es",
            session=session if session is not None else {},
            provider=MockAIIntentProvider(),
            now=self._now(),
        )

    def _confirm(self, session):
        return confirm_jrh_action(
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            session=session,
            language="es",
        )

    def test_query_need_exact(self):
        self._contact("Sofía Ruiz", need=NEED)
        result = self._ask("En qué zonas buscaba Sofía Ruiz?")
        self.assertEqual(result["intent"], QUERY_CONTACT_NEED)
        text = result["message"]
        self.assertIn("Martínez", text)
        self.assertIn("Olivos", text)
        self.assertIn("Venta", text)
        self.assertIn("departamento", text)
        self.assertIn("USD", text)
        self.assertIn("3", text)
        self.assertIn("cochera", text)
        self.assertIn("Quiere luz y patio.", text)
        self.assertIn("2", text)

    def test_query_need_fuzzy_and_budget_question(self):
        self._contact("Camila Torres", need=NEED)
        result = self._ask("Cuál era el presupuesto de Camila Torrs?")
        self.assertEqual(result["intent"], QUERY_CONTACT_NEED)
        self.assertIn("USD", result["message"])
        self.assertNotIn("todavía no tiene", result["message"])

    def test_query_need_homonyms(self):
        self._contact("Bruno Díaz", need=NEED)
        self._contact("Bruno Paz", need=NEED)
        result = self._ask("Qué estaba buscando Bruno?")
        self.assertEqual(result["status"], "needs_attention")
        names = {item["name"] for item in result["candidates"]}
        self.assertEqual(names, {"Bruno Díaz", "Bruno Paz"})

    def test_query_need_missing(self):
        self._contact("Laura Gómez")
        result = self._ask("Qué buscaba Laura Gómez?")
        self.assertEqual(result["intent"], QUERY_CONTACT_NEED)
        self.assertIn("todavía no tiene", result["message"])
        self.assertNotIn("USD", result["message"])

    def test_query_need_other_org_and_other_agent(self):
        self._contact(
            "Ana Foreign",
            agent=self.foreign_agent,
            org=self.other_org,
            need=NEED,
        )
        self._contact("Ana Colega", agent=self.other_agent, need=NEED)
        foreign = self._ask("Qué buscaba Ana Foreign?")
        self.assertEqual(foreign["status"], "needs_attention")
        self.assertFalse(foreign["candidates"])
        colleague = self._ask("Qué buscaba Ana Colega?")
        self.assertEqual(colleague["status"], "needs_attention")
        self.assertFalse(colleague["candidates"])

    def test_follow_up_preview_cancel_and_confirm(self):
        contact = self._contact("Juan Pérez")
        session = {}
        preview = self._ask(
            "Hablé con Juan Pérez, por ahora no vende. Llamarlo en dos meses.",
            session=session,
        )
        self.assertEqual(preview["intent"], LOG_CONTACT_FOLLOW_UP)
        self.assertTrue(preview["confirm_required"])
        self.assertFalse(preview["wrote"])
        blob = " ".join(card["title"] for card in preview["cards"])
        self.assertIn("Por ahora no vende", blob)
        self.assertIn("Largo plazo", blob)
        self.assertIn("24/11/2026", blob)
        unchanged = get_contact(contact["id"], self.org)
        self.assertFalse(unchanged.get("notes"))
        session.pop(SESSION_DRAFT_KEY, None)
        cancelled = self._confirm(session)
        self.assertFalse(cancelled["wrote"])
        self.assertFalse(get_contact(contact["id"], self.org).get("notes"))

        session = {}
        self._ask(
            "Hablé con Juan Pérez, por ahora no vende. Llamarlo en dos meses.",
            session=session,
        )
        confirmed = self._confirm(session)
        self.assertTrue(confirmed["wrote"])
        stored = get_contact(contact["id"], self.org)
        self.assertEqual(stored["follow_up_priority"], "long_term")
        self.assertIn("2026-11-24", stored["next_follow_up_at"])
        self.assertIn("Hablé con Juan", stored["notes"])
        history = decorate_contact(
            stored,
            organization_id=self.org,
            language="es",
        )["history"]
        kinds = [
            event["kind"]
            for group in history
            for event in group["events"]
        ]
        self.assertIn("follow_up", kinds)

    def test_reschedule_preview_confirm_keeps_google_event(self):
        contact = self._contact("Martín Pérez")
        task = create_task(
            self.org,
            self.agent,
            {
                "title": "Visita con Martín",
                "task_type": "visit",
                "due_date": "2026-09-25",
                "due_time": "10:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
            },
            created_by_user_id=self.user,
        )
        set_google_event_id(task["id"], self.org, "gcal-martin-1")
        before = list_agent_tasks(self.org, agent_id=self.agent, limit=20)
        session = {}
        preview = self._ask(
            "Pasame la visita con Martín Pérez al viernes a las 17",
            session=session,
        )
        self.assertEqual(preview["intent"], RESCHEDULE_TASK)
        self.assertTrue(preview["confirm_required"])
        self.assertFalse(preview["wrote"])
        self.assertEqual(get_agent_task(task["id"], self.org)["due_at"], task["due_at"])
        with patch("modules.google_calendar.sync_task_event") as sync:
            confirmed = self._confirm(session)
        self.assertTrue(confirmed["wrote"])
        self.assertEqual(confirmed["data"]["task_id"], task["id"])
        self.assertEqual(confirmed["data"]["google_event_id"], "gcal-martin-1")
        moved = get_agent_task(task["id"], self.org)
        self.assertNotEqual(moved["due_at"], task["due_at"])
        self.assertEqual(moved["google_event_id"], "gcal-martin-1")
        after = list_agent_tasks(self.org, agent_id=self.agent, limit=20)
        self.assertEqual(len(after), len(before))
        self.assertEqual(sync.call_args[0][0], "task_rescheduled")
        self.assertEqual(sync.call_args[0][1]["id"], task["id"])
        self.assertEqual(sync.call_args[0][1]["google_event_id"], "gcal-martin-1")
        stored = get_contact(contact["id"], self.org)
        kinds = [
            event["kind"]
            for group in decorate_contact(
                stored, organization_id=self.org, language="es"
            )["history"]
            for event in group["events"]
        ]
        self.assertIn("visit_rescheduled", kinds)

    def test_reschedule_ambiguous_does_not_write(self):
        contact = self._contact("Lucía Gómez")
        for hour in ("10:00", "12:00"):
            create_task(
                self.org,
                self.agent,
                {
                    "title": f"Llamada {hour}",
                    "task_type": "call",
                    "due_date": "2026-09-25",
                    "due_time": hour,
                    "contact_id": contact["id"],
                    "contact_name": contact["name"],
                },
                created_by_user_id=self.user,
            )
        result = self._ask("Mové la llamada con Lucía Gómez para mañana a las 10")
        self.assertEqual(result["status"], "needs_attention")
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertFalse(result["wrote"])
