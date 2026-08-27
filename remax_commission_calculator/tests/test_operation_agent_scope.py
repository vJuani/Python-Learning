"""
Tests: operation form filters properties by selected agent.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_operation_agent_scope.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
    list_operations_for_property,
)
from modules.database.properties_repository import (
    STATUS_APPROVED,
    get_properties,
)
from modules.operations import validate_operation_inputs
from web_app import app


class OperationAgentScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"
        create_tables()

        cls.org = add_organization("Scope Org")
        cls.other_org = add_organization("Other Org")
        cls.password = "Password1"
        pwd = hash_password(cls.password)

        cls.pablo = add_agent("Pablo Reynals", "Alto", cls.org)
        cls.tomy = add_agent("Tomy Pasman", "Alto", cls.org)
        cls.other_agent = add_agent(
            "Other Org Agent",
            "Alto",
            cls.other_org,
        )

        cls.prop_pablo_a = add_property(
            "Libertador 100",
            "CABA",
            cls.org,
            agent_id=cls.pablo,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_purpose="sale",
            listing_price=100000,
        )
        cls.prop_pablo_b = add_property(
            "Cabildo 200",
            "CABA",
            cls.org,
            agent_id=cls.pablo,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_purpose="sale",
            listing_price=120000,
        )
        cls.prop_tomy = add_property(
            "Santa Fe 300",
            "PBA",
            cls.org,
            agent_id=cls.tomy,
            status=STATUS_APPROVED,
            property_type="house",
            listing_purpose="sale",
            listing_price=200000,
        )
        cls.prop_other_org = add_property(
            "Other Org St 1",
            "CABA",
            cls.other_org,
            agent_id=cls.other_agent,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_purpose="sale",
            listing_price=90000,
        )

        cls.admin = add_user(
            "scope_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="scope_admin@example.com",
        )
        cls.agent_user = add_user(
            "scope_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.pablo,
            email="scope_agent@example.com",
        )

    def _login(self, client, user_id, role, organization_id, agent_id=None):
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = organization_id
            if agent_id is not None:
                sess["agent_id"] = agent_id

    def test_admin_pablo_only_sees_pablo_properties_query(self):
        props = get_properties(self.org, agent_id=self.pablo)
        ids = {item["id"] for item in props}
        self.assertEqual(
            ids,
            {self.prop_pablo_a, self.prop_pablo_b},
        )
        self.assertNotIn(self.prop_tomy, ids)

    def test_admin_tomy_only_sees_tomy_properties_query(self):
        props = get_properties(self.org, agent_id=self.tomy)
        ids = {item["id"] for item in props}
        self.assertEqual(ids, {self.prop_tomy})
        self.assertNotIn(self.prop_pablo_a, ids)

    def test_validate_blocks_property_of_other_agent(self):
        errors, parsed = validate_operation_inputs(
            str(self.pablo),
            str(self.prop_tomy),
            self.org,
            "100000",
            "5",
            "no",
            "0",
            operation_date="01/01/2026",
            currency="USD",
        )
        self.assertTrue(errors)
        self.assertIn(
            "Property does not belong to the selected agent.",
            errors,
        )

    def test_admin_post_mismatched_property_blocked(self):
        client = app.test_client()
        self._login(client, self.admin, ROLE_ADMIN, self.org)

        before = list_operations_for_property(
            self.prop_tomy,
            self.org,
        )

        response = client.post(
            "/operations/new",
            data={
                "action": "save",
                "operation_date": "01/01/2026",
                "agent_id": str(self.pablo),
                "property_id": str(self.prop_tomy),
                "currency": "USD",
                "original_amount": "100000",
                "seller_side_active": "1",
                "buyer_side_active": "1",
                "seller_commission_rate": "5",
                "buyer_commission_rate": "5",
                "seller_vat_amount": "0",
                "buyer_vat_amount": "0",
            },
            follow_redirects=False,
        )

        self.assertIn(response.status_code, (302, 403))
        after = list_operations_for_property(
            self.prop_tomy,
            self.org,
        )
        self.assertEqual(len(before), len(after))

    def test_admin_form_options_mark_agent_ids(self):
        client = app.test_client()
        self._login(client, self.admin, ROLE_ADMIN, self.org)

        response = client.get("/operations/new")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertIn('id="operation-new-form"', html)
        self.assertIn('id="property_search"', html)

    def test_agent_only_own_properties_in_form(self):
        client = app.test_client()
        self._login(
            client,
            self.agent_user,
            ROLE_AGENT,
            self.org,
            agent_id=self.pablo,
        )

        response = client.get("/operations/new")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertNotIn(f'value="{self.prop_tomy}"', html)

        response = client.post(
            "/operations/new",
            data={
                "action": "save",
                "operation_date": "01/01/2026",
                "agent_id": str(self.tomy),
                "property_id": str(self.prop_tomy),
                "currency": "USD",
                "original_amount": "100000",
                "seller_side_active": "1",
                "buyer_side_active": "1",
                "seller_commission_rate": "5",
                "buyer_commission_rate": "5",
                "seller_vat_amount": "0",
                "buyer_vat_amount": "0",
            },
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(
            list_operations_for_property(
                self.prop_tomy,
                self.org,
            ),
            [],
        )

    def test_other_organization_property_blocked(self):
        errors, parsed = validate_operation_inputs(
            str(self.pablo),
            str(self.prop_other_org),
            self.org,
            "100000",
            "5",
            "no",
            "0",
            operation_date="01/01/2026",
            currency="USD",
        )
        self.assertTrue(errors)
        self.assertIn(
            "Selected property was not found.",
            errors,
        )

        client = app.test_client()
        self._login(client, self.admin, ROLE_ADMIN, self.org)
        response = client.post(
            "/operations/new",
            data={
                "action": "save",
                "operation_date": "01/01/2026",
                "agent_id": str(self.pablo),
                "property_id": str(self.prop_other_org),
                "currency": "USD",
                "original_amount": "100000",
                "seller_side_active": "1",
                "buyer_side_active": "1",
                "seller_commission_rate": "5",
                "buyer_commission_rate": "5",
                "seller_vat_amount": "0",
                "buyer_vat_amount": "0",
            },
            follow_redirects=False,
        )
        # Not found stays as form error (200), never creates.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list_operations_for_property(
                self.prop_other_org,
                self.other_org,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
