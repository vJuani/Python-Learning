"""PWA + Web Push phase 1. Isolated temp DB."""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Throwaway PKCS8 P-256 key used only in tests. Not a production VAPID secret.
TEST_VAPID_PRIVATE_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgYy/9nRYvDz0vEk0u\n"
    "hHXPX2yFZHGT77ubToDeVI5+/9qhRANCAASx80u/hlCDL7+axnBccKcbXLbQmlJp\n"
    "gc4Q/3Q0t3i35TSzvPjxxIK2kMGkMg5t5YZ4G47FgiylSCcCEDz/Zsln\n"
    "-----END PRIVATE KEY-----"
)
TEST_VAPID_PRIVATE_B64 = base64.b64encode(TEST_VAPID_PRIVATE_PEM.encode("utf-8")).decode("ascii")
TEST_VAPID_PUBLIC_KEY = (
    "BLHzS7-GUIMvv5rGcFxwpxtcttCaUmmBzhD_dDS3eLflNLO8-PHEgraQwaQyDm3lhngbjsWCLKVIJwIQPP9myWc"
)
TEST_VAPID_PRIVATE_DER = (
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgYy_9nRYvDz0vEk0uhHXPX2yFZHGT77ubToDeVI5-_9qhRANCAASx80u_hlCDL7-axnBccKcbXLbQmlJpgc4Q_3Q0t3i35TSzvPjxxIK2kMGkMg5t5YZ4G47FgiylSCcCEDz_Zsln"
)
TEST_VAPID_OTHER_PUBLIC = (
    "BIGp9-U3y5GUaCyntr4GjyOV4Shf_HgMGq8JVsHHxbJzGeVeJ9W7fLJvZowY8io9wmWsQ8B_CZS_ALjX29tiYSo"
)

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_pwa_push.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"] = TEST_VAPID_PUBLIC_KEY
os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = TEST_VAPID_PRIVATE_PEM
os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY_B64", None)
os.environ["WEB_PUSH_VAPID_SUBJECT"] = "mailto:admin@jrhone.com"

from pywebpush import WebPushException
from py_vapid import Vapid
from cryptography.hazmat.primitives.asymmetric import ec

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import BASE_DIR, apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.push_subscriptions_repository import (
    get_push_subscription_by_endpoint,
    list_active_push_subscriptions,
    upsert_push_subscription,
)
from modules.web_push import TEST_BODY, TEST_TITLE, TEST_URL, WebPushError, load_vapid_private_key
from web_app import app


class _GoneResponse:
    status_code = 410


class PwaPushTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="pwa-push-tests")
        create_tables()
        cls.org = add_organization("PWA Org")
        cls.agent_id = add_agent("Ana Perez", "Alto", cls.org)
        cls.user_id = add_user(
            "ana_pwa",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Ana",
            last_name="Perez",
        )
        cls.other_user_id = add_user(
            "other_pwa",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
            first_name="Otro",
            last_name="User",
        )

    def setUp(self):
        self.client = app.test_client()

    def _login(self, user_id=None):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id or self.user_id
            session["role"] = ROLE_AGENT
            session["organization_id"] = self.org

    def _subscription_payload(self, suffix="1", user_id=None):
        payload = {
            "endpoint": f"https://push.example.com/device-{suffix}",
            "keys": {
                "p256dh": "BValidP256dhKeyMaterialForTests0123456789abcd",
                "auth": "ValidAuthSecret012345",
            },
            "user_agent": "Mozilla/5.0 (Linux; Android 14) Chrome/120.0.0.0",
        }
        if user_id is not None:
            payload["user_id"] = user_id
        return payload

    def test_01_manifest_loads(self):
        page = self.client.get("/manifest.webmanifest")
        self.assertEqual(page.status_code, 200)
        data = page.get_json()
        if data is None:
            import json

            data = json.loads(page.data.decode("utf-8"))
        self.assertEqual(data["name"], "JRH One")
        self.assertEqual(data["short_name"], "JRH One")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["display"], "standalone")
        sizes = {item["sizes"] for item in data["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        self.assertTrue(any("icon-192.png" in item["src"] for item in data["icons"]))
        self.assertTrue(any("maskable" in item.get("purpose", "") for item in data["icons"]))

    def test_02_service_worker_registers(self):
        page = self.client.get("/static/service-worker.js")
        self.assertEqual(page.status_code, 200)
        body = page.data.decode("utf-8")
        self.assertIn("addEventListener(\"push\"", body)
        self.assertIn("addEventListener(\"notificationclick\"", body)
        self.assertIn("Service-Worker-Allowed", page.headers)
        self.assertEqual(page.headers["Service-Worker-Allowed"], "/")
        html = self.client.get("/login")
        self.assertIn(b"/static/js/pwa.js", html.data)
        self.assertIn(b"service-worker.js", (BASE_DIR / "static" / "js" / "pwa.js").read_bytes())

    def test_03_public_key_endpoint(self):
        self._login()
        page = self.client.get("/api/push/public-key")
        self.assertEqual(page.status_code, 200)
        data = page.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(
            data["public_key"],
            os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"],
        )
        self.assertNotIn("private", str(data).lower())

    def test_04_subscribe_scoped_to_user_and_org(self):
        self._login()
        page = self.client.post(
            "/api/push/subscribe",
            json=self._subscription_payload("scoped"),
        )
        self.assertEqual(page.status_code, 200)
        data = page.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["user_id"], self.user_id)
        self.assertEqual(data["organization_id"], self.org)
        row = get_push_subscription_by_endpoint("https://push.example.com/device-scoped")
        self.assertEqual(row["user_id"], self.user_id)
        self.assertEqual(row["organization_id"], self.org)
        self.assertTrue(row["is_active"])

    def test_05_cannot_subscribe_another_user_id(self):
        self._login()
        page = self.client.post(
            "/api/push/subscribe",
            json=self._subscription_payload("hijack", user_id=self.other_user_id),
        )
        self.assertEqual(page.status_code, 200)
        row = get_push_subscription_by_endpoint("https://push.example.com/device-hijack")
        self.assertEqual(row["user_id"], self.user_id)
        self.assertNotEqual(row["user_id"], self.other_user_id)

    def test_06_unsubscribe(self):
        self._login()
        self.client.post("/api/push/subscribe", json=self._subscription_payload("off"))
        page = self.client.post(
            "/api/push/unsubscribe",
            json={"endpoint": "https://push.example.com/device-off"},
        )
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.get_json()["deactivated"])
        row = get_push_subscription_by_endpoint("https://push.example.com/device-off")
        self.assertFalse(row["is_active"])

    def test_07_test_push_only_current_user(self):
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example.com/mine",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        upsert_push_subscription(
            self.org,
            self.other_user_id,
            endpoint="https://push.example.com/theirs",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        self._login()
        seen = []

        def _capture(subscription_info, **kwargs):
            seen.append(subscription_info["endpoint"])
            self.assertIsInstance(kwargs.get("vapid_private_key"), Vapid)
            self.assertNotIsInstance(kwargs.get("vapid_private_key"), str)
            return None

        with patch("pywebpush.webpush", side_effect=_capture):
            page = self.client.post("/api/push/test", json={})
        self.assertEqual(page.status_code, 200)
        data = page.get_json()
        self.assertGreaterEqual(data["sent"], 1)
        self.assertIn("https://push.example.com/mine", seen)
        self.assertNotIn("https://push.example.com/theirs", seen)
        self.assertEqual(data["payload"]["title"], TEST_TITLE)
        self.assertEqual(data["payload"]["body"], TEST_BODY)
        self.assertEqual(data["payload"]["url"], TEST_URL)

    def test_08_gone_subscription_is_deactivated(self):
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example.com/gone",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        self._login()

        def _gone(*args, **kwargs):
            raise WebPushException("Gone", response=_GoneResponse())

        with patch("pywebpush.webpush", side_effect=_gone):
            page = self.client.post("/api/push/test", json={})
        self.assertEqual(page.status_code, 502)
        row = get_push_subscription_by_endpoint("https://push.example.com/gone")
        self.assertFalse(row["is_active"])
        self.assertFalse(
            any(
                item["endpoint"] == "https://push.example.com/gone"
                for item in list_active_push_subscriptions(self.org, self.user_id)
            )
        )

    def test_09_missing_vapid_is_clear_error(self):
        self._login()
        public = os.environ.pop("WEB_PUSH_VAPID_PUBLIC_KEY", None)
        private = os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY", None)
        private_b64 = os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY_B64", None)
        try:
            page = self.client.get("/api/push/public-key")
            self.assertEqual(page.status_code, 503)
            self.assertEqual(page.get_json()["error"], "pwa_push_err_vapid_missing")
        finally:
            if public is not None:
                os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"] = public
            if private is not None:
                os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = private
            if private_b64 is not None:
                os.environ["WEB_PUSH_VAPID_PRIVATE_KEY_B64"] = private_b64

    def test_10_service_worker_skips_private_routes(self):
        body = (BASE_DIR / "static" / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn("request.method !== \"GET\"", body)
        self.assertIn("/api/", body)
        self.assertIn("/billing", body)
        self.assertIn("/operations", body)
        self.assertIn("/cash", body)
        self.assertIn("/treasury", body)
        self.assertIn("isPrivatePath", body)
        self.assertIn("caches.match", body)
        self.assertIn("jrh-one-static-v3", body)
        self.assertIn("/static/js/pwa.js", body)
        notifications = self.client.get("/settings/notifications")
        self.assertIn(notifications.status_code, {302, 401})
        self._login()
        page = self.client.get("/settings/notifications")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Activar notificaciones".encode("utf-8"), page.data)
        self.assertIn("Enviar notificación de prueba".encode("utf-8"), page.data)
        self.assertIn("Desactivar notificaciones".encode("utf-8"), page.data)
        self.assertIn("Restablecer notificaciones".encode("utf-8"), page.data)
        self.assertIn("Desactivadas".encode("utf-8"), page.data)
        self.assertIn("Habilitadas".encode("utf-8"), page.data)
        self.assertIn("Bloqueadas por el navegador".encode("utf-8"), page.data)
        self.assertIn(b"id=\"pwa-push-disable\"", page.data)
        self.assertIn(b"id=\"pwa-push-reset\"", page.data)
        self.assertIn(b"id=\"pwa-push-blocked-help\"", page.data)

    def test_11_invalid_vapid_private_key_is_clear_error(self):
        self._login()
        private = os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY", None)
        private_b64 = os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY_B64", None)
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = "not-a-pem"
        try:
            page = self.client.get("/api/push/public-key")
            body = page.get_data(as_text=True)
            self.assertEqual(page.status_code, 503)
            self.assertEqual(page.get_json()["error"], "pwa_push_err_invalid_vapid_private_key")
            self.assertNotIn("Could not deserialize", body)
            self.assertNotIn("Traceback", body)
        finally:
            if private is not None:
                os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = private
            else:
                os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY", None)
            if private_b64 is not None:
                os.environ["WEB_PUSH_VAPID_PRIVATE_KEY_B64"] = private_b64
            else:
                os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY_B64", None)

    def test_12_unsubscribe_only_current_device(self):
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example.com/phone",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example.com/laptop",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        self._login()
        page = self.client.post(
            "/api/push/unsubscribe",
            json={"endpoint": "https://push.example.com/phone", "user_id": self.other_user_id},
        )
        self.assertEqual(page.status_code, 200)
        data = page.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["deactivated"])
        phone = get_push_subscription_by_endpoint("https://push.example.com/phone")
        laptop = get_push_subscription_by_endpoint("https://push.example.com/laptop")
        self.assertFalse(phone["is_active"])
        self.assertTrue(laptop["is_active"])
        active = {
            item["endpoint"]
            for item in list_active_push_subscriptions(self.org, self.user_id)
        }
        self.assertNotIn("https://push.example.com/phone", active)
        self.assertIn("https://push.example.com/laptop", active)

    def test_13_unsubscribe_is_idempotent(self):
        self._login()
        self.client.post("/api/push/subscribe", json=self._subscription_payload("once"))
        first = self.client.post(
            "/api/push/unsubscribe",
            json={"endpoint": "https://push.example.com/device-once"},
        )
        second = self.client.post(
            "/api/push/unsubscribe",
            json={"endpoint": "https://push.example.com/device-once"},
        )
        missing = self.client.post("/api/push/unsubscribe", json={})
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()["ok"])
        self.assertTrue(first.get_json()["deactivated"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["ok"])
        self.assertEqual(missing.status_code, 200)
        self.assertTrue(missing.get_json()["ok"])
        self.assertFalse(missing.get_json()["deactivated"])
        row = get_push_subscription_by_endpoint("https://push.example.com/device-once")
        self.assertFalse(row["is_active"])

    def test_14_cannot_unsubscribe_another_users_subscription(self):
        upsert_push_subscription(
            self.org,
            self.other_user_id,
            endpoint="https://push.example.com/theirs-off",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        self._login()
        page = self.client.post(
            "/api/push/unsubscribe",
            json={
                "endpoint": "https://push.example.com/theirs-off",
                "user_id": self.other_user_id,
            },
        )
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.get_json()["ok"])
        self.assertFalse(page.get_json()["deactivated"])
        row = get_push_subscription_by_endpoint("https://push.example.com/theirs-off")
        self.assertEqual(row["user_id"], self.other_user_id)
        self.assertTrue(row["is_active"])

    def test_15_notifications_ui_toggles_by_state(self):
        self._login()
        page = self.client.get("/settings/notifications")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Desactivadas", html)
        self.assertIn("Habilitadas", html)
        self.assertIn("Bloqueadas por el navegador", html)
        self.assertIn('id="pwa-push-enable"', html)
        self.assertIn("Activar notificaciones", html)
        self.assertIn("Desactivar notificaciones", html)
        self.assertIn("Restablecer notificaciones", html)
        self.assertRegex(html, r'id="pwa-push-disable"[^>]*>\s*Desactivar notificaciones')
        self.assertNotRegex(html, r'id="pwa-push-disable"[^>]*hidden')
        self.assertRegex(html, r'id="pwa-push-reset"[^>]*>\s*Restablecer notificaciones')
        self.assertNotRegex(html, r'id="pwa-push-reset"[^>]*hidden')
        script = (BASE_DIR / "static" / "js" / "pwa.js").read_text(encoding="utf-8")
        self.assertIn("pushManager.getSubscription()", script)
        self.assertIn("subscription.unsubscribe()", script)
        self.assertIn("/api/push/unsubscribe", script)
        self.assertIn("/api/push/reset", script)
        self.assertIn("pwa-push-disable", script)
        self.assertIn("pwa-push-reset", script)
        self.assertIn('show("pwa-push-enable", state === "disabled")', script)
        self.assertIn('show("pwa-push-test", state === "enabled")', script)
        self.assertIn('show("pwa-push-disable", state === "enabled")', script)
        self.assertIn("labelDisabledOk", script)
        self.assertNotIn("user_id", script.split("/api/push/unsubscribe")[1][:400])

    def test_16_disable_button_visible_when_subscription_active(self):
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example.com/active-ui",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        self._login()
        page = self.client.get("/settings/notifications")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-has-active="1"', html)
        self.assertIn("Habilitadas", html)
        self.assertRegex(html, r'id="pwa-push-disable"[^>]*>\s*Desactivar notificaciones')
        self.assertNotRegex(html, r'id="pwa-push-disable"[^>]*hidden')
        self.assertRegex(html, r'id="pwa-push-reset"[^>]*>\s*Restablecer notificaciones')
        self.assertNotRegex(html, r'id="pwa-push-reset"[^>]*hidden')
        self.assertRegex(html, r'id="pwa-push-enable"[^>]*hidden')
        self.assertNotRegex(html, r'id="pwa-push-test"[^>]*hidden')

    def test_17_reset_deactivates_only_current_user_subscriptions(self):
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example.com/me-one",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        upsert_push_subscription(
            self.org,
            self.user_id,
            endpoint="https://push.example.com/me-two",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        upsert_push_subscription(
            self.org,
            self.other_user_id,
            endpoint="https://push.example.com/other-keep",
            p256dh="BValidP256dhKeyMaterialForTests0123456789abcd",
            auth="ValidAuthSecret012345",
        )
        self._login()
        page = self.client.post(
            "/api/push/reset",
            json={"user_id": self.other_user_id},
        )
        self.assertEqual(page.status_code, 200)
        data = page.get_json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["deactivated"], 2)
        mine = list_active_push_subscriptions(self.org, self.user_id)
        self.assertEqual(mine, [])
        other = get_push_subscription_by_endpoint("https://push.example.com/other-keep")
        self.assertEqual(other["user_id"], self.other_user_id)
        self.assertTrue(other["is_active"])
        again = self.client.post("/api/push/reset", json={})
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.get_json()["ok"])


class VapidPrivateKeyLoaderTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "WEB_PUSH_VAPID_PUBLIC_KEY": os.environ.get("WEB_PUSH_VAPID_PUBLIC_KEY"),
            "WEB_PUSH_VAPID_PRIVATE_KEY": os.environ.get("WEB_PUSH_VAPID_PRIVATE_KEY"),
            "WEB_PUSH_VAPID_PRIVATE_KEY_B64": os.environ.get("WEB_PUSH_VAPID_PRIVATE_KEY_B64"),
        }
        os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"] = TEST_VAPID_PUBLIC_KEY

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _clear_private_sources(self):
        os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY", None)
        os.environ.pop("WEB_PUSH_VAPID_PRIVATE_KEY_B64", None)

    def _assert_valid_vapid(self, vapid):
        self.assertIsInstance(vapid, Vapid)
        self.assertIsInstance(vapid.private_key.curve, ec.SECP256R1)

    def test_valid_private_key_b64(self):
        self._clear_private_sources()
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = "not-a-pem"
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY_B64"] = TEST_VAPID_PRIVATE_B64
        self._assert_valid_vapid(load_vapid_private_key())

    def test_valid_multiline_pem(self):
        self._clear_private_sources()
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = TEST_VAPID_PRIVATE_PEM
        self._assert_valid_vapid(load_vapid_private_key())

    def test_escaped_newlines_pem(self):
        self._clear_private_sources()
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = TEST_VAPID_PRIVATE_PEM.replace("\n", "\\n")
        self._assert_valid_vapid(load_vapid_private_key())

    def test_valid_der_base64url(self):
        self._clear_private_sources()
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = TEST_VAPID_PRIVATE_DER
        self._assert_valid_vapid(load_vapid_private_key())

    def test_private_public_mismatch(self):
        self._clear_private_sources()
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = TEST_VAPID_PRIVATE_PEM
        os.environ["WEB_PUSH_VAPID_PUBLIC_KEY"] = TEST_VAPID_OTHER_PUBLIC
        with self.assertRaises(WebPushError) as raised:
            load_vapid_private_key()
        self.assertEqual(raised.exception.message_key, "pwa_push_err_vapid_key_mismatch")
        self.assertEqual(raised.exception.status_code, 503)

    def test_invalid_private_key(self):
        self._clear_private_sources()
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY"] = "dummy-private-key-not-a-pem"
        with self.assertRaises(WebPushError) as raised:
            load_vapid_private_key()
        self.assertEqual(raised.exception.message_key, "pwa_push_err_invalid_vapid_private_key")
        self.assertEqual(raised.exception.status_code, 503)

        self._clear_private_sources()
        os.environ["WEB_PUSH_VAPID_PRIVATE_KEY_B64"] = "%%%not-valid-base64%%%"
        with self.assertRaises(WebPushError) as raised:
            load_vapid_private_key()
        self.assertEqual(raised.exception.message_key, "pwa_push_err_invalid_vapid_private_key")


if __name__ == "__main__":
    unittest.main()
