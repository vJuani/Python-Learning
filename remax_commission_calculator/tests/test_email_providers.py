"""
Email provider factory and Resend API provider tests.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import requests

os.environ.setdefault("APP_ENV", "development")

from modules.email_delivery import (
    send_password_reset_email,
    send_verification_code_email,
)
from modules.email_providers import (
    EmailDeliveryError,
    OutboundEmail,
    get_email_provider,
    reset_email_provider_cache,
)
from modules.email_providers.resend_api import ResendApiEmailProvider
from modules.email_providers.smtp import SmtpEmailProvider


def _resend_env():
    return {
        "EMAIL_BACKEND": "resend",
        "APP_ENV": "development",
        "RESEND_API_KEY": "re_test_key",
        "EMAIL_FROM": "JRH One <noreply@jrhone.com>",
    }


class EmailProviderTests(unittest.TestCase):
    def tearDown(self):
        reset_email_provider_cache()

    def test_console_provider_in_development(self):
        with patch.dict(
            os.environ,
            {
                "EMAIL_BACKEND": "console",
                "APP_ENV": "development",
            },
            clear=False,
        ):
            reset_email_provider_cache()
            provider = get_email_provider()
            self.assertEqual(provider.backend_name, "console")

    def test_console_blocked_in_staging(self):
        with patch.dict(
            os.environ,
            {
                "EMAIL_BACKEND": "console",
                "APP_ENV": "staging",
            },
            clear=False,
        ):
            reset_email_provider_cache()
            with self.assertRaises(EmailDeliveryError) as ctx:
                get_email_provider()
            self.assertEqual(
                ctx.exception.error_key,
                "err_verify_email_not_configured",
            )

    def test_resend_api_provider_selected(self):
        with patch.dict(os.environ, _resend_env(), clear=False):
            reset_email_provider_cache()
            provider = get_email_provider()
            self.assertIsInstance(provider, ResendApiEmailProvider)
            self.assertEqual(provider.backend_name, "resend")

    def test_resend_config_valid_without_smtp_password(self):
        env = {
            "EMAIL_BACKEND": "resend",
            "APP_ENV": "staging",
            "RESEND_API_KEY": "re_test_key",
            "EMAIL_FROM": "JRH One <noreply@jrhone.com>",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SMTP_PASSWORD", None)
            reset_email_provider_cache()
            provider = get_email_provider()
            self.assertIsInstance(provider, ResendApiEmailProvider)
            provider.validate_config()

    def test_smtp_config_requires_smtp_password(self):
        with patch.dict(
            os.environ,
            {
                "EMAIL_BACKEND": "smtp",
                "APP_ENV": "development",
                "SMTP_HOST": "smtp.example.com",
                "EMAIL_FROM": "noreply@example.com",
            },
            clear=False,
        ):
            os.environ.pop("SMTP_PASSWORD", None)
            reset_email_provider_cache()
            provider = get_email_provider()
            self.assertIsInstance(provider, SmtpEmailProvider)
            with self.assertRaises(EmailDeliveryError) as ctx:
                provider.validate_config()
            self.assertIn("SMTP_PASSWORD", ctx.exception.detail)

    @patch("modules.email_providers.resend_api.requests.post")
    def test_resend_api_success(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"id": "email_123"}
        mock_post.return_value = response

        provider = ResendApiEmailProvider()
        with patch.dict(os.environ, _resend_env(), clear=False):
            provider.send(
                OutboundEmail(
                    to="user@example.com",
                    subject="Test",
                    text_body="Hello",
                    html_body="<p>Hello</p>",
                )
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(call_kwargs["timeout"], 20)
        self.assertEqual(
            call_kwargs["headers"]["Authorization"],
            "Bearer re_test_key",
        )
        self.assertEqual(
            call_kwargs["json"]["from"],
            "JRH One <noreply@jrhone.com>",
        )
        self.assertEqual(
            call_kwargs["json"]["to"],
            ["user@example.com"],
        )

    @patch("modules.email_providers.resend_api.requests.post")
    def test_resend_api_401(self, mock_post):
        response = MagicMock()
        response.status_code = 401
        response.json.return_value = {
            "message": "invalid api key",
        }
        mock_post.return_value = response

        provider = ResendApiEmailProvider()
        with patch.dict(os.environ, _resend_env(), clear=False):
            with self.assertRaises(EmailDeliveryError) as ctx:
                provider.send(
                    OutboundEmail(
                        to="user@example.com",
                        subject="Test",
                        text_body="Hello",
                    )
                )

        self.assertEqual(
            ctx.exception.error_key,
            "err_verify_email_send_failed",
        )
        self.assertIn("invalid api key", ctx.exception.detail)

    @patch("modules.email_providers.resend_api.requests.post")
    def test_resend_api_422(self, mock_post):
        response = MagicMock()
        response.status_code = 422
        response.json.return_value = {
            "message": "validation_error",
        }
        mock_post.return_value = response

        provider = ResendApiEmailProvider()
        with patch.dict(os.environ, _resend_env(), clear=False):
            with self.assertRaises(EmailDeliveryError):
                provider.send(
                    OutboundEmail(
                        to="bad",
                        subject="Test",
                        text_body="Hello",
                    )
                )

    @patch("modules.email_providers.resend_api.requests.post")
    def test_resend_api_429(self, mock_post):
        response = MagicMock()
        response.status_code = 429
        response.json.return_value = {
            "message": "rate limit exceeded",
        }
        mock_post.return_value = response

        provider = ResendApiEmailProvider()
        with patch.dict(os.environ, _resend_env(), clear=False):
            with self.assertRaises(EmailDeliveryError) as ctx:
                provider.send(
                    OutboundEmail(
                        to="user@example.com",
                        subject="Test",
                        text_body="Hello",
                    )
                )

        self.assertEqual(ctx.exception.error_key, "err_verify_email_send_failed")

    @patch("modules.email_providers.resend_api.requests.post")
    def test_resend_api_5xx(self, mock_post):
        response = MagicMock()
        response.status_code = 503
        response.json.return_value = {
            "message": "service unavailable",
        }
        mock_post.return_value = response

        provider = ResendApiEmailProvider()
        with patch.dict(os.environ, _resend_env(), clear=False):
            with self.assertRaises(EmailDeliveryError):
                provider.send(
                    OutboundEmail(
                        to="user@example.com",
                        subject="Test",
                        text_body="Hello",
                    )
                )

    @patch("modules.email_providers.resend_api.requests.post")
    def test_resend_api_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("timed out")

        provider = ResendApiEmailProvider()
        with patch.dict(os.environ, _resend_env(), clear=False):
            with self.assertRaises(EmailDeliveryError) as ctx:
                provider.send(
                    OutboundEmail(
                        to="user@example.com",
                        subject="Test",
                        text_body="Hello",
                    )
                )

        self.assertEqual(ctx.exception.detail, "timed out")

    @patch("modules.email_providers.resend_api.requests.post")
    def test_verification_email_success(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"id": "email_123"}
        mock_post.return_value = response

        with patch.dict(os.environ, _resend_env(), clear=False):
            reset_email_provider_cache()
            send_verification_code_email(
                "agent@example.com",
                "123456",
                language="es",
            )

        payload = mock_post.call_args.kwargs["json"]
        self.assertIn("123456", payload["text"])
        self.assertEqual(payload["to"], ["agent@example.com"])

    @patch("modules.email_providers.resend_api.requests.post")
    def test_password_recovery_email_success(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"id": "email_456"}
        mock_post.return_value = response

        with patch.dict(os.environ, _resend_env(), clear=False):
            reset_email_provider_cache()
            send_password_reset_email(
                "user@example.com",
                "https://app.example.com/reset/token",
                language="es",
            )

        payload = mock_post.call_args.kwargs["json"]
        self.assertIn(
            "https://app.example.com/reset/token",
            payload["text"],
        )
        self.assertEqual(payload["to"], ["user@example.com"])

    @patch("smtplib.SMTP")
    def test_smtp_send_success(self, mock_smtp_cls):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}
        mock_smtp_cls.return_value = smtp

        provider = SmtpEmailProvider()
        with patch.dict(
            os.environ,
            {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PASSWORD": "secret",
                "SMTP_USERNAME": "user",
                "EMAIL_FROM": "noreply@example.com",
            },
            clear=False,
        ):
            provider.send(
                OutboundEmail(
                    to="user@example.com",
                    subject="Test",
                    text_body="Hello",
                )
            )

        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once()


if __name__ == "__main__":
    unittest.main()
