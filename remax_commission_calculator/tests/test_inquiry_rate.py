"""Public inquiry rate limit is stored in the database, not process memory."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["APP_ENV"] = "development"
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "rate.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")

from modules.database import create_tables
from modules.database.connection import get_connection
from modules.inbound_inquiry import (
    RATE_LIMIT,
    allow_inquiry_rate,
    reset_inquiry_rate_limits,
)


class InquiryRateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_tables()

    @classmethod
    def tearDownClass(cls):
        _TMP.cleanup()

    def setUp(self):
        reset_inquiry_rate_limits()

    def test_five_per_token_per_hour_survives_a_new_lookup(self):
        now = time.time()
        for _ in range(RATE_LIMIT):
            self.assertTrue(allow_inquiry_rate("token-a", now=now))
        self.assertFalse(allow_inquiry_rate("token-a", now=now))
        self.assertTrue(allow_inquiry_rate("token-b", now=now))

    def test_window_expires_and_reset_clears_hits(self):
        now = time.time()
        for _ in range(RATE_LIMIT):
            allow_inquiry_rate("token-a", now=now)
        self.assertFalse(allow_inquiry_rate("token-a", now=now))
        later = now + 3601
        self.assertTrue(allow_inquiry_rate("token-a", now=later))
        reset_inquiry_rate_limits()
        connection = get_connection()
        count = connection.execute(
            "SELECT COUNT(*) FROM public_inquiry_rate_hits"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(count, 0)

    def test_hits_are_not_keyed_by_ip(self):
        connection = get_connection()
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(public_inquiry_rate_hits)"
            ).fetchall()
        ]
        connection.close()
        self.assertEqual(columns, ["id", "token", "hit_at"])
