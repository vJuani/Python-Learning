"""Agenda V2: today/upcoming/calendar/tasks chrome without changing business rules."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_agenda_v2.db")
os.environ.pop("DATABASE_URL", None)

from modules.agent_tasks import create_task, list_tasks_for_property
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.agent_tasks_repository import get_agent_task, list_agent_tasks
from modules.organization_time import now_utc, organization_timezone
from modules.visit_reminders import visit_reminder_event_key
from web_app import app


class AgendaV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="agenda-v2-tests")
        create_tables()

        cls.org_a = add_organization("Agenda V2 Org A")
        cls.org_b = add_organization("Agenda V2 Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)

        cls.agent_a = add_agent("V2 Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent("V2 Agent A2", "Alto", cls.org_a)
        cls.agent_b = add_agent("V2 Agent B", "Alto", cls.org_b)

        cls.admin_a = add_user("v2_admin_a", password_hash, ROLE_ADMIN, cls.org_a)
        cls.agent_user = add_user(
            "v2_agent_a",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.other_agent_user = add_user(
            "v2_agent_other",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_other,
        )
        cls.agent_user_b = add_user(
            "v2_agent_b",
            password_hash,
            ROLE_AGENT,
            cls.org_b,
            agent_id=cls.agent_b,
        )
        cls.property_a = add_property(
            "Santamarina 1335",
            "Martínez",
            cls.org_a,
            agent_id=cls.agent_a,
            status="approved",
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

    def _local_parts(self, offset=None):
        tz = organization_timezone(self.org_a)
        if offset is None:
            today = now_utc().astimezone(tz).date()
            return today.isoformat(), "09:00"
        moment = (now_utc() + offset).astimezone(tz)
        return moment.date().isoformat(), moment.strftime("%H:%M")

    def _payload(self, **extra):
        due_date, due_time = self._local_parts(extra.pop("offset", None))
        payload = {
            "title": extra.pop("title", "Visita de prueba"),
            "task_type": extra.pop("task_type", "visit"),
            "due_date": extra.pop("due_date", due_date),
            "due_time": extra.pop("due_time", due_time),
            "reminder_minutes": extra.pop("reminder_minutes", 30),
        }
        payload.update(extra)
        return payload

    def _create(self, *, agent_id=None, organization_id=None, **extra):
        return create_task(
            organization_id or self.org_a,
            agent_id if agent_id is not None else self.agent_a,
            self._payload(**extra),
            created_by_user_id=self.agent_user,
        )

    def test_today_view_is_default_assistant(self):
        self._create(title="Reunión de hoy")
        self._login("v2_agent_a")
        page = self.client.get("/agenda")
        body = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertIn("agenda-home--v2", body)
        self.assertIn("ds-page-header", body)
        self.assertIn("Tu día, organizado por JRH One.", body)
        self.assertIn("agenda-views", body)
        self.assertIn("Hoy", body)
        self.assertIn("Próximos", body)
        self.assertIn("Calendario", body)
        self.assertIn("Tareas", body)
        self.assertIn("Reunión de hoy", body)
        self.assertIn("Recordatorio: 30 min antes", body)
        self.assertNotIn("<table", body)

    def test_empty_today_uses_premium_state(self):
        empty_agent = add_agent("V2 Empty", "Alto", self.org_a)
        add_user(
            "v2_empty",
            hash_password(self.password),
            ROLE_AGENT,
            self.org_a,
            agent_id=empty_agent,
        )
        self._login("v2_empty")
        page = self.client.get("/agenda")
        body = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertIn("ds-empty", body)
        self.assertIn("No tenés nada más para hoy.", body)
        self.assertIn("seguimiento de contactos", body)
        self.assertIn("Agendar con JRH IA", body)

    def test_visit_shows_property_and_contact(self):
        contact = create_agent_contact(
            self.org_a,
            self.agent_a,
            {"name": "Martín Pérez", "status": "lead", "source": "manual"},
        )
        self._create(
            title="Visita Santamarina",
            property_id=self.property_a,
            contact_id=contact["id"],
            contact_name="Martín Pérez",
        )
        self._login("v2_agent_a")
        body = self.client.get("/agenda").get_data(as_text=True)

        self.assertIn("Santamarina 1335", body)
        self.assertIn("Martín Pérez", body)
        self.assertIn(f"/contacts/{contact['id']}", body)
        self.assertIn(f"/properties/{self.property_a}", body)
        self.assertIn("Ver propiedad", body)
        self.assertIn("Ver contacto", body)

    def test_google_overlay_stays_read_only(self):
        from modules.database.google_calendar_repository import (
            upsert_calendar_connection,
        )
        from modules.google_calendar import encrypt_token

        google_agent = add_agent("V2 Google", "Alto", self.org_a)
        google_user = add_user(
            "v2_google",
            hash_password(self.password),
            ROLE_AGENT,
            self.org_a,
            agent_id=google_agent,
        )
        os.environ["GOOGLE_CALENDAR_CLIENT_ID"] = "test-client"
        os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"] = "test-secret"
        self.addCleanup(os.environ.pop, "GOOGLE_CALENDAR_CLIENT_ID", None)
        self.addCleanup(os.environ.pop, "GOOGLE_CALENDAR_CLIENT_SECRET", None)
        upsert_calendar_connection(
            self.org_a,
            google_user,
            google_email="lucia@example.com",
            refresh_token_encrypted=encrypt_token("refresh-token"),
            access_token_encrypted=encrypt_token("access-token"),
            access_expires_at=(now_utc() + timedelta(hours=1))
            .replace(tzinfo=None)
            .isoformat(),
        )
        start = now_utc() + timedelta(hours=2)

        def fake_http(method, url, **kwargs):
            if method == "GET" and "/events" in url:
                return {
                    "items": [
                        {
                            "id": "g-lunch",
                            "status": "confirmed",
                            "summary": "Almuerzo con Martín",
                            "htmlLink": "https://calendar.google.com/event?eid=g-lunch",
                            "start": {"dateTime": start.isoformat()},
                            "extendedProperties": {"private": {}},
                        }
                    ]
                }
            raise AssertionError(f"unexpected {method} {url}")

        self._login("v2_google")
        with patch("modules.google_calendar._http_json", side_effect=fake_http):
            body = self.client.get("/agenda?filter=upcoming").get_data(as_text=True)
            google_only = self.client.get(
                "/agenda?filter=upcoming&origin=google"
            ).get_data(as_text=True)
            jrh_only = self.client.get(
                "/agenda?filter=upcoming&origin=jrh"
            ).get_data(as_text=True)

        self.assertIn("Almuerzo con Martín", body)
        self.assertIn("agenda-card--google", body)
        self.assertIn("Ver en Google", body)
        self.assertNotIn("agenda-reschedule", body)
        self.assertIn("Almuerzo con Martín", google_only)
        self.assertNotIn("Almuerzo con Martín", jrh_only)

    def test_create_edit_complete_reschedule_cancel(self):
        self._login("v2_agent_a")
        due_date, due_time = self._local_parts()
        created = self.client.post(
            "/agenda/new",
            data={
                "title": "Llamar a Lucía",
                "task_type": "call",
                "due_date": due_date,
                "due_time": due_time,
                "reminder_minutes": "30",
                "duration_minutes": "15",
            },
            follow_redirects=True,
        )
        self.assertEqual(created.status_code, 200)
        self.assertIn("Llamar a Lucía", created.get_data(as_text=True))

        tasks = list_agent_tasks(self.org_a, agent_id=self.agent_a, search="Llamar a Lucía")
        self.assertTrue(tasks)
        task_id = tasks[0]["id"]

        edit = self.client.get(f"/agenda/{task_id}/edit")
        self.assertEqual(edit.status_code, 200)
        self.assertIn("ds-page-header", edit.get_data(as_text=True))

        later_date, later_time = self._local_parts(timedelta(days=1, hours=2))
        moved = self.client.post(
            f"/agenda/{task_id}/reschedule",
            data={"due_date": later_date, "due_time": later_time},
            follow_redirects=True,
        )
        self.assertEqual(moved.status_code, 200)
        refreshed = get_agent_task(task_id, self.org_a)
        self.assertNotEqual(refreshed["due_at"], tasks[0]["due_at"])

        completed = self.client.post(
            f"/agenda/{task_id}/complete",
            follow_redirects=True,
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(get_agent_task(task_id, self.org_a)["status"], "completed")

        cancel_task = self._create(title="Evento a cancelar", task_type="meeting")
        cancelled = self.client.post(
            f"/agenda/{cancel_task['id']}/cancel",
            follow_redirects=True,
        )
        self.assertEqual(cancelled.status_code, 200)
        stored = get_agent_task(cancel_task["id"], self.org_a)
        self.assertEqual(stored["status"], "cancelled")
        self.assertIsNotNone(stored)

    def test_reschedule_changes_reminder_event_key(self):
        task = self._create(title="Visita con reminder", reminder_minutes=30)
        old_key = visit_reminder_event_key(task["id"], task["due_at"], 30)
        due_date, due_time = self._local_parts(timedelta(days=2))

        self._login("v2_agent_a")
        self.client.post(
            f"/agenda/{task['id']}/reschedule",
            data={"due_date": due_date, "due_time": due_time},
            follow_redirects=True,
        )
        moved = get_agent_task(task["id"], self.org_a)
        new_key = visit_reminder_event_key(moved["id"], moved["due_at"], 30)

        self.assertNotEqual(old_key, new_key)
        self.assertIn(str(moved["due_at"]).replace(" ", "T"), new_key)

    def test_agent_sees_only_own_tasks(self):
        self._create(title="Privado de A")
        create_task(
            self.org_a,
            self.agent_other,
            self._payload(title="Privado de A2"),
            created_by_user_id=self.other_agent_user,
        )
        self._login("v2_agent_a")
        body = self.client.get("/agenda").get_data(as_text=True)
        self.assertIn("Privado de A", body)
        self.assertNotIn("Privado de A2", body)

        forced = self.client.get(f"/agenda?agent_id={self.agent_other}")
        self.assertNotIn("Privado de A2", forced.get_data(as_text=True))

    def test_admin_can_filter_team_without_mixing_tenants(self):
        self._create(title="Tarea del equipo A")
        create_task(
            self.org_b,
            self.agent_b,
            self._payload(title="Tarea del equipo B"),
            created_by_user_id=self.agent_user_b,
        )
        self._login("v2_admin_a")
        team = self.client.get("/agenda").get_data(as_text=True)
        self.assertIn("Tarea del equipo A", team)
        self.assertNotIn("Tarea del equipo B", team)
        self.assertIn("Equipo", team)

        self._login("v2_agent_b")
        foreign = self.client.get("/agenda").get_data(as_text=True)
        self.assertNotIn("Tarea del equipo A", foreign)
        edit = self.client.get("/agenda/1/edit")
        self.assertEqual(edit.status_code, 404)

    def test_calendar_and_tasks_views_have_no_table(self):
        self._create(title="Evento de calendario")
        self._login("v2_agent_a")
        calendar = self.client.get("/agenda?view=calendar").get_data(as_text=True)
        week = self.client.get("/agenda?view=calendar&cal=week").get_data(as_text=True)
        tasks = self.client.get("/agenda?view=tasks").get_data(as_text=True)
        upcoming = self.client.get("/agenda?view=upcoming").get_data(as_text=True)

        self.assertIn("agenda-month", calendar)
        self.assertIn("agenda-week", week)
        self.assertIn("Evento de calendario", week)
        self.assertNotIn("<table", calendar)
        self.assertNotIn("<table", week)
        self.assertNotIn("<table", tasks)
        self.assertIn("agenda-views", upcoming)

    def test_filters_and_mobile_markup(self):
        self._create(title="Filtro visita", task_type="visit")
        self._login("v2_agent_a")
        body = self.client.get("/agenda").get_data(as_text=True)

        self.assertIn("agenda-tools", body)
        self.assertIn("Filtros", body)
        self.assertIn('name="type"', body)
        self.assertIn('name="origin"', body)
        self.assertIn("agenda-fab", body)
        self.assertIn("mobile-bottom-nav-label", body)
        self.assertRegex(body, r'name="prompt"\s+value=""')
        self.assertIn("data-agenda-voice", body)

        typed = self.client.get("/agenda?type=visit").get_data(as_text=True)
        self.assertIn("Filtro visita", typed)

    def test_quick_add_preview_does_not_save(self):
        before = list_agent_tasks(self.org_a, agent_id=self.agent_a)
        self._login("v2_agent_a")
        preview = self.client.post(
            "/agenda/compose",
            data={"prompt": "Visita Santamarina mañana 14:30 con Martín"},
        )
        body = preview.get_data(as_text=True)
        after = list_agent_tasks(self.org_a, agent_id=self.agent_a)

        self.assertEqual(preview.status_code, 200)
        self.assertIn("agenda-preview", body)
        self.assertEqual(len(before), len(after))

    def test_property_link_does_not_duplicate_inventory(self):
        task = self._create(
            title="Visita inventario",
            property_id=self.property_a,
        )
        linked = list_tasks_for_property(self.org_a, self.property_a)
        self.assertIn(task["id"], [item["id"] for item in linked])


if __name__ == "__main__":
    unittest.main()
