"""Phase 4B agent agenda and follow-up tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_agent_agenda.db"
)

from modules.agent_tasks import (
    AgentTaskError,
    build_agenda_summary,
    build_agenda_view,
    cancel_task,
    complete_task,
    create_task,
    list_tasks_for_operation,
    list_tasks_for_property,
    reschedule_task,
    update_task,
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
from modules.database.agent_tasks_repository import get_agent_task
from modules.database.schema import create_tables as create_tables_again
from modules.operations import (
    calculate_operation_details,
    save_calculated_operation,
)
from modules.organization_time import (
    local_datetime_to_utc_iso,
    now_utc,
    organization_timezone,
    to_local,
)
from modules.pending_actions import build_agent_pending_actions
from web_app import app


def _kinds(actions):
    return [action["kind"] for action in actions]


class AgentAgendaTests(unittest.TestCase):
    _counter = 0

    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(
            TESTING=True,
            SECRET_KEY="agent-agenda-test",
        )
        create_tables()

        cls.org_a = add_organization("Agenda Org A")
        cls.org_b = add_organization("Agenda Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)

        cls.agent_a = add_agent("Agenda Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent(
            "Agenda Agent A2",
            "Alto",
            cls.org_a,
        )
        cls.agent_b = add_agent("Agenda Agent B", "Alto", cls.org_b)

        cls.admin_a = add_user(
            "agenda_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "agenda_agent_user",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.other_agent_user = add_user(
            "agenda_agent_other",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_other,
        )
        cls.admin_b = add_user(
            "agenda_admin_b",
            password_hash,
            ROLE_ADMIN,
            cls.org_b,
        )

        cls.property_a = cls._make_property(cls.org_a, cls.agent_a)
        cls.property_b = cls._make_property(cls.org_b, cls.agent_b)
        cls.operation_a = cls._make_operation(
            cls.org_a,
            cls.agent_a,
            "Agenda Agent A",
        )
        cls.operation_b = cls._make_operation(
            cls.org_b,
            cls.agent_b,
            "Agenda Agent B",
        )

    def setUp(self):
        self.client = app.test_client()

    # ---------------- helpers ----------------

    @classmethod
    def _next_address(cls):
        cls._counter += 1

        return f"Agenda Street {cls._counter}"

    @classmethod
    def _make_property(cls, organization_id, agent_id):
        return add_property(
            cls._next_address(),
            "CABA",
            organization_id,
            agent_id=agent_id,
            status="approved",
        )

    @classmethod
    def _make_operation(cls, organization_id, agent_id, agent_name):
        address = cls._next_address()
        property_id = add_property(
            address,
            "CABA",
            organization_id,
            agent_id=agent_id,
            status="approved",
        )
        operation = calculate_operation_details(
            agent_name,
            "Alto",
            address,
            "CABA",
            100000,
            3,
            "yes",
            vat_amount=0,
        )
        operation["currency"] = "USD"
        operation["original_amount"] = 100000
        operation["exchange_rate"] = 1
        operation["side_data"] = {
            "seller_active": False,
            "buyer_active": True,
            "seller_commission_percent": 0,
            "buyer_commission_percent": 3,
            "seller_commission_amount": 0,
            "buyer_commission_amount": 3000,
        }
        operation_id, _ = save_calculated_operation(
            agent_id,
            property_id,
            organization_id,
            operation,
            status="approved",
            created_by_user_id=cls.admin_a,
        )

        return operation_id

    def _login(self, username):
        self.client.get("/logout", follow_redirects=True)

        return self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def _local_parts(self, organization_id, offset):
        """Local date/time strings for ``now + offset``."""
        tz = organization_timezone(organization_id)
        moment = (now_utc() + offset).astimezone(tz)

        return (
            moment.date().isoformat(),
            moment.strftime("%H:%M"),
        )

    def _payload(self, *, offset=None, organization_id=None, **extra):
        organization_id = organization_id or self.org_a
        due_date, due_time = self._local_parts(
            organization_id,
            offset if offset is not None else timedelta(hours=1),
        )
        payload = {
            "title": "Segunda visita con cliente",
            "task_type": "visit",
            "priority": "normal",
            "due_date": due_date,
            "due_time": due_time,
            "description": "Quiere coordinar segunda visita.",
        }
        payload.update(extra)

        return payload

    def _create(self, *, offset=None, agent_id=None, **extra):
        return create_task(
            self.org_a,
            agent_id if agent_id is not None else self.agent_a,
            self._payload(offset=offset, **extra),
            created_by_user_id=self.agent_user,
        )

    # ---------------- tests ----------------

    def test_01_agent_creates_own_task(self):
        task = self._create()

        self.assertEqual(task["organization_id"], self.org_a)
        self.assertEqual(task["agent_id"], self.agent_a)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["task_type"], "visit")
        self.assertEqual(task["created_by_user_id"], self.agent_user)
        self.assertIsNotNone(task["created_at"])
        self.assertIsNotNone(task["updated_at"])

    def test_02_agent_cannot_create_for_another_agent(self):
        """The route only ever passes the caller's own agent id."""
        response = self._login("agenda_agent_user")
        self.assertEqual(response.status_code, 200)

        created = self.client.post(
            "/agenda/new",
            data=self._payload(),
            follow_redirects=True,
        )
        self.assertEqual(created.status_code, 200)

        foreign = build_agenda_view(
            self.org_a,
            agent_id=self.agent_other,
        )
        self.assertEqual(foreign["total"], 0)

        # A task owned by another agent cannot be mutated either.
        other_task = create_task(
            self.org_a,
            self.agent_other,
            self._payload(title="Ajena"),
            created_by_user_id=self.other_agent_user,
        )
        blocked = self.client.post(
            f"/agenda/{other_task['id']}/complete",
            follow_redirects=True,
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(
            get_agent_task(other_task["id"], self.org_a)["status"],
            "pending",
        )

    def test_03_other_organization_rejected(self):
        with self.assertRaises(AgentTaskError) as ctx:
            create_task(
                self.org_a,
                self.agent_b,
                self._payload(),
                created_by_user_id=self.admin_a,
            )

        self.assertEqual(
            ctx.exception.message_key,
            "agent_task_err_agent_not_found",
        )

    def test_04_property_from_other_organization_rejected(self):
        with self.assertRaises(AgentTaskError) as ctx:
            self._create(property_id=self.property_b)

        self.assertEqual(
            ctx.exception.message_key,
            "agent_task_err_property_not_found",
        )

    def test_05_operation_from_other_organization_rejected(self):
        with self.assertRaises(AgentTaskError) as ctx:
            self._create(operation_id=self.operation_b)

        self.assertEqual(
            ctx.exception.message_key,
            "agent_task_err_operation_not_found",
        )

    def test_06_today_task_appears_in_dashboard_summary(self):
        task = self._create(title="Llamar a Martín López")

        summary = build_agenda_summary(self.org_a, self.agent_a)

        self.assertGreaterEqual(summary["today_count"], 1)
        self.assertIn(
            task["id"],
            [item["id"] for item in summary["tasks"]],
        )

    def test_07_overdue_task_is_flagged(self):
        task = self._create(
            offset=timedelta(hours=-2),
            title="Seguimiento vencido",
        )

        agenda = build_agenda_view(
            self.org_a,
            agent_id=self.agent_a,
            agenda_filter="overdue",
        )
        found = [
            item
            for section in agenda["sections"]
            for item in section["tasks"]
            if item["id"] == task["id"]
        ]

        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["is_overdue"])
        self.assertEqual(found[0]["section"], "overdue")
        self.assertIn("2", found[0]["overdue_label"])

    def test_08_completed_task_is_not_pending(self):
        task = self._create(offset=timedelta(hours=-3))

        completed = complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )

        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["completed_at"])

        agenda = build_agenda_view(
            self.org_a,
            agent_id=self.agent_a,
            agenda_filter="overdue",
        )
        pending_ids = [
            item["id"]
            for section in agenda["sections"]
            for item in section["tasks"]
        ]
        self.assertNotIn(task["id"], pending_ids)

        history = build_agenda_view(
            self.org_a,
            agent_id=self.agent_a,
            agenda_filter="completed",
        )
        history_ids = [
            item["id"]
            for section in history["sections"]
            for item in section["tasks"]
        ]
        self.assertIn(task["id"], history_ids)

    def test_09_reschedule_moves_the_same_task(self):
        task = self._create()
        due_date, due_time = self._local_parts(
            self.org_a,
            timedelta(days=2),
        )

        moved = reschedule_task(
            self.org_a,
            task["id"],
            due_date=due_date,
            due_time=due_time,
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )

        self.assertEqual(moved["id"], task["id"])
        self.assertNotEqual(moved["due_at"], task["due_at"])

        tz = organization_timezone(self.org_a)
        self.assertEqual(
            to_local(moved["due_at"], tz).strftime("%H:%M"),
            due_time,
        )

    def test_10_cancel_keeps_history(self):
        task = self._create(title="Tarea a cancelar")

        cancelled = cancel_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["cancelled_at"])
        self.assertIsNotNone(
            get_agent_task(task["id"], self.org_a),
            "cancelled tasks are never deleted",
        )

    def test_11_property_detail_shows_its_tasks(self):
        task = self._create(
            title="Visita Av. Libertador",
            property_id=self.property_a,
        )

        related = list_tasks_for_property(
            self.org_a,
            self.property_a,
            agent_id=self.agent_a,
        )
        self.assertIn(task["id"], [item["id"] for item in related])

        self._login("agenda_agent_user")
        response = self.client.get(
            f"/properties/{self.property_a}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Visita Av. Libertador", body)
        self.assertIn("agenda-entity-panel", body)

    def test_12_operation_detail_shows_its_tasks(self):
        task = self._create(
            title="Llamar al escribano",
            operation_id=self.operation_a,
        )

        related = list_tasks_for_operation(
            self.org_a,
            self.operation_a,
            agent_id=self.agent_a,
        )
        self.assertIn(task["id"], [item["id"] for item in related])

        self._login("agenda_agent_user")
        response = self.client.get(
            f"/operations/{self.operation_a}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Llamar al escribano",
            response.get_data(as_text=True),
        )

    def test_13_staff_only_sees_own_organization(self):
        self._create(title="Tarea org A")
        create_task(
            self.org_b,
            self.agent_b,
            self._payload(
                title="Tarea org B",
                organization_id=self.org_b,
            ),
            created_by_user_id=self.admin_b,
        )

        self._login("agenda_admin_a")
        response = self.client.get("/agenda?filter=upcoming")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Tarea org A", body)
        self.assertNotIn("Tarea org B", body)

    def test_14_agent_cannot_see_another_agenda(self):
        other_task = create_task(
            self.org_a,
            self.agent_other,
            self._payload(title="Agenda del otro agente"),
            created_by_user_id=self.other_agent_user,
        )

        self._login("agenda_agent_user")
        response = self.client.get("/agenda?filter=upcoming")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            "Agenda del otro agente",
            response.get_data(as_text=True),
        )

        edit = self.client.get(f"/agenda/{other_task['id']}/edit")
        self.assertEqual(edit.status_code, 404)

        # Staff filtering by agent id is not a way in for agents.
        forced = self.client.get(
            f"/agenda?agent_id={self.agent_other}"
        )
        self.assertNotIn(
            "Agenda del otro agente",
            forced.get_data(as_text=True),
        )

    def test_15_agenda_list_renders_as_cards(self):
        self._create(title="Tarea para render")

        self._login("agenda_agent_user")
        response = self.client.get("/agenda")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("agenda-home", body)
        self.assertIn("Tu día, organizado por JRH One.", body)
        self.assertIn("agenda-card", body)
        self.assertIn("agenda-fab", body)
        self.assertIn("agenda-ia", body)
        self.assertIn("agenda-tools", body)
        self.assertRegex(body, r'name="prompt"\s+value=""')
        self.assertIn("mobile-bottom-nav-label", body)
        self.assertIn("Agenda", body)
        self.assertNotIn("<table", body)

        form = self.client.get("/agenda/new")
        self.assertEqual(form.status_code, 200)

    def test_16_agent_dashboard_renders_agenda_block(self):
        """A fresh agent keeps the block deterministic (it shows 3)."""
        agent_id = add_agent("Agenda Dashboard", "Alto", self.org_a)
        add_user(
            "agenda_dashboard_user",
            hash_password(self.password),
            ROLE_AGENT,
            self.org_a,
            agent_id=agent_id,
        )
        self._create(title="Tarea del dashboard", agent_id=agent_id)

        self._login("agenda_dashboard_user")
        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("agenda-block", body)
        self.assertIn("Tarea del dashboard", body)

    def test_17_pending_center_includes_overdue_task(self):
        task = self._create(
            offset=timedelta(hours=-4),
            title="Llamar a Martín López",
        )

        actions = build_agent_pending_actions(
            self.org_a,
            self.agent_a,
            user_id=self.agent_user,
        )
        self.assertIn("own_task_overdue", _kinds(actions))

        overdue = [
            action
            for action in actions
            if action["kind"] == "own_task_overdue"
            and "Llamar a Martín López" in (action["subtitle"] or "")
        ]
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0]["priority"], "high")
        self.assertEqual(overdue[0]["endpoint"], "agenda_index")

        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )

        after = build_agent_pending_actions(
            self.org_a,
            self.agent_a,
            user_id=self.agent_user,
        )
        remaining = [
            action
            for action in after
            if action["kind"] == "own_task_overdue"
            and "Llamar a Martín López" in (action["subtitle"] or "")
        ]
        self.assertEqual(remaining, [])

    def test_18_migration_is_idempotent(self):
        create_tables_again()
        create_tables_again()

        task = self._create(title="Tarea post migración")
        self.assertIsNotNone(
            get_agent_task(task["id"], self.org_a)
        )

    def test_19_due_at_is_stored_in_utc(self):
        tz = organization_timezone(self.org_a)
        due_at = local_datetime_to_utc_iso("2026-09-10", "16:30", tz)

        task = create_task(
            self.org_a,
            self.agent_a,
            {
                "title": "Chequeo de timezone",
                "task_type": "call",
                "priority": "normal",
                "due_date": "2026-09-10",
                "due_time": "16:30",
            },
            created_by_user_id=self.agent_user,
        )

        self.assertEqual(task["due_at"], due_at)
        self.assertNotIn("+", task["due_at"])

        # Buenos Aires is UTC-3, so 16:30 local is 19:30 stored.
        self.assertEqual(task["due_at"], "2026-09-10T19:30:00")

        local = to_local(task["due_at"], tz)
        self.assertEqual(local.strftime("%Y-%m-%d %H:%M"), "2026-09-10 16:30")

        agenda = build_agenda_view(
            self.org_a,
            agent_id=self.agent_a,
            due_date="2026-09-10",
        )
        rendered = [
            item
            for section in agenda["sections"]
            for item in section["tasks"]
            if item["id"] == task["id"]
        ]
        self.assertEqual(len(rendered), 1)
        self.assertEqual(rendered[0]["due_time_label"], "16:30")

    def test_20_invalid_due_at_is_rejected(self):
        with self.assertRaises(AgentTaskError) as ctx:
            create_task(
                self.org_a,
                self.agent_a,
                {
                    "title": "Sin fecha",
                    "task_type": "call",
                    "priority": "normal",
                    "due_date": "",
                    "due_time": "",
                },
                created_by_user_id=self.agent_user,
            )

        self.assertEqual(
            ctx.exception.message_key,
            "agent_task_err_invalid_due_at",
        )

    def test_21_guest_has_no_agenda(self):
        self.client.get("/logout", follow_redirects=True)
        response = self.client.get("/agenda")

        self.assertNotEqual(response.status_code, 200)

    def test_22_nlp_parses_visit_prompt(self):
        from datetime import date as date_cls

        from modules.agenda_nlp import parse_agenda_prompt

        draft = parse_agenda_prompt(
            "Agendame una visita mañana a las 16 con Lucía para Libertador 3200",
            today=date_cls(2026, 9, 3),
        )

        self.assertEqual(draft["task_type"], "visit")
        self.assertEqual(draft["due_date"], "2026-09-04")
        self.assertEqual(draft["due_time"], "16:00")
        self.assertEqual(draft["contact_name"], "Lucía")
        self.assertIn("Libertador", draft["property_query"])

    def test_23_nlp_parses_call_prompt(self):
        from datetime import date as date_cls

        from modules.agenda_nlp import parse_agenda_prompt

        draft = parse_agenda_prompt(
            "El lunes a las 11 tengo que llamar a Pablo por Cabildo",
            today=date_cls(2026, 9, 4),
        )

        self.assertEqual(draft["task_type"], "call")
        self.assertEqual(draft["due_time"], "11:00")
        self.assertEqual(draft["contact_name"], "Pablo")
        self.assertIn("Cabildo", draft["property_query"])

    def test_24_compose_creates_task_from_prompt(self):
        self._login("agenda_agent_user")
        parsed = self.client.post(
            "/agenda/compose",
            data={
                "prompt": (
                    "Agendame una visita mañana a las 16 con Lucía "
                    "para Agenda Street"
                )
            },
        )
        self.assertEqual(parsed.status_code, 200)
        self.assertIn("agenda-preview", parsed.get_data(as_text=True))

        created = self.client.post(
            "/agenda/compose",
            data={
                "confirm": "1",
                "title": "Visita con Lucía",
                "task_type": "visit",
                "due_date": "2026-09-10",
                "due_time": "16:00",
                "contact_name": "Lucía",
                "description": "visita",
                "duration_minutes": "60",
            },
            follow_redirects=True,
        )
        self.assertEqual(created.status_code, 200)
        self.assertIn("Lucía", created.get_data(as_text=True))

    def test_25_complete_visit_opens_follow_up(self):
        task = self._create(title="Visita de prueba")
        self._login("agenda_agent_user")
        response = self.client.post(
            f"/agenda/{task['id']}/complete",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/follow-up", response.headers.get("Location", ""))

        page = self.client.get(f"/agenda/{task['id']}/follow-up")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("agenda-followup", body)
        self.assertIn("Ahora no", body)
        self.assertIn("Escribir resumen", body)

    def test_26_visit_outcome_is_saved(self):
        from modules.agent_tasks import save_visit_outcome
        from modules.database.agent_tasks_repository import get_agent_task

        task = self._create(title="Visita con outcome")
        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        save_visit_outcome(
            self.org_a,
            task["id"],
            {
                "interest": "positive",
                "objection": "El segundo dormitorio es chico.",
                "area": "Núñez",
                "budget": "180000",
                "next_action": "Buscar alternativas",
                "note": "Positiva",
            },
            agent_id=self.agent_a,
        )
        stored = get_agent_task(task["id"], self.org_a)
        self.assertIn("Núñez", stored["outcome_json"])
        self.assertIn("positive", stored["outcome_json"])

    # ---------------- Google Calendar ----------------

    def _google_env(self, configured=True):
        previous_id = os.environ.get("GOOGLE_CALENDAR_CLIENT_ID")
        previous_secret = os.environ.get("GOOGLE_CALENDAR_CLIENT_SECRET")

        if configured:
            os.environ["GOOGLE_CALENDAR_CLIENT_ID"] = "test-google-client"
            os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"] = "test-google-secret"
        else:
            os.environ.pop("GOOGLE_CALENDAR_CLIENT_ID", None)
            os.environ.pop("GOOGLE_CALENDAR_CLIENT_SECRET", None)

        def restore():
            if previous_id is None:
                os.environ.pop("GOOGLE_CALENDAR_CLIENT_ID", None)
            else:
                os.environ["GOOGLE_CALENDAR_CLIENT_ID"] = previous_id

            if previous_secret is None:
                os.environ.pop("GOOGLE_CALENDAR_CLIENT_SECRET", None)
            else:
                os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"] = previous_secret

        self.addCleanup(restore)

    def _seed_google_connection(self, *, email="lucia@example.com"):
        from modules.database.google_calendar_repository import (
            upsert_calendar_connection,
        )
        from modules.google_calendar import encrypt_token

        self._google_env(configured=True)
        upsert_calendar_connection(
            self.org_a,
            self.agent_user,
            google_email=email,
            refresh_token_encrypted=encrypt_token("refresh-token"),
            access_token_encrypted=encrypt_token("access-token"),
            access_expires_at=(
                now_utc() + timedelta(hours=1)
            ).replace(tzinfo=None).isoformat(),
        )

    def test_27_calendar_chip_unconfigured_without_secrets(self):
        self._google_env(configured=False)
        self._login("agenda_agent_user")
        response = self.client.get("/agenda")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Google Calendar", body)
        self.assertIn("No configurado", body)
        self.assertNotIn("Sincronizado", body)

        connect = self.client.get("/agenda/calendar/connect")
        self.assertEqual(connect.status_code, 302)
        self.assertIn("/settings/integrations", connect.headers.get("Location", ""))

    def test_28_calendar_connect_redirects_to_google(self):
        self._google_env(configured=True)
        self._login("agenda_agent_user")
        response = self.client.get("/agenda/calendar/connect")

        self.assertEqual(response.status_code, 302)
        location = response.headers.get("Location", "")
        self.assertIn("accounts.google.com", location)
        self.assertIn("calendar.events", location)

        with self.client.session_transaction() as stored:
            self.assertTrue(stored.get("google_calendar_oauth_state"))

    def test_29_calendar_callback_saves_connection(self):
        self._google_env(configured=True)
        self._login("agenda_agent_user")
        self.client.get("/agenda/calendar/connect")

        with self.client.session_transaction() as stored:
            state = stored["google_calendar_oauth_state"]

        def fake_http(method, url, **kwargs):
            if "oauth2.googleapis.com/token" in url:
                return {
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                }
            if "userinfo" in url:
                return {"email": "lucia@example.com"}
            if method == "GET" and "/events" in url:
                return {"items": []}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            callback = self.client.get(
                f"/agenda/calendar/callback?code=ok&state={state}"
            )
            page = self.client.get("/agenda")

        self.assertEqual(callback.status_code, 302)
        self.assertIn("/settings/integrations", callback.headers.get("Location", ""))
        body = page.get_data(as_text=True)
        self.assertIn("Sincronizado", body)
        integrations = self.client.get("/settings/integrations")
        self.assertIn("lucia@example.com", integrations.get_data(as_text=True))

    def test_30_create_task_pushes_google_event(self):
        self._seed_google_connection()
        calls = []

        def fake_http(method, url, **kwargs):
            calls.append((method, url))
            if method == "POST" and url.endswith("/events"):
                return {"id": "evt-created"}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            task = self._create(title="Visita Google")

        self.assertEqual(task["google_event_id"], "evt-created")
        self.assertTrue(any(method == "POST" for method, _url in calls))

    def test_31_push_failure_does_not_block_create(self):
        self._seed_google_connection()

        def fake_http(method, url, **kwargs):
            from modules.google_calendar import GoogleCalendarHttpError

            raise GoogleCalendarHttpError(500, "boom")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            task = self._create(title="Sigue en JRH")

        self.assertIsNotNone(task["id"])
        self.assertEqual(task["title"], "Sigue en JRH")
        self.assertFalse(task.get("google_event_id"))

        from modules.database.google_calendar_repository import (
            get_calendar_connection,
        )

        record = get_calendar_connection(self.org_a, self.agent_user)
        self.assertEqual(record["status"], "active")

    def test_32_google_events_overlay_as_readonly_cards(self):
        self._seed_google_connection()
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
                        },
                        {
                            "id": "g-jrh",
                            "status": "confirmed",
                            "summary": "Copia de tarea JRH",
                            "start": {"dateTime": start.isoformat()},
                            "extendedProperties": {
                                "private": {"jrhTaskId": "99"}
                            },
                        },
                    ]
                }
            raise AssertionError(f"unexpected {method} {url}")

        self._login("agenda_agent_user")
        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            page = self.client.get("/agenda?filter=upcoming")

        body = page.get_data(as_text=True)
        self.assertIn("Almuerzo con Martín", body)
        self.assertIn("agenda-card--google", body)
        self.assertIn("Ver en Google", body)
        self.assertNotIn("Copia de tarea JRH", body)

    def test_33_staff_cannot_connect_google_calendar(self):
        self._google_env(configured=True)
        self._login("agenda_admin_a")
        response = self.client.get("/agenda/calendar/connect")

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(
            "accounts.google.com",
            response.headers.get("Location", ""),
        )

    def test_34_disconnect_clears_connection(self):
        self._seed_google_connection()
        self._login("agenda_agent_user")

        def fake_http(method, url, **kwargs):
            if "revoke" in url:
                return {}
            if method == "GET" and "/events" in url:
                return {"items": []}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            disconnected = self.client.post("/agenda/calendar/disconnect")
            page = self.client.get("/agenda")

        self.assertEqual(disconnected.status_code, 302)
        body = page.get_data(as_text=True)
        self.assertIn("Conectar", body)
        self.assertNotIn("lucia@example.com", body)

    def test_35_google_calendar_migration_is_idempotent(self):
        from modules.database.schema import create_tables as migrate_again
        from modules.database.connection import get_connection

        migrate_again(create_backup=False)
        migrate_again(create_backup=False)
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
                AND name = 'google_calendar_connections'
            """
        )
        self.assertIsNotNone(cursor.fetchone())
        cursor.execute("PRAGMA table_info(agent_tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        connection.close()
        self.assertIn("google_event_id", columns)

    # ---------------- Honest composer (phase 2) ----------------

    def test_36_property_match_zero_hits_leaves_id_empty(self):
        from modules.agenda_ai import compose_from_prompt, match_property

        matched = match_property(
            self.org_a,
            self.agent_a,
            "Zeballos Falso 91",
        )
        self.assertEqual(matched["status"], "none")
        self.assertEqual(matched["candidates"], [])
        self.assertIsNone(matched["property"])

        draft = compose_from_prompt(
            "Agendame una visita mañana a las 16 con Lucía para Zeballos Falso 91",
            self.org_a,
            self.agent_a,
        )
        self.assertEqual(draft["property_id"], "")
        self.assertEqual(draft["property_match"], "none")
        self.assertIn("agenda_ai_warn_property_not_found", draft["warnings"])
        self.assertIn("agenda_ai_warn_visit_without_property", draft["warnings"])

    def test_37_property_match_single_hit_assigns_id(self):
        from modules.agenda_ai import compose_from_prompt, match_property

        property_id = add_property(
            "Uriarte Unica 450",
            "CABA",
            self.org_a,
            agent_id=self.agent_a,
            status="approved",
        )
        matched = match_property(
            self.org_a,
            self.agent_a,
            "Uriarte Unica 450",
        )
        self.assertEqual(matched["status"], "single")
        self.assertEqual(matched["property"]["id"], property_id)

        draft = compose_from_prompt(
            "Visita mañana a las 16 con Lucía para Uriarte Unica 450",
            self.org_a,
            self.agent_a,
        )
        self.assertEqual(draft["property_id"], property_id)
        self.assertEqual(draft["property_match"], "single")
        self.assertEqual(draft["ui_status"], "ready")

    def test_38_property_match_multiple_hits_does_not_guess(self):
        from modules.agenda_ai import compose_from_prompt, match_property

        first = add_property(
            "Libertador Norte 100",
            "CABA",
            self.org_a,
            agent_id=self.agent_a,
            status="approved",
        )
        second = add_property(
            "Libertador Sur 200",
            "CABA",
            self.org_a,
            agent_id=self.agent_a,
            status="approved",
        )
        matched = match_property(self.org_a, self.agent_a, "Libertador")
        self.assertEqual(matched["status"], "ambiguous")
        self.assertIsNone(matched["property"])
        self.assertEqual(
            {record["id"] for record in matched["candidates"]},
            {first, second},
        )

        draft = compose_from_prompt(
            "Visita mañana a las 16 con Lucía para Libertador",
            self.org_a,
            self.agent_a,
        )
        self.assertEqual(draft["property_id"], "")
        self.assertEqual(draft["property_match"], "ambiguous")
        self.assertEqual(len(draft["property_candidates"]), 2)
        self.assertEqual(draft["ui_status"], "properties_ambiguous")

    def test_39_ambiguous_confirm_requires_choice(self):
        from modules.database.agent_tasks_repository import list_agent_tasks

        first = add_property(
            "Cabildo Norte 10",
            "CABA",
            self.org_a,
            agent_id=self.agent_a,
            status="approved",
        )
        add_property(
            "Cabildo Sur 20",
            "CABA",
            self.org_a,
            agent_id=self.agent_a,
            status="approved",
        )
        before = len(list_agent_tasks(self.org_a, agent_id=self.agent_a))
        self._login("agenda_agent_user")
        preview = self.client.post(
            "/agenda/compose",
            data={"prompt": "Visita mañana a las 17 con Pablo para Cabildo"},
        )
        body = preview.get_data(as_text=True)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("property_choice", body)
        self.assertIn("Encontré 2 propiedades", body)

        blocked = self.client.post(
            "/agenda/compose",
            data={
                "confirm": "1",
                "needs_property_choice": "1",
                "prompt": "Visita mañana a las 17 con Pablo para Cabildo",
                "title": "Visita con Pablo · Cabildo",
                "task_type": "visit",
                "due_date": "2026-09-10",
                "due_time": "17:00",
                "contact_name": "Pablo",
                "description": "visita",
                "duration_minutes": "60",
            },
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("Elegí una de las propiedades posibles", blocked.get_data(as_text=True))
        self.assertEqual(
            len(list_agent_tasks(self.org_a, agent_id=self.agent_a)),
            before,
        )

        created = self.client.post(
            "/agenda/compose",
            data={
                "confirm": "1",
                "needs_property_choice": "1",
                "property_choice": str(first),
                "title": "Visita con Pablo · Cabildo",
                "task_type": "visit",
                "due_date": "2026-09-10",
                "due_time": "17:00",
                "contact_name": "Pablo",
                "description": "visita",
                "duration_minutes": "60",
            },
            follow_redirects=True,
        )
        self.assertEqual(created.status_code, 200)
        stored = [
            task
            for task in list_agent_tasks(self.org_a, agent_id=self.agent_a)
            if task.get("property_id") == first
        ]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["contact_name"], "Pablo")
        self.assertIn("Pablo", created.get_data(as_text=True))

    def test_40_whatsapp_paste_uses_same_compose_pipeline(self):
        from modules.agenda_ai import compose_from_prompt, compose_from_whatsapp_text

        self.assertIs(compose_from_whatsapp_text, compose_from_prompt)
        self._login("agenda_agent_user")
        pasted = (
            "Dale, mañana a las 18 podemos ir a ver el departamento "
            "de Libertador Unico 777"
        )
        response = self.client.post(
            "/agenda/capture",
            data={"whatsapp_text": pasted},
        )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("agenda-preview", body)
        self.assertIn("Libertador Unico 777", body)
        self.assertIn("18:00", body)

        confirmed = self.client.post(
            "/agenda/compose",
            data={
                "confirm": "1",
                "whatsapp_text": pasted,
                "title": "Visita · Libertador Unico 777",
                "task_type": "visit",
                "due_date": "2026-09-11",
                "due_time": "18:00",
                "contact_name": "",
                "description": pasted,
                "duration_minutes": "60",
            },
            follow_redirects=True,
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertIn("Libertador Unico 777", confirmed.get_data(as_text=True))

    def test_41_automatic_titles_do_not_invent(self):
        from modules.agenda_nlp import build_task_title, parse_agenda_prompt

        self.assertEqual(
            build_task_title("visit", "Lucía", "Libertador 3200"),
            "Visita con Lucía · Libertador 3200",
        )
        self.assertEqual(
            build_task_title("call", "Pablo", "Cabildo"),
            "Llamada con Pablo · Cabildo",
        )
        self.assertEqual(
            build_task_title("follow_up", "Martín", ""),
            "Seguimiento con Martín",
        )
        self.assertEqual(
            build_task_title("meeting", "Carolina", ""),
            "Reunión con Carolina",
        )
        self.assertEqual(
            build_task_title("visit", "", "Libertador 3200"),
            "Visita · Libertador 3200",
        )
        self.assertEqual(build_task_title("call", "", ""), "Llamada")

        draft = parse_agenda_prompt("Llamar a Pablo")
        self.assertEqual(draft["title"], "Llamada con Pablo")
        self.assertFalse(draft["date_found"])
        self.assertFalse(draft["time_found"])
        self.assertEqual(draft["due_date"], "")
        self.assertEqual(draft["due_time"], "")

    def test_42_multi_prompt_two_real_actions(self):
        from datetime import datetime, timezone

        from modules.agenda_ai import interpret_agenda_input
        from modules.agenda_nlp import split_agenda_segments

        when = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        segments = split_agenda_segments(
            "Mañana visita con Lucía a las 10 y llamada con Pablo a las 14"
        )
        self.assertEqual(len(segments), 2)

        bundle = interpret_agenda_input(
            "Mañana visita con Lucía a las 10 y llamada con Pablo a las 14",
            self.org_a,
            self.agent_a,
            now=when,
        )
        self.assertEqual(len(bundle["items"]), 2)
        self.assertEqual(bundle["items"][0]["task_type"], "visit")
        self.assertEqual(bundle["items"][1]["task_type"], "call")

    def test_43_multi_prompt_keeps_two_people_as_one_visit(self):
        from datetime import datetime, timezone

        from modules.agenda_ai import interpret_agenda_input

        bundle = interpret_agenda_input(
            "Mañana visita con Lucía y Pablo a las 10",
            self.org_a,
            self.agent_a,
            now=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(bundle["items"]), 1)
        self.assertEqual(bundle["items"][0]["task_type"], "visit")
        self.assertIn("Lucía", bundle["items"][0]["contact_name"])
        self.assertIn("Pablo", bundle["items"][0]["contact_name"])

    def test_44_multi_prompt_three_actions(self):
        from datetime import datetime, timezone

        from modules.agenda_ai import interpret_agenda_input

        bundle = interpret_agenda_input(
            "Mañana a las 10 visita Libertador 3200, a las 12 llamada a Pedro "
            "y el jueves seguimiento con Martín",
            self.org_a,
            self.agent_a,
            now=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(bundle["items"]), 3)
        self.assertEqual(bundle["items"][0]["task_type"], "visit")
        self.assertEqual(bundle["items"][1]["task_type"], "call")
        self.assertEqual(bundle["items"][2]["task_type"], "follow_up")
        self.assertEqual(bundle["items"][2]["item_status"], "ready")

    def test_45_despues_without_time_needs_attention(self):
        from datetime import datetime, timezone

        from modules.agenda_ai import interpret_agenda_input

        bundle = interpret_agenda_input(
            "El lunes tengo visita en Cabildo y después llamo al propietario",
            self.org_a,
            self.agent_a,
            now=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(bundle["items"]), 2)
        self.assertEqual(bundle["items"][0]["task_type"], "visit")
        self.assertEqual(bundle["items"][1]["task_type"], "call")
        self.assertTrue(bundle["items"][1]["date_found"])
        self.assertFalse(bundle["items"][1]["time_found"])
        self.assertEqual(bundle["items"][1]["item_status"], "needs_attention")
        self.assertEqual(bundle["items"][0]["item_status"], "needs_attention")

    def test_46_four_spoken_actions_one_preview(self):
        from datetime import datetime, timezone

        from modules.agenda_ai import interpret_agenda_input

        bundle = interpret_agenda_input(
            "Mañana visita 10hs, llamada 12hs, reunión 15hs "
            "y el viernes seguimiento con Carolina",
            self.org_a,
            self.agent_a,
            now=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(bundle["items"]), 4)
        self._login("agenda_agent_user")
        page = self.client.post(
            "/agenda/compose",
            data={
                "prompt": (
                    "Mañana visita 10hs, llamada 12hs, reunión 15hs "
                    "y el viernes seguimiento con Carolina"
                )
            },
        )
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Entendí 4 acciones", body)
        self.assertIn("confirm_ready", body)

    def test_47_integrations_page_renders_for_agent(self):
        self._google_env(configured=False)
        self._login("agenda_agent_user")
        page = self.client.get("/settings/integrations")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Google Calendar", page.get_data(as_text=True))
        self.assertIn("Integraciones", page.get_data(as_text=True))

    def _batch_item_fields(self, index, title):
        due_date, due_time = self._local_parts(
            self.org_a,
            timedelta(hours=2 + index),
        )
        prefix = f"items-{index}-"
        return {
            f"{prefix}title": title,
            f"{prefix}task_type": "visit",
            f"{prefix}due_date": due_date,
            f"{prefix}due_time": due_time,
            f"{prefix}contact_name": "",
            f"{prefix}property_id": "",
            f"{prefix}property_address": "",
            f"{prefix}property_query": "",
            f"{prefix}property_match": "none",
            f"{prefix}description": "",
            f"{prefix}duration_minutes": "",
            f"{prefix}reminder_minutes": "",
            f"{prefix}attendance_status": "",
            f"{prefix}source_prompt": "",
            f"{prefix}ui_status": "ready",
            f"{prefix}item_status": "ready",
            f"{prefix}date_found": "1",
            f"{prefix}time_found": "1",
            f"{prefix}candidates": "[]",
        }

    def test_48_update_patches_existing_google_event(self):
        self._seed_google_connection()
        calls = []

        def fake_http(method, url, **kwargs):
            calls.append((method, url))
            if method == "POST" and url.endswith("/events"):
                return {"id": "evt-keep"}
            if method == "PATCH" and "evt-keep" in url:
                return {"id": "evt-keep"}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            task = self._create(title="Visita original")
            update_task(
                self.org_a,
                task["id"],
                self._payload(title="Visita editada"),
                agent_id=self.agent_a,
                actor_user_id=self.agent_user,
            )
            update_task(
                self.org_a,
                task["id"],
                self._payload(title="Visita otra vez"),
                agent_id=self.agent_a,
                actor_user_id=self.agent_user,
            )

        posts = [item for item in calls if item[0] == "POST"]
        patches = [item for item in calls if item[0] == "PATCH"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(patches), 2)
        self.assertEqual(
            get_agent_task(task["id"], self.org_a)["google_event_id"],
            "evt-keep",
        )

    def test_49_cancel_deletes_google_event(self):
        self._seed_google_connection()
        calls = []

        def fake_http(method, url, **kwargs):
            calls.append((method, url))
            if method == "POST" and url.endswith("/events"):
                return {"id": "evt-cancel"}
            if method == "DELETE" and "evt-cancel" in url:
                return {}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            task = self._create(title="Visita a cancelar")
            cancel_task(
                self.org_a,
                task["id"],
                agent_id=self.agent_a,
                actor_user_id=self.agent_user,
            )

        deletes = [item for item in calls if item[0] == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertIn("evt-cancel", deletes[0][1])

    def test_50_batch_confirm_pushes_each_google_event(self):
        self._seed_google_connection()
        self._login("agenda_agent_user")
        calls = []
        created_ids = []

        def fake_http(method, url, **kwargs):
            calls.append((method, url))
            if method == "POST" and url.endswith("/events"):
                event_id = f"evt-batch-{len(created_ids) + 1}"
                created_ids.append(event_id)
                return {"id": event_id}
            raise AssertionError(f"unexpected {method} {url}")

        data = {"item_count": "3", "confirm_ready": "1"}
        data.update(self._batch_item_fields(0, "Visita lote 1"))
        data.update(self._batch_item_fields(1, "Visita lote 2"))
        data.update(self._batch_item_fields(2, "Visita lote 3"))

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            response = self.client.post("/agenda/compose", data=data)

        self.assertEqual(response.status_code, 302)
        posts = [item for item in calls if item[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertEqual(created_ids, ["evt-batch-1", "evt-batch-2", "evt-batch-3"])

        agenda = build_agenda_view(self.org_a, agent_id=self.agent_a)
        titles = {
            task["title"]: task.get("google_event_id")
            for section in agenda["sections"]
            for task in section["tasks"]
        }
        self.assertEqual(titles.get("Visita lote 1"), "evt-batch-1")
        self.assertEqual(titles.get("Visita lote 2"), "evt-batch-2")
        self.assertEqual(titles.get("Visita lote 3"), "evt-batch-3")

    def test_51_retry_restores_google_event_id(self):
        from modules.google_calendar import GoogleCalendarHttpError

        self._seed_google_connection()

        def failing_http(method, url, **kwargs):
            raise GoogleCalendarHttpError(500, "boom")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=failing_http,
        ):
            task = self._create(title="Visita pendiente Google")

        self.assertFalse(task.get("google_event_id"))
        self._login("agenda_agent_user")

        def retry_http(method, url, **kwargs):
            if method == "POST" and url.endswith("/events"):
                return {"id": "evt-retried"}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=retry_http,
        ):
            retried = self.client.post(
                "/agenda/calendar/retry",
                data={"task_id": str(task["id"])},
            )

        self.assertEqual(retried.status_code, 302)
        self.assertEqual(
            get_agent_task(task["id"], self.org_a)["google_event_id"],
            "evt-retried",
        )

    def test_52_task_card_shows_sync_states(self):
        from modules.google_calendar import GoogleCalendarHttpError

        self._seed_google_connection()
        created = {"count": 0}

        def fake_http(method, url, **kwargs):
            if method == "POST" and url.endswith("/events"):
                created["count"] += 1
                if created["count"] == 1:
                    return {"id": "evt-ok"}
                raise GoogleCalendarHttpError(500, "boom")
            if method == "GET" and "/events" in url:
                return {"items": []}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            self._create(title="Visita sincronizada OK")
            self._create(title="Visita sin Google")
            self._login("agenda_agent_user")
            page = self.client.get("/agenda")

        body = page.get_data(as_text=True)
        self.assertIn("En Google", body)
        self.assertIn("No pudimos sincronizar este evento con Google Calendar.", body)
        self.assertIn("Reintentar", body)
        self.assertIn("/agenda/calendar/retry", body)

    def test_53_integrations_page_when_connected(self):
        self._seed_google_connection()
        calls = []

        def fake_http(method, url, **kwargs):
            calls.append((method, url))
            if method == "POST" and url.endswith("/events"):
                return {"id": "evt-sync-stamp"}
            raise AssertionError(f"unexpected {method} {url}")

        with patch(
            "modules.google_calendar._http_json",
            side_effect=fake_http,
        ):
            self._create(title="Visita para stamp")

        self._login("agenda_agent_user")
        page = self.client.get("/settings/integrations")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("lucia@example.com", body)
        self.assertIn("Conectado", body)
        self.assertIn("Última sincronización", body)
        self.assertIn("Sincronizar", body)
        self.assertIn("Desconectar", body)
        self.assertNotIn("No configurado", body)

    def test_54_parser_extracts_structured_visit_outcome(self):
        from modules.agenda_ai import summarize_visit_outcome
        from modules.visit_outcome import format_budget_label, normalize_visit_outcome

        outcome = summarize_visit_outcome(
            "Le gustó mucho el departamento pero le pareció chica "
            "la segunda habitación. Quiere seguir viendo en Núñez, "
            "hasta 180 mil dólares, si puede ser con balcón."
        )
        self.assertEqual(outcome["interest"], "positive")
        self.assertTrue(
            any("habitación" in item.lower() or "chica" in item.lower()
                for item in outcome.get("objections") or [])
        )
        self.assertIn("Núñez", outcome.get("areas") or [])
        self.assertEqual((outcome.get("budget") or {}).get("max"), 180000)
        self.assertEqual((outcome.get("budget") or {}).get("currency"), "USD")
        self.assertIn("balcón", outcome.get("preferences") or [])
        self.assertEqual(outcome["next_action"], "Buscar alternativas")
        self.assertEqual(format_budget_label(outcome["budget"]), "USD 180.000")

        legacy = normalize_visit_outcome(
            {
                "note": "Viejo",
                "interest": "positive",
                "objection": "habitación chica",
                "area": "Núñez",
                "budget": "180000",
            }
        )
        self.assertEqual(legacy["objections"], ["habitación chica"])
        self.assertEqual(legacy["areas"], ["Núñez"])
        self.assertEqual(legacy["budget"]["max"], 180000)

    def test_55_now_skip_keeps_completed_visit_without_outcome(self):
        task = self._create(title="Visita omitida")
        self._login("agenda_agent_user")
        self.client.post(f"/agenda/{task['id']}/complete")
        page = self.client.get(f"/agenda/{task['id']}/follow-up")
        self.assertIn("Ahora no", page.get_data(as_text=True))
        stored = get_agent_task(task["id"], self.org_a)
        self.assertEqual(stored["status"], "completed")
        self.assertFalse(stored.get("outcome_json"))

    def test_56_save_and_edit_and_discard_preview(self):
        task = self._create(title="Visita resumen", contact_name="Carolina")
        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        self._login("agenda_agent_user")
        preview = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "summarize",
                "note": (
                    "Le gustó mucho pero le pareció chica la segunda "
                    "habitación. Quiere seguir viendo en Núñez hasta "
                    "180 mil dólares, si puede ser con balcón."
                ),
            },
        )
        body = preview.get_data(as_text=True)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Resumen de JRH", body)
        self.assertIn("Positivo", body)
        self.assertIn("Núñez", body)
        self.assertIn("Guardar seguimiento", body)
        self.assertFalse(get_agent_task(task["id"], self.org_a).get("outcome_json"))

        edited = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "edit",
                "outcome_json": json.dumps(
                    {
                        "note": "Positiva",
                        "interest": "positive",
                        "objections": ["segunda habitación chica"],
                        "areas": ["Núñez"],
                    }
                ),
            },
        )
        self.assertIn("name=\"interest\"", edited.get_data(as_text=True))

        discarded = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={"intent": "discard"},
        )
        discarded_body = discarded.get_data(as_text=True)
        self.assertIn("Escribir resumen", discarded_body)
        self.assertNotIn("Resumen de JRH", discarded_body)
        self.assertEqual(get_agent_task(task["id"], self.org_a)["status"], "completed")
        self.assertFalse(get_agent_task(task["id"], self.org_a).get("outcome_json"))

        saved = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "save",
                "outcome_json": json.dumps(
                    {
                        "note": "Positiva",
                        "interest": "positive",
                        "objections": ["segunda habitación chica"],
                        "areas": ["Núñez"],
                        "budget": {"max": 180000, "currency": "USD"},
                        "preferences": ["balcón"],
                        "next_action": "Buscar alternativas",
                    }
                ),
            },
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 302)
        self.assertIn("step=next", saved.headers.get("Location", ""))
        stored = get_agent_task(task["id"], self.org_a)
        payload = json.loads(stored["outcome_json"])
        self.assertEqual(payload["interest"], "positive")
        self.assertEqual(payload["areas"], ["Núñez"])
        self.assertEqual(payload["budget"]["max"], 180000)

    def test_57_next_action_preview_does_not_create_task(self):
        task = self._create(title="Visita con próxima", contact_name="Carolina")
        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        self._login("agenda_agent_user")
        response = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "save",
                "outcome_json": json.dumps(
                    {
                        "note": "Llamar mañana",
                        "interest": "positive",
                        "next_action": "Llamar",
                        "suggested_task": {
                            "type": "call",
                            "prompt": "Llamar a Carolina mañana a las 11",
                        },
                    }
                ),
            },
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)
        self.assertIn("JRH te sugiere", body)
        self.assertIn("Agendar", body)
        from modules.database.agent_tasks_repository import list_agent_tasks

        after = list_agent_tasks(
            self.org_a,
            agent_id=self.agent_a,
            statuses=("pending",),
            limit=200,
        )
        self.assertFalse(
            any(item.get("contact_name") == "Carolina" and item["task_type"] == "call" for item in after)
        )
        self.assertEqual(get_agent_task(task["id"], self.org_a)["status"], "completed")

    def test_58_confirm_next_action_creates_agenda_task(self):
        task = self._create(title="Visita a confirmar", contact_name="Carolina")
        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        self._login("agenda_agent_user")
        due_date, due_time = self._local_parts(self.org_a, timedelta(hours=20))
        scheduled = self.client.post(
            f"/agenda/{task['id']}/follow-up",
            data={
                "intent": "schedule",
                "item_count": "1",
                "items-0-title": "Llamar a Carolina",
                "items-0-task_type": "call",
                "items-0-due_date": due_date,
                "items-0-due_time": due_time,
                "items-0-contact_name": "Carolina",
                "items-0-item_status": "ready",
                "items-0-date_found": "1",
                "items-0-time_found": "1",
                "items-0-candidates": "[]",
            },
        )
        self.assertEqual(scheduled.status_code, 302)
        agenda = build_agenda_view(self.org_a, agent_id=self.agent_a)
        titles = [
            item["title"]
            for section in agenda["sections"]
            for item in section["tasks"]
        ]
        self.assertIn("Llamar a Carolina", titles)

    def test_59_completed_visit_without_outcome_is_recommended(self):
        task = self._create(title="Visita huérfana")
        complete_task(
            self.org_a,
            task["id"],
            agent_id=self.agent_a,
            actor_user_id=self.agent_user,
        )
        self._login("agenda_agent_user")
        page = self.client.get("/agenda")
        body = page.get_data(as_text=True)
        self.assertIn("Tenés una visita sin seguimiento.", body)
        self.assertIn("Completar seguimiento", body)
        self.assertIn(f"/agenda/{task['id']}/follow-up", body)

        foreign = self._login("agenda_agent_other")
        other_page = self.client.get("/agenda")
        self.assertNotIn(
            "Tenés una visita sin seguimiento.",
            other_page.get_data(as_text=True),
        )

    def test_60_follow_up_stays_in_agent_scope(self):
        task = self._create(title="Visita ajena")
        self._login("agenda_agent_other")
        blocked = self.client.get(f"/agenda/{task['id']}/follow-up")
        self.assertEqual(blocked.status_code, 404)

        self._login("agenda_admin_a")
        staff = self.client.get(f"/agenda/{task['id']}/follow-up")
        self.assertIn(staff.status_code, (302, 403))
        self.assertNotIn("Escribir resumen", staff.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()


