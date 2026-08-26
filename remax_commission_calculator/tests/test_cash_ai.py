"""
Tests for Cash AI (Caja v2) with mocked AI provider.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
_TEST_ROOT = Path(_TEST_TMP.name)
os.environ["DATABASE_PATH"] = str(_TEST_ROOT / "test_cash_ai.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_TEST_ROOT / "uploads")
os.environ["CASH_AI_PROVIDER"] = "mock"

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.cash_ai_service import (
    confirm_ai_draft,
    start_ai_analysis,
)
from modules.cash_treasury import get_balances
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_cash_movement,
    list_cash_movements,
)
from web_app import app


def _png_bytes():
    # Minimal valid 1x1 PNG
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class CashAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-cash-ai"
        create_tables()

        cls.org = add_organization("Cash AI Org")
        cls.other_org = add_organization("Other AI Org")
        pwd = hash_password("Password1")
        agent = add_agent("AI Agent", "Alto", cls.org)
        cls.admin = add_user(
            "cash_ai_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
        )
        cls.agent_user = add_user(
            "cash_ai_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=agent,
        )
        cls.other_admin = add_user(
            "cash_ai_other",
            pwd,
            ROLE_ADMIN,
            cls.other_org,
        )
        cls.password = "Password1"

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        return self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def test_text_only_draft_and_confirm(self):
        draft = start_ai_analysis(
            self.org,
            user_id=self.admin,
            user_context_text=(
                "Gasté 32000 pesos en productos de "
                "limpieza para la oficina. Lo pagué en efectivo."
            ),
        )
        self.assertEqual(draft["status"], "review")
        payload = draft["draft_payload"]
        self.assertEqual(payload["movement_type"], "expense")
        self.assertEqual(payload["currency"], "ARS")
        self.assertAlmostEqual(payload["amount"], 32000)
        self.assertEqual(payload["payment_method"], "cash")
        self.assertEqual(payload["category"], "cleaning")

        # Ensure payment method is valid for confirm
        draft["draft_payload"]["payment_method"] = "cash"
        movement = confirm_ai_draft(
            self.org,
            draft["id"],
            user_id=self.admin,
            confirm_token=draft["confirm_token"],
            form_values={
                "movement_type": "expense",
                "currency": "ARS",
                "amount": "32000",
                "category": "cleaning",
                "description": "Productos de limpieza",
                "payment_method": "cash",
                "movement_date": "2026-08-26",
                "merchant": "",
                "receipt_number": "",
                "notes": "",
            },
            acknowledge_duplicates=True,
        )
        self.assertEqual(movement["source"], "ai")
        self.assertEqual(movement["status"], "confirmed")

        again = confirm_ai_draft(
            self.org,
            draft["id"],
            user_id=self.admin,
            confirm_token=draft["confirm_token"],
            acknowledge_duplicates=True,
        )
        self.assertEqual(again["id"], movement["id"])
        same = [
            item
            for item in list_cash_movements(self.org)
            if item["id"] == movement["id"]
        ]
        self.assertEqual(len(same), 1)

    def test_partial_fields_need_review(self):
        draft = start_ai_analysis(
            self.org,
            user_id=self.admin,
            user_context_text="Compra de oficina",
        )
        self.assertIn(
            "payment_method",
            draft["fields_needing_review"],
        )

    def test_provider_failure_keeps_manual_path(self):
        from modules.cash_ai_service import CashAiError

        with self.assertRaises(CashAiError) as ctx:
            start_ai_analysis(
                self.org,
                user_id=self.admin,
                user_context_text="fail this analysis",
            )
        self.assertEqual(
            ctx.exception.message_key,
            "cash_ai_err_provider_failed",
        )

    def test_agent_cannot_access_ai_routes(self):
        self._login("cash_ai_agent")
        response = self.client.get(
            "/cash/ai",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_admin_can_open_ai_upload(self):
        self._login("cash_ai_admin")
        response = self.client.get("/cash/ai")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Analizar", response.data)

    def test_org_isolation_on_draft(self):
        draft = start_ai_analysis(
            self.org,
            user_id=self.admin,
            user_context_text=(
                "Gasté 1000 pesos en limpieza, efectivo"
            ),
        )
        self._login("cash_ai_other")
        response = self.client.get(
            f"/cash/ai/{draft['id']}",
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 404))

    def test_duplicate_hash_warning_path(self):
        from modules.cash_treasury import confirm_movement
        from modules.cash_treasury import (
            validate_movement_payload,
        )

        png = _png_bytes()
        # First confirmed movement with hash
        values = validate_movement_payload(
            {
                "movement_type": "expense",
                "currency": "ARS",
                "amount": "18500",
                "category": "office_supplies",
                "description": "Librería",
                "payment_method": "cash",
                "movement_date": "2026-08-26",
                "notes": "",
            }
        )[1]
        # Seed balance
        from modules.cash_treasury import set_opening_balances

        set_opening_balances(
            self.org,
            amounts_by_currency={"ARS": "100000"},
            user_id=self.admin,
        )
        first = confirm_movement(
            self.org,
            values,
            user_id=self.admin,
            source="ai",
            attachment_hash="abc123hash",
            merchant="Librería XYZ",
        )
        self.assertIsNotNone(first)

        # Draft with same hash should surface duplicates
        from modules.database.cash_ai_drafts_repository import (
            STATUS_REVIEW,
            create_cash_ai_draft,
            get_cash_ai_draft,
            update_cash_ai_draft,
        )
        from modules.cash_ai_service import (
            find_potential_duplicates,
        )

        draft_id = create_cash_ai_draft(
            self.org,
            created_by_user_id=self.admin,
            confirm_token="tok-dup-1",
            status=STATUS_REVIEW,
            attachment_hash="abc123hash",
        )
        update_cash_ai_draft(
            draft_id,
            self.org,
            draft_payload={
                "movement_type": "expense",
                "currency": "ARS",
                "amount": 18500,
                "movement_date": "2026-08-26",
                "category": "office_supplies",
                "description": "Librería",
                "merchant": "Librería XYZ",
                "payment_method": "cash",
                "receipt_number": None,
                "notes": "",
                "confidence": "high",
                "fields_needing_review": [],
            },
            fields_needing_review=[],
            confidence="high",
        )
        draft = get_cash_ai_draft(draft_id, self.org)
        duplicates = find_potential_duplicates(
            self.org,
            draft,
        )
        self.assertTrue(duplicates)
        self.assertEqual(
            duplicates[0]["id"],
            first["id"],
        )


if __name__ == "__main__":
    unittest.main()
