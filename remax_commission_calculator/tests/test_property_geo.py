"""FASE 5D — Maps V2: Haversine geo search, Need radius, ACM distance, JRH."""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_property_geo.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GOOGLE_MAPS_BROWSER_KEY", None)
os.environ.pop("GOOGLE_MAPS_SERVER_KEY", None)
os.environ["MAPS_PROVIDER"] = "none"

from modules.acm_engine import score_comparable
from modules.acm_service import create_acm_for_property, finalize_acm, get_acm_view
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact, normalize_preferences, preferences_from_form
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
    update_property,
)
from modules.database.properties_repository import (
    STATUS_APPROVED,
    filter_properties,
)
from modules.database.property_acm_migration import migrate_property_acm_sqlite
from modules.database.property_acm_repository import list_comparables
from modules.jrh_ai_classify import extract_geo_entities, extract_property_entities
from modules.jrh_ai_intents import QUERY_PROPERTIES
from modules.jrh_ai_service import ask_jrh
from modules.maps.config import GEOCODE_RESOLVED
from modules.maps.geo import (
    bounding_box,
    distance_between_coordinates,
    filter_by_radius,
    format_distance,
    need_geo_ratio,
)
from modules.maps.location import has_coordinates
from modules.properties import get_filtered_properties, validate_property_filters
from modules.property_match import MATCH, passes_hard_filters, rank_contact_properties, score_dimensions
from web_app import app


ITALIA = (-34.4940, -58.5060)
# ~500 m / 1.5 km / 4 km north of Italia 1341
OFFSET_500 = 500 / 111_320
OFFSET_1500 = 1500 / 111_320
OFFSET_4000 = 4000 / 111_320


class GeoMathTests(unittest.TestCase):
    def test_same_coordinates_are_zero(self):
        self.assertEqual(
            distance_between_coordinates(*ITALIA, *ITALIA),
            0.0,
        )

    def test_known_distance_is_reasonable(self):
        meters = distance_between_coordinates(
            ITALIA[0],
            ITALIA[1],
            ITALIA[0] + OFFSET_500,
            ITALIA[1],
        )
        self.assertAlmostEqual(meters, 500, delta=15)

    def test_radius_includes_500m_excludes_1500m(self):
        rows = [
            {"id": 1, "latitude": ITALIA[0] + OFFSET_500, "longitude": ITALIA[1]},
            {"id": 2, "latitude": ITALIA[0] + OFFSET_1500, "longitude": ITALIA[1]},
        ]
        kept = filter_by_radius(rows, ITALIA[0], ITALIA[1], 1000)
        self.assertEqual([row["id"] for row in kept], [1])

    def test_bounding_box_keeps_valid_candidate(self):
        box = bounding_box(ITALIA[0], ITALIA[1], 1000)
        lat = ITALIA[0] + OFFSET_500
        lng = ITALIA[1]
        self.assertTrue(box["south"] <= lat <= box["north"])
        self.assertTrue(box["west"] <= lng <= box["east"])

    def test_need_geo_ratio_bands(self):
        self.assertEqual(need_geo_ratio(100, 1000), 1.0)
        self.assertEqual(need_geo_ratio(400, 1000), 0.85)
        self.assertEqual(need_geo_ratio(1100, 1000), None)

    def test_format_distance(self):
        self.assertEqual(format_distance(650), "650 m")
        self.assertEqual(format_distance(1200, "es"), "1,2 km")

    def test_geo_module_never_calls_google(self):
        source = Path(__file__).resolve().parents[1] / "modules" / "maps" / "geo.py"
        self.assertNotIn("googleapis", source.read_text(encoding="utf-8"))
        self.assertNotIn("urllib", source.read_text(encoding="utf-8"))


class GeoSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="geo-tests")
        create_tables()
        cls.org = add_organization("Geo Org")
        cls.other_org = add_organization("Geo Other")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Geo Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Geo Other Agent", "Alto", cls.org)
        cls.foreign_agent = add_agent("Geo Foreign", "Alto", cls.other_org)
        cls.admin = add_user("geo_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_user = add_user(
            "geo_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.other_user = add_user(
            "geo_other",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.admin_record = {
            "id": cls.admin,
            "role": ROLE_ADMIN,
            "organization_id": cls.org,
        }
        cls.agent_record = {
            "id": cls.agent_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.agent_id,
        }
        cls.target = cls._prop("Italia 1341", *ITALIA)
        cls.near = cls._prop(
            "Cerca 500",
            ITALIA[0] + OFFSET_500,
            ITALIA[1],
        )
        cls.mid = cls._prop(
            "Media 1500",
            ITALIA[0] + OFFSET_1500,
            ITALIA[1],
        )
        cls.far = cls._prop(
            "Lejos 4000",
            ITALIA[0] + OFFSET_4000,
            ITALIA[1],
        )
        cls.plain = add_property(
            "Sin coords",
            "PBA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=200000,
            listing_purpose="sale",
            listing_currency="USD",
            neighborhood="Martínez",
            rooms=3,
            bedrooms=2,
            covered_m2=80,
        )
        cls.foreign = cls._prop(
            "Otra org",
            ITALIA[0] + OFFSET_500,
            ITALIA[1],
            org=cls.other_org,
            agent_id=cls.foreign_agent,
        )
        cls.other_agent_prop = cls._prop(
            "Otro agente 500",
            ITALIA[0] + OFFSET_500,
            ITALIA[1] + 0.0004,
            agent_id=cls.other_agent,
        )
        cls.snapshot_comp = cls._prop(
            "Snapshot 500",
            ITALIA[0] + OFFSET_500,
            ITALIA[1] - 0.0003,
        )

    @classmethod
    def _prop(cls, address, lat, lng, *, org=None, agent_id=None, rooms=3):
        return add_property(
            address,
            "PBA",
            org or cls.org,
            agent_id=agent_id or cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=320000,
            listing_purpose="sale",
            listing_currency="USD",
            neighborhood="Martínez",
            rooms=rooms,
            bedrooms=2,
            covered_m2=85,
            total_m2=85,
            latitude=lat,
            longitude=lng,
            geocode_status=GEOCODE_RESOLVED,
        )

    def _login(self, user_id, role, org, agent_id=None):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["role"] = role
            sess["organization_id"] = org
            if agent_id is not None:
                sess["agent_id"] = agent_id
        return client

    def test_radius_1km_includes_only_a(self):
        rows = filter_properties(
            self.org,
            center_lat=ITALIA[0],
            center_lng=ITALIA[1],
            radius_m=1000,
            exclude_property_id=self.target,
        )
        ids = {row["id"] for row in rows}
        self.assertIn(self.near, ids)
        self.assertNotIn(self.mid, ids)
        self.assertNotIn(self.far, ids)
        self.assertNotIn(self.plain, ids)
        self.assertNotIn(self.foreign, ids)

    def test_radius_2km_includes_a_and_b(self):
        rows = filter_properties(
            self.org,
            center_lat=ITALIA[0],
            center_lng=ITALIA[1],
            radius_m=2000,
            exclude_property_id=self.target,
        )
        ids = {row["id"] for row in rows}
        self.assertIn(self.near, ids)
        self.assertIn(self.mid, ids)
        self.assertNotIn(self.far, ids)

    def test_radius_5km_includes_a_b_c(self):
        rows = filter_properties(
            self.org,
            center_lat=ITALIA[0],
            center_lng=ITALIA[1],
            radius_m=5000,
            exclude_property_id=self.target,
        )
        ids = {row["id"] for row in rows}
        self.assertTrue({self.near, self.mid, self.far} <= ids)

    def test_normal_search_still_includes_ungeocoded(self):
        rows = filter_properties(self.org, neighborhood="Martínez")
        ids = {row["id"] for row in rows}
        self.assertIn(self.plain, ids)
        self.assertIn(self.target, ids)

    def test_another_org_never_appears(self):
        rows = filter_properties(
            self.org,
            center_lat=ITALIA[0],
            center_lng=ITALIA[1],
            radius_m=5000,
        )
        self.assertNotIn(self.foreign, {row["id"] for row in rows})

    def test_agent_scope_respected(self):
        rows = filter_properties(
            self.org,
            agent_id=self.agent_id,
            center_lat=ITALIA[0],
            center_lng=ITALIA[1],
            radius_m=2000,
            exclude_property_id=self.target,
        )
        ids = {row["id"] for row in rows}
        self.assertIn(self.near, ids)
        self.assertNotIn(self.other_agent_prop, ids)

    def test_nearby_http_and_mobile_200(self):
        client = self._login(self.admin, ROLE_ADMIN, self.org)
        response = client.get(
            f"/properties?nearby_id={self.target}&radius_km=1",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Cerca 500", body)
        self.assertNotIn("Media 1500", body)
        self.assertNotIn("Otra org", body)

    def test_need_radius_stored_and_matching(self):
        prefs = normalize_preferences(
            {
                "rooms": 3,
                "center_latitude": ITALIA[0],
                "center_longitude": ITALIA[1],
                "radius_km": 2,
                "location_reference_text": "Italia 1341",
            }
        )
        self.assertEqual(prefs["radius_km"], 2.0)
        contact = create_agent_contact(
            self.org,
            self.agent_id,
            {"name": "Geo Need", "preferences": prefs},
        )
        ranked = rank_contact_properties(
            self.org,
            contact,
            agent_id=self.agent_id,
        )
        ids = {item.get("property_id") or item.get("internal_property_id") for item in ranked}
        self.assertIn(self.near, ids)
        self.assertIn(self.mid, ids)
        self.assertNotIn(self.far, ids)
        self.assertNotIn(self.plain, ids)
        near_hit = next(
            item for item in ranked
            if (item.get("property_id") or item.get("internal_property_id")) == self.near
        )
        self.assertEqual(near_hit["dimensions"]["zone"]["state"], MATCH)
        self.assertIsNotNone(near_hit["dimensions"]["zone"].get("distance_label"))

    def test_need_outside_radius_hard_fail(self):
        listing = {
            "latitude": ITALIA[0] + OFFSET_4000,
            "longitude": ITALIA[1],
            "rooms": 3,
            "price": 200000,
            "currency": "USD",
        }
        criteria = {
            "center_latitude": ITALIA[0],
            "center_longitude": ITALIA[1],
            "radius_km": 2,
        }
        self.assertFalse(passes_hard_filters(criteria, listing))

    def test_acm_distance_and_geo_score(self):
        close = {
            "neighborhood": "Olivos",
            "jurisdiction": "PBA",
            "property_type": "apartment",
            "latitude": ITALIA[0] + OFFSET_500,
            "longitude": ITALIA[1],
            "covered_m2": 85,
            "rooms": 3,
            "bedrooms": 2,
            "listing_currency": "USD",
        }
        far = dict(close)
        far["latitude"] = ITALIA[0] + OFFSET_4000
        subject = {
            "neighborhood": "Martínez",
            "jurisdiction": "PBA",
            "property_type": "apartment",
            "latitude": ITALIA[0],
            "longitude": ITALIA[1],
            "covered_m2": 85,
            "rooms": 3,
            "bedrooms": 2,
            "listing_currency": "USD",
        }
        close_score = score_comparable(subject, close)
        far_score = score_comparable(subject, far)
        self.assertGreater(close_score, far_score)
        self.assertIsNotNone(close.get("distance_meters"))
        self.assertLess(close["distance_meters"], 600)

    def test_acm_fallback_textual_without_coords(self):
        subject = {
            "neighborhood": "Martínez",
            "jurisdiction": "PBA",
            "property_type": "apartment",
            "listing_currency": "USD",
        }
        same = {"neighborhood": "Martínez", "jurisdiction": "PBA", "property_type": "apartment", "listing_currency": "USD"}
        other = {"neighborhood": "Palermo", "jurisdiction": "CABA", "property_type": "apartment", "listing_currency": "USD"}
        self.assertGreater(score_comparable(subject, same), score_comparable(subject, other))

    def test_finalized_acm_distance_snapshot_immutable(self):
        view = create_acm_for_property(
            self.org,
            user=self.agent_record,
            property_id=self.target,
        )
        acm_id = view["acm"]["id"]
        before = {
            row["comparable_property_id"]: row.get("distance_meters")
            for row in list_comparables(acm_id, self.org)
            if row.get("comparable_property_id") == self.snapshot_comp
        }
        self.assertTrue(before)
        if view.get("can_finalize"):
            finalize_acm(acm_id, self.org, user=self.agent_record)
        update_property(
            self.snapshot_comp,
            "Snapshot moved",
            "PBA",
            self.org,
            latitude=-34.40,
            longitude=-58.40,
        )
        stored = {
            row["comparable_property_id"]: row.get("distance_meters")
            for row in list_comparables(acm_id, self.org)
        }
        self.assertEqual(stored.get(self.snapshot_comp), before.get(self.snapshot_comp))
        snapshot = list_comparables(acm_id, self.org)
        near_row = next(
            row for row in snapshot if row.get("comparable_property_id") == self.snapshot_comp
        )
        self.assertIsNotNone(near_row.get("snapshot_latitude") or near_row.get("distance_meters"))

    def test_jrh_parses_radius_and_uses_context(self):
        entities = extract_property_entities(
            "buscame propiedades a menos de 2 km de Italia 1341"
        )
        self.assertEqual(entities.get("radius_km"), 2.0)
        self.assertIn("italia 1341", entities.get("center_text") or "")
        result = ask_jrh(
            "buscame propiedades a menos de 2 km de Italia 1341",
            organization_id=self.org,
            user=self.admin_record,
        )
        self.assertEqual(result["intent"], QUERY_PROPERTIES)
        titles = " ".join(card.get("title") or "" for card in result.get("cards") or [])
        self.assertIn("Cerca 500", titles)
        self.assertIn("Media 1500", titles)
        self.assertNotIn("Lejos 4000", titles)

        session = {}
        first = ask_jrh(
            "mostrame la propiedad Italia 1341",
            organization_id=self.org,
            user=self.admin_record,
            session=session,
        )
        self.assertEqual(first["intent"], QUERY_PROPERTIES)
        second = ask_jrh(
            "qué tengo a menos de 1 km?",
            organization_id=self.org,
            user=self.admin_record,
            session=session,
        )
        self.assertEqual(second["intent"], QUERY_PROPERTIES)
        near_titles = " ".join(card.get("title") or "" for card in second.get("cards") or [])
        self.assertIn("Cerca 500", near_titles)
        self.assertNotIn("Media 1500", near_titles)

    def test_jrh_unlocated_property(self):
        session = {
            "jrh_ai_context": {
                "last_entity": {
                    "kind": "property",
                    "id": self.plain,
                    "label": "Sin coords",
                }
            }
        }
        result = ask_jrh(
            "qué tengo a menos de 1 km?",
            organization_id=self.org,
            user=self.admin_record,
            session=session,
        )
        self.assertEqual(result["message_key"], "jrh_ai_geo_unlocated")

    def test_migration_idempotent(self):
        migrate_property_acm_sqlite()
        migrate_property_acm_sqlite()

    def test_preferences_from_form_point_mode(self):
        class Form(dict):
            def getlist(self, name):
                return []

        form = Form(
            {
                "location_mode": "point",
                "center_latitude": str(ITALIA[0]),
                "center_longitude": str(ITALIA[1]),
                "radius_km": "2",
                "location_reference_text": "Italia 1341",
            }
        )
        prefs = preferences_from_form(form)
        self.assertEqual(prefs["radius_km"], 2.0)
        self.assertAlmostEqual(prefs["center_latitude"], ITALIA[0])


if __name__ == "__main__":
    unittest.main()
