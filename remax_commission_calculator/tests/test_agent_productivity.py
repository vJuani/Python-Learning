"""FASE 5A — Agent-only productivity and goals."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_productivity.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_productivity import (  # noqa: E402
    ProductivityError,
    build_productivity_view,
    jrh_productivity_answer,
    progress_ratio,
    propose_logged_activity,
    require_productivity_agent,
    save_goals,
)
from modules.agent_tasks import cancel_task, complete_task, create_task  # noqa: E402
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password  # noqa: E402
from modules.config import apply_config  # noqa: E402
from modules.database import (  # noqa: E402
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.agent_goals_migration import (  # noqa: E402
    migrate_agent_goals_sqlite,
)
from modules.database.connection import get_connection  # noqa: E402
from modules.database.operations_repository import add_operation  # noqa: E402
from modules.database.properties_repository import STATUS_APPROVED  # noqa: E402
from modules.database.schema import create_tables as create_tables_again  # noqa: E402
from modules.jrh_ai_intents import QUERY_PRODUCTIVITY  # noqa: E402
from modules.jrh_ai_service import ask_jrh  # noqa: E402
from modules.organization_time import now_utc  # noqa: E402
from web_app import app  # noqa: E402


FIXED_NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)


class AgentProductivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="prod-tests")
        create_tables()
        cls.org = add_organization("Prod Org")
        cls.other_org = add_organization("Prod Other")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana Prod", "Alto", cls.org)
        cls.other_agent = add_agent("Otro Prod", "Alto", cls.org)
        cls.foreign_agent = add_agent("Foreign Prod", "Alto", cls.other_org)
        cls.empty_agent = add_agent("Empty Prod", "Alto", cls.org)
        cls.agent_user = add_user(
            "prod_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.other_user = add_user(
            "prod_other",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.foreign_user = add_user(
            "prod_foreign",
            pwd,
            ROLE_AGENT,
            cls.other_org,
            agent_id=cls.foreign_agent,
        )
        cls.empty_user = add_user(
            "prod_empty",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.empty_agent,
        )
        cls.admin = add_user("prod_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_record = {
            "id": cls.agent_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.agent_id,
        }
        cls.other_record = {
            "id": cls.other_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.other_agent,
        }
        cls.admin_record = {
            "id": cls.admin,
            "role": ROLE_ADMIN,
            "organization_id": cls.org,
        }
        cls.foreign_record = {
            "id": cls.foreign_user,
            "role": ROLE_AGENT,
            "organization_id": cls.other_org,
            "agent_id": cls.foreign_agent,
        }
        cls.empty_record = {
            "id": cls.empty_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.empty_agent,
        }
        cls.property_id = add_property(
            "Italia 1341",
            "PBA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=320000,
            listing_purpose="sale",
            listing_currency="USD",
            neighborhood="Martinez",
            rooms=4,
            bedrooms=3,
            covered_m2=131,
            total_m2=131,
        )

    def _login(self, user_id, role, org):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = org
        return client

    def _task(self, task_type, *, agent_id=None, title="Tarea"):
        due = (FIXED_NOW + timedelta(hours=2)).date().isoformat()
        return create_task(
            self.org,
            agent_id if agent_id is not None else self.agent_id,
            {
                "title": title,
                "task_type": task_type,
                "priority": "normal",
                "due_date": due,
                "due_time": "18:00",
            },
            created_by_user_id=self.agent_user,
        )

    def _complete(self, task, when=None):
        complete_task(
            self.org,
            task["id"],
            agent_id=task["agent_id"],
            actor_user_id=self.agent_user,
        )
        if when is not None:
            connection = get_connection()
            try:
                connection.execute(
                    "UPDATE agent_tasks SET completed_at = ? WHERE id = ?",
                    (when.replace(microsecond=0).isoformat(), task["id"]),
                )
                connection.commit()
            finally:
                connection.close()

    def _insert_movement(self, *, currency, amount, movement_type, movement_date):
        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO agent_account_movements (
                    organization_id, agent_id, movement_type, currency, amount,
                    description, balance_before, balance_after, status,
                    movement_date, created_by_user_id, created_at,
                    is_internal_reversal
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'confirmed', ?, ?, ?, 0)
                """,
                (
                    self.org,
                    self.agent_id,
                    movement_type,
                    currency,
                    str(amount),
                    "test",
                    str(amount),
                    movement_date,
                    self.agent_user,
                    FIXED_NOW.replace(tzinfo=None).isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_01_agent_configures_goal(self):
        saved = save_goals(
            self.org,
            user=self.agent_record,
            items=[{"metric_key": "contacts_called", "period_type": "daily", "target_value": "15"}],
        )
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["metric_key"], "contacts_called")
        self.assertEqual(saved[0]["target_value"], "15")

    def test_02_agent_sees_own_goals_only(self):
        save_goals(
            self.org,
            user=self.agent_record,
            items=[{"metric_key": "followups_completed", "period_type": "daily", "target_value": "5"}],
        )
        save_goals(
            self.org,
            user=self.other_record,
            items=[{"metric_key": "visits_completed", "period_type": "weekly", "target_value": "6"}],
        )
        mine = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        other = build_productivity_view(self.org, user=self.other_record, now=FIXED_NOW)
        self.assertTrue(any(goal["metric_key"] == "followups_completed" for goal in mine["goals"]))
        self.assertFalse(any(goal["metric_key"] == "visits_completed" for goal in mine["goals"]))
        self.assertTrue(any(goal["metric_key"] == "visits_completed" for goal in other["goals"]))

    def test_03_staff_admin_403(self):
        with self.assertRaises(ProductivityError) as caught:
            require_productivity_agent(self.admin_record)
        self.assertEqual(caught.exception.status_code, 403)
        client = self._login(self.admin, ROLE_ADMIN, self.org)
        self.assertEqual(client.get("/productivity").status_code, 403)
        self.assertEqual(client.get("/productivity/goals").status_code, 403)

    def test_04_other_org_blocked(self):
        with self.assertRaises(ProductivityError) as caught:
            save_goals(
                self.org,
                user=self.foreign_record,
                items=[{"metric_key": "contacts_called", "period_type": "daily", "target_value": "9"}],
            )
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(ProductivityError):
            build_productivity_view(self.org, user=self.foreign_record, now=FIXED_NOW)
        view = build_productivity_view(
            self.other_org, user=self.foreign_record, now=FIXED_NOW
        )
        self.assertEqual(view["activity"]["contacts_called"], 0)

    def test_05_completed_call_counts(self):
        task = self._task("call", title="Llamar")
        self._complete(task, when=FIXED_NOW)
        with patch("modules.agent_productivity.now_utc", return_value=FIXED_NOW):
            view = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        self.assertGreaterEqual(view["activity"]["contacts_called"], 1)

    def test_06_pending_call_does_not_count(self):
        before = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        self._task("call", title="Pendiente")
        after = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        self.assertEqual(after["activity"]["contacts_called"], before["activity"]["contacts_called"])

    def test_07_followup_completed_counts(self):
        task = self._task("follow_up", title="Seguimiento")
        self._complete(task, when=FIXED_NOW)
        view = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        self.assertGreaterEqual(view["activity"]["followups_completed"], 1)

    def test_08_visit_completed_counts(self):
        task = self._task("visit", title="Visita")
        self._complete(task, when=FIXED_NOW)
        view = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        self.assertGreaterEqual(view["activity"]["visits_completed"], 1)

    def test_09_cancelled_does_not_count(self):
        before = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        task = self._task("call", title="Cancelada")
        cancel_task(self.org, task["id"], agent_id=self.agent_id, actor_user_id=self.agent_user)
        after = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        self.assertEqual(after["activity"]["contacts_called"], before["activity"]["contacts_called"])

    def test_10_daily_period(self):
        view = build_productivity_view(
            self.org, user=self.agent_record, period="daily", now=FIXED_NOW
        )
        self.assertEqual(view["period"], "daily")
        self.assertEqual(view["bounds"]["start_local"], "2026-09-08")

    def test_11_weekly_period(self):
        view = build_productivity_view(
            self.org, user=self.agent_record, period="weekly", now=FIXED_NOW
        )
        self.assertEqual(view["period"], "weekly")
        self.assertEqual(view["bounds"]["start_local"], "2026-09-07")

    def test_12_monthly_period(self):
        view = build_productivity_view(
            self.org, user=self.agent_record, period="monthly", now=FIXED_NOW
        )
        self.assertEqual(view["period"], "monthly")
        self.assertEqual(view["bounds"]["start_local"], "2026-09-01")

    def test_13_progress_percent(self):
        self.assertEqual(progress_ratio(4, 10), Decimal("40"))

    def test_14_over_target_does_not_break(self):
        save_goals(
            self.org,
            user=self.agent_record,
            items=[{"metric_key": "tasks_completed", "period_type": "daily", "target_value": "1"}],
        )
        task = self._task("other", title="Extra")
        self._complete(task, when=FIXED_NOW)
        view = build_productivity_view(self.org, user=self.agent_record, now=FIXED_NOW)
        row = next(item for item in view["rows"] if item["metric_key"] == "tasks_completed")
        self.assertGreaterEqual(row["percent"], 100)
        self.assertLessEqual(row["bar_percent"], 100)
        html = self._login(self.agent_user, ROLE_AGENT, self.org).get("/productivity").get_data(as_text=True)
        self.assertIn("prod-bar", html)

    def test_15_goal_without_activity_is_zero(self):
        save_goals(
            self.org,
            user=self.other_record,
            items=[{"metric_key": "meetings_completed", "period_type": "daily", "target_value": "3"}],
        )
        view = build_productivity_view(self.org, user=self.other_record, now=FIXED_NOW)
        row = next(item for item in view["rows"] if item["metric_key"] == "meetings_completed")
        self.assertEqual(row["current"], Decimal("0"))

    def test_16_agent_without_goals_sees_activity(self):
        view = build_productivity_view(self.org, user=self.empty_record, now=FIXED_NOW)
        self.assertFalse(view["has_goals"])
        self.assertIn("contacts_called", view["activity"])
        html = self._login(self.empty_user, ROLE_AGENT, self.org).get("/productivity").get_data(as_text=True)
        self.assertIn("prod-empty", html)
        self.assertIn("objetivos", html)

    def test_17_acm_created_counts(self):
        from modules.acm_service import create_acm_for_property

        before = build_productivity_view(
            self.org, user=self.agent_record, period="monthly", now=FIXED_NOW
        )
        create_acm_for_property(
            self.org,
            user=self.agent_record,
            property_id=self.property_id,
            language="es",
        )
        after = build_productivity_view(
            self.org, user=self.agent_record, period="monthly", now=FIXED_NOW
        )
        self.assertGreater(after["activity"]["acms_created"], before["activity"]["acms_created"])

    def test_18_operations_closed_uses_real_status(self):
        add_operation(
            "08/09/2026",
            self.agent_id,
            self.property_id,
            "yes",
            0,
            200000,
            3,
            6000,
            6000,
            0,
            0,
            0,
            0,
            0,
            self.org,
        )
        view = build_productivity_view(
            self.org, user=self.agent_record, period="monthly", now=FIXED_NOW
        )
        self.assertGreaterEqual(view["activity"]["operations_closed"], 1)

    def test_19_commissions_separated_by_currency(self):
        self._insert_movement(
            currency="USD",
            amount="3250",
            movement_type="commission",
            movement_date="2026-09-08",
        )
        self._insert_movement(
            currency="ARS",
            amount="450000",
            movement_type="commission",
            movement_date="2026-09-08",
        )
        view = build_productivity_view(
            self.org, user=self.agent_record, period="monthly", now=FIXED_NOW
        )
        commissions = view["activity"]["commissions_credited"]
        self.assertIn("USD", commissions)
        self.assertIn("ARS", commissions)
        self.assertEqual(commissions["USD"], Decimal("3250"))
        self.assertEqual(commissions["ARS"], Decimal("450000"))
        self.assertTrue(any("USD" in item for item in view["money"]["commissions"]))
        self.assertTrue(any("ARS" in item for item in view["money"]["commissions"]))

    def test_20_jrh_uses_backend_metrics(self):
        save_goals(
            self.org,
            user=self.agent_record,
            items=[
                {"metric_key": "contacts_called", "period_type": "daily", "target_value": "10"},
                {"metric_key": "followups_completed", "period_type": "daily", "target_value": "5"},
            ],
        )
        result = ask_jrh(
            "cómo vengo hoy?",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            language="es",
            session={},
        )
        self.assertEqual(result["intent"], QUERY_PRODUCTIVITY)
        self.assertIn("llamadas", result["message"].lower())
        self.assertIn("activity", result["data"])

    def test_21_timezone_uses_org_local_day(self):
        late_utc = datetime(2026, 9, 9, 1, 30, tzinfo=timezone.utc)
        task = self._task("call", title="Noche ART")
        self._complete(task, when=late_utc)
        view = build_productivity_view(self.org, user=self.agent_record, now=late_utc)
        self.assertEqual(view["bounds"]["start_local"], "2026-09-08")
        self.assertGreaterEqual(view["activity"]["contacts_called"], 1)

    def test_22_dashboard_mobile_200(self):
        client = self._login(self.agent_user, ROLE_AGENT, self.org)
        response = client.get("/productivity")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("prod-hero", html)
        self.assertIn("Hoy", html)
        self.assertIn("prod-gauges", html)
        self.assertIn("Contale a JRH", html)
        self.assertIn("Qué te falta hoy", html)

    def test_25_jrh_log_parses_real_channels(self):
        proposals = propose_logged_activity(
            "Hoy hablé con Ro y me junté a tomar un café con ella",
            language="es",
        )
        channels = {item["channel"] for item in proposals}
        self.assertIn("call", channels)
        self.assertIn("meeting", channels)
        self.assertTrue(all(item["contact_name"] == "Ro" for item in proposals))

    def test_23_migration_idempotent(self):
        migrate_agent_goals_sqlite()
        migrate_agent_goals_sqlite()

    def test_24_create_tables_twice(self):
        create_tables_again(create_backup=False)
        create_tables_again(create_backup=False)


if __name__ == "__main__":
    unittest.main()
