"""Push/notification reliability: dedupe races, per-user keys, logout, purge."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_push_reliability.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
# Throwaway P-256 test key, same material as the other push suites.
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
os.environ["WEB_PUSH_VAPID_SUBJECT"] = "mailto:admin@jrhone.com"
os.environ.pop("NOTIFICATION_DISPATCHER", None)

from pywebpush import WebPushException

from modules.agent_tasks import reschedule_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.agent_tasks_repository import create_agent_task
from modules.database.connection import get_connection
from modules.database.notifications_migration import (
    migrate_notification_events_sqlite,
)
from modules.database.notifications_repository import list_notifications
from modules.database.push_subscriptions_repository import (
    get_push_subscription_by_endpoint,
    purge_inactive_push_subscriptions,
    upsert_push_subscription,
)
from modules.notifications.service import notify_user
from modules.notifications_service import (
    notify_agent_for_operation,
    status_transition_event_key,
)
from web_app import app


P256DH = "BValidP256dhKeyMaterialForTests0123456789abcd"
AUTH = "ValidAuthSecret012345"
PUSH_TARGET = "modules.notifications_service.send_user_pushes"
PUSH_OK = {"push_targets_count": 1, "sent_count": 1, "failed_count": 0}


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


def _subscribe(org, user_id, endpoint):
    return upsert_push_subscription(
        org,
        user_id,
        endpoint=endpoint,
        p256dh=P256DH,
        auth=AUTH,
    )


def _rows(org, user_id, kind=None):
    rows = list_notifications(user_id, org, limit=500)
    return [row for row in rows if kind is None or row["kind"] == kind]


class PushReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="push-reliability")
        create_tables()
        pwd = hash_password("Password1")
        cls.org = add_organization("Reliability Org A")
        cls.org_b = add_organization("Reliability Org B")
        cls.agent_a = add_agent("Ana Rel", "Alto", cls.org)
        cls.agent_a2 = add_agent("Bruno Rel", "Alto", cls.org)
        cls.agent_b = add_agent("Carla Rel", "Alto", cls.org_b)
        cls.user_a = add_user("ana_rel", pwd, ROLE_AGENT, cls.org, agent_id=cls.agent_a)
        cls.user_a2 = add_user("bruno_rel", pwd, ROLE_AGENT, cls.org, agent_id=cls.agent_a2)
        cls.user_b = add_user("carla_rel", pwd, ROLE_AGENT, cls.org_b, agent_id=cls.agent_b)
        cls.admin_a = add_user("admin_rel", pwd, ROLE_ADMIN, cls.org)

    def _client(self, user_id, org, role=ROLE_AGENT):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = org
        return client

    # 1. Concurrent creation of the same event.

    def test_race_loser_gets_created_false_and_no_second_push(self):
        """The losing insert of a race hits the unique index: no second push."""
        from modules.database import notifications_repository

        real_lookup = notifications_repository._find_id_by_event_key
        calls = []

        def _stale_precheck(cursor, organization_id, event_key, user_id=None):
            calls.append(event_key)
            if len(calls) == 1:
                return None
            return real_lookup(cursor, organization_id, event_key, user_id=user_id)

        with patch(PUSH_TARGET, return_value=PUSH_OK) as push:
            first = notify_user(
                self.user_a, self.org, "office_announcement", "Hola", event_key="race-seq"
            )
            with patch(
                "modules.notifications.service.find_notification_by_event_key",
                return_value=None,
            ), patch.object(
                notifications_repository,
                "_find_id_by_event_key",
                side_effect=_stale_precheck,
            ):
                second = notify_user(
                    self.user_a, self.org, "office_announcement", "Hola", event_key="race-seq"
                )
        self.assertEqual(len(calls), 2, "pre-check + IntegrityError recovery")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["deduped"])
        self.assertEqual(second["notification_id"], first["notification_id"])
        self.assertEqual(push.call_count, 1)
        self.assertEqual(
            len([row for row in _rows(self.org, self.user_a) if row["payload"].get("event_key") == "race-seq"]),
            1,
        )

    def test_two_concurrent_workers_same_event_one_row_one_push(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def _worker():
            try:
                barrier.wait(timeout=5)
                results.append(
                    notify_user(
                        self.user_a,
                        self.org,
                        "office_announcement",
                        "Concurrente",
                        event_key="race-threads",
                    )
                )
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)

        with patch(PUSH_TARGET, return_value=PUSH_OK) as push:
            threads = [threading.Thread(target=_worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for item in results if item["created"]), 1)
        self.assertEqual(len({item["notification_id"] for item in results}), 1)
        self.assertEqual(push.call_count, 1)
        rows = [
            row
            for row in _rows(self.org, self.user_a)
            if row["payload"].get("event_key") == "race-threads"
        ]
        self.assertEqual(len(rows), 1)

    # 2. Same event_key, two users of one organization.

    def test_same_event_key_two_users_each_get_their_own(self):
        with patch(PUSH_TARGET, return_value=PUSH_OK) as push:
            first = notify_user(
                self.user_a, self.org, "office_announcement", "Aviso", event_key="shared-key"
            )
            second = notify_user(
                self.user_a2, self.org, "office_announcement", "Aviso", event_key="shared-key"
            )
        self.assertTrue(first["created"])
        self.assertTrue(second["created"])
        self.assertNotEqual(first["notification_id"], second["notification_id"])
        self.assertEqual(push.call_count, 2)
        pushed_users = sorted(call.args[1] for call in push.call_args_list)
        self.assertEqual(pushed_users, sorted([self.user_a, self.user_a2]))
        mine = [row["id"] for row in _rows(self.org, self.user_a)]
        theirs = [row["id"] for row in _rows(self.org, self.user_a2)]
        self.assertIn(first["notification_id"], mine)
        self.assertNotIn(second["notification_id"], mine)
        self.assertIn(second["notification_id"], theirs)

    # 3. Two organizations: total isolation.

    def test_two_organizations_are_isolated(self):
        _subscribe(self.org, self.user_a, "https://push.example.com/iso-a")
        _subscribe(self.org_b, self.user_b, "https://push.example.com/iso-b")
        seen = []

        def _capture(*args, **kwargs):
            seen.append(kwargs["subscription_info"]["endpoint"])

        with patch("pywebpush.webpush", side_effect=_capture):
            a = notify_user(self.user_a, self.org, "office_announcement", "A", event_key="iso-key")
            b = notify_user(self.user_b, self.org_b, "office_announcement", "B", event_key="iso-key")
        self.assertTrue(a["created"])
        self.assertTrue(b["created"])
        self.assertIn("https://push.example.com/iso-a", seen)
        self.assertIn("https://push.example.com/iso-b", seen)
        self.assertEqual(seen.count("https://push.example.com/iso-a"), 1)
        self.assertEqual(seen.count("https://push.example.com/iso-b"), 1)
        ids_a = {row["id"] for row in _rows(self.org, self.user_a)}
        ids_b = {row["id"] for row in _rows(self.org_b, self.user_b)}
        self.assertIn(a["notification_id"], ids_a)
        self.assertNotIn(b["notification_id"], ids_a)
        self.assertIn(b["notification_id"], ids_b)
        self.assertEqual(_rows(self.org_b, self.user_a), [])

        client = self._client(self.user_a, self.org)
        foreign = client.post(
            "/api/push/device", json={"endpoint": "https://push.example.com/iso-b"}
        ).get_json()
        self.assertFalse(foreign["known"])
        client.get("/logout")
        self.assertTrue(
            get_push_subscription_by_endpoint("https://push.example.com/iso-b")["is_active"]
        )

    # 4 + 5. Logout disables only the current device.

    def test_logout_disables_current_device_only(self):
        client = self._client(self.user_a2, self.org)
        page = client.post(
            "/api/push/subscribe",
            json={
                "endpoint": "https://push.example.com/logout-phone",
                "keys": {"p256dh": P256DH, "auth": AUTH},
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            },
        )
        self.assertEqual(page.status_code, 200)
        _subscribe(self.org, self.user_a2, "https://push.example.com/logout-laptop")

        client.get("/logout")

        phone = get_push_subscription_by_endpoint("https://push.example.com/logout-phone")
        laptop = get_push_subscription_by_endpoint("https://push.example.com/logout-laptop")
        self.assertFalse(phone["is_active"])
        self.assertTrue(laptop["is_active"])

        seen = []
        with patch(
            "pywebpush.webpush",
            side_effect=lambda *a, **k: seen.append(k["subscription_info"]["endpoint"]),
        ):
            notify_user(
                self.user_a2, self.org, "office_announcement", "Tras logout",
                event_key="after-logout",
            )
        self.assertIn("https://push.example.com/logout-laptop", seen)
        self.assertNotIn("https://push.example.com/logout-phone", seen)

    def test_logout_uses_device_bound_by_settings_panel(self):
        _subscribe(self.org, self.user_a, "https://push.example.com/bound-phone")
        _subscribe(self.org, self.user_a, "https://push.example.com/bound-tablet")
        client = self._client(self.user_a, self.org)
        bound = client.post(
            "/api/push/device", json={"endpoint": "https://push.example.com/bound-phone"}
        ).get_json()
        self.assertTrue(bound["known"])
        self.assertTrue(bound["device_active"])
        client.get("/logout")
        self.assertFalse(
            get_push_subscription_by_endpoint("https://push.example.com/bound-phone")["is_active"]
        )
        self.assertTrue(
            get_push_subscription_by_endpoint("https://push.example.com/bound-tablet")["is_active"]
        )

    def test_logout_without_known_device_touches_nothing(self):
        _subscribe(self.org, self.user_a, "https://push.example.com/untouched")
        client = self._client(self.user_a, self.org)
        client.get("/logout")
        self.assertTrue(
            get_push_subscription_by_endpoint("https://push.example.com/untouched")["is_active"]
        )

    # 6. 404/410 deactivate immediately and keep last_failure_at.

    def test_gone_statuses_deactivate_subscription(self):
        for status in (404, 410):
            endpoint = f"https://push.example.com/gone-{status}"
            _subscribe(self.org, self.user_a, endpoint)

            def _gone(*args, _status=status, **kwargs):
                raise WebPushException("Gone", response=_Response(_status))

            with patch("pywebpush.webpush", side_effect=_gone):
                notify_user(
                    self.user_a, self.org, "office_announcement", "Gone",
                    event_key=f"gone-{status}",
                )
            row = get_push_subscription_by_endpoint(endpoint)
            self.assertFalse(row["is_active"], status)
            self.assertTrue(row["last_failure_at"], status)

    def test_transient_failure_keeps_subscription_active(self):
        endpoint = "https://push.example.com/flaky-500"
        _subscribe(self.org, self.user_a, endpoint)
        with patch(
            "pywebpush.webpush",
            side_effect=WebPushException("Server error", response=_Response(500)),
        ):
            notify_user(
                self.user_a, self.org, "office_announcement", "Flaky", event_key="flaky-500"
            )
        row = get_push_subscription_by_endpoint(endpoint)
        self.assertTrue(row["is_active"])
        self.assertTrue(row["last_failure_at"])

    # 7. Purge after 30 days.

    def _age(self, endpoint, *, active, failure_days=None, updated_days=None):
        now = datetime.utcnow().replace(microsecond=0)
        failure = None if failure_days is None else (now - timedelta(days=failure_days)).isoformat()
        updated = (now - timedelta(days=updated_days or 0)).isoformat()
        connection = get_connection()
        try:
            connection.cursor().execute(
                """
                UPDATE push_subscriptions
                SET is_active = ?, last_failure_at = ?, updated_at = ?
                WHERE endpoint = ?
                """,
                (1 if active else 0, failure, updated, endpoint),
            )
            connection.commit()
        finally:
            connection.close()

    def test_purge_deletes_only_long_inactive_subscriptions(self):
        cases = {
            "https://push.example.com/purge-old-failure": dict(active=False, failure_days=31),
            "https://push.example.com/purge-recent-failure": dict(active=False, failure_days=10),
            "https://push.example.com/purge-old-logout": dict(active=False, updated_days=40),
            "https://push.example.com/purge-active-old": dict(active=True, failure_days=90),
        }
        for endpoint, kwargs in cases.items():
            _subscribe(self.org, self.user_a, endpoint)
            self._age(endpoint, **kwargs)

        deleted = purge_inactive_push_subscriptions()

        self.assertGreaterEqual(deleted, 2)
        self.assertIsNone(get_push_subscription_by_endpoint("https://push.example.com/purge-old-failure"))
        self.assertIsNone(get_push_subscription_by_endpoint("https://push.example.com/purge-old-logout"))
        recent = get_push_subscription_by_endpoint("https://push.example.com/purge-recent-failure")
        self.assertIsNotNone(recent)
        self.assertTrue(recent["last_failure_at"])
        self.assertIsNotNone(get_push_subscription_by_endpoint("https://push.example.com/purge-active-old"))

    def test_notification_jobs_run_the_purge(self):
        from modules.notifications.jobs import run_notification_jobs

        endpoint = "https://push.example.com/purge-by-job"
        _subscribe(self.org, self.user_a, endpoint)
        self._age(endpoint, active=False, failure_days=45)
        summary = run_notification_jobs(source="test")
        self.assertGreaterEqual(summary["push_subscriptions_purged"], 1)
        self.assertIsNone(get_push_subscription_by_endpoint(endpoint))

    # 8. Agenda edited twice notifies twice.

    def test_agenda_rescheduled_twice_notifies_each_change(self):
        task = create_agent_task(
            self.org,
            self.agent_a,
            title="Llamar a Juan",
            task_type="call",
            due_at="2030-01-10T13:00:00",
        )
        with patch(PUSH_TARGET, return_value=PUSH_OK) as push:
            reschedule_task(
                self.org, task["id"], due_date="2030-01-11", due_time="10:00",
                actor_user_id=self.admin_a,
            )
            reschedule_task(
                self.org, task["id"], due_date="2030-01-12", due_time="11:00",
                actor_user_id=self.admin_a,
            )
        changes = [
            row
            for row in _rows(self.org, self.user_a, kind="agenda_changed")
            if row["entity_id"] == task["id"]
        ]
        self.assertEqual(len(changes), 2)
        self.assertEqual(len({row["payload"]["event_key"] for row in changes}), 2)
        self.assertEqual(push.call_count, 2)

    def test_agenda_same_version_retry_still_dedupes(self):
        from modules.agent_tasks import emit_task_event

        task = create_agent_task(
            self.org,
            self.agent_a,
            title="Visita duplicada",
            task_type="call",
            due_at="2030-02-10T13:00:00",
        )
        with patch(PUSH_TARGET, return_value=PUSH_OK) as push:
            emit_task_event("task_updated", task, actor_user_id=self.admin_a)
            emit_task_event("task_updated", task, actor_user_id=self.admin_a)
        changes = [
            row
            for row in _rows(self.org, self.user_a, kind="agenda_changed")
            if row["entity_id"] == task["id"]
        ]
        self.assertEqual(len(changes), 1)
        self.assertEqual(push.call_count, 1)

    # 9. Operation changes state more than once.

    def _review(self, operation_id, kind, from_status, to_status, reviewed_at):
        return notify_agent_for_operation(
            self.org,
            self.agent_a,
            kind,
            operation_id,
            {"status": to_status},
            actor_user_id=self.admin_a,
            event_key=status_transition_event_key(
                "operation", operation_id, from_status, to_status, reviewed_at
            ),
        )

    def test_operation_rejected_twice_then_approved_notifies_each_review(self):
        operation_id = 9101
        with patch(PUSH_TARGET, return_value=PUSH_OK) as push:
            first = self._review(operation_id, "operation_rejected", "pending", "rejected", "2030-03-01T10:00:00")
            retry = self._review(operation_id, "operation_rejected", "pending", "rejected", "2030-03-01T10:00:00")
            second = self._review(operation_id, "operation_rejected", "pending", "rejected", "2030-03-02T09:30:00")
            approved = self._review(operation_id, "operation_approved", "pending", "approved", "2030-03-03T12:00:00")
        self.assertEqual(retry, first)
        self.assertEqual(len({first, second, approved}), 3)
        rows = [
            row
            for row in _rows(self.org, self.user_a)
            if row["entity_type"] == "operation" and row["entity_id"] == operation_id
        ]
        self.assertEqual(
            sorted(row["kind"] for row in rows),
            ["operation_approved", "operation_rejected", "operation_rejected"],
        )
        self.assertEqual(push.call_count, 3)

    # Migration.

    def test_migration_replaces_legacy_index_on_existing_database(self):
        """A database created before this change migrates in place, data intact."""
        import sqlite3

        legacy_path = Path(_TEST_TMP.name) / "legacy_notifications.db"
        raw = sqlite3.connect(legacy_path)
        try:
            raw.executescript(
                """
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    entity_type TEXT,
                    entity_id INTEGER,
                    payload_json TEXT,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    actor_user_id INTEGER,
                    created_at TEXT,
                    event_key TEXT,
                    read_at TEXT,
                    priority TEXT
                );
                CREATE UNIQUE INDEX idx_notifications_event_key
                    ON notifications (organization_id, event_key)
                    WHERE event_key IS NOT NULL AND event_key != '';
                INSERT INTO notifications (organization_id, user_id, kind, event_key)
                VALUES (1, 10, 'office_announcement', 'legacy-key');
                """
            )
            raw.commit()
        finally:
            raw.close()

        with patch.dict(os.environ, {"DATABASE_PATH": str(legacy_path)}):
            migrate_notification_events_sqlite()
            migrate_notification_events_sqlite()
            connection = get_connection()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'notifications'"
                )
                indexes = {row[0]: row[1] or "" for row in cursor.fetchall()}
                cursor.execute(
                    "INSERT INTO notifications (organization_id, user_id, kind, event_key) "
                    "VALUES (1, 11, 'office_announcement', 'legacy-key')"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    cursor.execute(
                        "INSERT INTO notifications (organization_id, user_id, kind, event_key) "
                        "VALUES (1, 10, 'office_announcement', 'legacy-key')"
                    )
                connection.rollback()
                cursor.execute("SELECT COUNT(*) FROM notifications")
                remaining = cursor.fetchone()[0]
            finally:
                connection.close()
        self.assertNotIn("idx_notifications_event_key", indexes)
        self.assertIn("idx_notifications_user_event_key", indexes)
        self.assertIn("user_id", indexes["idx_notifications_user_event_key"])
        self.assertIn("UNIQUE", indexes["idx_notifications_user_event_key"].upper())
        self.assertEqual(remaining, 1)


class SingleDispatcherTests(unittest.TestCase):
    def test_default_dispatcher_is_inprocess(self):
        from modules.notifications.dispatcher import configured_dispatcher

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOTIFICATION_DISPATCHER", None)
            self.assertEqual(configured_dispatcher(), "inprocess")

    def test_invalid_dispatcher_fails_closed(self):
        from modules.notifications.dispatcher import configured_dispatcher

        with patch.dict(os.environ, {"NOTIFICATION_DISPATCHER": "both"}):
            self.assertEqual(configured_dispatcher(), "off")

    def test_worker_and_cron_scripts_skip_when_inprocess(self):
        import dispatch_visit_reminders
        import notification_worker

        with patch.dict(os.environ, {"NOTIFICATION_DISPATCHER": "inprocess"}), patch(
            "modules.notifications.jobs.run_notification_jobs"
        ) as jobs, patch("notification_worker.run_notification_jobs") as worker_jobs:
            self.assertEqual(notification_worker.main([]), 0)
            self.assertEqual(notification_worker.main(["--loop"]), 0)
            self.assertEqual(dispatch_visit_reminders.main([]), 0)
        jobs.assert_not_called()
        worker_jobs.assert_not_called()

    def test_inprocess_thread_disabled_when_other_dispatcher_selected(self):
        from modules.visit_reminder_scheduler import _env_enabled

        with patch.dict(os.environ, {"NOTIFICATION_DISPATCHER": "cron"}):
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            os.environ.pop("VISIT_REMINDER_SCHEDULER", None)
            self.assertFalse(_env_enabled())
        with patch.dict(os.environ, {"NOTIFICATION_DISPATCHER": "inprocess"}):
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            os.environ.pop("VISIT_REMINDER_SCHEDULER", None)
            self.assertTrue(_env_enabled())

    def test_procfile_declares_no_second_dispatcher(self):
        from modules.config import BASE_DIR

        procfile = (Path(BASE_DIR) / "Procfile").read_text(encoding="utf-8")
        self.assertNotIn("worker:", procfile)
        self.assertIn("web:", procfile)


class PushDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="push-reliability")
        create_tables()
        pwd = hash_password("Password1")
        cls.org = add_organization("Diagnostics Org")
        cls.admin = add_user("diag_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent = add_user("diag_agent", pwd, ROLE_AGENT, cls.org)

    def _client(self, user_id, role):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = self.org
        return client

    def test_admin_sees_booleans_without_key_material(self):
        page = self._client(self.admin, ROLE_ADMIN).get("/api/push/diagnostics")
        self.assertEqual(page.status_code, 200)
        data = page.get_json()
        self.assertTrue(data["vapid_public_key_present"])
        self.assertTrue(data["vapid_private_key_present"])
        self.assertTrue(data["vapid_subject_set"])
        self.assertTrue(data["vapid_valid"])
        self.assertEqual(data["dispatcher"], "inprocess")
        body = page.get_data(as_text=True)
        self.assertNotIn("PRIVATE KEY", body)
        self.assertNotIn("MIGHAgEAMBMG", body)
        self.assertNotIn(os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"], body)
        self.assertNotIn("admin@jrhone.com", body)

    def test_agent_cannot_read_diagnostics(self):
        page = self._client(self.agent, ROLE_AGENT).get("/api/push/diagnostics")
        self.assertEqual(page.status_code, 403)


if __name__ == "__main__":
    unittest.main()
