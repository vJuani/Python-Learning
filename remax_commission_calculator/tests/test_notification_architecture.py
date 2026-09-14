"""Central notification service, event bus, agenda reminder windows, prefs."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_notification_architecture.db")
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
os.environ["NOTIFICATION_JOB_SECRET"] = "job-secret"

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.agent_tasks_repository import create_agent_task
from modules.database.notifications_repository import (
    count_unread_notifications,
    find_notification_by_event_key,
    list_notifications,
)
from modules.database.push_subscriptions_repository import (
    list_active_push_subscriptions,
    upsert_push_subscription,
)
from modules.database.user_notification_preferences_repository import (
    save_user_notification_preferences,
)
from modules.notifications.events import emit_event
from modules.notifications.service import notify_user
from modules.organization_time import UTC, now_utc, to_utc_iso
from modules.visit_reminders import (
    in_reminder_window,
    scan_visit_reminders,
    visit_reminder_event_key,
)
from web_app import app

ART = ZoneInfo("America/Argentina/Buenos_Aires")


class NotificationArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="notif-arch")
        create_tables()
        cls.org = add_organization("Arch Org")
        cls.org_b = add_organization("Arch Org B")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Ana Arch", "Alto", cls.org)
        cls.agent_b = add_agent("Beta Arch", "Alto", cls.org_b)
        cls.user_id = add_user(
            "ana_arch",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Arch",
        )
        cls.user_b = add_user(
            "beta_arch",
            pwd,
            ROLE_AGENT,
            cls.org_b,
            agent_id=cls.agent_b,
        )
        cls.admin_id = add_user("admin_arch", pwd, ROLE_ADMIN, cls.org)

    def test_notify_user_creates_inbox_and_priority(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = notify_user(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita en 30 minutos",
                "Libertador 100",
                "/agenda/1/edit",
                event_key="agenda_event_arch_reminder_30m",
                minutes_until=30,
            )
        self.assertTrue(result["created"])
        self.assertEqual(result["priority"], "important")
        self.assertTrue(result["pushed"])
        notes = list_notifications(self.user_id, self.org)
        item = next(
            row
            for row in notes
            if row["payload"].get("event_key") == "agenda_event_arch_reminder_30m"
        )
        self.assertEqual(item["priority"], "important")
        self.assertEqual(item["payload"]["url"], "/agenda/1/edit")

    def test_five_minute_reminder_is_urgent(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 0, "failed_count": 0, "push_targets_count": 0},
        ):
            result = notify_user(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita en 5 minutos",
                "Ya",
                "/agenda/2/edit",
                event_key="agenda_event_arch_reminder_5m",
                minutes_until=5,
            )
        self.assertEqual(result["priority"], "urgent")

    def test_emit_event_invoice_ready(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = emit_event(
                "operation.invoice_ready",
                {
                    "organization_id": self.org,
                    "user_id": self.user_id,
                    "title": "Ya podés facturar",
                    "body": "Casa",
                    "url": "/billing?tab=pending",
                    "event_key": "operation_555_invoice_ready",
                    "entity_id": 555,
                },
            )
        self.assertTrue(result["created"])
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                "operation_555_invoice_ready",
                user_id=self.user_id,
            )
        )

    def test_dedupe_same_event_key(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            first = notify_user(
                self.user_id,
                self.org,
                "property_match",
                "Match",
                "Need",
                "/contacts/80/property-matches",
                event_key="need_80_property_420_match",
            )
            second = notify_user(
                self.user_id,
                self.org,
                "property_match",
                "Match",
                "Need",
                "/contacts/80/property-matches",
                event_key="need_80_property_420_match",
            )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["deduped"])
        self.assertEqual(mocked.call_count, 1)

    def test_preference_off_keeps_inbox_skips_push(self):
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_visit_reminders=False,
        )
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = notify_user(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita",
                "Body",
                "/agenda/3/edit",
                event_key="agenda_event_pref_off",
            )
        self.assertTrue(result["created"])
        mocked.assert_not_called()
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_visit_reminders=True,
        )

    def test_cross_tenant_blocked(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            notify_user(
                self.user_id,
                self.org,
                "office_announcement",
                "Aviso",
                "Solo org A",
                "/notifications",
                event_key="office_announcement_org_a",
            )
        self.assertIsNone(
            find_notification_by_event_key(
                self.org_b,
                "office_announcement_org_a",
                user_id=self.user_b,
            )
        )
        self.assertEqual(count_unread_notifications(self.user_b, self.org_b), 0)

    def test_push_failure_does_not_break_action(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            side_effect=RuntimeError("push down"),
        ):
            result = notify_user(
                self.user_id,
                self.org,
                "task_overdue",
                "Tarea vencida",
                "Llamar",
                "/agenda/9/edit",
                event_key="task_90_overdue_2026-09-14",
            )
        self.assertTrue(result["created"])
        self.assertFalse(result["pushed"])

    def test_gone_subscription_deactivates(self):
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example/arch-gone",
            p256dh="B" * 20,
            auth="A" * 16,
            user_agent="Test",
            device_label="Phone",
        )
        with patch(
            "modules.web_push.send_web_push",
            return_value={"ok": False, "status": 410, "gone": True},
        ):
            notify_user(
                self.user_id,
                self.org,
                "office_announcement",
                "Aviso",
                "410",
                "/notifications",
                event_key="office_announcement_gone",
            )
        self.assertEqual(list_active_push_subscriptions(self.org, self.user_id), [])

    def test_click_url_is_internal_open(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            result = notify_user(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita",
                "Click",
                "/agenda/12/edit",
                event_key="agenda_event_click_url",
            )
        self.assertEqual(
            mocked.call_args.args[2]["url"],
            f"/notifications/{result['notification_id']}/open",
        )
        self.assertEqual(mocked.call_args.args[2]["type"], "visit_reminder")

    def test_badge_counts_unread(self):
        before = count_unread_notifications(self.user_id, self.org)
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 0, "failed_count": 0, "push_targets_count": 0},
        ):
            notify_user(
                self.user_id,
                self.org,
                "property_match",
                "Match",
                "Badge",
                "/contacts/1/property-matches",
                event_key="need_1_property_2_match",
            )
        self.assertEqual(count_unread_notifications(self.user_id, self.org), before + 1)

    def test_reminder_minutes_15_fires_and_30_does_not(self):
        now = datetime(2026, 9, 14, 17, 0, tzinfo=UTC)
        due_15 = to_utc_iso(now + timedelta(minutes=15))
        fifteen = create_agent_task(
            self.org,
            self.agent_id,
            title="Visita 15",
            task_type="visit",
            due_at=due_15,
            reminder_minutes=15,
        )
        create_agent_task(
            self.org,
            self.agent_id,
            title="Visita con reminder 30",
            task_type="visit",
            due_at=due_15,
            reminder_minutes=30,
        )
        self.assertTrue(in_reminder_window(due_15, now, 15))
        self.assertFalse(in_reminder_window(due_15, now, 30))
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = scan_visit_reminders(self.org, now=now)
        ids = [row["event_id"] for row in result["candidates"] if row.get("candidate")]
        self.assertIn(fifteen["id"], ids)
        self.assertEqual(result["notifications_created"], 1)
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                visit_reminder_event_key(fifteen["id"], due_15, 15),
                user_id=self.user_id,
            )
        )

    def test_meeting_with_reminder_is_candidate(self):
        now = datetime(2026, 9, 15, 17, 0, tzinfo=UTC)
        due_at = to_utc_iso(now + timedelta(minutes=30))
        meeting = create_agent_task(
            self.org,
            self.agent_id,
            title="Reunión",
            task_type="meeting",
            due_at=due_at,
            reminder_minutes=30,
        )
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = scan_visit_reminders(self.org, now=now)
        ids = [row["event_id"] for row in result["candidates"] if row.get("candidate")]
        self.assertIn(meeting["id"], ids)

    def test_timezone_buenos_aires(self):
        now_local = datetime(2026, 9, 16, 14, 0, tzinfo=ART)
        visit_local = datetime(2026, 9, 16, 14, 30, tzinfo=ART)
        due_at = to_utc_iso(visit_local)
        self.assertEqual(due_at, "2026-09-16T17:30:00")
        task = create_agent_task(
            self.org,
            self.agent_id,
            title="Visita AR",
            task_type="visit",
            due_at=due_at,
        )
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = scan_visit_reminders(self.org, now=now_local)
        self.assertEqual(result["timezone"], "America/Argentina/Buenos_Aires")
        self.assertEqual(result["notifications_created"], 1)
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                visit_reminder_event_key(task["id"], due_at, 30),
                user_id=self.user_id,
            )
        )

    def test_http_tick_requires_secret(self):
        client = app.test_client()
        denied = client.post("/internal/jobs/notifications/tick")
        self.assertEqual(denied.status_code, 404)
        with patch(
            "modules.notifications.jobs.run_notification_jobs",
            return_value={
                "now": to_utc_iso(now_utc()),
                "agenda_created": 0,
                "overdue_created": 0,
            },
        ):
            ok = client.post(
                "/internal/jobs/notifications/tick",
                headers={"X-Job-Secret": "job-secret"},
            )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
