"""
Email provider factory and SMTP provider tests.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_ENV", "development")

from modules.email_providers import (
    EmailDeliveryError,
    OutboundEmail,
    get_email_provider,
    reset_email_provider_cache,
)
from modules.email_providers.smtp import SmtpEmailProvider


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

    def test_resend_preset_defaults(self):
        with patch.dict(
            os.environ,
            {
                "EMAIL_BACKEND": "resend",
                "APP_ENV": "development",
                "SMTP_PASSWORD": "re_test",
                "EMAIL_FROM": "test@example.com",
            },
            clear=False,
        ):
            reset_email_provider_cache()
            provider = get_email_provider()
            self.assertEqual(provider.backend_name, "resend")
            cfg = provider._config()
            self.assertEqual(cfg["host"], "smtp.resend.com")
            self.assertEqual(cfg["username"], "resend")

    @patch("smtplib.SMTP")
    def test_smtp_send_success(self, mock_smtp_cls):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.send_message.return_value = {}
        mock_smtp_cls.return_value = smtp

        provider = SmtpEmailProvider(preset="resend")
        with patch.dict(
            os.environ,
            {
                "SMTP_PASSWORD": "re_test",
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
