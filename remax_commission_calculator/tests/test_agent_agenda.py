"""Phase 4B agent agenda and follow-up tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

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
        self.assertIn("agenda-card", body)
        self.assertIn("mobile-fab", body)
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


if __name__ == "__main__":
    unittest.main()
