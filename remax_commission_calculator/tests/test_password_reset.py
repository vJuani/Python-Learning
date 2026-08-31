"""Password reset flow tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from modules.auth import hash_password, verify_password
from modules.password_reset import (
    complete_password_reset,
    generate_reset_token,
    verify_reset_token,
)


class _FakeApp:
  config = {"SECRET_KEY": "test-password-reset-secret"}


class PasswordResetTests(unittest.TestCase):
    def test_token_round_trip(self):
        with patch(
            "modules.password_reset.current_app",
            _FakeApp(),
        ):
            token = generate_reset_token(42)
            self.assertEqual(verify_reset_token(token), 42)

    def test_invalid_token_returns_none(self):
        with patch(
            "modules.password_reset.current_app",
            _FakeApp(),
        ):
            self.assertIsNone(verify_reset_token("not-a-valid-token"))

    def test_complete_reset_validates_policy(self):
        fake_user = {
            "id": 1,
            "is_active": True,
            "account_status": "active",
        }

        with patch(
            "modules.password_reset.current_app",
            _FakeApp(),
        ), patch(
            "modules.password_reset.get_user_by_id",
            return_value=fake_user,
        ), patch(
            "modules.password_reset.update_user_password",
        ):
            token = generate_reset_token(1)
            error = complete_password_reset(token, "short", "short")
            self.assertEqual(error, "err_password_short")


if __name__ == "__main__":
    unittest.main()
