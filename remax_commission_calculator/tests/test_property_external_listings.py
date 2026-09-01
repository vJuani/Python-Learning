"""
Tests for manual external property listings (Stage 1).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_property_listings.db"
)

from modules.access_codes import hash_access_secret
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    PROPERTY_STATUS_APPROVED,
    PROPERTY_STATUS_PENDING,
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_property_external_listing,
    create_tables,
    get_property_external_listing,
    list_property_external_listings,
    update_property_status,
)
from modules.database.connection import get_connection
from modules.database.guest_access_repository import create_guest_access
from modules.database.property_external_listings_repository import (
    ListingPersistenceError,
)
from modules.property_external_listings import (
    PROVIDER_OTHER,
    PROVIDER_REMAX_WEB,
    PROVIDER_ZONAPROP,
    STATUS_ACTIVE,
    save_new_listing,
)
from web_app import app


class PropertyExternalListingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org_a = add_organization("Org A Listings")
        cls.org_b = add_organization("Org B Listings")

        cls.agent_a1 = add_agent("Agent A1", "Alto", cls.org_a)
        cls.agent_a2 = add_agent("Agent A2", "Alto", cls.org_a)
        cls.agent_b1 = add_agent("Agent B1", "Alto", cls.org_b)

        pwd = hash_password("Password1")

        cls.admin_a = add_user(
            "admin_a_listings",
            pwd,
            ROLE_ADMIN,
            cls.org_a,
            is_active=True,
            email="admin_a_listings@example.com",
        )
        cls.user_a1 = add_user(
            "agent_a1_listings",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a1,
            is_active=True,
            email="agent_a1_listings@example.com",
        )
        cls.user_a2 = add_user(
            "agent_a2_listings",
            pwd,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a2,
            is_active=True,
            email="agent_a2_listings@example.com",
        )
        cls.admin_b = add_user(
            "admin_b_listings",
            pwd,
            ROLE_ADMIN,
            cls.org_b,
            is_active=True,
            email="admin_b_listings@example.com",
        )

        cls.property_a1 = add_property(
            "Approved A1",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a1,
            status=PROPERTY_STATUS_APPROVED,
        )
        cls.property_a2 = add_property(
            "Approved A2",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a2,
            status=PROPERTY_STATUS_APPROVED,
        )
        cls.property_pending = add_property(
            "Pending A1",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a1,
            status=PROPERTY_STATUS_PENDING,
        )
        cls.property_b1 = add_property(
            "Approved B1",
            "CABA",
            cls.org_b,
            agent_id=cls.agent_b1,
            status=PROPERTY_STATUS_APPROVED,
        )

        cls.guest_token_hash = hash_access_secret(
            "guest-listings-token"
        )
        create_guest_access(
            cls.org_a,
            cls.guest_token_hash,
            cls.admin_a,
            label="Guest listings",
        )

    @classmethod
    def tearDownClass(cls):
        _TEST_TMP.cleanup()

    def setUp(self):
        self.client = app.test_client()
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM property_external_listings"
        )
        connection.commit()
        connection.close()

    def _assert_forbidden_redirect(self, response):
        self.assertEqual(response.status_code, 302)
        location = response.headers.get("Location", "")
        self.assertTrue(
            location.endswith("/")
            or "dashboard" in location
        )

    def _login(self, username):
        return self.client.post(
            "/login",
            data={
                "username": username,
                "password": "Password1",
            },
            follow_redirects=True,
        )

    def _login_guest(self):
        return self.client.get(
            "/guest/guest-listings-token",
            follow_redirects=True,
        )

    def test_create_remax_listing_for_property(self):
        listing = create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_REMAX_WEB,
            "https://www.remax.com.ar/listing/123",
            STATUS_ACTIVE,
            external_id="RMX-123",
            created_by_user_id=self.admin_a,
        )

        self.assertEqual(listing["provider"], PROVIDER_REMAX_WEB)
        self.assertEqual(
            listing["url"],
            "https://www.remax.com.ar/listing/123",
        )
        self.assertIsNone(listing["last_synced_at"])

    def test_duplicate_structured_provider_rejected(self):
        create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_ZONAPROP,
            "https://zonaprop.com/a",
            STATUS_ACTIVE,
        )

        with self.assertRaises(ListingPersistenceError):
            create_property_external_listing(
                self.org_a,
                self.property_a1,
                PROVIDER_ZONAPROP,
                "https://zonaprop.com/b",
                STATUS_ACTIVE,
            )

    def test_multiple_other_listings_allowed(self):
        first = create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_OTHER,
            "https://portal-one.example/p/1",
            STATUS_ACTIVE,
            provider_label="Portal Uno",
        )
        second = create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_OTHER,
            "https://portal-two.example/p/2",
            STATUS_ACTIVE,
            provider_label="Portal Dos",
        )

        listings = list_property_external_listings(
            self.property_a1,
            self.org_a,
        )
        other_listings = [
            item
            for item in listings
            if item["provider"] == PROVIDER_OTHER
        ]

        self.assertEqual(len(other_listings), 2)
        self.assertNotEqual(first["id"], second["id"])

    def test_duplicate_external_id_across_properties_rejected(self):
        create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_REMAX_WEB,
            "https://www.remax.com.ar/listing/111",
            STATUS_ACTIVE,
            external_id="SHARED-EXT-1",
        )

        with self.assertRaises(ListingPersistenceError):
            create_property_external_listing(
                self.org_a,
                self.property_a2,
                PROVIDER_REMAX_WEB,
                "https://www.remax.com.ar/listing/222",
                STATUS_ACTIVE,
                external_id="SHARED-EXT-1",
            )

    def test_tenant_isolation_on_read(self):
        listing = create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_REMAX_WEB,
            "https://www.remax.com.ar/listing/tenant",
            STATUS_ACTIVE,
        )

        self.assertIsNone(
            get_property_external_listing(
                listing["id"],
                self.org_b,
            )
        )

    def test_agent_can_manage_own_property_listing_via_http(self):
        self._login("agent_a1_listings")

        response = self.client.post(
            f"/properties/{self.property_a1}/listings/new",
            data={
                "provider": PROVIDER_REMAX_WEB,
                "url": "https://www.remax.com.ar/listing/agent",
                "status": STATUS_ACTIVE,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ver en RE/MAX", response.data)

    def test_agent_cannot_access_other_agent_property_detail(self):
        self._login("agent_a1_listings")

        response = self.client.get(
            f"/properties/{self.property_a2}",
            follow_redirects=False,
        )

        self._assert_forbidden_redirect(response)

    def test_guest_can_view_approved_property_listings(self):
        create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_REMAX_WEB,
            "https://www.remax.com.ar/listing/guest",
            STATUS_ACTIVE,
        )

        self._login_guest()

        response = self.client.get(
            f"/properties/{self.property_a1}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ver en RE/MAX", response.data)

    def test_guest_cannot_view_pending_property(self):
        self._login_guest()

        response = self.client.get(
            f"/properties/{self.property_pending}",
            follow_redirects=False,
        )

        self._assert_forbidden_redirect(response)

    def test_guest_cannot_create_listing(self):
        self._login_guest()

        response = self.client.post(
            f"/properties/{self.property_a1}/listings/new",
            data={
                "provider": PROVIDER_REMAX_WEB,
                "url": "https://www.remax.com.ar/listing/guest-write",
                "status": STATUS_ACTIVE,
            },
        )

        self.assertIn(response.status_code, (302, 403))

    def test_save_new_listing_returns_localized_duplicate_error(self):
        create_property_external_listing(
            self.org_a,
            self.property_a2,
            PROVIDER_REMAX_WEB,
            "https://www.remax.com.ar/listing/dup",
            STATUS_ACTIVE,
        )

        errors, listing, _parsed, conflict = save_new_listing(
            self.org_a,
            self.property_a2,
            {
                "provider": PROVIDER_REMAX_WEB,
                "url": "https://www.remax.com.ar/listing/dup-2",
                "status": STATUS_ACTIVE,
            },
            language="es",
        )

        self.assertIsNone(listing)
        self.assertEqual(len(errors), 1)
        self.assertIn("proveedor", errors[0].lower())
        self.assertIsNotNone(conflict)
        self.assertTrue(conflict["same_property"])

    def test_save_new_listing_external_id_conflict_includes_property(self):
        create_property_external_listing(
            self.org_a,
            self.property_a1,
            PROVIDER_REMAX_WEB,
            "https://www.remax.com.ar/listing/original",
            STATUS_ACTIVE,
            external_id="RMX-SHARED-99",
        )

        errors, listing, _parsed, conflict = save_new_listing(
            self.org_a,
            self.property_a2,
            {
                "provider": PROVIDER_REMAX_WEB,
                "url": "https://www.remax.com.ar/listing/other",
                "status": STATUS_ACTIVE,
                "external_id": "RMX-SHARED-99",
            },
            language="es",
        )

        self.assertIsNone(listing)
        self.assertEqual(len(errors), 1)
        self.assertIn("PROP-", errors[0])
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["property_id"], self.property_a1)
        self.assertFalse(conflict["same_property"])

    def test_property_list_shows_view_link(self):
        self._login("admin_a_listings")

        response = self.client.get("/properties")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"properties-page", response.data)
        self.assertIn(b'name="agent_id"', response.data)
        self.assertIn(b"/properties/", response.data)

    def test_listing_visible_after_property_approval(self):
        pending_property_id = add_property(
            "Pending With Listing",
            "CABA",
            self.org_a,
            agent_id=self.agent_a1,
            status=PROPERTY_STATUS_PENDING,
            property_type="apartment",
        )

        self._login("agent_a1_listings")

        create_response = self.client.post(
            f"/properties/{pending_property_id}/listings/new",
            data={
                "provider": PROVIDER_REMAX_WEB,
                "url": (
                    "https://www.remax.com.ar/listings/"
                    "regression-after-approval"
                ),
                "status": STATUS_ACTIVE,
            },
            follow_redirects=True,
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertIn(b"Ver en RE/MAX", create_response.data)

        detail_before = self.client.get(
            f"/properties/{pending_property_id}"
        )
        self.assertEqual(detail_before.status_code, 200)
        self.assertIn(b"Ver en RE/MAX", detail_before.data)

        self.client.get("/logout", follow_redirects=True)
        self._login("admin_a_listings")

        update_property_status(
            pending_property_id,
            self.org_a,
            PROPERTY_STATUS_APPROVED,
        )

        approve_response = self.client.post(
            f"/approvals/properties/{pending_property_id}/approve",
            follow_redirects=True,
        )
        self.assertEqual(approve_response.status_code, 200)

        detail_after_admin = self.client.get(
            f"/properties/{pending_property_id}"
        )
        self.assertEqual(detail_after_admin.status_code, 200)
        self.assertIn(
            b"Ver en RE/MAX",
            detail_after_admin.data,
            "Admin should still see listing after approval",
        )

        self.client.get("/logout", follow_redirects=True)
        self._login("agent_a1_listings")

        detail_after_agent = self.client.get(
            f"/properties/{pending_property_id}"
        )
        self.assertEqual(detail_after_agent.status_code, 200)
        self.assertIn(
            b"Ver en RE/MAX",
            detail_after_agent.data,
            "Agent should still see listing after approval",
        )

        stored = list_property_external_listings(
            pending_property_id,
            self.org_a,
        )
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["property_id"], pending_property_id)

    def test_pending_property_review_shows_listings(self):
        pending_property_id = add_property(
            "Pending Review Listings",
            "CABA",
            self.org_a,
            agent_id=self.agent_a1,
            status=PROPERTY_STATUS_PENDING,
            property_type="house",
        )

        create_property_external_listing(
            self.org_a,
            pending_property_id,
            PROVIDER_REMAX_WEB,
            "https://www.remax.com.ar/listing/review-panel",
            STATUS_ACTIVE,
            created_by_user_id=self.user_a1,
        )

        self._login("admin_a_listings")

        response = self.client.get(
            f"/approvals/properties/{pending_property_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ver en RE/MAX", response.data)


if __name__ == "__main__":
    unittest.main()
