"""
Phase 3A.2: agent payments loaded from a receipt with AI.

The AI provider is always mocked (CASH_AI_PROVIDER=mock), so no
test performs a network call.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
_TEST_ROOT = Path(_TEST_TMP.name)
os.environ["DATABASE_PATH"] = str(
    _TEST_ROOT / "test_agent_payment_ai.db"
)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_TEST_ROOT / "uploads")
os.environ["CASH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from werkzeug.datastructures import FileStorage

from modules.agent_account import AgentAccountError, create_movement
from modules.agent_account_charges import VAT_MODE_NONE
from modules.agent_payment_ai_service import (
    AgentPaymentAiError,
    confirm_agent_payment_draft,
    discard_draft,
    start_agent_payment_analysis,
    update_draft_from_form,
)
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config, get_private_upload_root
from modules.database import (
    add_agent,
    add_organization,
    add_user,
    create_tables,
    get_agent_balances,
)
from modules.database.agent_payment_ai_drafts_repository import (
    get_agent_payment_ai_draft,
)
from modules.database.cash_treasury_repository import (
    list_cash_movements,
)
from modules.database.connection import get_connection
from modules.database.schema import (
    _migrate_agent_payment_ai,
    _table_exists,
)
from modules.database.treasury_accounts_repository import (
    create_treasury_account,
    get_treasury_account,
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


_DEFAULT_FILE = object()


def _receipt_file(name="comprobante.png", data=None):
    return FileStorage(
        stream=io.BytesIO(data if data is not None else _png_bytes()),
        filename=name,
        content_type="image/png",
    )


class AgentPaymentAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-agent-payment-ai"
        create_tables()

        cls.org_a = add_organization("Payment AI Org A")
        cls.org_b = add_organization("Payment AI Org B")
        cls.password = "Password1"
        pwd = hash_password(cls.password)

        cls.shared_agent = add_agent(
            "Shared AI Agent",
            "Alto",
            cls.org_a,
        )
        cls.admin_a = add_user(
            "payment_ai_admin",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "payment_ai_agent",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.shared_agent,
        )
        cls.admin_b = add_user(
            "payment_ai_admin_b",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls._counter = 0

    def setUp(self):
        self.client = app.test_client()
        AgentPaymentAiTests._counter += 1
        self.suffix = AgentPaymentAiTests._counter
        self.agent_id = add_agent(
            f"AI Agent {self.suffix}",
            "Alto",
            self.org_a,
        )

    def _fresh_org(self):
        AgentPaymentAiTests._counter += 1
        return add_organization(
            f"Payment AI Scope Org {AgentPaymentAiTests._counter}"
        )

    def _login(self, username="payment_ai_admin"):
        self.client.get("/logout", follow_redirects=True)
        self.client.post(
            "/login",
            data={
                "username": username,
                "password": self.password,
            },
            follow_redirects=True,
        )

    def _analyze(
        self,
        context,
        *,
        organization_id=None,
        agent_id=None,
        file_storage=_DEFAULT_FILE,
    ):
        return start_agent_payment_analysis(
            organization_id or self.org_a,
            user_id=self.admin_a,
            file_storage=(
                _receipt_file()
                if file_storage is _DEFAULT_FILE
                else file_storage
            ),
            user_context_text=context,
            agent_id=agent_id,
        )

    def _charge(
        self,
        *,
        organization_id=None,
        agent_id=None,
        amount="78.65",
        currency="USD",
    ):
        return create_movement(
            organization_id or self.org_a,
            agent_id or self.agent_id,
            {
                "charge_category": "fee",
                "currency": currency,
                "amount": amount,
                "vat_mode": VAT_MODE_NONE,
                "billing_period": "Septiembre 2026",
                "movement_date": "2026-09-01",
            },
            created_by_user_id=self.admin_a,
        )

    # 1
    def test_usd_receipt_is_detected(self):
        draft = self._analyze(
            "payer=Test Payer; amount=78,65; currency=USD; "
            "method=transfer; date=02/09/2026; reference=123456789",
            agent_id=self.agent_id,
        )
        payload = draft["draft_payload"]

        self.assertEqual(draft["status"], "review")
        self.assertEqual(payload["currency"], "USD")
        self.assertAlmostEqual(payload["amount"], 78.65)
        self.assertEqual(payload["payment_date"], "2026-09-02")
        self.assertEqual(payload["payment_method"], "transfer")
        self.assertEqual(payload["reference_number"], "123456789")

    # 2
    def test_ars_receipt_is_detected(self):
        draft = self._analyze(
            "payer=Test Payer; amount=125.000,50; currency=ARS; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )
        payload = draft["draft_payload"]

        self.assertEqual(payload["currency"], "ARS")
        self.assertAlmostEqual(payload["amount"], 125000.5)

    def test_ambiguous_currency_symbol_needs_review(self):
        draft = self._analyze(
            "payer=Test Payer; amount=1000; method=transfer",
            agent_id=self.agent_id,
        )
        payload = draft["draft_payload"]

        self.assertIsNone(payload["currency"])
        self.assertIn("currency", payload["fields_needing_review"])

    # 3
    def test_agent_is_suggested_from_payer_name(self):
        org = self._fresh_org()
        expected = add_agent("Jose Luis Barreiro", "Alto", org)
        add_agent("Marcela Fontana", "Alto", org)

        draft = self._analyze(
            "payer=Jose Luis Barreiro; amount=78,65; currency=USD; "
            "method=transfer; date=2026-09-02",
            organization_id=org,
        )

        self.assertEqual(draft["agent_id"], expected)
        self.assertEqual(
            draft["resolution"]["agent"]["source"],
            "name_match",
        )

    # 4
    def test_ambiguous_agent_requires_review(self):
        org = self._fresh_org()
        add_agent("Jose Luis Barreiro", "Alto", org)
        add_agent("Jose Antonio Barreiro", "Alto", org)

        draft = self._analyze(
            "payer=Jose Barreiro; amount=78,65; currency=USD; "
            "method=transfer; date=2026-09-02",
            organization_id=org,
        )
        agent_resolution = draft["resolution"]["agent"]

        self.assertIsNone(draft["agent_id"])
        self.assertTrue(agent_resolution["needs_selection"])
        self.assertEqual(agent_resolution["source"], "ambiguous")
        self.assertEqual(len(agent_resolution["candidates"]), 2)

        with self.assertRaises(AgentPaymentAiError) as ctx:
            confirm_agent_payment_draft(
                org,
                draft["id"],
                user_id=self.admin_a,
                confirm_token=draft["confirm_token"],
            )
        self.assertEqual(
            ctx.exception.message_key,
            "agent_payment_ai_err_agent_required",
        )

    # 5
    def test_exact_charge_is_suggested_by_amount_and_currency(self):
        charge = self._charge(amount="78.65", currency="USD")
        draft = self._analyze(
            "payer=Test Payer; amount=78,65; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )

        self.assertEqual(draft["charge_movement_id"], charge["id"])
        self.assertEqual(
            draft["resolution"]["charge"]["source"],
            "exact_amount",
        )

    # 6
    def test_ars_charge_is_never_matched_to_usd_payment(self):
        ars_charge = self._charge(amount="1000", currency="ARS")
        draft = self._analyze(
            "payer=Test Payer; amount=1000; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )

        self.assertIsNone(draft["charge_movement_id"])
        self.assertEqual(
            draft["resolution"]["charge"]["candidates"],
            [],
        )

        with self.assertRaises(AgentPaymentAiError) as ctx:
            confirm_agent_payment_draft(
                self.org_a,
                draft["id"],
                user_id=self.admin_a,
                confirm_token=draft["confirm_token"],
                form_values={
                    "amount": "1000",
                    "currency": "USD",
                    "payment_date": "2026-09-02",
                    "payment_method": "transfer",
                    "agent_id": str(self.agent_id),
                    "charge_movement_id": str(ars_charge["id"]),
                },
            )
        self.assertEqual(
            ctx.exception.message_key,
            "agent_payment_ai_err_invalid_charge",
        )

    # 7
    def test_destination_account_matches_payment_currency(self):
        org = self._fresh_org()
        agent_id = add_agent("Currency Match Agent", "Alto", org)
        bank_usd = create_treasury_account(
            org,
            name="Banco Galicia USD",
            account_type="bank",
            currency="USD",
        )
        create_treasury_account(
            org,
            name="Banco Galicia ARS",
            account_type="bank",
            currency="ARS",
        )

        draft = self._analyze(
            "payer=Currency Match Agent; amount=78,65; currency=USD; "
            "method=transfer; date=2026-09-02; bank=Galicia",
            organization_id=org,
            agent_id=agent_id,
        )
        selected = get_treasury_account(
            draft["treasury_account_id"],
            org,
        )

        self.assertEqual(draft["treasury_account_id"], bank_usd["id"])
        self.assertEqual(selected["currency"], "USD")
        self.assertTrue(
            all(
                candidate["currency"] == "USD"
                for candidate in draft["resolution"][
                    "treasury_account"
                ]["candidates"]
            )
        )

    # 8
    def test_confirm_uses_the_manual_payment_service(self):
        charge = self._charge(amount="78.65", currency="USD")
        draft = self._analyze(
            "payer=Test Payer; amount=78,65; currency=USD; "
            "method=transfer; date=2026-09-02; reference=987654321",
            agent_id=self.agent_id,
        )

        with patch(
            "modules.agent_payment_ai_service.create_movement",
            wraps=create_movement,
        ) as spy:
            movement = confirm_agent_payment_draft(
                self.org_a,
                draft["id"],
                user_id=self.admin_a,
                confirm_token=draft["confirm_token"],
            )

        self.assertEqual(spy.call_count, 1)
        self.assertEqual(
            spy.call_args.args[2]["movement_type"],
            "payment",
        )
        self.assertEqual(movement["movement_type"], "payment")
        self.assertEqual(movement["source_type"], "cash")
        self.assertIsNotNone(movement["source_id"])

        from modules.database.agent_account_payment_repository import (
            list_payment_allocations,
        )

        allocations = list_payment_allocations(
            self.org_a,
            movement["id"],
        )
        self.assertEqual(len(allocations), 1)
        self.assertEqual(
            allocations[0]["charge_movement_id"],
            charge["id"],
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            0.0,
        )

    # 9
    def test_double_confirm_does_not_duplicate(self):
        draft = self._analyze(
            "payer=Test Payer; amount=50; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )
        cash_before = len(list_cash_movements(self.org_a))

        first = confirm_agent_payment_draft(
            self.org_a,
            draft["id"],
            user_id=self.admin_a,
            confirm_token=draft["confirm_token"],
        )
        second = confirm_agent_payment_draft(
            self.org_a,
            draft["id"],
            user_id=self.admin_a,
            confirm_token=draft["confirm_token"],
        )

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(
            len(list_cash_movements(self.org_a)),
            cash_before + 1,
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            50.0,
        )

    # 10
    def test_cash_failure_rolls_back_everything(self):
        draft = self._analyze(
            "payer=Test Payer; amount=60; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )
        cash_before = len(list_cash_movements(self.org_a))

        with patch(
            "modules.database.agent_account_payment_repository"
            ".create_cash_movement_atomic",
            side_effect=ValueError("cash_failed"),
        ):
            with self.assertRaises(AgentAccountError):
                confirm_agent_payment_draft(
                    self.org_a,
                    draft["id"],
                    user_id=self.admin_a,
                    confirm_token=draft["confirm_token"],
                )

        reloaded = get_agent_payment_ai_draft(
            draft["id"],
            self.org_a,
        )
        self.assertEqual(reloaded["status"], "review")
        self.assertIsNone(reloaded["confirmed_movement_id"])
        self.assertEqual(
            len(list_cash_movements(self.org_a)),
            cash_before,
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            0.0,
        )

    # 11
    def test_discarded_draft_moves_no_money(self):
        draft = self._analyze(
            "payer=Test Payer; amount=70; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )
        cash_before = len(list_cash_movements(self.org_a))
        discarded = discard_draft(self.org_a, draft["id"])

        self.assertEqual(discarded["status"], "discarded")
        self.assertEqual(
            len(list_cash_movements(self.org_a)),
            cash_before,
        )
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            0.0,
        )

        with self.assertRaises(AgentPaymentAiError) as ctx:
            confirm_agent_payment_draft(
                self.org_a,
                draft["id"],
                user_id=self.admin_a,
                confirm_token=draft["confirm_token"],
            )
        self.assertEqual(
            ctx.exception.message_key,
            "agent_payment_ai_err_discarded",
        )

    # 12
    def test_invalid_image_is_rejected(self):
        with self.assertRaises(AgentPaymentAiError) as ctx:
            self._analyze(
                "payer=Test Payer; amount=10; currency=USD",
                file_storage=_receipt_file(
                    data=b"this is definitely not an image",
                ),
            )
        self.assertEqual(
            ctx.exception.message_key,
            "cash_ai_err_file_type",
        )

        with self.assertRaises(AgentPaymentAiError) as ctx:
            self._analyze(
                "payer=Test Payer; amount=10; currency=USD",
                file_storage=None,
            )
        self.assertEqual(
            ctx.exception.message_key,
            "cash_ai_err_file_required",
        )

    def test_invalid_token_is_rejected(self):
        draft = self._analyze(
            "payer=Test Payer; amount=10; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )

        with self.assertRaises(AgentPaymentAiError) as ctx:
            confirm_agent_payment_draft(
                self.org_a,
                draft["id"],
                user_id=self.admin_a,
                confirm_token="not-the-token",
            )
        self.assertEqual(
            ctx.exception.message_key,
            "agent_payment_ai_err_invalid_token",
        )

    # 13
    def test_organization_scope_is_enforced(self):
        draft = self._analyze(
            "payer=Test Payer; amount=20; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )

        self.assertIsNone(
            get_agent_payment_ai_draft(draft["id"], self.org_b)
        )

        self._login("payment_ai_admin_b")
        response = self.client.get(
            f"/agent-accounts/ai/payments/{draft['id']}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        with self.assertRaises(AgentPaymentAiError):
            confirm_agent_payment_draft(
                self.org_b,
                draft["id"],
                user_id=self.admin_b,
                confirm_token=draft["confirm_token"],
            )

    def test_agent_role_cannot_reach_the_flow(self):
        self._login("payment_ai_agent")
        response = self.client.get(
            "/agent-accounts/ai/payments",
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 403))

    # 14
    def test_receipt_is_linked_to_payment_and_cash_movement(self):
        draft = self._analyze(
            "payer=Test Payer; amount=90; currency=USD; "
            "method=transfer; date=2026-09-02; reference=A-55512",
            agent_id=self.agent_id,
        )
        stored = (
            get_private_upload_root() / draft["attachment_path"]
        )

        self.assertTrue(stored.is_file())
        self.assertIn(
            "agent_payments",
            draft["attachment_path"],
        )

        movement = confirm_agent_payment_draft(
            self.org_a,
            draft["id"],
            user_id=self.admin_a,
            confirm_token=draft["confirm_token"],
        )
        reloaded = get_agent_payment_ai_draft(
            draft["id"],
            self.org_a,
        )
        cash_row = next(
            row
            for row in list_cash_movements(self.org_a)
            if row["source_reference"] == str(movement["id"])
        )

        self.assertEqual(reloaded["status"], "confirmed")
        self.assertEqual(
            reloaded["confirmed_movement_id"],
            movement["id"],
        )
        self.assertEqual(
            reloaded["confirmed_cash_movement_id"],
            cash_row["id"],
        )
        self.assertEqual(
            cash_row["attachment_path"],
            draft["attachment_path"],
        )
        self.assertEqual(
            cash_row["attachment_hash"],
            draft["attachment_hash"],
        )
        self.assertEqual(cash_row["receipt_number"], "A-55512")

    # 15
    def test_mock_provider_makes_no_network_call(self):
        with patch(
            "modules.cash_ai_provider.request_structured_json",
            side_effect=AssertionError("no real AI call allowed"),
        ) as spy:
            draft = self._analyze(
                "payer=Test Payer; amount=15; currency=ARS; "
                "method=transfer; date=2026-09-02",
                agent_id=self.agent_id,
            )

        spy.assert_not_called()
        self.assertEqual(draft["status"], "review")
        self.assertEqual(draft["provider"], "mock")

    def test_provider_failure_marks_draft_failed(self):
        with self.assertRaises(AgentPaymentAiError) as ctx:
            self._analyze(
                "fail this analysis",
                agent_id=self.agent_id,
            )
        self.assertEqual(
            ctx.exception.message_key,
            "cash_ai_err_provider_failed",
        )

    def test_human_edit_overrides_extraction(self):
        draft = self._analyze(
            "payer=Test Payer; amount=1000; method=transfer",
            agent_id=self.agent_id,
        )
        updated = update_draft_from_form(
            self.org_a,
            draft["id"],
            {
                "amount": "123,45",
                "currency": "USD",
                "payment_date": "2026-09-03",
                "payment_method": "cash",
                "agent_id": str(self.agent_id),
                "charge_movement_id": "general",
                "description": "Pago a cuenta",
            },
        )
        payload = updated["draft_payload"]

        self.assertAlmostEqual(payload["amount"], 123.45)
        self.assertEqual(payload["currency"], "USD")
        self.assertEqual(payload["payment_method"], "cash")
        self.assertEqual(payload["fields_needing_review"], [])
        self.assertIsNone(updated["charge_movement_id"])

    def test_review_page_renders_for_admin(self):
        draft = self._analyze(
            "payer=Test Payer; amount=42; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )
        self._login()

        upload_page = self.client.get(
            "/agent-accounts/ai/payments",
        )
        review_page = self.client.get(
            f"/agent-accounts/ai/payments/{draft['id']}",
        )
        receipt = self.client.get(
            f"/agent-accounts/ai/payments/{draft['id']}/receipt",
        )

        self.assertEqual(upload_page.status_code, 200)
        self.assertEqual(review_page.status_code, 200)
        self.assertEqual(receipt.status_code, 200)

    def test_http_confirm_registers_payment(self):
        draft = self._analyze(
            "payer=Test Payer; amount=64; currency=USD; "
            "method=transfer; date=2026-09-02",
            agent_id=self.agent_id,
        )
        self._login()

        response = self.client.post(
            f"/agent-accounts/ai/payments/{draft['id']}",
            data={
                "action": "confirm",
                "confirm_token": draft["confirm_token"],
                "amount": "64",
                "currency": "USD",
                "payment_date": "2026-09-02",
                "payment_method": "transfer",
                "agent_id": str(self.agent_id),
                "charge_movement_id": "general",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertAlmostEqual(
            get_agent_balances(self.org_a, self.agent_id)["USD"],
            64.0,
        )

    def test_migration_is_idempotent(self):
        connection = get_connection()
        cursor = connection.cursor()
        try:
            _migrate_agent_payment_ai(cursor)
            _migrate_agent_payment_ai(cursor)
            connection.commit()
            self.assertTrue(
                _table_exists(cursor, "agent_payment_ai_drafts")
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
