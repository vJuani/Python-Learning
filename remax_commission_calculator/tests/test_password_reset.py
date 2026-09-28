"""Password reset must not pick an arbitrary organization."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_password_reset.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
os.environ["APP_ENV"] = "development"

from modules.auth import ROLE_AGENT, hash_password, verify_password
from modules.config import apply_config
from modules.database import add_organization, add_user, create_tables
from modules.database.users_repository import get_user_by_id
from modules.password_reset import (
    RESET_MAX_AGE_SECONDS,
    active_users_for_identifier,
    complete_password_reset,
    generate_reset_token,
    request_password_reset,
    verify_reset_token,
)
from modules.passwords import validate_password_policy
from web_app import app


class PasswordResetTokenTests(unittest.TestCase):
    def setUp(self):
        apply_config(app)
        self.context = app.test_request_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def test_token_binds_user_and_organization(self):
        token = generate_reset_token(42, 7)
        identity = verify_reset_token(token)
        self.assertEqual(identity["user_id"], 42)
        self.assertEqual(identity["organization_id"], 7)

    def test_tampered_token_is_rejected(self):
        token = generate_reset_token(42, 7)
        self.assertIsNone(verify_reset_token(token[:-1] + ("a" if token[-1] != "a" else "b")))

    def test_expired_token_is_rejected(self):
        token = generate_reset_token(42, 7)
        with patch("modules.password_reset.RESET_MAX_AGE_SECONDS", -1):
            self.assertIsNone(verify_reset_token(token))
        self.assertGreater(RESET_MAX_AGE_SECONDS, 0)

    def test_token_without_purpose_is_rejected(self):
        from modules.password_reset import _serializer

        token = _serializer().dumps({"user_id": 42})
        self.assertIsNone(verify_reset_token(token))

    def test_complete_reset_validates_policy(self):
        fake_user = {
            "id": 1,
            "organization_id": 3,
            "is_active": True,
            "account_status": "active",
        }
        with patch(
            "modules.password_reset.get_user_by_id",
            return_value=fake_user,
        ), patch(
            "modules.password_reset.update_user_password",
        ):
            token = generate_reset_token(1, 3)
            error = complete_password_reset(token, "short", "short")
            self.assertEqual(error, "err_password_short")
            self.assertEqual(validate_password_policy("short", "short"), "err_password_short")


class PasswordResetTenantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="password-reset-tenant")
        create_tables()
        cls.org_a = add_organization("Oficina Norte")
        cls.org_b = add_organization("Oficina Sur")
        cls.shared = "shared.reset@example.com"
        cls.user_a = add_user(
            "reset_norte",
            hash_password("OldPassword1"),
            ROLE_AGENT,
            cls.org_a,
            email=cls.shared,
            first_name="Norte",
        )
        cls.user_b = add_user(
            "reset_sur",
            hash_password("OldPassword1"),
            ROLE_AGENT,
            cls.org_b,
            email=cls.shared,
            first_name="Sur",
        )
        cls.inactive = add_user(
            "reset_inactive",
            hash_password("OldPassword1"),
            ROLE_AGENT,
            add_organization("Oficina Cerrada"),
            email="inactive.reset@example.com",
            is_active=False,
            account_status="disabled",
        )
        cls.only = add_user(
            "reset_only",
            hash_password("OldPassword1"),
            ROLE_AGENT,
            cls.org_a,
            email="only.reset@example.com",
        )

    def setUp(self):
        self.context = app.test_request_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def test_unique_email_sends_one_link(self):
        with patch("modules.password_reset.send_password_reset_email") as send:
            request_password_reset("only.reset@example.com")
        self.assertEqual(send.call_count, 1)
        self.assertIsNone(send.call_args.kwargs.get("choices"))
        identity = verify_reset_token(send.call_args.args[1].rsplit("/", 1)[-1])
        self.assertEqual(identity["user_id"], self.only)
        self.assertEqual(identity["organization_id"], self.org_a)

    def test_same_email_in_two_organizations_sends_distinct_links(self):
        matches = active_users_for_identifier(self.shared)
        self.assertEqual({user["id"] for user in matches}, {self.user_a, self.user_b})
        with patch("modules.password_reset.send_password_reset_email") as send:
            request_password_reset(self.shared)
        self.assertEqual(send.call_count, 1)
        choices = send.call_args.kwargs["choices"]
        self.assertEqual(len(choices), 2)
        identities = [
            verify_reset_token(item["url"].rsplit("/", 1)[-1])
            for item in choices
        ]
        self.assertEqual(
            {(item["user_id"], item["organization_id"]) for item in identities},
            {(self.user_a, self.org_a), (self.user_b, self.org_b)},
        )
        labels = {item["label"] for item in choices}
        self.assertIn("Oficina Norte", labels)
        self.assertIn("Oficina Sur", labels)

    def test_token_for_org_a_does_not_change_org_b(self):
        token = generate_reset_token(self.user_a, self.org_a)
        before_b = get_user_by_id(self.user_b)["password_hash"]
        error = complete_password_reset(token, "NewPassword1", "NewPassword1")
        self.assertIsNone(error)
        self.assertTrue(
            verify_password(get_user_by_id(self.user_a)["password_hash"], "NewPassword1")
        )
        self.assertEqual(get_user_by_id(self.user_b)["password_hash"], before_b)
        wrong = generate_reset_token(self.user_a, self.org_b)
        self.assertEqual(
            complete_password_reset(wrong, "OtherPassword1", "OtherPassword1"),
            "reset_password_invalid",
        )
        self.assertTrue(
            verify_password(get_user_by_id(self.user_a)["password_hash"], "NewPassword1")
        )

    def test_inactive_user_is_not_emailed_and_cannot_complete(self):
        with patch("modules.password_reset.send_password_reset_email") as send:
            request_password_reset("inactive.reset@example.com")
        send.assert_not_called()
        token = generate_reset_token(self.inactive, get_user_by_id(self.inactive)["organization_id"])
        self.assertEqual(
            complete_password_reset(token, "NewPassword1", "NewPassword1"),
            "reset_password_invalid",
        )

    def test_public_response_does_not_reveal_existence(self):
        client = app.test_client()
        with patch("modules.password_reset.send_password_reset_email"):
            known = client.post("/forgot-password", data={"email": self.shared})
            unknown = client.post("/forgot-password", data={"email": "nobody@example.com"})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        known_html = known.get_data(as_text=True)
        unknown_html = unknown.get_data(as_text=True)
        self.assertIn("Si el email está registrado", known_html)
        self.assertIn("Si el email está registrado", unknown_html)
        self.assertNotIn("Oficina Norte", known_html)
        self.assertNotIn("Oficina Sur", known_html)
        self.assertNotIn(self.shared, known_html)
        self.assertEqual(
            known_html.split("auth-feedback-text")[1],
            unknown_html.split("auth-feedback-text")[1],
        )
