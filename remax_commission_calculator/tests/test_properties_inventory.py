"""Phase 4A property inventory: currency, enrichment, filters, listing shape."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_properties_inventory.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_operation,
    add_organization,
    add_property,
    add_user,
    create_property_external_listing,
    create_tables,
    filter_properties,
    get_pending_change_for_property,
    get_property_record,
    update_property,
)
from modules.database.connection import get_connection
from modules.database.property_inventory_migration import (
    backfill_unique_listing_currency,
)
from modules.database.properties_repository import UNSET
from modules.listings_normalize import normalize_listing
from modules.properties import get_filtered_properties
from modules.property_features import normalize_property_features
from modules.property_inventory import is_commercially_available
from modules.property_types import normalize_property_type
from modules.validators import validate_property_form
from web_app import app


class PropertiesInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="inventory-test")
        create_tables()

        cls.org = add_organization("Inventory Org")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent = add_agent("Inventory Agent", "Alto", cls.org)
        cls.admin = add_user(
            "inventory_admin",
            password_hash,
            ROLE_ADMIN,
            cls.org,
        )
        cls.agent_user = add_user(
            "inventory_agent",
            password_hash,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        self.client.get("/logout", follow_redirects=True)
        return self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def _create(self, address="Av. Cabildo 3200", **extra):
        payload = {
            "jurisdiction": "CABA",
            "agent_id": self.agent,
            "property_type": "apartment",
            "listing_price": 185000,
            "listing_purpose": "sale",
        }
        payload.update(extra)
        return add_property(address, **payload, organization_id=self.org)

    def _form(self, **extra):
        data = {
            "address": "Av. Cabildo 3200",
            "jurisdiction": "CABA",
            "property_type": "apartment",
            "listing_price": "185000",
            "listing_purpose": "sale",
            "agent_id": str(self.agent),
            "listing_currency": "USD",
            "commercial_status": "available",
        }
        data.update(extra)
        return data

    def test_01_new_property_defaults_available(self):
        property_id = self._create()
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["commercial_status"], "available")
        self.assertIsNone(row["listing_currency"])

    def test_02_commercial_status_null_keeps_legacy(self):
        property_id = add_property(
            "Legacy Street 1",
            "CABA",
            self.org,
            agent_id=self.agent,
            commercial_status=None,
        )
        row = get_property_record(property_id, self.org)
        self.assertIsNone(row["commercial_status"])
        self.assertTrue(is_commercially_available(row))

        row["commercial_status"] = "sold"
        self.assertFalse(is_commercially_available(row))
        row["commercial_status"] = "available"
        self.assertTrue(is_commercially_available(row))

    def test_03_unset_field_does_not_change(self):
        property_id = self._create(
            neighborhood="Belgrano",
            rooms=3,
        )
        update_property(
            property_id,
            "Av. Cabildo 3200",
            "CABA",
            self.org,
            agent_id=self.agent,
        )
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["neighborhood"], "Belgrano")
        self.assertEqual(row["rooms"], 3)
        self.assertIs(UNSET, UNSET)

    def test_04_empty_clears_nullable(self):
        property_id = self._create(
            neighborhood="Belgrano",
            rooms=3,
            description="Luminoso",
        )
        update_property(
            property_id,
            "Av. Cabildo 3200",
            "CABA",
            self.org,
            agent_id=self.agent,
            neighborhood="",
            rooms=None,
            description="",
        )
        row = get_property_record(property_id, self.org)
        self.assertIsNone(row["neighborhood"])
        self.assertIsNone(row["rooms"])
        self.assertIsNone(row["description"])

    def test_05_total_m2_less_than_covered_rejected(self):
        errors = validate_property_form(
            "Addr",
            "CABA",
            "apartment",
            listing_price="100000",
            listing_purpose="sale",
            covered_m2="90",
            total_m2="80",
        )
        self.assertIn("err_total_m2_less_than_covered", errors)

    def test_06_property_type_aliases(self):
        self.assertEqual(normalize_property_type("departamento"), "apartment")
        self.assertEqual(normalize_property_type("depto"), "apartment")
        self.assertEqual(normalize_property_type("dpto"), "apartment")
        self.assertEqual(normalize_property_type("casa"), "house")
        self.assertEqual(normalize_property_type("terreno"), "land")
        self.assertEqual(normalize_property_type("lote"), "land")
        self.assertEqual(normalize_property_type("local"), "commercial")
        self.assertEqual(normalize_property_type("oficina"), "office")
        self.assertEqual(normalize_property_type("ph"), "ph")

    def test_07_ars_is_not_shown_as_usd(self):
        property_id = self._create(
            listing_currency="ARS",
            listing_price=185000,
        )
        self._login("inventory_admin")
        response = self.client.get(f"/properties/{property_id}")
        html = response.get_data(as_text=True)
        self.assertIn("ARS", html)
        self.assertNotIn("USD 185", html)
        self.assertNotIn("Moneda no informada", html)

    def test_08_unknown_currency_is_not_invented(self):
        property_id = self._create(listing_price=185000)
        self._login("inventory_admin")
        response = self.client.get(f"/properties/{property_id}")
        html = response.get_data(as_text=True)
        self.assertIn("Moneda no informada", html)
        self.assertNotIn("USD 185", html)

    def test_09_null_rooms_not_shown_as_zero(self):
        property_id = self._create()
        self._login("inventory_admin")
        detail = self.client.get(f"/properties/{property_id}").get_data(
            as_text=True
        )
        listing = self.client.get("/properties").get_data(as_text=True)
        self.assertNotIn("0 ambientes", detail)
        self.assertNotIn("0 ambientes", listing)

    def test_10_unique_external_currency_backfill(self):
        property_id = self._create()
        create_property_external_listing(
            self.org,
            property_id,
            "remax_web",
            "https://example.com/1",
            "active",
            listing_currency="ARS",
        )
        connection = get_connection()
        try:
            updated = backfill_unique_listing_currency(connection.cursor())
            connection.commit()
        finally:
            connection.close()
        self.assertGreaterEqual(updated, 1)
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["listing_currency"], "ARS")

    def test_11_ambiguous_external_currency_no_backfill(self):
        property_id = self._create()
        create_property_external_listing(
            self.org,
            property_id,
            "remax_web",
            "https://example.com/a",
            "active",
            listing_currency="USD",
        )
        create_property_external_listing(
            self.org,
            property_id,
            "zonaprop",
            "https://example.com/b",
            "active",
            listing_currency="ARS",
        )
        connection = get_connection()
        try:
            backfill_unique_listing_currency(connection.cursor())
            connection.commit()
        finally:
            connection.close()
        row = get_property_record(property_id, self.org)
        self.assertIsNone(row["listing_currency"])

    def test_12_directory_min_max_uses_listing_price(self):
        cheap = self._create("Cheap St 1", listing_price=80000)
        expensive = self._create("Expensive St 1", listing_price=250000)
        add_operation(
            "01/09/2026",
            self.agent,
            cheap,
            "no",
            0,
            400000,
            3,
            12000,
            10800,
            1200,
            5400,
            5400,
            0,
            5400,
            self.org,
        )
        errors, rows = get_filtered_properties(
            {"min_price": "200000", "max_price": ""},
            self.org,
        )
        self.assertEqual(errors, [])
        ids = {row["id"] for row in rows}
        self.assertIn(expensive, ids)
        self.assertNotIn(cheap, ids)

    def test_13_operation_sale_price_filters_still_work(self):
        property_id = self._create("Sale Price St", listing_price=50000)
        add_operation(
            "02/09/2026",
            self.agent,
            property_id,
            "no",
            0,
            300000,
            3,
            9000,
            8100,
            900,
            4050,
            4050,
            0,
            4050,
            self.org,
        )
        sale_rows = filter_properties(
            self.org,
            min_price=250000,
            include_all_statuses=True,
        )
        listing_rows = filter_properties(
            self.org,
            min_listing_price=200000,
            include_all_statuses=True,
        )
        sale_ids = {row["id"] for row in sale_rows}
        listing_ids = {row["id"] for row in listing_rows}
        self.assertIn(property_id, sale_ids)
        self.assertNotIn(property_id, listing_ids)

    def test_14_unknown_feature_does_not_break(self):
        features = normalize_property_features(
            {"balcony": True, "helipad": True, "broken": "nope"}
        )
        self.assertTrue(features["balcony"])
        self.assertTrue(features["helipad"])
        self.assertNotIn("broken", features)
        property_id = self._create(features={"balcony": True, "helipad": True})
        row = get_property_record(property_id, self.org)
        self.assertTrue(row["features"]["balcony"])
        self.assertTrue(row["features"]["helipad"])
        self._login("inventory_admin")
        response = self.client.get(f"/properties/{property_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Balcón", response.get_data(as_text=True))

    def test_15_normalize_listing_with_missing_fields(self):
        listing = normalize_listing(source="remax")
        self.assertEqual(listing["source"], "remax")
        self.assertIsNone(listing["address"])
        self.assertIsNone(listing["price"])
        self.assertIsNone(listing["currency"])
        self.assertIsNone(listing["rooms"])
        self.assertEqual(listing["features"], {})
        self.assertEqual(listing["images"], [])

    def test_16_create_form_saves_inventory(self):
        self._login("inventory_admin")
        response = self.client.post(
            "/properties/new",
            data=self._form(
                neighborhood="Belgrano",
                rooms="3",
                bedrooms="2",
                covered_m2="84",
                feature="balcony",
                listing_currency="USD",
            ),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        rows = filter_properties(
            self.org,
            address="Av. Cabildo 3200",
            include_all_statuses=True,
        )
        self.assertTrue(rows)
        created = rows[-1]
        self.assertEqual(created["neighborhood"], "Belgrano")
        self.assertEqual(created["rooms"], 3)
        self.assertEqual(created["listing_currency"], "USD")
        self.assertTrue(created["features"].get("balcony"))
        self.assertEqual(created["commercial_status"], "available")

    def test_17_agent_enrichment_skips_change_request(self):
        property_id = self._create(
            status="approved",
            listing_currency="USD",
        )
        self._login("inventory_agent")
        response = self.client.post(
            f"/properties/{property_id}/edit",
            data=self._form(
                neighborhood="Palermo",
                rooms="4",
            ),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        pending = get_pending_change_for_property(property_id, self.org)
        self.assertIsNone(pending)
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["neighborhood"], "Palermo")
        self.assertEqual(row["rooms"], 4)

    def test_18_agent_identity_change_creates_request(self):
        property_id = self._create(status="approved", listing_currency="USD")
        self._login("inventory_agent")
        response = self.client.post(
            f"/properties/{property_id}/edit",
            data=self._form(
                address="Nueva Direccion 100",
                listing_currency="ARS",
                neighborhood="Núñez",
            ),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        pending = get_pending_change_for_property(property_id, self.org)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["proposed_address"], "Nueva Direccion 100")
        self.assertEqual(pending["proposed_listing_currency"], "ARS")
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["address"], "Av. Cabildo 3200")
        self.assertEqual(row["listing_currency"], "USD")
        self.assertEqual(row["neighborhood"], "Núñez")

    def test_19_directory_filters_neighborhood_purpose_currency(self):
        match = self._create(
            "Filter Match 1",
            neighborhood="Belgrano",
            listing_purpose="sale",
            listing_currency="USD",
            commercial_status="available",
        )
        self._create(
            "Filter Other 1",
            neighborhood="Palermo",
            listing_purpose="rental",
            listing_currency="ARS",
            commercial_status="sold",
        )
        errors, rows = get_filtered_properties(
            {
                "address": "Filter Match 1",
                "neighborhood": "belgrano",
                "listing_purpose": "sale",
                "listing_currency": "USD",
                "commercial_status": "available",
            },
            self.org,
        )
        self.assertEqual(errors, [])
        ids = {row["id"] for row in rows}
        self.assertEqual(ids, {match})


if __name__ == "__main__":
    unittest.main()
