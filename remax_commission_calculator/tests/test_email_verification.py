"""
Email verification flow tests (registration codes).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_email_verification.db"
)
os.environ["EMAIL_BACKEND"] = "console"
os.environ["APP_ENV"] = "development"

from modules.access_codes import (
    generate_email_verification_code,
    hash_access_secret,
)
from modules.auth import ROLE_ADMIN, hash_password
from modules.config import apply_config
from modules.database import (
    add_organization,
    add_user,
    create_tables,
    create_email_verification_token,
    get_active_verification_token,
    get_registration_request,
    set_registration_code,
)
from modules.email_delivery import (
    EmailDeliveryError,
    send_verification_code_email,
)
from modules.registration import (
    RESEND_COOLDOWN_SECONDS,
    resend_verification_code,
    submit_agent_registration,
    validate_registration_form,
    verify_registration_code,
)
from web_app import app


class EmailVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org = add_organization("Email Test Org")
        cls.raw_org_code = "ORG-EMAIL-TEST"
        set_registration_code(
            cls.org,
            hash_access_secret(cls.raw_org_code),
            enabled=True,
        )
        pwd = hash_password("Password1")
        cls.admin_id = add_user(
            "email_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
        )

    def _registration_form(self, email="agent@example.com"):
        return {
            "first_name": "Agent",
            "last_name": "Test",
            "email": email,
            "phone": "",
            "organization_code": self.raw_org_code,
            "password": "Password1!",
            "confirm_password": "Password1!",
        }

    def test_validate_email_normalized(self):
        errors, parsed = validate_registration_form(
            {
                **self._registration_form(),
                "email": "  Agent@Example.COM ",
            }
        )
        self.assertEqual(errors, [])
        self.assertEqual(parsed["email"], "agent@example.com")

    def test_validate_email_invalid(self):
        errors, _parsed = validate_registration_form(
            {
                **self._registration_form(),
                "email": "not-an-email",
            }
        )
        self.assertIn("err_email_invalid", errors)

    @patch(
        "modules.registration.send_verification_code_email"
    )
    def test_registration_sends_verification_email(
        self,
        mock_send,
    ):
        errors, result = submit_agent_registration(
            self._registration_form(),
            language="es",
        )
        self.assertEqual(errors, [])
        self.assertEqual(result["action"], "created")
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], "agent@example.com")

    @patch(
        "modules.registration.send_verification_code_email",
        side_effect=EmailDeliveryError(
            "err_verify_email_send_failed",
            detail="smtp rejected",
        ),
    )
    def test_registration_email_failure_surfaces_error(
        self,
        _mock_send,
    ):
        errors, result = submit_agent_registration(
            self._registration_form(
                email="fail@example.com"
            ),
            language="es",
        )
        self.assertEqual(
            errors,
            ["err_verify_email_send_failed"],
        )
        self.assertIsNone(result)

    def test_console_backend_sends_in_development(self):
        with patch.dict(
            os.environ,
            {
                "EMAIL_BACKEND": "console",
                "APP_ENV": "development",
            },
            clear=False,
        ):
            send_verification_code_email(
                "  Dev@Example.com ",
                "123456",
                language="es",
            )

    def test_console_backend_blocked_when_deployed(self):
        with patch.dict(
            os.environ,
            {
                "EMAIL_BACKEND": "console",
                "APP_ENV": "staging",
            },
            clear=False,
        ):
            with self.assertRaises(EmailDeliveryError) as ctx:
                send_verification_code_email(
                    "agent@example.com",
                    "123456",
                )
            self.assertEqual(
                ctx.exception.error_key,
                "err_verify_email_not_configured",
            )

    @patch("modules.email_delivery._send_smtp_email")
    def test_smtp_provider_failure(self, mock_smtp):
        mock_smtp.side_effect = RuntimeError(
            "SMTP refused recipient"
        )
        with patch.dict(
            os.environ,
            {
                "EMAIL_BACKEND": "smtp",
                "APP_ENV": "development",
            },
            clear=False,
        ):
            with self.assertRaises(EmailDeliveryError) as ctx:
                send_verification_code_email(
                    "agent@example.com",
                    "123456",
                )
            self.assertEqual(
                ctx.exception.error_key,
                "err_verify_email_send_failed",
            )

    def _create_pending_request(self, email="verify@example.com"):
        errors, result = submit_agent_registration(
            self._registration_form(email=email),
            language="es",
        )
        self.assertEqual(errors, [])
        return result["request_id"]

    @patch(
        "modules.registration.send_verification_code_email"
    )
    def test_verify_correct_code(self, mock_send):
        captured = {}

        def _capture(email, code, language="es"):
            captured["code"] = code

        mock_send.side_effect = _capture
        request_id = self._create_pending_request(
            email="good@example.com"
        )
        ok, error_key = verify_registration_code(
            request_id,
            captured["code"],
        )
        self.assertTrue(ok)
        self.assertIsNone(error_key)

    @patch(
        "modules.registration.send_verification_code_email"
    )
    def test_verify_wrong_code(self, mock_send):
        request_id = self._create_pending_request(
            email="wrong@example.com"
        )
        ok, error_key = verify_registration_code(
            request_id,
            "000000",
        )
        self.assertFalse(ok)
        self.assertEqual(
            error_key,
            "err_verify_code_invalid",
        )

    def test_verify_expired_code(self):
        request_id = self._create_pending_request(
            email="expired@example.com"
        )
        raw_code = "654321"
        connection = __import__(
            "modules.database.connection",
            fromlist=["get_connection"],
        ).get_connection()
        cursor = connection.cursor()
        expired = (
            datetime.utcnow() - timedelta(minutes=1)
        ).replace(microsecond=0).isoformat()
        cursor.execute(
            """
            INSERT INTO email_verification_tokens (
                registration_request_id,
                token_hash,
                expires_at,
                created_at,
                attempt_count,
                last_sent_at
            )
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (
                request_id,
                hash_access_secret(raw_code),
                expired,
                expired,
                expired,
            ),
        )
        connection.commit()
        connection.close()

        ok, error_key = verify_registration_code(
            request_id,
            raw_code,
        )
        self.assertFalse(ok)
        self.assertEqual(error_key, "err_verify_expired")

    @patch(
        "modules.registration.send_verification_code_email"
    )
    def test_resend_success(self, mock_send):
        request_id = self._create_pending_request(
            email="resend@example.com"
        )
        token = get_active_verification_token(request_id)
        connection = __import__(
            "modules.database.connection",
            fromlist=["get_connection"],
        ).get_connection()
        cursor = connection.cursor()
        old = (
            datetime.utcnow()
            - timedelta(seconds=RESEND_COOLDOWN_SECONDS + 5)
        ).replace(microsecond=0).isoformat()
        cursor.execute(
            """
            UPDATE email_verification_tokens
            SET last_sent_at = ?
            WHERE id = ?
            """,
            (old, token["id"]),
        )
        connection.commit()
        connection.close()

        ok, error_key = resend_verification_code(
            request_id,
            language="es",
        )
        self.assertTrue(ok)
        self.assertIsNone(error_key)
        self.assertEqual(mock_send.call_count, 2)

    @patch(
        "modules.registration.send_verification_code_email"
    )
    def test_resend_cooldown(self, mock_send):
        request_id = self._create_pending_request(
            email="cooldown@example.com"
        )
        token = get_active_verification_token(request_id)
        connection = __import__(
            "modules.database.connection",
            fromlist=["get_connection"],
        ).get_connection()
        cursor = connection.cursor()
        now = datetime.utcnow().replace(
            microsecond=0
        ).isoformat()
        cursor.execute(
            """
            UPDATE email_verification_tokens
            SET last_sent_at = ?
            WHERE id = ?
            """,
            (now, token["id"]),
        )
        connection.commit()
        connection.close()

        ok, error_key = resend_verification_code(
            request_id,
            language="es",
        )
        self.assertFalse(ok)
        self.assertEqual(
            error_key,
            "err_verify_resend_cooldown",
        )
        self.assertEqual(mock_send.call_count, 1)

    @patch(
        "modules.registration.send_verification_code_email"
    )
    def test_agent_registration_flow_end_to_end(
        self,
        mock_send,
    ):
        errors, result = submit_agent_registration(
            self._registration_form(
                email="agent.flow@example.com"
            ),
            language="es",
        )
        self.assertEqual(errors, [])
        request_data = get_registration_request(
            result["request_id"]
        )
        self.assertEqual(
            request_data["status"],
            "email_pending",
        )
        self.assertEqual(
            request_data["email"],
            "agent.flow@example.com",
        )
        mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
