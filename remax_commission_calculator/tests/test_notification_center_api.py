"""Notification center API, live bell, devices and service worker (phases 2 + 3)."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_notification_center_api.db")
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

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.notifications_repository import (
    count_unread_notifications,
    create_notification,
    get_notification,
)
from modules.database.push_subscriptions_repository import (
    get_push_subscription_by_endpoint,
    upsert_push_subscription,
)
from modules.notification_center import notification_open_url
from modules.notifications.catalog import TYPES, kinds_for_category, resolve_url
from modules.notifications.service import notify_user
from modules.pwa_routes import _device_label
from web_app import app

P256DH = "BValidP256dhKeyMaterialForTests0123456789abcd"
AUTH = "ValidAuthSecret012345"
AUTH_NEW = "RotatedAuthSecret67890"
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
WINDOWS_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
ANDROID_CHROME_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
)
SERVICE_WORKER = Path(__file__).resolve().parents[1] / "static" / "service-worker.js"


def _notify(org, user_id, kind="office_announcement", *, read=False, title=None):
    notification_id = create_notification(
        org,
        user_id,
        kind,
        "notification",
        0,
        payload={"title": title or kind},
    )
    if read:
        connection = get_connection()
        try:
            connection.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
            connection.commit()
        finally:
            connection.close()
    return notification_id


def _clear_notifications(org, user_id):
    connection = get_connection()
    try:
        connection.execute(
            "DELETE FROM notifications WHERE organization_id = ? AND user_id = ?",
            (org, user_id),
        )
        connection.commit()
    finally:
        connection.close()


def _subscribe_row(org, user_id, endpoint, *, label=None, user_agent=None, auth=AUTH):
    return upsert_push_subscription(
        org,
        user_id,
        endpoint=endpoint,
        p256dh=P256DH,
        auth=auth,
        user_agent=user_agent,
        device_label=label,
    )


class NotificationCenterApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="notification-center-api")
        create_tables()
        pwd = hash_password("Password1")
        cls.org = add_organization("Center Org A")
        cls.org_b = add_organization("Center Org B")
        cls.agent_a = add_agent("Ana Center", "Alto", cls.org)
        cls.agent_a2 = add_agent("Bruno Center", "Alto", cls.org)
        cls.agent_b = add_agent("Carla Center", "Alto", cls.org_b)
        cls.agent_many = add_agent("Dario Center", "Alto", cls.org)
        cls.user_a = add_user("ana_center", pwd, ROLE_AGENT, cls.org, agent_id=cls.agent_a)
        cls.user_a2 = add_user("bruno_center", pwd, ROLE_AGENT, cls.org, agent_id=cls.agent_a2)
        cls.user_b = add_user("carla_center", pwd, ROLE_AGENT, cls.org_b, agent_id=cls.agent_b)
        cls.user_many = add_user("dario_center", pwd, ROLE_AGENT, cls.org, agent_id=cls.agent_many)
        cls.admin_a = add_user("admin_center", pwd, ROLE_ADMIN, cls.org)

    def setUp(self):
        for org, user in (
            (self.org, self.user_a),
            (self.org, self.user_a2),
            (self.org_b, self.user_b),
        ):
            _clear_notifications(org, user)

    def _client(self, user_id, org, role=ROLE_AGENT):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["organization_id"] = org
        return client

    # Guests.

    def test_endpoints_require_login(self):
        client = app.test_client()
        for method, url in (
            ("get", "/api/notifications/unread-count"),
            ("get", "/api/notifications"),
            ("post", "/api/notifications/1/read"),
            ("post", "/api/notifications/read-all"),
            ("get", "/api/push/devices"),
            ("post", "/api/push/devices/1/deactivate"),
        ):
            response = getattr(client, method)(url)
            self.assertEqual(response.status_code, 401, url)

    # unread-count.

    def test_unread_count_is_scoped_to_user_and_org(self):
        _notify(self.org, self.user_a)
        _notify(self.org, self.user_a)
        _notify(self.org, self.user_a, read=True)
        _notify(self.org, self.user_a2)
        _notify(self.org_b, self.user_b)
        _notify(self.org_b, self.user_b)
        _notify(self.org_b, self.user_b)

        def unread(user_id, org):
            response = self._client(user_id, org).get("/api/notifications/unread-count")
            self.assertEqual(response.status_code, 200)
            return response.get_json()["unread_count"]

        self.assertEqual(unread(self.user_a, self.org), 2)
        self.assertEqual(unread(self.user_a2, self.org), 1)
        self.assertEqual(unread(self.user_b, self.org_b), 3)

    # Mark one read.

    def test_mark_read_returns_new_count_without_reload(self):
        first = _notify(self.org, self.user_a)
        _notify(self.org, self.user_a)
        client = self._client(self.user_a, self.org)
        response = client.post(f"/api/notifications/{first}/read")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["id"], first)
        self.assertEqual(data["unread_count"], 1)
        self.assertTrue(get_notification(first, self.user_a, self.org)["is_read"])
        listing = client.get("/api/notifications").get_json()
        by_id = {item["id"]: item for item in listing["items"]}
        self.assertTrue(by_id[first]["is_read"])

    def test_mark_read_of_foreign_notification_is_404(self):
        other_user = _notify(self.org, self.user_a2)
        other_org = _notify(self.org_b, self.user_b)
        client = self._client(self.user_a, self.org)
        self.assertEqual(client.post(f"/api/notifications/{other_user}/read").status_code, 404)
        self.assertEqual(client.post(f"/api/notifications/{other_org}/read").status_code, 404)
        self.assertFalse(get_notification(other_user, self.user_a2, self.org)["is_read"])
        self.assertFalse(get_notification(other_org, self.user_b, self.org_b)["is_read"])

    # read-all.

    def test_read_all_is_scoped(self):
        for _ in range(3):
            _notify(self.org, self.user_a)
        _notify(self.org, self.user_a2)
        _notify(self.org_b, self.user_b)
        response = self._client(self.user_a, self.org).post("/api/notifications/read-all")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["updated"], 3)
        self.assertEqual(data["unread_count"], 0)
        self.assertEqual(count_unread_notifications(self.user_a2, self.org), 1)
        self.assertEqual(count_unread_notifications(self.user_b, self.org_b), 1)

    # Pagination.

    def test_cursor_pagination_has_no_gaps_or_overlap(self):
        created = [_notify(self.org, self.user_a, title=f"n{i}") for i in range(25)]
        client = self._client(self.user_a, self.org)
        seen = []
        before = None
        pages = 0
        while True:
            url = "/api/notifications?limit=10" + (f"&before={before}" if before else "")
            data = client.get(url).get_json()
            pages += 1
            seen.extend(item["id"] for item in data["items"])
            if not data["has_more"]:
                self.assertIsNone(data["next_before"])
                break
            before = data["next_before"]
        self.assertEqual(pages, 3)
        self.assertEqual(seen, sorted(created, reverse=True))

    def test_page_number_pagination_and_limit_cap(self):
        for i in range(12):
            _notify(self.org, self.user_a, title=f"p{i}")
        client = self._client(self.user_a, self.org)
        first = client.get("/api/notifications?limit=5&page=1").get_json()
        second = client.get("/api/notifications?limit=5&page=2").get_json()
        third = client.get("/api/notifications?limit=5&page=3").get_json()
        self.assertEqual(len(first["items"]), 5)
        self.assertTrue(second["has_more"])
        self.assertEqual(len(third["items"]), 2)
        self.assertFalse(third["has_more"])
        ids = [i["id"] for i in first["items"] + second["items"] + third["items"]]
        self.assertEqual(len(set(ids)), 12)
        capped = client.get("/api/notifications?limit=500").get_json()
        self.assertEqual(capped["limit"], 50)

    def test_center_page_has_no_fixed_limit_and_offers_load_more(self):
        for i in range(23):
            _notify(self.org, self.user_a, title=f"page-{i}")
        body = self._client(self.user_a, self.org).get("/notifications").get_data(as_text=True)
        self.assertEqual(body.count("data-notification-id="), 20)
        match = re.search(r'id="notif-load-more"\s+data-before="(\d+)"\s*>', body)
        self.assertIsNotNone(match, "load more visible when there are more items")
        rest = self._client(self.user_a, self.org).get(
            f"/api/notifications?before={match.group(1)}"
        ).get_json()
        self.assertEqual(len(rest["items"]), 3)
        self.assertFalse(rest["has_more"])

    # Filters.

    def test_unread_and_category_filters(self):
        unread_office = _notify(self.org, self.user_a, "office_announcement")
        _notify(self.org, self.user_a, "office_announcement", read=True)
        unread_visit = _notify(self.org, self.user_a, "visit_reminder")
        client = self._client(self.user_a, self.org)

        unread = client.get("/api/notifications?unread_only=1").get_json()
        self.assertEqual({item["id"] for item in unread["items"]}, {unread_office, unread_visit})

        agenda = client.get("/api/notifications?category=agenda").get_json()
        self.assertEqual([item["id"] for item in agenda["items"]], [unread_visit])
        self.assertTrue(all(item["category"] == "agenda" for item in agenda["items"]))

        office_unread = client.get("/api/notifications?category=office&unread_only=1").get_json()
        self.assertEqual([item["id"] for item in office_unread["items"]], [unread_office])

        self.assertEqual(client.get("/api/notifications?category=nope").status_code, 400)

        page = client.get("/notifications?filter=unread").get_data(as_text=True)
        self.assertEqual(page.count("data-notification-id="), 2)
        self.assertIn('notif-tab is-active"', page)

    def test_every_category_has_types(self):
        from modules.notifications.catalog import CATEGORY_ORDER

        for category in CATEGORY_ORDER:
            self.assertTrue(kinds_for_category(category), category)
        self.assertIsNone(kinds_for_category(""))

    # Multi-tenant.

    def test_list_never_leaks_other_users_or_orgs(self):
        mine = _notify(self.org, self.user_a)
        _notify(self.org, self.user_a2)
        _notify(self.org_b, self.user_b)
        data = self._client(self.user_a, self.org).get("/api/notifications").get_json()
        self.assertEqual([item["id"] for item in data["items"]], [mine])
        for item in data["items"]:
            self.assertNotIn("payload", item)
            self.assertNotIn("user_id", item)
            self.assertEqual(item["open_url"], f"/notifications/{item['id']}/open")

    # Bell badge.

    def test_bell_badge_shows_only_unread_and_caps_at_99(self):
        _clear_notifications(self.org, self.user_many)
        client = self._client(self.user_many, self.org)
        empty = client.get("/notifications").get_data(as_text=True)
        self.assertRegex(empty, r"data-unread-badge[^>]*hidden[^>]*>0<")

        for _ in range(3):
            _notify(self.org, self.user_many)
        _notify(self.org, self.user_many, read=True)
        three = client.get("/notifications").get_data(as_text=True)
        badges = re.findall(r"data-unread-badge([^>]*)>([^<]*)<", three)
        self.assertTrue(badges)
        for attrs, text in badges:
            self.assertEqual(text, "3")
            self.assertNotIn("hidden", attrs)

        for _ in range(100):
            _notify(self.org, self.user_many)
        many = client.get("/notifications").get_data(as_text=True)
        self.assertIn(">99+<", many)
        self.assertEqual(
            client.get("/api/notifications/unread-count").get_json()["unread_count"], 103
        )
        _clear_notifications(self.org, self.user_many)

    def test_push_payload_carries_unread_badge_count(self):
        _notify(self.org, self.user_a)
        _notify(self.org, self.user_a, read=True)
        captured = []
        with patch(
            "modules.notifications_service.send_user_pushes",
            side_effect=lambda org, user, payload: captured.append(payload)
            or {"push_targets_count": 1, "sent_count": 1, "failed_count": 0},
        ):
            result = notify_user(
                self.user_a, self.org, "office_announcement", "Nueva", event_key="badge-count"
            )
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["unread_count"], 2)
        self.assertEqual(captured[0]["notification_id"], result["notification_id"])

    # Destinations.

    def test_every_catalog_destination_is_a_real_route(self):
        with app.test_request_context():
            adapter = app.url_map.bind("localhost")
            for kind in TYPES:
                url = resolve_url(kind, entity_id=1)
                path = url.split("?", 1)[0].split("#", 1)[0]
                try:
                    adapter.match(path, method="GET")
                except Exception as error:  # pragma: no cover - reported below
                    if error.__class__.__name__ != "RequestRedirect":
                        self.fail(f"{kind} -> {url}: {error.__class__.__name__}")

    def test_dead_stored_url_falls_back_to_a_live_route(self):
        with app.test_request_context():
            url = notification_open_url(
                {
                    "id": 1,
                    "kind": "invoice_ready",
                    "entity_type": "operation",
                    "entity_id": 7,
                    "payload": {"url": "/my-wallet"},
                }
            )
        self.assertNotEqual(url, "/my-wallet")
        self.assertTrue(url.startswith("/"))

    # Devices.

    def test_device_labels(self):
        self.assertEqual(_device_label(IPHONE_UA), "iPhone · Safari")
        self.assertEqual(_device_label(WINDOWS_CHROME_UA), "Windows · Chrome")
        self.assertEqual(_device_label(ANDROID_CHROME_UA), "Android · Chrome")
        self.assertEqual(
            _device_label(WINDOWS_CHROME_UA.replace("Safari/537.36", "Safari/537.36 Edg/128.0")),
            "Windows · Edge",
        )
        self.assertEqual(
            _device_label(IPHONE_UA.replace("Version/17.4", "CriOS/128.0")),
            "iPhone · Chrome",
        )
        self.assertIsNone(_device_label(""))

    def test_existing_device_label_is_kept(self):
        endpoint = "https://push.example.com/label-kept"
        _subscribe_row(self.org, self.user_a, endpoint, label="Celu de Ana", user_agent=IPHONE_UA)
        client = self._client(self.user_a, self.org)
        client.post(
            "/api/push/subscribe",
            json={
                "endpoint": endpoint,
                "keys": {"p256dh": P256DH, "auth": AUTH},
                "user_agent": WINDOWS_CHROME_UA,
            },
        )
        self.assertEqual(get_push_subscription_by_endpoint(endpoint)["device_label"], "Celu de Ana")

    def test_two_devices_deactivate_one_keeps_the_other(self):
        client = self._client(self.user_a2, self.org)
        for endpoint, ua in (
            ("https://push.example.com/two-phone", IPHONE_UA),
            ("https://push.example.com/two-laptop", WINDOWS_CHROME_UA),
        ):
            response = client.post(
                "/api/push/subscribe",
                json={"endpoint": endpoint, "keys": {"p256dh": P256DH, "auth": AUTH}, "user_agent": ua},
            )
            self.assertEqual(response.status_code, 200)
        client.post("/api/push/device", json={"endpoint": "https://push.example.com/two-laptop"})

        devices = client.get("/api/push/devices").get_json()["devices"]
        labels = {device["label"]: device for device in devices}
        self.assertIn("iPhone · Safari", labels)
        self.assertIn("Windows · Chrome", labels)
        self.assertTrue(labels["Windows · Chrome"]["is_current"])
        self.assertFalse(labels["iPhone · Safari"]["is_current"])
        for device in devices:
            self.assertNotIn("endpoint", device)
            self.assertNotIn("auth", device)

        phone_id = labels["iPhone · Safari"]["id"]
        response = client.post(f"/api/push/devices/{phone_id}/deactivate")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_push_subscription_by_endpoint("https://push.example.com/two-phone")["is_active"])
        self.assertTrue(get_push_subscription_by_endpoint("https://push.example.com/two-laptop")["is_active"])

        settings_page = client.get("/settings/notifications").get_data(as_text=True)
        self.assertIn('id="push-devices"', settings_page)
        self.assertIn("Este dispositivo", settings_page)
        self.assertIn("Desactivar todos mis dispositivos", settings_page)

    def test_cannot_deactivate_someone_elses_device(self):
        foreign = _subscribe_row(self.org_b, self.user_b, "https://push.example.com/foreign-dev")
        client = self._client(self.user_a, self.org)
        self.assertEqual(client.post(f"/api/push/devices/{foreign['id']}/deactivate").status_code, 404)
        self.assertTrue(get_push_subscription_by_endpoint("https://push.example.com/foreign-dev")["is_active"])
        listing = client.get("/api/push/devices").get_json()["devices"]
        self.assertNotIn(foreign["id"], [device["id"] for device in listing])

    def test_logout_still_deactivates_only_current_device(self):
        client = self._client(self.user_a, self.org)
        for endpoint in ("https://push.example.com/out-phone", "https://push.example.com/out-laptop"):
            client.post(
                "/api/push/subscribe",
                json={"endpoint": endpoint, "keys": {"p256dh": P256DH, "auth": AUTH}, "user_agent": IPHONE_UA},
            )
        client.post("/api/push/device", json={"endpoint": "https://push.example.com/out-phone"})
        client.get("/logout")
        self.assertFalse(get_push_subscription_by_endpoint("https://push.example.com/out-phone")["is_active"])
        self.assertTrue(get_push_subscription_by_endpoint("https://push.example.com/out-laptop")["is_active"])

    # pushsubscriptionchange.

    def _resubscribe(self, client, old_endpoint, new_endpoint, *, old_auth=AUTH):
        return client.post(
            "/api/push/resubscribe",
            json={
                "old_endpoint": old_endpoint,
                "old_auth": old_auth,
                "subscription": {"endpoint": new_endpoint, "keys": {"p256dh": P256DH, "auth": AUTH_NEW}},
            },
        )

    def test_resubscribe_updates_endpoint_in_place_without_session(self):
        old = _subscribe_row(self.org, self.user_a, "https://push.example.com/rot-old", label="iPhone · Safari")
        response = self._resubscribe(app.test_client(), "https://push.example.com/rot-old", "https://push.example.com/rot-new")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(get_push_subscription_by_endpoint("https://push.example.com/rot-old"))
        new = get_push_subscription_by_endpoint("https://push.example.com/rot-new")
        self.assertEqual(new["id"], old["id"])
        self.assertEqual(new["user_id"], self.user_a)
        self.assertEqual(new["organization_id"], self.org)
        self.assertEqual(new["auth"], AUTH_NEW)
        self.assertEqual(new["device_label"], "iPhone · Safari")
        self.assertTrue(new["is_active"])

    def test_resubscribe_requires_proof_of_the_old_subscription(self):
        _subscribe_row(self.org, self.user_a, "https://push.example.com/rot-proof")
        anonymous = app.test_client()
        self.assertEqual(
            self._resubscribe(anonymous, "https://push.example.com/rot-proof", "https://push.example.com/x1", old_auth="wrong").status_code,
            404,
        )
        self.assertEqual(
            self._resubscribe(anonymous, "https://push.example.com/unknown", "https://push.example.com/x2").status_code,
            404,
        )
        other_user = self._client(self.user_a2, self.org)
        self.assertEqual(
            self._resubscribe(other_user, "https://push.example.com/rot-proof", "https://push.example.com/x3", old_auth="").status_code,
            404,
        )
        self.assertIsNotNone(get_push_subscription_by_endpoint("https://push.example.com/rot-proof"))

    def test_resubscribe_with_owner_session_and_no_duplicate_rows(self):
        _subscribe_row(self.org, self.user_a, "https://push.example.com/dup-old")
        _subscribe_row(self.org, self.user_a, "https://push.example.com/dup-new")
        client = self._client(self.user_a, self.org)
        response = self._resubscribe(client, "https://push.example.com/dup-old", "https://push.example.com/dup-new", old_auth="")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(get_push_subscription_by_endpoint("https://push.example.com/dup-old"))
        connection = get_connection()
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM push_subscriptions WHERE endpoint = ?",
                ("https://push.example.com/dup-new",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_resubscribe_never_reactivates_or_steals(self):
        row = _subscribe_row(self.org, self.user_a, "https://push.example.com/off-old")
        connection = get_connection()
        try:
            connection.execute("UPDATE push_subscriptions SET is_active = 0 WHERE id = ?", (row["id"],))
            connection.commit()
        finally:
            connection.close()
        response = self._resubscribe(app.test_client(), "https://push.example.com/off-old", "https://push.example.com/off-new")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["device_active"])
        self.assertFalse(get_push_subscription_by_endpoint("https://push.example.com/off-new")["is_active"])

        _subscribe_row(self.org, self.user_a, "https://push.example.com/steal-old")
        _subscribe_row(self.org_b, self.user_b, "https://push.example.com/victim")
        response = self._resubscribe(app.test_client(), "https://push.example.com/steal-old", "https://push.example.com/victim")
        self.assertEqual(response.status_code, 409)
        victim = get_push_subscription_by_endpoint("https://push.example.com/victim")
        self.assertEqual(victim["user_id"], self.user_b)
        self.assertEqual(victim["organization_id"], self.org_b)
        self.assertIsNotNone(get_push_subscription_by_endpoint("https://push.example.com/steal-old"))

    # Service worker and front-end wiring.

    def test_service_worker_handles_rotation_badge_and_broadcast(self):
        source = SERVICE_WORKER.read_text(encoding="utf-8")
        self.assertIn('const CACHE_NAME = "jrh-one-static-v7";', source)
        self.assertIn('addEventListener("pushsubscriptionchange"', source)
        self.assertIn("/api/push/resubscribe", source)
        self.assertIn("old_auth", source)
        self.assertIn("setAppBadge", source)
        self.assertIn("clearAppBadge", source)
        self.assertIn("postMessage", source)
        self.assertRegex(
            source,
            r'type:\s*"notification-received",\s*unread_count:[^,]+,\s*notification_id:',
        )
        push_handler = source.split('addEventListener("push"', 1)[1].split("addEventListener(", 1)[0]
        self.assertIn("showNotification", push_handler)
        self.assertIn("broadcastToClients", push_handler)
        self.assertIn("updateAppBadge", push_handler)

    def test_pages_load_live_bell_and_soft_prompt(self):
        body = self._client(self.user_a, self.org).get("/notifications").get_data(as_text=True)
        self.assertIn("js/notifications-live.js", body)
        self.assertIn("js/notifications-center.js", body)
        self.assertIn('id="push-soft-prompt"', body)
        self.assertIn("Activá las notificaciones para recibir recordatorios y novedades importantes.", body)
        self.assertIn("agregá JRH One a tu pantalla de inicio", body)
        settings_page = self._client(self.user_a, self.org).get("/settings/notifications").get_data(as_text=True)
        self.assertNotIn('id="push-soft-prompt"', settings_page)
        self.assertIn("data-push-eligible", settings_page)

    def test_live_script_updates_badges_and_listens_to_worker(self):
        root = SERVICE_WORKER.parent / "js"
        live = (root / "notifications-live.js").read_text(encoding="utf-8")
        self.assertIn("/api/notifications/unread-count", live)
        self.assertIn("[data-unread-badge]", live)
        self.assertIn('"99+"', live)
        self.assertIn("notification-received", live)
        self.assertIn("visibilitychange", live)
        self.assertIn("setAppBadge", live)
        pwa = (root / "pwa.js").read_text(encoding="utf-8")
        self.assertIn("resyncSubscription", pwa)
        self.assertIn("jrh_push_prompt_dismissed_at", pwa)
        self.assertIn("ios_install_required", pwa)


if __name__ == "__main__":
    unittest.main()
