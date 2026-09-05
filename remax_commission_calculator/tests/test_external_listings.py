"""Phase 5A.1: indexed external listings + connector contract."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_external_listings.db"
)

from modules.agent_tasks import AgentTaskError, create_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.connection import get_connection
from modules.database.external_listings_repository import (
    UPSERT_CREATED,
    UPSERT_UNCHANGED,
    UPSERT_UPDATED,
    get_external_listing,
    get_external_listing_by_source_id,
    list_active_external_listings,
    list_external_listings,
    mark_external_listing_inactive,
    upsert_external_listing,
)
from modules.listing_connectors import (
    get_listing_connector,
    listing_source_capabilities,
)
from modules.listing_connectors.internal import InternalListingConnector
from modules.listing_connectors.mercadolibre import ML_FIXTURES, MercadoLibreConnector
from modules.listing_sources import (
    SEARCH_NOT_AUTHORIZED,
    SEARCH_UNSUPPORTED_SEARCH,
    SOURCE_ARGENPROP,
    SOURCE_INTERNAL,
    SOURCE_MERCADOLIBRE,
    SOURCE_REMAX,
    SOURCE_ZONAPROP,
    UnknownListingSource,
)
from modules.listings_normalize import (
    listing_from_external_listing,
    listing_from_property,
    normalize_listing,
)
from modules.organization_time import now_utc
from modules.property_match import (
    build_whatsapp_message,
    decorate_match,
    match_properties,
    rank_contact_properties,
)
from web_app import app


CABILDO_URL = "https://departamento.mercadolibre.com.ar/MLA111-cabildo"


def _property_count(organization_id):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM properties WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()[0]
    finally:
        connection.close()


def _cabildo_record(**extra):
    payload = {
        "source": SOURCE_MERCADOLIBRE,
        "external_id": "MLA111",
        "external_url": CABILDO_URL,
        "address": "Av. Cabildo 3200",
        "neighborhood": "Belgrano",
        "property_type": "apartment",
        "purpose": "sale",
        "price": 185000,
        "currency": "USD",
        "rooms": 3,
        "bedrooms": 2,
        "bathrooms": 1,
        "covered_m2": 84,
        "features": {"balcony": True},
        "images": ["https://http2.mlstatic.com/fixture-cabildo.jpg"],
        "commercial_status": "available",
        "description": "Departamento en Belgrano",
    }
    payload.update(extra)
    return payload


def _remax_record(**extra):
    payload = _cabildo_record(source=SOURCE_REMAX, **extra)
    if "external_url" not in extra:
        payload["external_url"] = "https://remax.com.ar/listings/RM-CABILDO"
    return payload


class ExternalListingsBase(unittest.TestCase):
    _shared = None

    @classmethod
    def setUpClass(cls):
        if ExternalListingsBase._shared:
            for key, value in ExternalListingsBase._shared.items():
                setattr(cls, key, value)
            return
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="external-listings-test")
        create_tables()

        cls.org_a = add_organization("External Org A")
        cls.org_b = add_organization("External Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent_a = add_agent("External Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent("External Agent A2", "Alto", cls.org_a)
        cls.agent_b = add_agent("External Agent B", "Alto", cls.org_b)
        cls.admin_a = add_user(
            "ext_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "ext_agent_user",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.other_agent_user = add_user(
            "ext_agent_other",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_other,
        )
        cls.agent_user_b = add_user(
            "ext_agent_b",
            password_hash,
            ROLE_AGENT,
            cls.org_b,
            agent_id=cls.agent_b,
        )
        cls.prefs = {
            "areas": ["Belgrano"],
            "budget": {"min": 150000, "max": 190000, "currency": "USD"},
            "property_types": ["departamento"],
            "rooms": 3,
            "bedrooms": 2,
            "features": ["balcony"],
            "purpose": "sale",
        }
        cls.contact = create_agent_contact(
            cls.org_a,
            cls.agent_a,
            {
                "name": "Carolina López",
                "phone": "5491112345678",
                "preferences": cls.prefs,
            },
        )
        cls.internal_id = add_property(
            "Av. Cabildo 3200",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a,
            status="approved",
            property_type="apartment",
            listing_price=185000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
            rooms=3,
            bedrooms=2,
            covered_m2=84,
            features={"balcony": True},
        )
        add_property(
            "Pending 1",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a,
            status="pending",
            property_type="apartment",
            listing_price=180000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
        )
        add_property(
            "Quesada 1800",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_other,
            status="approved",
            property_type="apartment",
            listing_price=180000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
        )
        add_property(
            "Libertador 4100",
            "CABA",
            cls.org_b,
            agent_id=cls.agent_b,
            status="approved",
            property_type="apartment",
            listing_price=180000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
        )
        ExternalListingsBase._shared = {
            key: getattr(cls, key)
            for key in (
                "org_a",
                "org_b",
                "password",
                "agent_a",
                "agent_other",
                "agent_b",
                "admin_a",
                "agent_user",
                "other_agent_user",
                "agent_user_b",
                "prefs",
                "contact",
                "internal_id",
            )
        }

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        response = self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        return response

    def _due_payload(self, **extra):
        due = now_utc() + timedelta(hours=3)
        payload = {
            "title": "Visita Cabildo",
            "task_type": "visit",
            "due_date": due.date().isoformat(),
            "due_time": due.strftime("%H:%M"),
            "contact_id": self.contact["id"],
        }
        payload.update(extra)
        return payload


class ExternalListingRepositoryTests(ExternalListingsBase):
    def test_upsert_create_unique_and_seen(self):
        first_seen = "2026-01-01T10:00:00"
        later = "2026-01-01T11:00:00"
        stamps = iter((first_seen, later, later, later))
        with patch(
            "modules.database.external_listings_repository._now_iso",
            side_effect=lambda: next(stamps),
        ):
            created = upsert_external_listing(self.org_a, _cabildo_record())
            unchanged = upsert_external_listing(self.org_a, _cabildo_record())

        self.assertEqual(created["status"], UPSERT_CREATED)
        listing = created["listing"]
        self.assertEqual(listing["source"], SOURCE_MERCADOLIBRE)
        self.assertEqual(listing["external_id"], "MLA111")
        self.assertEqual(listing["first_seen_at"], first_seen)
        self.assertEqual(listing["last_seen_at"], first_seen)
        self.assertTrue(listing["is_active"])
        self.assertEqual(listing["images"], [
            "https://http2.mlstatic.com/fixture-cabildo.jpg",
        ])

        self.assertEqual(unchanged["status"], UPSERT_UNCHANGED)
        self.assertEqual(unchanged["listing"]["id"], listing["id"])
        self.assertEqual(unchanged["listing"]["first_seen_at"], first_seen)
        self.assertEqual(unchanged["listing"]["last_seen_at"], later)
        self.assertEqual(unchanged["listing"]["updated_at"], first_seen)

        self.assertEqual(
            get_external_listing_by_source_id(
                self.org_a,
                SOURCE_MERCADOLIBRE,
                "MLA111",
            )["id"],
            listing["id"],
        )
        self.assertEqual(
            len([
                row
                for row in list_external_listings(
                    self.org_a,
                    source=SOURCE_MERCADOLIBRE,
                )
                if row["external_id"] == "MLA111"
            ]),
            1,
        )

    def test_content_change_updates_hash_not_first_seen(self):
        stamps = iter(("2026-02-01T10:00:00", "2026-02-01T12:00:00"))
        with patch(
            "modules.database.external_listings_repository._now_iso",
            side_effect=lambda: next(stamps),
        ):
            created = upsert_external_listing(
                self.org_a,
                _cabildo_record(external_id="MLA-CHANGE"),
            )
            first_seen = created["listing"]["first_seen_at"]
            original_hash = created["listing"]["content_hash"]
            updated = upsert_external_listing(
                self.org_a,
                _cabildo_record(external_id="MLA-CHANGE", price=190000),
            )
        listing = updated["listing"]
        self.assertEqual(updated["status"], UPSERT_UPDATED)
        self.assertEqual(listing["first_seen_at"], first_seen)
        self.assertEqual(listing["price"], 190000)
        self.assertNotEqual(listing["content_hash"], original_hash)
        self.assertNotEqual(listing["updated_at"], first_seen)
        self.assertEqual(listing["last_seen_at"], listing["updated_at"])

    def test_unique_is_per_organization(self):
        left = upsert_external_listing(
            self.org_a,
            _cabildo_record(external_id="MLA-ORG"),
        )
        right = upsert_external_listing(
            self.org_b,
            _cabildo_record(external_id="MLA-ORG"),
        )
        self.assertNotEqual(left["listing"]["id"], right["listing"]["id"])
        self.assertIsNone(
            get_external_listing(left["listing"]["id"], self.org_b)
        )
        self.assertIsNone(
            get_external_listing_by_source_id(
                self.org_b,
                SOURCE_MERCADOLIBRE,
                "missing",
            )
        )

    def test_mark_inactive_and_reactivate(self):
        created = upsert_external_listing(
            self.org_a,
            _cabildo_record(external_id="MLA-OFF"),
        )
        listing_id = created["listing"]["id"]
        inactive = mark_external_listing_inactive(listing_id, self.org_a)
        self.assertFalse(inactive["is_active"])
        active_ids = [
            row["id"]
            for row in list_active_external_listings(self.org_a)
        ]
        self.assertNotIn(listing_id, active_ids)
        again = upsert_external_listing(
            self.org_a,
            _cabildo_record(external_id="MLA-OFF"),
        )
        self.assertEqual(again["status"], UPSERT_UNCHANGED)
        self.assertTrue(again["listing"]["is_active"])

    def test_source_validation_rejects_internal_and_unknown(self):
        with self.assertRaises(ValueError):
            upsert_external_listing(
                self.org_a,
                _cabildo_record(source=SOURCE_INTERNAL, external_id="INT1"),
            )
        with self.assertRaises(UnknownListingSource):
            upsert_external_listing(
                self.org_a,
                _cabildo_record(source="portal-inventado", external_id="X1"),
            )

    def test_upsert_does_not_create_internal_property(self):
        before = _property_count(self.org_a)
        upsert_external_listing(
            self.org_a,
            _cabildo_record(external_id="MLA-NOPROP"),
        )
        self.assertEqual(_property_count(self.org_a), before)


class NormalizationAndConnectorTests(ExternalListingsBase):
    def test_internal_and_external_share_listing_contract(self):
        internal = listing_from_property(
            {
                "address": "Av. Cabildo 3200",
                "neighborhood": "Belgrano",
                "listing_price": 185000,
                "listing_currency": "USD",
                "property_type": "apartment",
                "listing_purpose": "sale",
                "rooms": 3,
                "bedrooms": 2,
                "features": {"balcony": True},
                "commercial_status": "available",
            }
        )
        created = upsert_external_listing(
            self.org_a,
            _cabildo_record(external_id="MLA-NORM"),
        )
        external = listing_from_external_listing(created["listing"])
        self.assertEqual(set(internal), set(external))
        self.assertEqual(internal["price"], external["price"])
        self.assertEqual(internal["neighborhood"], external["neighborhood"])
        self.assertIsInstance(external["images"], list)
        self.assertTrue(external["images"])
        self.assertEqual(external["source"], SOURCE_MERCADOLIBRE)
        self.assertEqual(internal["source"], SOURCE_INTERNAL)
        self.assertEqual(external["external_url"], CABILDO_URL)

    def test_images_json_roundtrip(self):
        listing = normalize_listing(
            source=SOURCE_MERCADOLIBRE,
            images='["https://img.example/a.jpg"]',
        )
        self.assertEqual(listing["images"], ["https://img.example/a.jpg"])

    def test_internal_connector_respects_scope(self):
        result = InternalListingConnector().search(
            self.prefs,
            organization_id=self.org_a,
            agent_id=self.agent_a,
        )
        addresses = {item.get("address") for item in result.listings}
        self.assertIn("Av. Cabildo 3200", addresses)
        self.assertNotIn("Pending 1", addresses)
        self.assertNotIn("Quesada 1800", addresses)
        self.assertNotIn("Libertador 4100", addresses)
        for item in result.listings:
            self.assertEqual(item["source"], SOURCE_INTERNAL)
            self.assertIsNotNone(item.get("internal_property_id"))

    def test_mercadolibre_fixture_normalizes_without_http(self):
        connector = MercadoLibreConnector()
        result = connector.search({}, organization_id=self.org_a)
        self.assertEqual(result.status, SEARCH_NOT_AUTHORIZED)
        self.assertFalse(result.ok)
        self.assertEqual(result.listings, [])
        listing = connector.normalize(ML_FIXTURES[0])
        self.assertEqual(listing["source"], SOURCE_MERCADOLIBRE)
        self.assertEqual(listing["address"], "Av. Cabildo 3200")
        self.assertEqual(listing["neighborhood"], "Belgrano")
        self.assertEqual(listing["price"], 185000)
        self.assertEqual(listing["currency"], "USD")
        self.assertEqual(listing["rooms"], 3)
        self.assertEqual(listing["external_url"], CABILDO_URL)
        self.assertIsInstance(listing["images"], list)
        fetched = connector.fetch("MLA111", organization_id=self.org_a)
        self.assertEqual(fetched["external_id"], "MLA111")
        capabilities = listing_source_capabilities()
        self.assertEqual(
            capabilities[SOURCE_MERCADOLIBRE]["search"],
            SEARCH_NOT_AUTHORIZED,
        )
        self.assertFalse(capabilities[SOURCE_MERCADOLIBRE]["visible_in_match"])

    def test_zonaprop_and_argenprop_do_not_simulate_results(self):
        capabilities = listing_source_capabilities()
        self.assertEqual(
            capabilities[SOURCE_ZONAPROP]["search"],
            SEARCH_UNSUPPORTED_SEARCH,
        )
        self.assertEqual(
            capabilities[SOURCE_ARGENPROP]["search"],
            SEARCH_UNSUPPORTED_SEARCH,
        )
        for source in (SOURCE_ZONAPROP, SOURCE_ARGENPROP):
            result = get_listing_connector(source).search(
                self.prefs,
                organization_id=self.org_a,
            )
            self.assertEqual(result.status, SEARCH_UNSUPPORTED_SEARCH)
            self.assertEqual(result.listings, [])
            self.assertIsNone(
                get_listing_connector(source).fetch(
                    "anything",
                    organization_id=self.org_a,
                )
            )


class MatcherAgendaWhatsappTests(ExternalListingsBase):
    def test_matcher_mixed_list_keeps_same_score_and_identity(self):
        created = upsert_external_listing(
            self.org_a,
            _remax_record(external_id="RM-MATCH"),
        )
        internal = listing_from_property(
            {
                "id": self.internal_id,
                "address": "Av. Cabildo 3200",
                "neighborhood": "Belgrano",
                "listing_price": 185000,
                "listing_currency": "USD",
                "property_type": "apartment",
                "listing_purpose": "sale",
                "rooms": 3,
                "bedrooms": 2,
                "features": {"balcony": True},
                "commercial_status": "available",
            }
        )
        internal["internal_property_id"] = self.internal_id
        external = listing_from_external_listing(created["listing"])
        external["external_listing_id"] = created["listing"]["id"]
        ranked = match_properties(
            {"id": self.contact["id"], "name": "Carolina", "preferences": self.prefs},
            [internal, external],
        )
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["score"], ranked[1]["score"])
        self.assertEqual(ranked[0]["score"], 100)
        by_source = {item["source"]: item for item in ranked}
        self.assertEqual(by_source["internal"]["internal_property_id"], self.internal_id)
        self.assertIsNone(by_source["internal"]["external_listing_id"])
        self.assertEqual(
            by_source[SOURCE_REMAX]["external_listing_id"],
            created["listing"]["id"],
        )
        self.assertTrue(by_source[SOURCE_REMAX]["external_url"])
        self.assertIsNone(by_source[SOURCE_REMAX]["internal_property_id"])

        mixed = rank_contact_properties(
            self.org_a,
            self.contact,
            agent_id=self.agent_a,
        )
        sources = {item["source"] for item in mixed}
        self.assertIn(SOURCE_INTERNAL, sources)
        self.assertIn(SOURCE_REMAX, sources)
        self.assertNotIn(SOURCE_MERCADOLIBRE, sources)

    def test_whatsapp_external_uses_original_url(self):
        created = upsert_external_listing(
            self.org_a,
            _remax_record(external_id="RM-WA"),
        )
        ranked = match_properties(
            self.contact,
            [listing_from_external_listing(created["listing"]) | {
                "external_listing_id": created["listing"]["id"],
            }],
        )
        card = decorate_match(ranked[0], language="es")
        message = build_whatsapp_message(self.contact, [card], language="es")
        self.assertIn("remax.com.ar", message)
        self.assertNotIn(f"/properties/{self.internal_id}", message)

    def test_whatsapp_external_without_url_shares_basic_data(self):
        created = upsert_external_listing(
            self.org_a,
            _remax_record(external_id="RM-WA-NO-URL", external_url=None),
        )
        ranked = match_properties(
            self.contact,
            [listing_from_external_listing(created["listing"]) | {
                "external_listing_id": created["listing"]["id"],
            }],
        )
        card = decorate_match(ranked[0], language="es")
        message = build_whatsapp_message(self.contact, [card], language="es")
        self.assertIn("Av. Cabildo 3200", message)
        self.assertNotIn("http", message)
        self.assertNotIn("remax.com.ar", message)
        self.assertNotIn(f"/properties/{self.internal_id}", message)

    def test_agenda_compose_and_create_keep_external_listing(self):
        before = _property_count(self.org_a)
        created = upsert_external_listing(
            self.org_a,
            _remax_record(external_id="RM-AGENDA"),
        )
        listing_id = created["listing"]["id"]
        self._login("ext_agent_user")
        preview = self.client.get(
            "/agenda/compose",
            query_string={
                "contact_id": self.contact["id"],
                "external_listing_id": listing_id,
                "type": "visit",
            },
        )
        self.assertEqual(preview.status_code, 200)
        body = preview.get_data(as_text=True)
        self.assertIn("VISITA", body.upper())
        self.assertIn("Carolina López", body)
        self.assertIn("RE/MAX", body)
        self.assertIn("Av. Cabildo 3200", body)
        self.assertIn("Belgrano", body)
        self.assertIn("185", body)
        self.assertIn(
            f'name="items-0-external_listing_id" value="{listing_id}"',
            body,
        )
        self.assertIn('name="items-0-property_id" value=""', body)

        task = create_task(
            self.org_a,
            self.agent_a,
            self._due_payload(external_listing_id=listing_id),
            created_by_user_id=self.agent_user,
        )
        self.assertEqual(task["external_listing_id"], listing_id)
        self.assertIsNone(task["property_id"])
        self.assertEqual(task["property_address"], "Av. Cabildo 3200")
        self.assertIn("remax.com.ar", task["external_url"])
        self.assertEqual(_property_count(self.org_a), before)

        self.client.get("/logout", follow_redirects=True)
        self._login("ext_agent_b")
        forbidden = self.client.get(
            "/agenda/compose",
            query_string={
                "contact_id": self.contact["id"],
                "external_listing_id": listing_id,
                "type": "visit",
            },
        )
        self.assertEqual(forbidden.status_code, 404)
        with self.assertRaises(AgentTaskError):
            create_task(
                self.org_b,
                self.agent_b,
                self._due_payload(
                    contact_id=None,
                    contact_name="Externo",
                    external_listing_id=listing_id,
                ),
                created_by_user_id=self.agent_user_b,
            )

    def test_share_and_matches_page_keep_external_url(self):
        before = _property_count(self.org_a)
        created = upsert_external_listing(
            self.org_a,
            _remax_record(external_id="RM-SHARE"),
        )
        listing_id = created["listing"]["id"]
        self._login("ext_agent_user")
        page = self.client.get(
            f"/contacts/{self.contact['id']}/property-matches"
        )
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Ver publicación", body)
        self.assertIn("remax.com.ar", body)
        self.assertIn("RE/MAX", body)
        self.assertNotIn("✓ Mercado Libre", body)

        share = self.client.get(
            f"/contacts/{self.contact['id']}/property-matches/share",
            query_string={"external_listing_id": listing_id},
        )
        self.assertEqual(share.status_code, 302)
        location = unquote(share.headers["Location"])
        self.assertIn("wa.me/5491112345678", location)
        self.assertIn("remax.com.ar", location)
        self.assertEqual(_property_count(self.org_a), before)


if __name__ == "__main__":
    unittest.main()
