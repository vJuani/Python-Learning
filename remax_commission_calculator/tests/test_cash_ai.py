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
from modules.cash_treasury import get_balances, set_opening_balances
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_cash_movement,
    list_cash_movements,
)
from modules.database.cash_ai_drafts_repository import (
    get_cash_ai_draft,
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
        from modules.cash_treasury import set_opening_balances

        org = add_organization("Cash Dup Org")
        admin = add_user(
            "cash_dup_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            org,
        )
        set_opening_balances(
            org,
            amounts_by_currency={"ARS": "100000"},
            user_id=admin,
        )
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
        first = confirm_movement(
            org,
            values,
            user_id=admin,
            source="ai",
            attachment_hash="abc123hash",
            merchant="Librería XYZ",
        )
        self.assertIsNotNone(first)

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
            org,
            created_by_user_id=admin,
            confirm_token="tok-dup-1",
            status=STATUS_REVIEW,
            attachment_hash="abc123hash",
        )
        update_cash_ai_draft(
            draft_id,
            org,
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
        draft = get_cash_ai_draft(draft_id, org)
        duplicates = find_potential_duplicates(org, draft)
        self.assertTrue(duplicates)
        self.assertEqual(
            duplicates[0]["id"],
            first["id"],
        )

    def test_confirm_post_creates_movement_and_redirects(self):
        set_opening_balances(
            self.org,
            amounts_by_currency={"ARS": "100000"},
            user_id=self.admin,
        )
        draft = start_ai_analysis(
            self.org,
            user_id=self.admin,
            user_context_text=(
                "Egreso de 25000 pesos por productos de "
                "limpieza, pagado por transferencia."
            ),
        )
        self.assertEqual(draft["status"], "review")
        before = get_balances(self.org)["ARS"]

        self._login("cash_ai_admin")
        response = self.client.post(
            f"/cash/ai/{draft['id']}",
            data={
                "action": "confirm",
                "confirm_token": draft["confirm_token"],
                "movement_type": "expense",
                "currency": "ARS",
                "amount": "25000",
                "category": "cleaning",
                "description": "Productos de limpieza",
                "merchant": "",
                "payment_method": "transfer",
                "receipt_number": "",
                "movement_date": "2026-08-26",
                "notes": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/cash/", response.headers["Location"])

        updated = get_cash_ai_draft(draft["id"], self.org)
        self.assertEqual(updated["status"], "confirmed")
        self.assertIsNotNone(updated["confirmed_movement_id"])

        movement = get_cash_movement(
            updated["confirmed_movement_id"],
            self.org,
        )
        self.assertEqual(movement["source"], "ai")
        self.assertEqual(
            movement["created_by_user_id"],
            self.admin,
        )
        self.assertEqual(
            movement["organization_id"],
            self.org,
        )
        self.assertAlmostEqual(
            get_balances(self.org)["ARS"],
            before - 25000,
            places=2,
        )

        # Double submit is idempotent.
        again = self.client.post(
            f"/cash/ai/{draft['id']}",
            data={
                "action": "confirm",
                "confirm_token": draft["confirm_token"],
                "movement_type": "expense",
                "currency": "ARS",
                "amount": "25000",
                "category": "cleaning",
                "description": "Productos de limpieza",
                "payment_method": "transfer",
                "movement_date": "2026-08-26",
            },
            follow_redirects=False,
        )
        self.assertEqual(again.status_code, 302)
        same = get_cash_ai_draft(draft["id"], self.org)
        self.assertEqual(
            same["confirmed_movement_id"],
            updated["confirmed_movement_id"],
        )

    def test_confirm_without_action_defaults_does_not_create(self):
        """Regression: disabled submit button dropped action=confirm."""
        draft = start_ai_analysis(
            self.org,
            user_id=self.admin,
            user_context_text=(
                "Egreso de 1000 pesos en limpieza, transferencia."
            ),
        )
        self._login("cash_ai_admin")
        # Simulate missing action (old buggy browser submit).
        response = self.client.post(
            f"/cash/ai/{draft['id']}",
            data={
                "confirm_token": draft["confirm_token"],
                "movement_type": "expense",
                "currency": "ARS",
                "amount": "1000",
                "category": "cleaning",
                "description": "Limpieza",
                "payment_method": "transfer",
                "movement_date": "2026-08-26",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        updated = get_cash_ai_draft(draft["id"], self.org)
        self.assertEqual(updated["status"], "review")
        self.assertIsNone(updated.get("confirmed_movement_id"))


if __name__ == "__main__":
    unittest.main()
