"""Phase 4: overdue tasks, office announcements, badge, navigation."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_notification_phase4.db")
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
    count_unread_notifications,
    create_tables,
)
from modules.database.agent_tasks_repository import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    create_agent_task,
    set_agent_task_status,
    update_agent_task_fields,
)
from modules.database.notifications_repository import (
    find_notification_by_event_key,
    get_notification,
    list_notifications,
)
from modules.database.user_notification_preferences_repository import (
    save_user_notification_preferences,
)
from modules.notification_center import (
    decorate_notification_feed,
    notification_open_url,
)
from modules.notifications_service import send_user_notification
from modules.office_announcements import (
    OfficeAnnouncementError,
    send_office_announcement,
)
from modules.organization_time import now_utc, to_utc_iso
from modules.task_overdue import dispatch_overdue_tasks, overdue_task_event_key
from web_app import app


class NotificationPhase4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="phase4-notifications")
        create_tables()
        cls.org = add_organization("Phase4 Org")
        cls.org_b = add_organization("Phase4 Org B")
        cls.agent_id = add_agent("Ana Perez", "Alto", cls.org)
        cls.other_agent_id = add_agent("Otro Agente", "Alto", cls.org)
        cls.agent_b = add_agent("Agente B", "Alto", cls.org_b)
        pwd = hash_password("Password1")
        cls.user_id = add_user(
            "ana_p4",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Perez",
        )
        cls.other_user_id = add_user(
            "otro_p4",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent_id,
        )
        cls.admin_id = add_user("admin_p4", pwd, ROLE_ADMIN, cls.org)
        cls.user_b = add_user(
            "agent_b_p4",
            pwd,
            ROLE_AGENT,
            cls.org_b,
            agent_id=cls.agent_b,
        )
        cls.admin_b = add_user("admin_b_p4", pwd, ROLE_ADMIN, cls.org_b)

    def setUp(self):
        self.client = app.test_client()
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_visit_reminders=True,
            push_invoice_ready=True,
            push_property_matches=True,
            push_task_overdue=True,
            push_office_announcements=True,
        )

    def _login(self, user_id, role, organization_id=None):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = organization_id or self.org

    def _count_kind(self, user_id, kind, organization_id=None):
        org = organization_id or self.org
        return sum(
            1
            for item in list_notifications(user_id, org)
            if item["kind"] == kind
        )

    def _overdue_task(self, *, agent_id=None, minutes_ago=40, title="Seguimiento con Martín"):
        due_at = to_utc_iso(now_utc() - timedelta(minutes=minutes_ago))
        return create_agent_task(
            self.org,
            agent_id or self.agent_id,
            title=title,
            task_type="follow_up",
            due_at=due_at,
        )

    def test_overdue_sends_when_pending(self):
        task = self._overdue_task()
        before = self._count_kind(self.user_id, "task_overdue")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            result = dispatch_overdue_tasks(self.org)
        self.assertGreaterEqual(result["dispatched"], 1)
        self.assertEqual(self._count_kind(self.user_id, "task_overdue"), before + 1)
        self.assertTrue(
            find_notification_by_event_key(
                self.org,
                overdue_task_event_key(task["id"], task["due_at"]),
                user_id=self.user_id,
            )
        )
        self.assertEqual(mocked.call_args.args[2]["url"], f"/agenda/{task['id']}/edit")
        self.assertIn("Seguimiento con Martín", mocked.call_args.args[2]["body"])

    def test_overdue_skips_visits(self):
        due_at = to_utc_iso(now_utc() - timedelta(minutes=40))
        create_agent_task(
            self.org,
            self.agent_id,
            title="Visita pasada",
            task_type="visit",
            due_at=due_at,
        )
        before = self._count_kind(self.user_id, "task_overdue")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            dispatch_overdue_tasks(self.org)
        self.assertEqual(self._count_kind(self.user_id, "task_overdue"), before)
        mocked.assert_not_called()

    def test_overdue_skips_completed(self):
        task = self._overdue_task(title="Completada")
        set_agent_task_status(task["id"], self.org, status=STATUS_COMPLETED)
        before = self._count_kind(self.user_id, "task_overdue")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            dispatch_overdue_tasks(self.org)
        self.assertEqual(self._count_kind(self.user_id, "task_overdue"), before)
        mocked.assert_not_called()

    def test_overdue_skips_cancelled(self):
        task = self._overdue_task(title="Cancelada")
        set_agent_task_status(task["id"], self.org, status=STATUS_CANCELLED)
        before = self._count_kind(self.user_id, "task_overdue")
        dispatch_overdue_tasks(self.org)
        self.assertEqual(self._count_kind(self.user_id, "task_overdue"), before)

    def test_overdue_no_duplicate(self):
        task = self._overdue_task(title="Una sola")
        event_key = overdue_task_event_key(task["id"], task["due_at"])
        before = self._count_kind(self.user_id, "task_overdue")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            first = dispatch_overdue_tasks(self.org)
            second = dispatch_overdue_tasks(self.org)
        self.assertGreaterEqual(first["dispatched"], 1)
        self.assertEqual(second["dispatched"], 0)
        self.assertEqual(self._count_kind(self.user_id, "task_overdue"), before + 1)
        self.assertTrue(
            find_notification_by_event_key(self.org, event_key, user_id=self.user_id)
        )

    def test_overdue_new_key_when_due_at_changes(self):
        task = self._overdue_task(title="Reprogramada")
        first_key = overdue_task_event_key(task["id"], task["due_at"])
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            dispatch_overdue_tasks(self.org)
        self.assertTrue(
            find_notification_by_event_key(self.org, first_key, user_id=self.user_id)
        )
        new_due = to_utc_iso(now_utc() - timedelta(minutes=5))
        update_agent_task_fields(task["id"], self.org, due_at=new_due)
        second_key = overdue_task_event_key(task["id"], new_due)
        self.assertNotEqual(first_key, second_key)
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ) as mocked:
            result = dispatch_overdue_tasks(self.org)
        self.assertGreaterEqual(result["dispatched"], 1)
        self.assertTrue(
            find_notification_by_event_key(self.org, second_key, user_id=self.user_id)
        )
        self.assertEqual(mocked.call_args.args[2]["url"], f"/agenda/{task['id']}/edit")

    def test_overdue_only_assigned_agent(self):
        self._overdue_task(title="Solo Ana")
        other_before = self._count_kind(self.other_user_id, "task_overdue")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            dispatch_overdue_tasks(self.org)
        self.assertGreater(self._count_kind(self.user_id, "task_overdue"), 0)
        self.assertEqual(self._count_kind(self.other_user_id, "task_overdue"), other_before)

    def test_overdue_respects_preference_off(self):
        self._overdue_task(title="Pref off")
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_task_overdue=False,
        )
        before = self._count_kind(self.user_id, "task_overdue")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            result = dispatch_overdue_tasks(self.org)
        self.assertGreaterEqual(result["dispatched"], 1)
        self.assertEqual(self._count_kind(self.user_id, "task_overdue"), before + 1)
        mocked.assert_not_called()

    def test_office_admin_can_send_to_all_agents(self):
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = send_office_announcement(
                self.org,
                self.admin_id,
                "Reunión comercial mañana 9:00",
                "Nos vemos en la oficina.",
                url="/agenda",
            )
        self.assertEqual(result["target_count"], 2)
        self.assertEqual(result["sent_count"], 2)
        self.assertEqual(result["url"], "/agenda")
        self.assertGreaterEqual(self._count_kind(self.user_id, "office_announcement"), 1)
        self.assertGreaterEqual(self._count_kind(self.other_user_id, "office_announcement"), 1)
        self.assertEqual(self._count_kind(self.user_b, "office_announcement", self.org_b), 0)

    def test_office_selected_agents_only(self):
        before_user = self._count_kind(self.user_id, "office_announcement")
        before_other = self._count_kind(self.other_user_id, "office_announcement")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            result = send_office_announcement(
                self.org,
                self.admin_id,
                "Solo Ana",
                "Mensaje puntual.",
                agent_ids=[self.agent_id],
            )
        self.assertEqual(result["sent_count"], 1)
        self.assertEqual(self._count_kind(self.user_id, "office_announcement"), before_user + 1)
        self.assertEqual(self._count_kind(self.other_user_id, "office_announcement"), before_other)

    def test_office_rejects_cross_tenant_agent_ids(self):
        with self.assertRaises(OfficeAnnouncementError) as ctx:
            send_office_announcement(
                self.org,
                self.admin_id,
                "Cruzado",
                "No debería llegar.",
                agent_ids=[self.agent_b],
            )
        self.assertEqual(ctx.exception.message_key, "office_announcement_err_recipients")
        self.assertEqual(self._count_kind(self.user_b, "office_announcement", self.org_b), 0)

    def test_office_respects_preference_off(self):
        save_user_notification_preferences(
            self.org,
            self.user_id,
            push_office_announcements=False,
        )
        before = self._count_kind(self.user_id, "office_announcement")
        with patch("modules.notifications_service.send_user_pushes") as mocked:
            send_office_announcement(
                self.org,
                self.admin_id,
                "Nuevo procedimiento para reservas",
                "Leé el instructivo interno.",
                agent_ids=[self.agent_id],
            )
        self.assertEqual(self._count_kind(self.user_id, "office_announcement"), before + 1)
        mocked.assert_not_called()

    def test_office_rejects_external_url(self):
        with self.assertRaises(OfficeAnnouncementError) as ctx:
            send_office_announcement(
                self.org,
                self.admin_id,
                "Link malo",
                "Cuerpo",
                url="https://evil.example/phish",
            )
        self.assertEqual(ctx.exception.message_key, "office_announcement_err_url")

    def test_office_http_admin_ok_agent_forbidden(self):
        self._login(self.user_id, ROLE_AGENT)
        before_agent_attempt = self._count_kind(self.user_id, "office_announcement")
        denied = self.client.post(
            "/settings/office-notifications",
            data={
                "title": "No",
                "body": "Un agente no puede",
                "audience": "all",
            },
        )
        self.assertIn(denied.status_code, (302, 403))
        self.assertEqual(
            self._count_kind(self.user_id, "office_announcement"),
            before_agent_attempt,
        )

        self._login(self.admin_id, ROLE_ADMIN)
        before = self._count_kind(self.user_id, "office_announcement")
        with patch(
            "modules.notifications_service.send_user_pushes",
            return_value={"sent_count": 1, "failed_count": 0, "push_targets_count": 1},
        ):
            page = self.client.post(
                "/settings/office-notifications",
                data={
                    "title": "La oficina cierra a las 16:00",
                    "body": "Avisá a tus clientes.",
                    "audience": "all",
                    "url": "/agenda",
                },
                follow_redirects=True,
            )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            self._count_kind(self.user_id, "office_announcement"),
            before + 1,
        )

    def test_badge_counts_and_mark_read(self):
        before = count_unread_notifications(self.user_id, self.org)
        other_before = count_unread_notifications(self.other_user_id, self.org)
        org_b_before = count_unread_notifications(self.user_b, self.org_b)
        send_user_notification(
            self.user_id,
            self.org,
            "task_overdue",
            "Tarea vencida",
            "Uno",
            "/agenda/1/edit",
            event_key="badge_one",
            entity_type="agent_task",
            entity_id=1,
        )
        send_user_notification(
            self.user_id,
            self.org,
            "task_overdue",
            "Tarea vencida",
            "Dos",
            "/agenda/2/edit",
            event_key="badge_two",
            entity_type="agent_task",
            entity_id=2,
        )
        send_user_notification(
            self.other_user_id,
            self.org,
            "task_overdue",
            "Tarea vencida",
            "Ajena",
            "/agenda/3/edit",
            event_key="badge_other",
            entity_type="agent_task",
            entity_id=3,
        )
        send_user_notification(
            self.user_b,
            self.org_b,
            "task_overdue",
            "Tarea vencida",
            "Otra org",
            "/agenda/4/edit",
            event_key="badge_org_b",
            entity_type="agent_task",
            entity_id=4,
        )
        self.assertEqual(count_unread_notifications(self.user_id, self.org), before + 2)
        self.assertEqual(
            count_unread_notifications(self.other_user_id, self.org),
            other_before + 1,
        )
        self.assertEqual(
            count_unread_notifications(self.user_b, self.org_b),
            org_b_before + 1,
        )

        self._login(self.user_id, ROLE_AGENT)
        page = self.client.get("/notifications")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("nav-notifications-link", html)
        self.assertIn(str(before + 2), html)

        mine = [
            item
            for item in list_notifications(self.user_id, self.org)
            if item["payload"].get("event_key") == "badge_one"
        ]
        self.client.post(f"/notifications/{mine[0]['id']}/read", follow_redirects=True)
        self.assertEqual(count_unread_notifications(self.user_id, self.org), before + 1)

        self.client.post("/notifications/read-all", follow_redirects=True)
        self.assertEqual(count_unread_notifications(self.user_id, self.org), 0)
        self.assertEqual(
            count_unread_notifications(self.other_user_id, self.org),
            other_before + 1,
        )

    def test_navigation_urls_by_type(self):
        cases = (
            ("visit_reminder", 11, "/agenda/11/edit", "/agenda/11/edit"),
            ("operation_side_ready_to_invoice", 22, "/billing?tab=pending", "/billing?tab=pending"),
            ("property_match", 33, "/contacts/33/property-matches", "/contacts/33/property-matches"),
            ("task_overdue", 44, "/agenda/44/edit", "/agenda/44/edit"),
            ("office_announcement", 0, "/agenda", "/agenda"),
            ("office_announcement", 0, None, "/notifications"),
        )
        for kind, entity_id, url, expected in cases:
            item = {
                "kind": kind,
                "entity_id": entity_id,
                "payload": {"url": url} if url else {},
            }
            self.assertEqual(notification_open_url(item), expected, kind)

        rejected = {
            "kind": "office_announcement",
            "entity_id": 0,
            "payload": {"url": "https://evil.example"},
        }
        self.assertEqual(notification_open_url(rejected), "/notifications")
        protocol_relative = {
            "kind": "office_announcement",
            "entity_id": 0,
            "payload": {"url": "//evil.example/phish"},
        }
        self.assertEqual(notification_open_url(protocol_relative), "/notifications")
        javascript_url = {
            "kind": "office_announcement",
            "entity_id": 0,
            "payload": {"url": "javascript:alert(1)"},
        }
        self.assertEqual(notification_open_url(javascript_url), "/notifications")

    def test_open_marks_read_and_redirects(self):
        result = send_user_notification(
            self.user_id,
            self.org,
            "task_overdue",
            "Tarea vencida",
            "Abrir",
            "/agenda/88/edit",
            event_key="open_nav",
            entity_type="agent_task",
            entity_id=88,
        )
        self._login(self.user_id, ROLE_AGENT)
        page = self.client.get(
            f"/notifications/{result['notification_id']}/open",
            follow_redirects=False,
        )
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].endswith("/agenda/88/edit"))
        opened = get_notification(result["notification_id"], self.user_id, self.org)
        self.assertTrue(opened["is_read"])

    def test_open_does_not_leak_other_user(self):
        result = send_user_notification(
            self.other_user_id,
            self.org,
            "task_overdue",
            "Tarea vencida",
            "Ajena",
            "/agenda/99/edit",
            event_key="open_foreign",
            entity_type="agent_task",
            entity_id=99,
        )
        self._login(self.user_id, ROLE_AGENT)
        page = self.client.get(
            f"/notifications/{result['notification_id']}/open"
        )
        self.assertEqual(page.status_code, 404)

    def test_visual_group_keeps_individual_events(self):
        items = []
        for index in range(3):
            items.append(
                {
                    "id": index + 1,
                    "kind": "property_match",
                    "entity_id": 10,
                    "is_read": False,
                    "created_at": "2026-09-14T12:00:00",
                    "payload": {
                        "title": "Nuevo match de propiedad",
                        "body": f"Match {index}",
                        "url": "/contacts/10/property-matches",
                    },
                }
            )
        groups = decorate_notification_feed(items, self.org, "es")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["count"], 3)
        self.assertIn("3", groups[0]["heading"])
        self.assertEqual(len(groups[0]["items"]), 3)


if __name__ == "__main__":
    unittest.main()
