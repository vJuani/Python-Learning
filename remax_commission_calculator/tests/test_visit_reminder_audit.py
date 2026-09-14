"""Agenda visit reminder window, timezone, type aliases, and QA scan."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_visit_reminder_audit.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"] = (
    "BLHzS7-GUIMvv5rGcFxwpxtcttCaUmmBzhD_dDS3eLflNLO8-PHEgraQwaQyDm3lhngbjsWCLKVIJwIQPP9myWc"
)
os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgYy/9nRYvDz0vEk0u\n"
    "hHXPX2yFZHGT77ubToDeVI5+/9qhRANCAASx80u/hlCDL7+axnBccKcbXLbQmlJp\n"
    "gc4Q/3Q0t3i35TSzvPjxxIK2kMGkMg5t5YZ4G47FgiylSCcCEDz/Zsln\n"
    "-----END PRIVATE KEY-----"
)
os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY_B64", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
)
from modules.database.agent_tasks_repository import (
    STATUS_CANCELLED,
    create_agent_task,
    set_agent_task_status,
)
from modules.database.notifications_repository import find_notification_by_event_key
from modules.database.user_notification_preferences_repository import (
    get_user_notification_preferences,
    save_user_notification_preferences,
)
from modules.organization_time import UTC, to_utc_iso
from modules.visit_reminders import (
    VISIT_TYPE,
    clear_visit_reminder_dedupe,
    in_reminder_window,
    is_visit_task_type,
    normalize_visit_task_type,
    resolve_visit_recipient,
    scan_visit_reminders,
    visit_reminder_event_key,
)
from web_app import app

ART = ZoneInfo("America/Argentina/Buenos_Aires")


class VisitReminderAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="visit-reminder-audit")
        create_tables()
        cls.org = add_organization("Visit Audit Org")
        pwd = hash_password("Password1")
        cls.admin_id = add_user("admin_visit_qa", pwd, ROLE_ADMIN, cls.org)
        cls.agent_id = add_agent("Ana Visit", "Alto", cls.org)
        cls.other_agent_id = add_agent("Otro Visit", "Alto", cls.org)
        cls.user_id = add_user(
            "ana_visit_qa",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Visit",
        )
        cls.other_user_id = add_user(
            "otro_visit_qa",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent_id,
        )
        cls._seq = 0

    def setUp(self):
        self.client = app.test_client()
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_visit_reminders=True,
        )

    def _now(self):
        type(self)._seq += 1
        return datetime(2026, 10, 1, 17, 0, tzinfo=UTC) + timedelta(
            days=type(self)._seq
        )

    def _login(self, user_id, role):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = self.org

    def _visit(self, due_at, *, agent_id=None, task_type="visit", title="Visita QA"):
        return create_agent_task(
            self.org,
            agent_id or self.agent_id,
            title=title,
            task_type=task_type,
            due_at=due_at,
        )

    def test_type_aliases_normalize_to_visit(self):
        for alias in ("visit", "property_visit", "showing", "visita", "visita_propiedad"):
            self.assertEqual(normalize_visit_task_type(alias), VISIT_TYPE)
            self.assertTrue(is_visit_task_type(alias))
        self.assertFalse(is_visit_task_type("meeting"))
        self.assertFalse(is_visit_task_type("follow_up"))

    def test_window_30m_in_10m_and_60m_out(self):
        now = datetime(2026, 9, 14, 17, 0, tzinfo=UTC)
        self.assertTrue(
            in_reminder_window(to_utc_iso(now + timedelta(minutes=30)), now)
        )
        self.assertTrue(
            in_reminder_window(to_utc_iso(now + timedelta(minutes=25)), now)
        )
        self.assertTrue(
            in_reminder_window(to_utc_iso(now + timedelta(minutes=35)), now)
        )
        self.assertFalse(
            in_reminder_window(to_utc_iso(now + timedelta(minutes=10)), now)
        )
        self.assertFalse(
            in_reminder_window(to_utc_iso(now + timedelta(minutes=60)), now)
        )

    def test_buenos_aires_14_00_visit_14_30_is_in_window(self):
        now_local = datetime(2026, 9, 14, 14, 0, tzinfo=ART)
        visit_local = datetime(2026, 9, 14, 14, 30, tzinfo=ART)
        due_at = to_utc_iso(visit_local)
        self.assertEqual(due_at, "2026-09-14T17:30:00")
        task = self._visit(due_at)
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            result = scan_visit_reminders(self.org, now=now_local)
        self.assertEqual(result["candidates_found"], 1)
        self.assertEqual(result["notifications_created"], 1)
        self.assertEqual(result["timezone"], "America/Argentina/Buenos_Aires")
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                visit_reminder_event_key(task["id"], due_at),
                user_id=self.user_id,
            )
        )
        mocked.assert_called_once()

    def test_plus_10_and_plus_60_do_not_send(self):
        now = self._now()
        self._visit(to_utc_iso(now + timedelta(minutes=10)), title="Cerca")
        self._visit(to_utc_iso(now + timedelta(minutes=60)), title="Lejos")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = scan_visit_reminders(self.org, now=now)
        self.assertEqual(result["candidates_found"], 0)
        self.assertEqual(result["notifications_created"], 0)
        mocked.assert_not_called()

    def test_cancelled_is_excluded(self):
        now = self._now()
        task = self._visit(to_utc_iso(now + timedelta(minutes=30)))
        set_agent_task_status(task["id"], self.org, status=STATUS_CANCELLED)
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = scan_visit_reminders(self.org, now=now)
        self.assertEqual(result["notifications_created"], 0)
        mocked.assert_not_called()

    def test_recipient_is_agent_user_not_agent_id(self):
        self.assertNotEqual(self.agent_id, self.user_id)
        now = self._now()
        task = self._visit(to_utc_iso(now + timedelta(minutes=30)))
        user = resolve_visit_recipient(task, self.org)
        self.assertEqual(user["id"], self.user_id)
        self.assertEqual(user["agent_id"], self.agent_id)
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = scan_visit_reminders(self.org, now=now)
        self.assertEqual(result["candidates"][0]["user_id"], self.user_id)
        self.assertNotEqual(result["candidates"][0]["user_id"], self.agent_id)

    def test_preference_default_on_and_off_skips_push(self):
        prefs = get_user_notification_preferences(self.org, self.other_user_id)
        self.assertTrue(prefs["push_visit_reminders"])
        now = self._now()
        self._visit(to_utc_iso(now + timedelta(minutes=30)))
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_visit_reminders=False,
        )
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = scan_visit_reminders(self.org, now=now)
        self.assertEqual(result["notifications_created"], 1)
        mocked.assert_not_called()
        self.assertEqual(result["candidates"][0]["skipped_reason"], "preference_off")

    def test_dedupe_and_reset(self):
        now = self._now()
        task = self._visit(to_utc_iso(now + timedelta(minutes=30)))
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            first = scan_visit_reminders(self.org, now=now)
            second = scan_visit_reminders(self.org, now=now)
        self.assertEqual(first["notifications_created"], 1)
        self.assertEqual(second["notifications_created"], 0)
        self.assertTrue(second["candidates"][0]["already_sent"])
        self.assertEqual(mocked.call_count, 1)
        deleted = clear_visit_reminder_dedupe(self.org, task["id"])
        self.assertGreaterEqual(deleted, 1)
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked_again:
            third = scan_visit_reminders(self.org, now=now)
        self.assertEqual(third["notifications_created"], 1)
        mocked_again.assert_called_once()

    def test_type_alias_from_query_is_kept(self):
        now = self._now()
        fake = {
            "id": 9001,
            "agent_id": self.agent_id,
            "title": "Showing",
            "task_type": "showing",
            "due_at": to_utc_iso(now + timedelta(minutes=30)),
            "status": "pending",
        }
        with patch(
            "modules.visit_reminders.list_agent_tasks",
            return_value=[fake],
        ), patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = scan_visit_reminders(self.org, now=now)
        self.assertEqual(result["candidates_found"], 1)
        self.assertEqual(result["candidates"][0]["type"], "showing")

    def test_push_failure_does_not_break_scan(self):
        now = self._now()
        self._visit(to_utc_iso(now + timedelta(minutes=30)))
        with patch(
            "modules.notifications_service.send_user_pushes",
            side_effect=RuntimeError("push down"),
        ):
            result = scan_visit_reminders(self.org, now=now)
        self.assertEqual(result["candidates_found"], 1)
        self.assertEqual(result["notifications_created"], 1)
        self.assertEqual(result["push_sent"], 0)
        self.assertGreaterEqual(result["candidates"][0]["failed_count"], 0)

    def test_manual_admin_scan_finds_candidate(self):
        now = self._now()
        due_at = to_utc_iso(now + timedelta(minutes=30))
        self._visit(due_at)
        self._login(self.user_id, ROLE_AGENT)
        denied = self.client.post("/settings/agenda-reminders")
        self.assertIn(denied.status_code, (302, 403))

        self._login(self.admin_id, ROLE_ADMIN)
        with patch(
            "modules.visit_reminders.now_utc",
            return_value=now,
        ), patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            page = self.client.post(
                "/settings/agenda-reminders",
                data={"action": "scan"},
            )
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("candidates_found=", html)
        self.assertIn("notifications_created=", html)
        self.assertIn("push_sent=", html)
        self.assertIn("last_cron_run=never", html)
        self.assertIn("automatic_running=False", html)

    def test_probe_creates_plus_30_visit_and_reports_fields(self):
        now = self._now()
        self._login(self.admin_id, ROLE_ADMIN)
        with patch(
            "modules.visit_reminders.now_utc",
            return_value=now,
        ), patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            page = self.client.post(
                "/settings/agenda-reminders",
                data={"action": "probe"},
            )
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("candidates_found=1", html)
        self.assertIn("notification_created", html)
        self.assertIn("minutes_until_start", html)
        self.assertIn("skip_reason", html)
        self.assertIn("resolved_user_id", html)
        self.assertIn("candidatos encontrados=1", html)
        self.assertIn("enviados=1", html)

    def test_scan_report_exposes_exact_fields(self):
        now = self._now()
        due_at = to_utc_iso(now + timedelta(minutes=30))
        task = self._visit(due_at)
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = scan_visit_reminders(self.org, now=now)
        row = result["candidates"][0]
        self.assertEqual(result["timezone"], "America/Argentina/Buenos_Aires")
        self.assertTrue(result["now_local"])
        self.assertEqual(row["event_id"], task["id"])
        self.assertEqual(row["event_type"], "visit")
        self.assertEqual(row["event_status"], "pending")
        self.assertEqual(row["starts_at"], due_at)
        self.assertAlmostEqual(row["minutes_until_start"], 30.0)
        self.assertEqual(row["assigned_user_id"], self.user_id)
        self.assertEqual(row["resolved_user_id"], self.user_id)
        self.assertTrue(row["push_visit_reminders"])
        self.assertEqual(
            row["dedupe_key"],
            visit_reminder_event_key(task["id"], due_at),
        )
        self.assertFalse(row["already_sent"])
        self.assertTrue(row["candidate"])
        self.assertIsNone(row["skip_reason"])
        self.assertTrue(row["notification_created"])
        self.assertEqual(row["push_targets_count"], 1)
        self.assertEqual(row["push_sent_count"], 1)
        self.assertEqual(row["push_failed_count"], 0)

    def test_probe_does_not_touch_other_organization(self):
        other = add_organization("Other Visit Org")
        other_agent = add_agent("Other Agent", "Alto", other)
        now = self._now()
        create_agent_task(
            other,
            other_agent,
            title="Foreign visit",
            task_type="visit",
            due_at=to_utc_iso(now + timedelta(minutes=30)),
        )
        from modules.database.agent_tasks_repository import list_agent_tasks

        before = len(list_agent_tasks(other, limit=50))
        self._login(self.admin_id, ROLE_ADMIN)
        with patch(
            "modules.visit_reminders.now_utc",
            return_value=now,
        ), patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            page = self.client.post(
                "/settings/agenda-reminders",
                data={"action": "probe"},
            )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(list_agent_tasks(other, limit=50)), before)

    def test_inprocess_scheduler_starts_only_when_enabled(self):
        from modules.visit_reminder_scheduler import (
            inprocess_scheduler_state,
            start_visit_reminder_scheduler,
            stop_visit_reminder_scheduler,
        )

        stop_visit_reminder_scheduler()
        self.assertFalse(
            start_visit_reminder_scheduler(enabled=False, first_delay_seconds=30)
        )
        with patch("modules.visit_reminder_scheduler._tick"):
            started = start_visit_reminder_scheduler(
                enabled=True,
                interval_seconds=30,
                first_delay_seconds=30,
            )
            self.assertTrue(started)
            self.assertTrue(inprocess_scheduler_state()["alive"])
            stop_visit_reminder_scheduler()
        self.assertFalse(inprocess_scheduler_state()["alive"])


if __name__ == "__main__":
    unittest.main()
