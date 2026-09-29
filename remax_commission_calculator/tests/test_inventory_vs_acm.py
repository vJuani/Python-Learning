"""Office inventory stays separate from ACM market listings."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "inventory_vs_acm.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)
os.environ["APP_ENV"] = "development"

from modules.acm_market import search_market_comparables
from modules.auth import hash_password
from modules.database import create_tables
from modules.database.agents_repository import add_agent
from modules.database.contacts_repository import (
    create_contact,
    get_contact,
    update_contact,
)
from modules.database.external_listings_repository import upsert_external_listing
from modules.database.organizations_repository import provision_organization
from modules.database.properties_repository import add_property, get_properties
from modules.database.property_sync_hub_repository import (
    STATUS_CONNECTED,
    upsert_property_integration,
)
from modules.database.users_repository import get_user_by_id
from modules.inventory_boundary import organization_uses_redremax
from modules.marketing_service import MarketingError, prepare_create_view
from modules.property_match import rank_contact_properties
from modules.property_shortlist import top_matches
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX
from modules.public_share import PublicShareError, ensure_property_link
from web_app import app


def _office(name, email):
    created = provision_organization(
        name=name,
        display_name=name,
        default_language="es",
        default_currency="USD",
        timezone="America/Argentina/Buenos_Aires",
        admin_username=email,
        admin_password_hash=hash_password("Oficina2026"),
        admin_role="admin",
        admin_email=email,
        first_name="Ana",
        last_name="Admin",
    )
    return created["organization_id"], created["admin_user_id"]


class InventoryVersusAcmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        create_tables()
        cls.remax_org, cls.remax_admin = _office(
            "RE/MAX Data House",
            "remax-admin@inventory.test",
        )
        cls.other_org, cls.other_admin = _office(
            "Pepito Propiedades",
            "pepito-admin@inventory.test",
        )
        upsert_property_integration(
            cls.remax_org,
            PROVIDER_REDREMAX,
            status=STATUS_CONNECTED,
            sync_enabled=True,
            config={"external_office_id": "data-house"},
        )
        cls.remax_agent = add_agent("Agente RE/MAX", "Alto", cls.remax_org)
        cls.other_agent = add_agent("Agente Pepito", "Alto", cls.other_org)
        cls.remax_property = add_property(
            "Cabildo 100 Data House",
            "CABA",
            cls.remax_org,
            agent_id=cls.remax_agent,
            listing_purpose="sale",
            property_type="apartment",
            listing_price=150000,
            listing_currency="USD",
        )
        cls.manual_property = add_property(
            "Manual de Pepito",
            "CABA",
            cls.other_org,
            agent_id=cls.other_agent,
            listing_purpose="sale",
            property_type="apartment",
            listing_price=120000,
            listing_currency="USD",
        )
        upsert_external_listing(
            cls.other_org,
            {
                "source": "zonaprop",
                "external_id": "ZP-ACM-1",
                "external_url": "https://www.zonaprop.com.ar/aviso-acm-1",
                "address": "Comparable Zonaprop",
                "price": 99000,
                "currency": "USD",
                "purpose": "sale",
                "property_type": "apartment",
            },
        )
        contact = create_contact(
            cls.other_org,
            cls.other_agent,
            name="Cliente Pepito",
        )
        cls.other_contact = contact["id"]
        update_contact(
            cls.other_contact,
            cls.other_org,
            preferences_json=json.dumps(
                {
                    "budget": {"max": 200000, "currency": "USD"},
                    "property_types": ["departamento"],
                    "purpose": "sale",
                },
                ensure_ascii=False,
            ),
        )
        cls.client = app.test_client()

    def _login(self, email):
        self.client.get("/logout")
        response = self.client.post(
            "/login",
            data={"username": email, "password": "Oficina2026"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_remax_office_sees_only_its_properties(self):
        self.assertTrue(organization_uses_redremax(self.remax_org))
        addresses = {row["address"] for row in get_properties(self.remax_org)}
        self.assertIn("Cabildo 100 Data House", addresses)
        self.assertNotIn("Manual de Pepito", addresses)
        self.assertNotIn("Comparable Zonaprop", addresses)

    def test_non_remax_office_does_not_see_remax_inventory_or_catalog(self):
        self.assertFalse(organization_uses_redremax(self.other_org))
        addresses = {row["address"] for row in get_properties(self.other_org)}
        self.assertEqual(addresses, {"Manual de Pepito"})
        self._login("pepito-admin@inventory.test")
        catalog = self.client.get("/integrations/remax/catalog")
        office_import = self.client.get("/integrations/remax")
        csv_import = self.client.get("/integrations/csv")
        home = self.client.get("/")
        self.assertEqual(catalog.status_code, 404)
        self.assertEqual(office_import.status_code, 404)
        self.assertEqual(csv_import.status_code, 200)
        self.assertNotIn("Catálogo RE/MAX", home.get_data(as_text=True))
        self.assertNotIn("Importar RE/MAX", home.get_data(as_text=True))

    def test_remax_office_can_open_its_connector(self):
        self._login("remax-admin@inventory.test")
        response = self.client.get("/integrations/remax")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Catálogo RE/MAX", self.client.get("/").get_data(as_text=True))

    def test_manual_property_belongs_to_its_office(self):
        rows = get_properties(self.other_org)
        manual = next(row for row in rows if row["id"] == self.manual_property)
        self.assertEqual(manual["organization_id"], self.other_org)
        self.assertEqual(manual["agent_id"], self.other_agent)

    def test_matcher_shortlist_marketing_and_share_ignore_external_listings(self):
        contact = get_contact(self.other_contact, self.other_org)
        ranked = rank_contact_properties(self.other_org, contact, agent_id=self.other_agent)
        ranked_ids = {
            item.get("internal_property_id") or item.get("property_id")
            for item in ranked
        }
        self.assertIn(self.manual_property, ranked_ids)
        self.assertNotIn(self.remax_property, ranked_ids)
        self.assertTrue(
            all((item.get("source") or "internal") == "internal" for item in ranked)
        )
        self.assertFalse(any(item.get("external_listing_id") for item in ranked))
        matches = top_matches(self.other_org, contact, agent_id=self.other_agent)
        self.assertTrue(matches)
        self.assertTrue(all(item["property_id"] == self.manual_property for item in matches))
        user = get_user_by_id(self.other_admin)
        with self.assertRaises(MarketingError):
            prepare_create_view(
                self.other_org,
                user,
                property_id=self.remax_property,
            )
        own = prepare_create_view(
            self.other_org,
            user,
            property_id=self.manual_property,
        )
        self.assertEqual(own["property"]["id"], self.manual_property)
        with self.assertRaises(PublicShareError):
            ensure_property_link(self.other_org, self.remax_property)
        link = ensure_property_link(self.other_org, self.manual_property)
        self.assertTrue(link["token"])

    def test_acm_reads_market_listings_without_adding_them_to_inventory(self):
        comparables = search_market_comparables(self.other_org)
        self.assertEqual(len(comparables), 1)
        comparable = comparables[0]
        self.assertEqual(comparable["external_source"], "zonaprop")
        self.assertEqual(comparable["external_listing_id"], "ZP-ACM-1")
        self.assertIn("zonaprop.com.ar", comparable["external_url"])
        inventory = {row["address"] for row in get_properties(self.other_org)}
        self.assertNotIn("Comparable Zonaprop", inventory)
        contact = get_contact(self.other_contact, self.other_org)
        ranked = rank_contact_properties(self.other_org, contact)
        self.assertFalse(
            any(item.get("address") == "Comparable Zonaprop" for item in ranked)
        )
        matches = top_matches(self.other_org, contact, agent_id=self.other_agent)
        self.assertFalse(
            any(item.get("address") == "Comparable Zonaprop" for item in matches)
        )
