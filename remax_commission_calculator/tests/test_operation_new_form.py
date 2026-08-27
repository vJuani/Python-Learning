"""
Tests for the refactored New Operation form (side commissions, property search).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_operation_new_form.db"
)

from modules.auth import ROLE_ADMIN, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
    get_operation_record,
    get_parties_for_operation,
    list_available_properties_for_operation,
)
from modules.database.properties_repository import STATUS_APPROVED
from modules.database.schema import migrate_schema
from modules.operation_prefill import (
    get_property_operation_prefill,
    suggest_available_properties,
)
from modules.operations import (
    prepare_new_operation_from_form,
    save_calculated_operation,
    validate_new_operation_inputs,
)
from web_app import app


def _new_operation_post_data(**overrides):
    data = {
        "action": "save",
        "operation_date": "01/01/2026",
        "search_mode": "agent",
        "agent_id": "",
        "property_id": "",
        "currency": "USD",
        "original_amount": "200000",
        "exchange_rate": "",
        "seller_side_active": "1",
        "buyer_side_active": "1",
        "is_referred": "",
        "referred_side": "",
        "seller_commission_rate": "2.5",
        "buyer_commission_rate": "3",
        "seller_vat_amount": "500",
        "buyer_vat_amount": "600",
    }
    data.update(overrides)
    return data


class OperationNewFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        create_tables()
        migrate_schema(create_backup=False)

        cls.org = add_organization("New Op Org")
        cls.other_org = add_organization("Other Org")
        pwd = hash_password("Password1")

        cls.agent_a = add_agent("Agent A", "Alto", cls.org)
        cls.agent_b = add_agent("Agent B", "Alto", cls.org)
        cls.other_agent = add_agent(
            "Other Agent",
            "Alto",
            cls.other_org,
        )

        cls.prop_available = add_property(
            "Diagonal Santa Rosalia 2512",
            "CABA",
            cls.org,
            agent_id=cls.agent_a,
            status=STATUS_APPROVED,
            listing_price=145000,
            external_id="420051161-498",
        )
        cls.prop_available_b = add_property(
            "Paunero 1078",
            "CABA",
            cls.org,
            agent_id=cls.agent_a,
            status=STATUS_APPROVED,
            listing_price=98000,
            external_id="420051161-514",
        )
        cls.prop_other_agent = add_property(
            "Other Agent St",
            "CABA",
            cls.org,
            agent_id=cls.agent_b,
            status=STATUS_APPROVED,
            listing_price=120000,
            external_id="999000111",
        )
        add_property(
            "Foreign St",
            "CABA",
            cls.other_org,
            agent_id=cls.other_agent,
            status=STATUS_APPROVED,
            listing_price=80000,
            external_id="FOREIGN-1",
        )

        cls.admin = add_user(
            "new_op_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="new_op_admin@example.com",
        )

    def setUp(self):
        self.test_property = add_property(
            f"Test Property {self._testMethodName}",
            "CABA",
            self.org,
            agent_id=self.agent_a,
            status=STATUS_APPROVED,
            listing_price=100000,
            external_id=f"TEST-{self._testMethodName[-12:]}",
        )

    def _login(self, client, user_id, role, organization_id):
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = organization_id

    def test_suggest_by_external_id_partial(self):
        prop_x = add_property(
            "Suggest X",
            "CABA",
            self.org,
            agent_id=self.agent_a,
            status=STATUS_APPROVED,
            listing_price=100,
            external_id="420051161-498",
        )
        prop_y = add_property(
            "Suggest Y",
            "CABA",
            self.org,
            agent_id=self.agent_a,
            status=STATUS_APPROVED,
            listing_price=100,
            external_id="420051161-514",
        )
        results = suggest_available_properties(
            "42005",
            self.org,
        )
        ids = {item["id"] for item in results}
        self.assertIn(prop_x, ids)
        self.assertIn(prop_y, ids)

    def test_suggest_scoped_by_agent(self):
        results = suggest_available_properties(
            "",
            self.org,
            agent_id=self.agent_a,
            limit=20,
        )
        ids = {item["id"] for item in results}
        self.assertIn(self.test_property, ids)
        self.assertNotIn(self.prop_other_agent, ids)

    def test_other_org_property_not_in_suggest(self):
        results = suggest_available_properties(
            "FOREIGN",
            self.org,
        )
        self.assertEqual(results, [])

    def test_prefill_returns_agent_currency_value(self):
        prefill = get_property_operation_prefill(
            self.test_property,
            self.org,
        )
        self.assertIsNotNone(prefill)
        self.assertEqual(prefill["agent_id"], self.agent_a)
        self.assertEqual(prefill["currency"], "USD")
        self.assertEqual(prefill["operation_value"], 100000)

    def test_property_disappears_after_operation_created(self):
        client = app.test_client()
        self._login(client, self.admin, ROLE_ADMIN, self.org)

        response = client.post(
            "/operations/new",
            data=_new_operation_post_data(
                agent_id=str(self.agent_a),
                property_id=str(self.prop_available),
            ),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        available = list_available_properties_for_operation(
            self.org,
        )
        available_ids = {item["id"] for item in available}
        self.assertNotIn(self.prop_available, available_ids)

    def test_manual_property_id_rejected_server_side(self):
        client = app.test_client()
        self._login(client, self.admin, ROLE_ADMIN, self.org)
        prop_id = self.test_property

        client.post(
            "/operations/new",
            data=_new_operation_post_data(
                agent_id=str(self.agent_a),
                property_id=str(prop_id),
            ),
        )

        response = client.post(
            "/operations/new",
            data=_new_operation_post_data(
                agent_id=str(self.agent_a),
                property_id=str(prop_id),
            ),
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertTrue(
            "property_already_used_in_operation" in html
            or "ya tiene una operación" in html.lower()
        )

    def test_validate_side_commissions_and_total(self):
        errors, parsed = validate_new_operation_inputs(
            _new_operation_post_data(
                agent_id=str(self.agent_a),
                property_id=str(self.test_property),
                original_amount="200000",
            ),
            self.org,
        )
        self.assertEqual(errors, [])
        self.assertAlmostEqual(
            parsed["seller_commission_amount"],
            5000.0,
        )
        self.assertAlmostEqual(
            parsed["buyer_commission_amount"],
            6000.0,
        )
        self.assertAlmostEqual(
            parsed["total_commission_original"],
            11000.0,
        )

    def test_validate_requires_at_least_one_side(self):
        errors, _parsed = validate_new_operation_inputs(
            _new_operation_post_data(
                agent_id=str(self.agent_a),
                property_id=str(self.test_property),
                seller_side_active="",
                buyer_side_active="",
            ),
            self.org,
        )
        self.assertIn("operation_side_required", errors)

    def test_create_persists_parties_and_side_vat(self):
        form_values = _new_operation_post_data(
            agent_id=str(self.agent_a),
            property_id=str(self.test_property),
            seller_vat_amount="111",
            buyer_vat_amount="222",
        )
        errors, operation, parsed = prepare_new_operation_from_form(
            form_values,
            self.org,
        )
        self.assertEqual(errors, [])

        operation_id, _saved = save_calculated_operation(
            parsed["agent_id"],
            parsed["property_id"],
            self.org,
            operation,
            status="approved",
        )

        record = get_operation_record(operation_id, self.org)
        self.assertAlmostEqual(record["seller_vat_original"], 111.0)
        self.assertAlmostEqual(record["buyer_vat_original"], 222.0)

        parties = {
            party["party_role"]: party
            for party in get_parties_for_operation(
                self.org,
                operation_id,
            )
        }
        self.assertTrue(parties["seller"]["is_participating"])
        self.assertAlmostEqual(
            parties["buyer"]["commission_amount"],
            6000.0,
        )

    def test_api_suggest_scoped_endpoint(self):
        client = app.test_client()
        self._login(client, self.admin, ROLE_ADMIN, self.org)
        add_property(
            "Api Suggest A",
            "CABA",
            self.org,
            agent_id=self.agent_a,
            status=STATUS_APPROVED,
            external_id="420051161-777",
        )
        add_property(
            "Api Suggest B",
            "CABA",
            self.org,
            agent_id=self.agent_a,
            status=STATUS_APPROVED,
            external_id="420051161-888",
        )

        response = client.get(
            "/api/properties/suggest?q=42005"
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertGreaterEqual(len(payload), 2)

        response = client.get(
            "/api/properties/suggest"
            f"?q=42005&agent_id={self.agent_b}"
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(payload, [])

    def test_api_prefill_endpoint(self):
        client = app.test_client()
        self._login(client, self.admin, ROLE_ADMIN, self.org)

        response = client.get(
            f"/api/properties/{self.test_property}/operation-prefill"
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(payload["agent_id"], self.agent_a)
        self.assertEqual(payload["operation_value"], 100000)


if __name__ == "__main__":
    unittest.main()
