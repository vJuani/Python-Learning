"""Phase 4B: deterministic contact ↔ internal property matching."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_property_match.db"
)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.agent_tasks import create_task
from modules.config import apply_config
from modules.contacts import (
    create_agent_contact,
    merge_contact_preferences,
    normalize_preferences,
)
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.agent_tasks_repository import save_task_outcome
from modules.database.contacts_repository import get_contact as get_contact_row
from modules.listings_normalize import listing_from_property, normalize_listing
from modules.organization_time import now_utc
from modules.property_match import (
    CONFLICT,
    MATCH,
    UNKNOWN,
    build_whatsapp_message,
    match_properties,
    normalize_score,
    passes_hard_filters,
    resolve_criteria,
    score_dimensions,
)
from modules.property_inventory import is_commercially_available
from web_app import app


def _listing(**fields):
    return normalize_listing(
        source="internal",
        address=fields.pop("address", "Av. Cabildo 3200"),
        **fields,
    )


def _contact(prefs=None, **extra):
    payload = {"id": extra.get("id", 1), "name": extra.get("name", "Carolina")}
    if prefs is not None:
        payload["preferences"] = prefs
    return payload


class PropertyMatchScoringTests(unittest.TestCase):
    def test_contact_without_preferences_scores_zero(self):
        ranked = match_properties(
            _contact({}),
            [_listing(neighborhood="Belgrano", price=185000, currency="USD")],
        )
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["score"], 0)
        self.assertTrue(ranked[0]["hidden"])

    def test_perfect_match_is_100(self):
        ranked = match_properties(
            _contact(
                {
                    "areas": ["Belgrano"],
                    "budget": {"min": 150000, "max": 190000, "currency": "USD"},
                    "property_types": ["departamento"],
                    "rooms": 3,
                    "bedrooms": 2,
                    "features": ["balcony"],
                }
            ),
            [
                _listing(
                    neighborhood="Belgrano",
                    price=185000,
                    currency="USD",
                    property_type="apartment",
                    rooms=3,
                    bedrooms=2,
                    features={"balcony": True},
                )
            ],
        )
        self.assertEqual(ranked[0]["score"], 100)
        self.assertEqual(ranked[0]["level"], "excellent")
        self.assertFalse(ranked[0]["hidden"])

    def test_budget_inside_range_matches(self):
        dims = score_dimensions(
            {"budget": {"max": 190000, "currency": "USD"}},
            _listing(price=185000, currency="USD"),
        )
        self.assertEqual(dims["budget"]["state"], MATCH)
        self.assertEqual(dims["budget"]["ratio"], 1.0)

    def test_budget_over_max_is_hard_filtered(self):
        ranked = match_properties(
            _contact({"budget": {"max": 190000, "currency": "USD"}}),
            [_listing(price=250000, currency="USD")],
        )
        self.assertEqual(ranked, [])
        self.assertFalse(
            passes_hard_filters(
                {"budget": {"max": 190000, "currency": "USD"}},
                _listing(price=250000, currency="USD"),
            )
        )

    def test_budget_below_min_is_partial_match(self):
        dims = score_dimensions(
            {"budget": {"min": 200000, "max": 250000, "currency": "USD"}},
            _listing(price=185000, currency="USD"),
        )
        self.assertEqual(dims["budget"]["state"], MATCH)
        self.assertLess(dims["budget"]["ratio"], 1.0)
        ranked = match_properties(
            _contact({"budget": {"min": 200000, "max": 250000, "currency": "USD"}}),
            [_listing(price=185000, currency="USD")],
        )
        self.assertEqual(len(ranked), 1)
        self.assertGreater(ranked[0]["score"], 0)

    def test_distinct_currency_is_unknown(self):
        dims = score_dimensions(
            {"budget": {"max": 190000, "currency": "USD"}},
            _listing(price=185000, currency="ARS"),
        )
        self.assertEqual(dims["budget"]["state"], UNKNOWN)
        ranked = match_properties(
            _contact({"budget": {"max": 190000, "currency": "USD"}}),
            [_listing(price=999999999, currency="ARS", neighborhood="Belgrano")],
        )
        self.assertEqual(len(ranked), 1)

    def test_null_currency_is_unknown(self):
        dims = score_dimensions(
            {"budget": {"max": 190000, "currency": "USD"}},
            _listing(price=185000, currency=None),
        )
        self.assertEqual(dims["budget"]["state"], UNKNOWN)
        ranked = match_properties(
            _contact({"budget": {"max": 100, "currency": "USD"}}),
            [_listing(price=500000, currency=None)],
        )
        self.assertEqual(len(ranked), 1)

    def test_zone_match_and_conflict(self):
        hit = score_dimensions(
            {"areas": ["Belgrano"]},
            _listing(neighborhood="Belgrano"),
        )
        miss = score_dimensions(
            {"areas": ["Belgrano"]},
            _listing(neighborhood="Palermo"),
        )
        self.assertEqual(hit["zone"]["state"], MATCH)
        self.assertEqual(miss["zone"]["state"], CONFLICT)

    def test_type_rooms_bedrooms_and_features(self):
        dims = score_dimensions(
            {
                "property_types": ["departamento"],
                "rooms": 3,
                "bedrooms": 2,
                "features": ["balcony", "pool"],
            },
            _listing(
                property_type="apartment",
                rooms=3,
                bedrooms=1,
                features={"balcony": True},
            ),
        )
        self.assertEqual(dims["type"]["state"], MATCH)
        self.assertEqual(dims["rooms"]["state"], MATCH)
        self.assertEqual(dims["bedrooms"]["state"], CONFLICT)
        features = {item["key"]: item["state"] for item in dims["features"]["items"]}
        self.assertEqual(features["balcony"], MATCH)
        self.assertEqual(features["pool"], UNKNOWN)

    def test_unknown_feature_is_never_conflict(self):
        dims = score_dimensions(
            {"features": ["pileta"]},
            _listing(features={}),
        )
        self.assertEqual(dims["features"]["state"], UNKNOWN)
        self.assertEqual(dims["features"]["items"][0]["state"], UNKNOWN)

    def test_unknown_does_not_penalize_normalized_score(self):
        only_known = {
            "budget": {"state": MATCH, "ratio": 1},
            "zone": {"state": MATCH, "ratio": 1},
            "type": {"state": UNKNOWN, "ratio": 0},
            "rooms": {"state": UNKNOWN, "ratio": 0},
            "bedrooms": {"state": UNKNOWN, "ratio": 0},
            "features": {"state": UNKNOWN, "ratio": 0},
        }
        self.assertEqual(normalize_score(only_known), 100)

    def test_conflict_lowers_normalized_score(self):
        mixed = {
            "budget": {"state": MATCH, "ratio": 1},
            "zone": {"state": CONFLICT, "ratio": 0},
            "type": {"state": UNKNOWN, "ratio": 0},
            "rooms": {"state": UNKNOWN, "ratio": 0},
            "bedrooms": {"state": UNKNOWN, "ratio": 0},
            "features": {"state": UNKNOWN, "ratio": 0},
        }
        self.assertEqual(normalize_score(mixed), 55)

    def test_order_is_deterministic(self):
        contact = _contact({"areas": ["Belgrano"]})
        first = _listing(neighborhood="Belgrano", address="A")
        first["id"] = 20
        second = _listing(neighborhood="Belgrano", address="B")
        second["id"] = 10
        ranked = match_properties(contact, [first, second])
        self.assertEqual([row["property_id"] for row in ranked], [10, 20])

    def test_unavailable_and_legacy_commercial_status(self):
        available = {
            **_listing(price=100, currency="USD"),
            "status": "approved",
            "commercial_status": None,
        }
        sold = {
            **_listing(price=100, currency="USD"),
            "status": "approved",
            "commercial_status": "sold",
        }
        self.assertTrue(is_commercially_available(available))
        self.assertFalse(is_commercially_available(sold))
        ranked = match_properties(_contact({}), [available, sold])
        self.assertEqual(len(ranked), 1)

    def test_visits_sort_new_then_seen_then_discarded(self):
        listings = []
        for index, address in enumerate(("New", "Seen", "Discarded"), start=1):
            item = _listing(neighborhood="Belgrano", address=address)
            item["id"] = index
            listings.append(item)
        ranked = match_properties(
            _contact({"areas": ["Belgrano"]}),
            listings,
            visits={
                2: {"visited": True, "discarded": False},
                3: {"visited": True, "discarded": True},
            },
        )
        self.assertEqual(
            [row["listing"]["address"] for row in ranked],
            ["New", "Seen", "Discarded"],
        )


class PropertyMatchRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="property-match-test")
        create_tables()

        cls.org_a = add_organization("Match Org A")
        cls.org_b = add_organization("Match Org B")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent_a = add_agent("Match Agent A", "Alto", cls.org_a)
        cls.agent_other = add_agent("Match Agent A2", "Alto", cls.org_a)
        cls.agent_b = add_agent("Match Agent B", "Alto", cls.org_b)
        cls.admin_a = add_user(
            "match_admin_a",
            password_hash,
            ROLE_ADMIN,
            cls.org_a,
        )
        cls.agent_user = add_user(
            "match_agent_user",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_a,
        )
        cls.other_agent_user = add_user(
            "match_agent_other",
            password_hash,
            ROLE_AGENT,
            cls.org_a,
            agent_id=cls.agent_other,
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
                "name": "Carolina Pérez",
                "phone": "5491112345678",
                "preferences": cls.prefs,
            },
        )
        cls.foreign_contact = create_agent_contact(
            cls.org_b,
            cls.agent_b,
            {"name": "Otro", "preferences": cls.prefs},
        )
        cls.perfect = add_property(
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
        cls.other_agent_property = add_property(
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
            rooms=3,
            bedrooms=2,
            features={"balcony": True},
        )
        cls.other_org_property = add_property(
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
            rooms=3,
            bedrooms=2,
            features={"balcony": True},
        )
        cls.sold = add_property(
            "Sold 100",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a,
            status="approved",
            property_type="apartment",
            listing_price=170000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
            rooms=3,
            bedrooms=2,
            commercial_status="sold",
            features={"balcony": True},
        )
        cls.low = add_property(
            "Palermo 99",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a,
            status="approved",
            property_type="apartment",
            listing_price=160000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Palermo",
            rooms=1,
            bedrooms=1,
        )
        cls.visited = add_property(
            "Cuba 500",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a,
            status="approved",
            property_type="apartment",
            listing_price=175000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
            rooms=3,
            bedrooms=2,
            features={"balcony": True},
        )
        due = now_utc() + timedelta(hours=2)
        payload = {
            "title": "Visita Cabildo",
            "task_type": "visit",
            "due_date": due.date().isoformat(),
            "due_time": due.strftime("%H:%M"),
            "contact_id": cls.contact["id"],
            "property_id": cls.visited,
        }
        cls.visit_task = create_task(
            cls.org_a,
            cls.agent_a,
            payload,
            created_by_user_id=cls.agent_user,
        )
        discarded_property = add_property(
            "Descartada 12",
            "CABA",
            cls.org_a,
            agent_id=cls.agent_a,
            status="approved",
            property_type="apartment",
            listing_price=172000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Belgrano",
            rooms=3,
            bedrooms=2,
            features={"balcony": True},
        )
        cls.discarded = discarded_property
        discarded_task = create_task(
            cls.org_a,
            cls.agent_a,
            {
                **payload,
                "title": "Visita descartada",
                "property_id": discarded_property,
            },
            created_by_user_id=cls.agent_user,
        )
        save_task_outcome(
            discarded_task["id"],
            cls.org_a,
            json.dumps({"interest": "negative"}),
        )

    def setUp(self):
        self.client = app.test_client()

    def _login(self, username):
        self.client.post(
            "/login",
            data={"username": username, "password": self.password},
            follow_redirects=True,
        )

    def test_matches_page_hides_low_scores_until_show_more(self):
        self._login("match_agent_user")
        response = self.client.get(
            f"/contacts/{self.contact['id']}/property-matches"
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Av. Cabildo 3200", body)
        self.assertIn("100% MATCH", body)
        self.assertNotIn("Palermo 99", body)
        self.assertIn("Ver más opciones", body)
        self.assertIn("Ya visitada", body)
        self.assertIn("Descartada anteriormente", body)
        self.assertNotIn("Quesada 1800", body)
        self.assertNotIn("Libertador 4100", body)
        self.assertNotIn("Sold 100", body)

        more = self.client.post(
            f"/contacts/{self.contact['id']}/property-matches",
            data={"show_more": "1"},
        )
        self.assertEqual(more.status_code, 200)
        self.assertIn("Palermo 99", more.get_data(as_text=True))

    def test_other_org_and_other_agent_are_out_of_scope(self):
        self._login("match_agent_user")
        forbidden = self.client.get(
            f"/contacts/{self.foreign_contact['id']}/property-matches"
        )
        self.assertEqual(forbidden.status_code, 404)

        self.client.get("/logout", follow_redirects=True)
        self._login("match_agent_other")
        response = self.client.get(
            f"/contacts/{self.contact['id']}/property-matches"
        )
        self.assertEqual(response.status_code, 404)

    def test_temporary_search_does_not_change_contact(self):
        self._login("match_agent_user")
        before = normalize_preferences(
            get_contact_row(self.contact["id"], self.org_a)["preferences_json"]
        )
        response = self.client.post(
            f"/contacts/{self.contact['id']}/property-matches",
            data={
                "areas": "Palermo",
                "budget_max": "190000",
                "budget_currency": "USD",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Búsqueda temporal", response.get_data(as_text=True))
        after = normalize_preferences(
            get_contact_row(self.contact["id"], self.org_a)["preferences_json"]
        )
        self.assertEqual(after["areas"], before["areas"])

    def test_saving_search_merges_preferences(self):
        extra = create_agent_contact(
            self.org_a,
            self.agent_a,
            {
                "name": "Marina",
                "preferences": {"areas": ["Núñez"]},
            },
        )
        self._login("match_agent_user")
        response = self.client.post(
            f"/contacts/{extra['id']}/property-matches",
            data={
                "areas": "Belgrano",
                "save_search": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        stored = normalize_preferences(
            get_contact_row(extra["id"], self.org_a)["preferences_json"]
        )
        self.assertEqual(stored["areas"], ["Núñez", "Belgrano"])

    def test_schedule_visit_passes_ids(self):
        self._login("match_agent_user")
        response = self.client.get(
            "/agenda/compose",
            query_string={
                "contact_id": self.contact["id"],
                "property_id": self.perfect,
                "type": "visit",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(f'name="items-0-contact_id" value="{self.contact["id"]}"', body)
        self.assertIn(f'name="items-0-property_id" value="{self.perfect}"', body)
        self.assertIn('name="items-0-task_type" value="visit"', body)
        self.assertIn("Av. Cabildo 3200", body)

    def test_whatsapp_one_and_many(self):
        self._login("match_agent_user")
        one = self.client.get(
            f"/contacts/{self.contact['id']}/property-matches/share",
            query_string={"property_id": self.perfect},
        )
        self.assertEqual(one.status_code, 302)
        location = unquote(one.headers["Location"])
        self.assertIn("wa.me/5491112345678", location)
        self.assertIn("Hola Carolina", location)
        self.assertIn("Av. Cabildo 3200", location)
        self.assertIn("Belgrano", location)

        many = self.client.get(
            f"/contacts/{self.contact['id']}/property-matches/share",
            query_string=[
                ("property_id", str(self.perfect)),
                ("property_id", str(self.visited)),
            ],
        )
        self.assertEqual(many.status_code, 302)
        many_location = unquote(many.headers["Location"])
        self.assertIn("2 propiedades", many_location)
        self.assertIn("Cabildo 3200", many_location)
        self.assertIn("Cuba 500", many_location)

    def test_listing_contract_and_merge_helper(self):
        row = {
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
            "status": "approved",
        }
        listing = listing_from_property(row)
        self.assertEqual(listing["source"], "internal")
        self.assertEqual(listing["price"], 185000)
        merged = merge_contact_preferences(
            {"areas": ["Núñez"]},
            {"areas": ["Belgrano"]},
        )
        self.assertEqual(merged["areas"], ["Núñez", "Belgrano"])
        self.assertEqual(
            resolve_criteria(
                {"preferences": {"areas": ["Núñez"]}},
                {"areas": ["Palermo"]},
            )["areas"],
            ["Palermo"],
        )
        message = build_whatsapp_message(
            {"name": "Carolina", "phone": "54911"},
            [
                {
                    "share_lines": ["Av. Cabildo 3200", "Belgrano", "USD 185.000"],
                }
            ],
        )
        self.assertIn("Hola Carolina", message)
        self.assertIn("Av. Cabildo 3200", message)


if __name__ == "__main__":
    unittest.main()
