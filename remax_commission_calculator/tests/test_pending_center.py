"""Phase 4A pending center and notification integration tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_pending_center.db"
)

from modules.agent_account import create_movement
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
    upsert_billing_issuer_profile,
)
from modules.database.agent_billing_profiles_repository import (
    upsert_profile,
)
from modules.database.agent_payment_ai_drafts_repository import (
    STATUS_REVIEW,
    create_agent_payment_ai_draft,
    update_agent_payment_ai_draft,
)
from modules.database.notifications_repository import (
    count_unread_notifications,
    create_notification,
    list_notifications,
    mark_notification_read,
)
from modules.database.schema import create_tables as create_tables_again
from modules.invoicing import (
    ISSUER_MODE_OFFICE,
    create_draft_for_charge,
)
from modules.operation_commission_credit import (
    credit_operation_commission,
)
from modules.operations import (
    calculate_operation_details,
    save_calculated_operation,
)
from modules.pending_actions import (
    build_agent_pending_actions,
    build_staff_pending_actions,
    summarize_pending_actions,
)
from modules.recurring_agent_charges import (
    create_recurring_charge,
    generate_due_recurring_charges,
)
from web_app import app


def _kinds(actions):
    return [action["kind"] for action in actions]


class PendingCenterTests(unittest.TestCase):
    _counter = 0

    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(
            TESTING=True,
            SECRET_KEY="pending-center-test",
        )
        create_tables()

        cls.org_a = add_organization("Pending Org A")
        cls.org_b = add_organization("Pending Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)

        cls.agent_a = add_agent("Pending Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent(
            "Pending Agent A2",
            "Alto",
            cls.org_a,
        )
        cls.agent_b = add_agent("Pending Agent B", "Alto", cls.org_b)

        cls.admin_a = add_user(
            "pending_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.admin_b = add_user(
            "pending_admin_b",
            password_hash,
            ROLE_ADMIN,
            cls.org_b,
        )
        cls.agent_user = add_user(
            "pending_agent_user",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )

        cls.admin_user_record = {
            "id": cls.admin_a,
            "organization_id": cls.org_a,
            "role": ROLE_ADMIN,
        }

        cls.issuer = upsert_billing_issuer_profile(
            cls.org_a,
            issuer_type="organization",
            display_name="Pending Office",
            legal_name="Pending Office SA",
            tax_id="30-71234567-8",
            tax_condition="responsable_inscripto",
            fiscal_address="Oficina Pendientes 123",
            email="office@example.com",
            is_default=True,
        )

        # Every fiscal-profile-blocking test opts in explicitly, so the
        # default agent profile is complete.
        upsert_profile(
            cls.org_a,
            cls.agent_a,
            legal_name="Pending Agent A SRL",
            tax_id="20-30123456-7",
            tax_condition="responsable_inscripto",
            fiscal_address="Av. Siempre Viva 742",
            email="agent-a@example.com",
        )

    def setUp(self):
        self.client = app.test_client()

    # ---------------- helpers ----------------

    def _login(self, username):
        self.client.get("/logout", follow_redirects=True)
        return self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def _charge(
        self,
        *,
        organization_id=None,
        agent_id=None,
        amount="65",
        description="Fee",
        created_by_user_id=None,
    ):
        organization_id = organization_id or self.org_a
        agent_id = agent_id if agent_id is not None else self.agent_a

        return create_movement(
            organization_id,
            agent_id,
            {
                "movement_type": "charge",
                "charge_category": "fee",
                "currency": "USD",
                "amount": amount,
                "vat_mode": "add_vat",
                "vat_rate": "21",
                "description": description,
                "movement_date": "2026-09-02",
            },
            created_by_user_id=(
                created_by_user_id or self.admin_a
            ),
        )

    def _operation_ready_for_commission(
        self,
        *,
        organization_id=None,
        agent_id=None,
    ):
        organization_id = organization_id or self.org_a
        agent_id = agent_id if agent_id is not None else self.agent_a
        PendingCenterTests._counter += 1
        address = f"Pending Street {PendingCenterTests._counter}"
        property_id = add_property(
            address,
            "CABA",
            organization_id,
            agent_id=agent_id,
            status="approved",
        )
        operation = calculate_operation_details(
            "Pending Agent A",
            "Alto",
            address,
            "CABA",
            100000,
            3,
            "yes",
            vat_amount=0,
        )
        operation["currency"] = "USD"
        operation["original_amount"] = 100000
        operation["exchange_rate"] = 1
        operation["side_data"] = {
            "seller_active": False,
            "buyer_active": True,
            "seller_commission_percent": 0,
            "buyer_commission_percent": 3,
            "seller_commission_amount": 0,
            "buyer_commission_amount": 3000,
        }
        operation_id, _ = save_calculated_operation(
            agent_id,
            property_id,
            organization_id,
            operation,
            status="approved",
            created_by_user_id=self.admin_a,
        )
        return operation_id

    def _receipt_draft_in_review(self, *, organization_id=None):
        organization_id = organization_id or self.org_a
        PendingCenterTests._counter += 1
        draft_id = create_agent_payment_ai_draft(
            organization_id,
            created_by_user_id=self.admin_a,
            confirm_token=f"token-{PendingCenterTests._counter}",
            idempotency_key=(
                f"pending-draft-{PendingCenterTests._counter}"
            ),
        )
        update_agent_payment_ai_draft(
            draft_id,
            organization_id,
            status=STATUS_REVIEW,
            draft_payload={"amount": 78.65, "currency": "USD"},
        )
        return draft_id

    def _discard_draft(self, draft_id, *, organization_id=None):
        update_agent_payment_ai_draft(
            draft_id,
            organization_id or self.org_a,
            status="discarded",
        )

    def _recurring_due(self, *, agent_id=None):
        return create_recurring_charge(
            self.org_a,
            agent_id if agent_id is not None else self.agent_a,
            {
                "charge_category": "fee",
                "currency": "USD",
                "amount": "65",
                "vat_mode": "add_vat",
                "vat_rate": "21",
                "recurrence_type": "monthly",
                "billing_day": "1",
                "start_date": "2026-10-01",
                "end_date": "",
                "description": "",
            },
            actor_user_id=self.admin_a,
        )

    def _staff_actions(self, **kwargs):
        return build_staff_pending_actions(self.org_a, **kwargs)

    # ---------------- tests ----------------

    def test_01_receipt_review_appears_for_staff(self):
        draft_id = self._receipt_draft_in_review()
        try:
            actions = self._staff_actions()
            receipt = next(
                action
                for action in actions
                if action["kind"] == "agent_payment_receipt_review"
            )
            self.assertEqual(receipt["priority"], "high")
            self.assertEqual(receipt["category"], "finance")
            self.assertEqual(
                receipt["endpoint_args"]["draft_id"],
                draft_id,
            )
        finally:
            self._discard_draft(draft_id)

    def test_02_commission_ready_appears(self):
        operation_id = self._operation_ready_for_commission()
        actions = self._staff_actions()
        commission = next(
            action
            for action in actions
            if action["kind"] == "commission_ready_to_credit"
            and action["endpoint_args"]["operation_id"] == operation_id
        )
        self.assertEqual(commission["category"], "operations")
        self.assertEqual(commission["currency"], "USD")

    def test_03_charge_without_invoice_appears(self):
        charge = self._charge(description="Fee sin factura")
        charge_kinds = [
            action["endpoint_args"].get("charge_id")
            for action in self._staff_actions()
            if action["kind"] == "charge_without_invoice"
        ]
        self.assertIn(charge["id"], charge_kinds)

    def test_04_created_invoice_removes_pending(self):
        charge = self._charge(description="Fee facturable")
        create_draft_for_charge(
            self.org_a,
            charge["id"],
            self.admin_user_record,
            issuer_mode=ISSUER_MODE_OFFICE,
            issuer_profile_id=self.issuer["id"],
        )
        remaining = [
            action["endpoint_args"].get("charge_id")
            for action in self._staff_actions()
            if action["kind"] == "charge_without_invoice"
        ]
        self.assertNotIn(charge["id"], remaining)

    def test_05_fiscal_profile_pending_only_when_blocking(self):
        # No billable charge yet: an incomplete profile is not a pending.
        blocked_before = [
            action
            for action in self._staff_actions()
            if action["kind"] == "agent_fiscal_profile_incomplete"
            and action["endpoint_args"]["agent_id"] == self.agent_other
        ]
        self.assertEqual(blocked_before, [])

        charge = self._charge(
            agent_id=self.agent_other,
            description="Fee sin perfil",
        )
        actions = self._staff_actions()
        blocked_after = [
            action
            for action in actions
            if action["kind"] == "agent_fiscal_profile_incomplete"
            and action["endpoint_args"]["agent_id"] == self.agent_other
        ]
        self.assertEqual(len(blocked_after), 1)

        # The charge itself is not listed twice: completing the profile
        # is the actionable step.
        charges = [
            action["endpoint_args"].get("charge_id")
            for action in actions
            if action["kind"] == "charge_without_invoice"
        ]
        self.assertNotIn(charge["id"], charges)

    def test_06_recurring_due_appears(self):
        self._recurring_due()
        actions = self._staff_actions(as_of="2026-10-03")
        recurring = next(
            action
            for action in actions
            if action["kind"] == "recurring_charges_due"
        )
        self.assertEqual(recurring["priority"], "low")
        self.assertEqual(recurring["endpoint"], "agent_recurring_generate")

    def test_07_agent_sees_only_own_data(self):
        self._charge(
            agent_id=self.agent_other,
            description="Fee de otro agente",
        )
        self._charge(description="Fee propio")

        actions = build_agent_pending_actions(
            self.org_a,
            self.agent_a,
            user_id=self.agent_user,
        )
        subtitles = " ".join(
            action["subtitle"] or "" for action in actions
        )
        self.assertIn("Fee propio", subtitles)
        self.assertNotIn("Fee de otro agente", subtitles)

    def test_08_agent_does_not_see_staff_pendings(self):
        draft_id = self._receipt_draft_in_review()
        operation_id = self._operation_ready_for_commission()
        try:
            actions = build_agent_pending_actions(
                self.org_a,
                self.agent_a,
                user_id=self.agent_user,
            )
            kinds = _kinds(actions)
            self.assertNotIn("agent_payment_receipt_review", kinds)
            self.assertNotIn("commission_ready_to_credit", kinds)
            self.assertNotIn("charge_without_invoice", kinds)
            self.assertIsNotNone(operation_id)
        finally:
            self._discard_draft(draft_id)

    def test_09_other_organization_is_isolated(self):
        self._charge(
            organization_id=self.org_b,
            agent_id=self.agent_b,
            description="Fee org B",
            created_by_user_id=self.admin_b,
        )
        subtitles = " ".join(
            action["subtitle"] or ""
            for action in self._staff_actions()
        )
        self.assertNotIn("Fee org B", subtitles)

        other_org = build_staff_pending_actions(self.org_b)
        other_subtitles = " ".join(
            action["subtitle"] or "" for action in other_org
        )
        self.assertNotIn("Fee propio", other_subtitles)

    def test_10_reading_notification_does_not_resolve_pending(self):
        charge = self._charge(description="Fee con notificación")
        notification_id = create_notification(
            self.org_a,
            self.agent_user,
            "invoice_created",
            "agent_account_movement",
            charge["id"],
            event_key=f"test-read:{charge['id']}",
        )
        mark_notification_read(
            notification_id,
            self.agent_user,
            self.org_a,
        )
        notification = next(
            item
            for item in list_notifications(self.agent_user, self.org_a)
            if item["id"] == notification_id
        )

        self.assertTrue(notification["is_read"])
        self.assertIsNotNone(notification["read_at"])

        still_pending = [
            action["endpoint_args"].get("charge_id")
            for action in self._staff_actions()
            if action["kind"] == "charge_without_invoice"
        ]
        self.assertIn(charge["id"], still_pending)

    def test_11_resolving_entity_removes_pending(self):
        operation_id = self._operation_ready_for_commission()
        before = [
            action
            for action in self._staff_actions()
            if action["kind"] == "commission_ready_to_credit"
            and action["endpoint_args"]["operation_id"] == operation_id
        ]
        self.assertEqual(len(before), 1)

        credit_operation_commission(
            self.org_a,
            operation_id,
            amount=str(before[0]["amount"]),
            currency="USD",
            created_by_user_id=self.admin_a,
        )

        after = [
            action
            for action in self._staff_actions()
            if action["kind"] == "commission_ready_to_credit"
            and action["endpoint_args"]["operation_id"] == operation_id
        ]
        self.assertEqual(after, [])

    def test_12_bell_count_matches_center(self):
        draft_id = self._receipt_draft_in_review()
        try:
            self._login("pending_admin_a")
            actions = self._staff_actions()
            response = self.client.get("/pendings")
            body = response.get_data(as_text=True)

            self.assertEqual(response.status_code, 200)
            self.assertIn(
                f'class="badge badge-pending">{len(actions)}<',
                body,
            )
        finally:
            self._discard_draft(draft_id)

    def test_13_notification_event_is_not_duplicated(self):
        recurring = self._recurring_due()
        result = generate_due_recurring_charges(
            self.org_a,
            as_of="2026-11-03",
            actor_user_id=self.admin_a,
        )
        generated = [
            movement
            for movement in result["generated"]
            if movement["source_id"] == recurring["id"]
        ]
        self.assertTrue(generated)

        movement_id = generated[0]["id"]
        first = create_notification(
            self.org_a,
            self.agent_user,
            "recurring_charge_generated",
            "agent_account_movement",
            movement_id,
            event_key=f"recurring_charge_generated:{movement_id}",
        )
        second = create_notification(
            self.org_a,
            self.agent_user,
            "recurring_charge_generated",
            "agent_account_movement",
            movement_id,
            event_key=f"recurring_charge_generated:{movement_id}",
        )

        self.assertEqual(first, second)

        matching = [
            item
            for item in list_notifications(
                self.agent_user,
                self.org_a,
                limit=200,
            )
            if item["entity_id"] == movement_id
            and item["kind"] == "recurring_charge_generated"
        ]
        self.assertEqual(len(matching), 1)

    def test_14_staff_dashboard_renders(self):
        self._charge(description="Fee dashboard staff")
        self._login("pending_admin_a")
        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Requiere tu atención", body)

    def test_15_agent_dashboard_renders(self):
        self._charge(description="Fee dashboard agente")
        self._login("pending_agent_user")
        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Para vos", body)
        self.assertNotIn("Requiere tu atención", body)

    def test_16_pending_center_renders_for_both_roles(self):
        self._login("pending_admin_a")
        staff_response = self.client.get("/pendings")
        self.assertEqual(staff_response.status_code, 200)

        self._login("pending_agent_user")
        agent_response = self.client.get("/pendings")
        self.assertEqual(agent_response.status_code, 200)

    def test_17_mobile_structure_uses_cards_not_tables(self):
        self._charge(description="Fee mobile")
        self._login("pending_admin_a")
        body = self.client.get("/pendings").get_data(as_text=True)

        self.assertIn("pending-card", body)
        self.assertNotIn("table-wrapper", body)
        self.assertNotIn("<table", body)

    def test_18_queries_are_aggregated_not_per_agent(self):
        for index in range(6):
            self._charge(description=f"Fee batch {index}")

        from modules.database import connection as connection_module

        original = connection_module.get_connection
        calls = {"count": 0}

        def counting_connection(*args, **kwargs):
            calls["count"] += 1
            return original(*args, **kwargs)

        connection_module.get_connection = counting_connection
        try:
            actions = self._staff_actions()
        finally:
            connection_module.get_connection = original

        self.assertTrue(actions)
        # Aggregated reads: the query count must not grow with the
        # number of agents or charges involved.
        self.assertLessEqual(calls["count"], 12)

    def test_19_i18n_and_priority_labels_exist(self):
        from modules.i18n import translate

        keys = (
            "pending_center_title",
            "pending_empty_title",
            "pending_empty_hint",
            "pending_category_finance",
            "pending_priority_high",
            "dashboard_attention_title",
            "dashboard_for_you_title",
            "notification_commission_credited",
        )
        for key in keys:
            for language in ("es", "en"):
                value = translate(key, language)
                self.assertNotEqual(value, key)
                self.assertTrue(value.strip())

        spanish = build_staff_pending_actions(
            self.org_a,
            language="es",
        )
        english = build_staff_pending_actions(
            self.org_a,
            language="en",
        )
        if spanish and english:
            self.assertNotEqual(
                spanish[0]["title"],
                english[0]["title"],
            )

    def test_20_guest_and_summary_permissions(self):
        self.client.get("/logout", follow_redirects=True)
        response = self.client.get("/pendings", follow_redirects=False)
        self.assertIn(response.status_code, (302, 401, 403))

        summary = summarize_pending_actions(
            self._staff_actions(),
            language="es",
        )
        self.assertEqual(summary["total"], len(self._staff_actions()))
        self.assertLessEqual(len(summary["top"]), 5)

    def test_21_notification_unread_count_and_migration(self):
        before = count_unread_notifications(
            self.agent_user,
            self.org_a,
        )
        create_notification(
            self.org_a,
            self.agent_user,
            "agent_payment_confirmed",
            "agent_account_movement",
            999999,
            event_key="test-unread:999999",
        )
        after = count_unread_notifications(
            self.agent_user,
            self.org_a,
        )
        self.assertEqual(after, before + 1)

        # create_tables twice must stay idempotent.
        create_tables_again()
        create_tables_again()
        self.assertEqual(
            count_unread_notifications(self.agent_user, self.org_a),
            after,
        )


if __name__ == "__main__":
    unittest.main()
