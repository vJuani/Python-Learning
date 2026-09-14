"""Phase 3: real push events (visit, invoice-ready, property match). Isolated temp DB."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_push_events.db")
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
from modules.contacts import create_agent_contact
from modules.database import (
    add_agent,
    add_operation,
    add_organization,
    add_property,
    add_user,
    create_tables,
    get_property_record,
)
from modules.database.agent_tasks_repository import (
    STATUS_CANCELLED,
    create_agent_task,
    set_agent_task_status,
)
from modules.database.notifications_repository import (
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
from modules.invoicing import set_party_invoice_amount
from modules.notifications_service import (
    send_user_notification,
)
from modules.organization_time import now_utc, to_utc_iso
from modules.visit_reminders import (
    dispatch_due_visit_reminders,
    visit_reminder_event_key,
)
from web_app import app


class PushEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="push-events-tests")
        create_tables()
        cls.org = add_organization("Push Events Org")
        cls.org_b = add_organization("Push Events Org B")
        cls.agent_id = add_agent("Ana Perez", "Alto", cls.org)
        cls.other_agent_id = add_agent("Otro Agente", "Alto", cls.org)
        cls.agent_b = add_agent("Agente B", "Alto", cls.org_b)
        pwd = hash_password("Password1")
        cls.user_id = add_user(
            "ana_push",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Perez",
        )
        cls.other_user_id = add_user(
            "otro_push",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent_id,
            first_name="Otro",
            last_name="Agente",
        )
        cls.admin_id = add_user("admin_push", pwd, ROLE_ADMIN, cls.org)
        cls.user_b = add_user(
            "agent_b_push",
            pwd,
            ROLE_AGENT,
            cls.org_b,
            agent_id=cls.agent_b,
        )
        cls.property_id = add_property(
            "Santamarina 1335",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            property_type="apartment",
            neighborhood="Palermo",
            rooms=3,
            bedrooms=2,
            listing_price=180000,
            listing_currency="USD",
            listing_purpose="sale",
        )

    def setUp(self):
        self.client = app.test_client()
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_visit_reminders=True,
            push_invoice_ready=True,
            push_property_matches=True,
        )

    def _count_kind(self, user_id, kind, organization_id=None):
        return sum(
            1
            for item in list_notifications(user_id, organization_id or self.org)
            if item["kind"] == kind
        )

    def _login(self, user_id=None, role=ROLE_AGENT):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id or self.user_id
            session["role"] = role
            session["organization_id"] = self.org

    def _kinds(self, user_id):
        return [item["kind"] for item in list_notifications(user_id, self.org)]

    def _subscribe(self, user_id, suffix="1"):
        return upsert_push_subscription(
            self.org,
            user_id,
            endpoint=f"https://push.example.com/events-{user_id}-{suffix}",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )

    def _visit_at(self, minutes, *, agent_id=None, status=None):
        due_at = to_utc_iso(now_utc() + timedelta(minutes=minutes))
        task = create_agent_task(
            self.org,
            agent_id or self.agent_id,
            title="Visita Santamarina",
            task_type="visit",
            due_at=due_at,
            property_id=self.property_id,
            contact_name="Martín",
        )
        if status == STATUS_CANCELLED:
            set_agent_task_status(
                task["id"],
                self.org,
                status=STATUS_CANCELLED,
            )
            from modules.database.agent_tasks_repository import get_agent_task

            task = get_agent_task(task["id"], self.org)
        return task

    def _operation(self, agent_id=None):
        return add_operation(
            "14/09/2026",
            agent_id or self.agent_id,
            self.property_id,
            "no",
            0,
            100000,
            3,
            3000,
            2700,
            300,
            1350,
            1350,
            0,
            1350,
            self.org,
        )

    def _matching_contact(self, agent_id=None, name="Martín Gómez", org=None):
        return create_agent_contact(
            org or self.org,
            agent_id or self.agent_id,
            {
                "name": name,
                "first_name": name.split()[0],
                "preferences": {
                    "areas": ["Palermo"],
                    "property_types": ["apartment"],
                    "rooms": 3,
                    "bedrooms": 2,
                },
            },
        )

    def test_visit_sends_30_minutes_before(self):
        task = self._visit_at(30)
        before = self._count_kind(self.user_id, "visit_reminder")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            result = dispatch_due_visit_reminders(self.org)
        self.assertEqual(result["dispatched"], 1)
        self.assertEqual(self._count_kind(self.user_id, "visit_reminder"), before + 1)
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                visit_reminder_event_key(task["id"], task["due_at"]),
                user_id=self.user_id,
            )
        )
        mocked.assert_called_once()
        payload = mocked.call_args.args[2]
        self.assertTrue(payload["url"].startswith("/notifications/"))
        self.assertTrue(payload["url"].endswith("/open"))
        self.assertEqual(payload["type"], "visit_reminder")
        self.assertIn("Santamarina 1335", payload["body"])

    def test_visit_skips_outside_window(self):
        self._visit_at(20)
        self._visit_at(40)
        before = self._count_kind(self.user_id, "visit_reminder")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = dispatch_due_visit_reminders(self.org)
        self.assertEqual(result["dispatched"], 0)
        mocked.assert_not_called()
        self.assertEqual(self._count_kind(self.user_id, "visit_reminder"), before)

    def test_visit_skips_cancelled(self):
        self._visit_at(30, status=STATUS_CANCELLED)
        before = self._count_kind(self.user_id, "visit_reminder")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = dispatch_due_visit_reminders(self.org)
        self.assertEqual(result["dispatched"], 0)
        mocked.assert_not_called()
        self.assertEqual(self._count_kind(self.user_id, "visit_reminder"), before)

    def test_visit_no_duplicate(self):
        self._visit_at(30)
        before = self._count_kind(self.user_id, "visit_reminder")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            first = dispatch_due_visit_reminders(self.org)
            second = dispatch_due_visit_reminders(self.org)
        self.assertEqual(first["dispatched"], 1)
        self.assertEqual(second["dispatched"], 0)
        self.assertEqual(self._count_kind(self.user_id, "visit_reminder"), before + 1)
        self.assertEqual(mocked.call_count, 1)

    def test_visit_only_assigned_agent(self):
        self._visit_at(30)
        other_before = self._count_kind(self.other_user_id, "visit_reminder")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            dispatch_due_visit_reminders(self.org)
        self.assertGreater(self._count_kind(self.user_id, "visit_reminder"), 0)
        self.assertEqual(
            self._count_kind(self.other_user_id, "visit_reminder"),
            other_before,
        )

    def test_visit_respects_preference_off(self):
        self._visit_at(30)
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_visit_reminders=False,
        )
        before = self._count_kind(self.user_id, "visit_reminder")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = dispatch_due_visit_reminders(self.org)
        self.assertEqual(result["dispatched"], 1)
        self.assertEqual(self._count_kind(self.user_id, "visit_reminder"), before + 1)
        mocked.assert_not_called()

    def test_invoice_sends_on_enable(self):
        op_id = self._operation()
        before = self._count_kind(self.user_id, "operation_side_ready_to_invoice")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            set_party_invoice_amount(
                self.org,
                op_id,
                "buyer",
                "100000",
                "ARS",
                None,
                self.admin_id,
            )
        self.assertEqual(
            self._count_kind(self.user_id, "operation_side_ready_to_invoice"),
            before + 1,
        )
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                f"operation_{op_id}_buyer_ready_to_invoice",
                user_id=self.user_id,
            )
        )
        mocked.assert_called_once()
        self.assertTrue(mocked.call_args.args[2]["url"].startswith("/notifications/"))
        self.assertTrue(mocked.call_args.args[2]["url"].endswith("/open"))

    def test_invoice_skips_save_without_state_change(self):
        op_id = self._operation()
        before = self._count_kind(self.user_id, "operation_side_ready_to_invoice")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            set_party_invoice_amount(
                self.org, op_id, "buyer", "100000", "ARS", None, self.admin_id
            )
            set_party_invoice_amount(
                self.org, op_id, "buyer", "120000", "ARS", None, self.admin_id
            )
        self.assertEqual(
            self._count_kind(self.user_id, "operation_side_ready_to_invoice"),
            before + 1,
        )
        self.assertEqual(mocked.call_count, 1)

    def test_invoice_only_related_agent(self):
        op_id = self._operation()
        other_before = self._count_kind(
            self.other_user_id, "operation_side_ready_to_invoice"
        )
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            set_party_invoice_amount(
                self.org, op_id, "seller", "80000", "ARS", None, self.admin_id
            )
        self.assertGreater(
            self._count_kind(self.user_id, "operation_side_ready_to_invoice"),
            0,
        )
        self.assertEqual(
            self._count_kind(self.other_user_id, "operation_side_ready_to_invoice"),
            other_before,
        )

    def test_invoice_respects_preference_off(self):
        op_id = self._operation()
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_invoice_ready=False,
        )
        before = self._count_kind(self.user_id, "operation_side_ready_to_invoice")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            set_party_invoice_amount(
                self.org, op_id, "buyer", "100000", "ARS", None, self.admin_id
            )
        self.assertEqual(
            self._count_kind(self.user_id, "operation_side_ready_to_invoice"),
            before + 1,
        )
        mocked.assert_not_called()

    def test_match_sends_for_new_listing(self):
        from modules.notifications_service import (
            notify_new_property_matches_for_contact,
        )

        contact = self._matching_contact()
        property_row = get_property_record(self.property_id, self.org)
        before = self._count_kind(self.user_id, "property_match")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            sent = notify_new_property_matches_for_contact(
                self.org,
                contact,
                listings=[property_row],
            )
        self.assertEqual(len(sent), 1)
        self.assertEqual(self._count_kind(self.user_id, "property_match"), before + 1)
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                f"need_{contact['id']}_property_{self.property_id}_match",
                user_id=self.user_id,
            )
        )
        mocked.assert_called_once()
        self.assertTrue(mocked.call_args.args[2]["url"].startswith("/notifications/"))
        self.assertTrue(mocked.call_args.args[2]["url"].endswith("/open"))
        self.assertIn("Martín", mocked.call_args.args[2]["body"])

    def test_match_no_duplicate_same_need_property(self):
        from modules.notifications_service import (
            notify_new_property_matches_for_contact,
        )

        contact = self._matching_contact()
        property_row = get_property_record(self.property_id, self.org)
        before = self._count_kind(self.user_id, "property_match")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            first = notify_new_property_matches_for_contact(
                self.org,
                contact,
                listings=[property_row],
            )
            second = notify_new_property_matches_for_contact(
                self.org,
                contact,
                listings=[property_row],
            )
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(self._count_kind(self.user_id, "property_match"), before + 1)
        self.assertEqual(mocked.call_count, 1)

    def test_match_skips_below_threshold(self):
        from modules.notifications_service import (
            notify_new_property_matches_for_contact,
        )

        contact = create_agent_contact(
            self.org,
            self.agent_id,
            {
                "name": "Bajo Score",
                "preferences": {
                    "areas": ["Belgrano"],
                    "property_types": ["house"],
                    "rooms": 8,
                    "bedrooms": 7,
                },
            },
        )
        property_row = get_property_record(self.property_id, self.org)
        before = self._count_kind(self.user_id, "property_match")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            sent = notify_new_property_matches_for_contact(
                self.org,
                contact,
                listings=[property_row],
            )
        self.assertEqual(sent, [])
        mocked.assert_not_called()
        self.assertEqual(self._count_kind(self.user_id, "property_match"), before)

    def test_match_respects_organization_scope(self):
        from modules.notifications_service import (
            notify_new_property_matches_for_contact,
        )

        foreign = self._matching_contact(
            agent_id=self.agent_b,
            name="Martín Extraño",
            org=self.org_b,
        )
        property_row = get_property_record(self.property_id, self.org)
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            sent = notify_new_property_matches_for_contact(
                self.org,
                foreign,
                listings=[property_row],
            )
        self.assertEqual(sent, [])
        mocked.assert_not_called()
        self.assertEqual(list_notifications(self.user_b, self.org_b), [])

    def test_match_respects_preference_off(self):
        from modules.notifications_service import (
            notify_new_property_matches_for_contact,
        )

        contact = self._matching_contact(name="Lucía Pérez")
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_property_matches=False,
        )
        property_row = get_property_record(self.property_id, self.org)
        before = self._count_kind(self.user_id, "property_match")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            sent = notify_new_property_matches_for_contact(
                self.org,
                contact,
                listings=[property_row],
            )
        self.assertEqual(len(sent), 1)
        self.assertEqual(self._count_kind(self.user_id, "property_match"), before + 1)
        mocked.assert_not_called()

    def test_gone_subscription_is_deactivated(self):
        self._subscribe(self.user_id, "gone")
        with patch(
            "modules.web_push.send_web_push",
            return_value={"ok": False, "status": 410, "gone": True},
        ):
            result = send_user_notification(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita en 30 minutos",
                "Santamarina 1335 · 14:30",
                "/agenda/1/edit",
                event_key="agenda_visit_gone_30m",
                entity_type="agent_task",
                entity_id=1,
            )
        self.assertTrue(result["created"])
        self.assertEqual(list_active_push_subscriptions(self.org, self.user_id), [])

    def test_push_failure_does_not_break_invoice(self):
        self._subscribe(self.user_id, "fail")
        op_id = self._operation()
        with patch(
            "modules.web_push.send_web_push",
            side_effect=RuntimeError("push down"),
        ):
            party = set_party_invoice_amount(
                self.org, op_id, "buyer", "100000", "ARS", None, self.admin_id
            )
        self.assertTrue(party.get("billing_enabled"))
        self.assertIn("operation_side_ready_to_invoice", self._kinds(self.user_id))

    def test_external_url_is_rejected(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            result = send_user_notification(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita en 30 minutos",
                "Cuerpo",
                "https://evil.example/phish",
                event_key="agenda_visit_url_check",
                entity_type="agent_task",
                entity_id=99,
            )
        self.assertTrue(result["created"])
        notes = list_notifications(self.user_id, self.org)
        stored = next(
            item
            for item in notes
            if item["payload"].get("event_key") == "agenda_visit_url_check"
        )
        self.assertEqual(stored["payload"]["url"], "/")
        self.assertEqual(
            mocked.call_args.args[2]["url"],
            f"/notifications/{result['notification_id']}/open",
        )

    def test_dedupe_blocks_second_send(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            first = send_user_notification(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita en 30 minutos",
                "Uno",
                "/agenda/2/edit",
                event_key="agenda_visit_2_30m",
                entity_type="agent_task",
                entity_id=2,
            )
            second = send_user_notification(
                self.user_id,
                self.org,
                "visit_reminder",
                "Visita en 30 minutos",
                "Dos",
                "/agenda/2/edit",
                event_key="agenda_visit_2_30m",
                entity_type="agent_task",
                entity_id=2,
            )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(mocked.call_count, 1)

    def test_settings_toggles_roundtrip(self):
        self._login()
        page = self.client.get("/settings/notifications")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Recordatorios de Agenda", html)
        self.assertIn("Facturación habilitada", html)
        self.assertIn("Propiedades y matches", html)
        saved = self.client.post(
            "/settings/notifications",
            data={"push_invoice_ready": "1"},
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        from modules.database.user_notification_preferences_repository import (
            get_user_notification_preferences,
        )

        prefs = get_user_notification_preferences(self.org, self.user_id)
        self.assertFalse(prefs["push_visit_reminders"])
        self.assertTrue(prefs["push_invoice_ready"])
        self.assertFalse(prefs["push_property_matches"])


if __name__ == "__main__":
    unittest.main()
