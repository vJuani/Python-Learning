"""Maps V1: property location, stale geocode, agenda directions. No live Google."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_property_maps.db")
os.environ.pop("GOOGLE_MAPS_BROWSER_KEY", None)
os.environ.pop("GOOGLE_MAPS_SERVER_KEY", None)
os.environ["MAPS_PROVIDER"] = "none"

from modules.agent_tasks import create_task, decorate_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
    get_property_record,
    update_property,
)
from modules.database.connection import get_connection
from modules.database.property_maps_migration import migrate_property_maps_sqlite
from modules.maps.config import GEOCODE_MANUAL, GEOCODE_RESOLVED, GEOCODE_STALE
from modules.maps.links import build_directions_url, build_open_maps_url
from modules.maps.location import (
    apply_place_to_location,
    location_from_form,
    parse_address_components,
)
from modules.maps.provider import DisabledMapsProvider, get_maps_provider
from modules.organization_time import now_utc, organization_timezone
from web_app import app


MOCK_PLACE = {
    "place_id": "ChIJmockSantaFe410",
    "formatted_address": (
        "Avenida Santa Fe 410, Martínez, Provincia de Buenos Aires, Argentina"
    ),
    "latitude": -34.4941,
    "longitude": -58.4972,
    "address_components": [
        {"long_name": "Avenida Santa Fe", "short_name": "Av. Santa Fe", "types": ["route"]},
        {"long_name": "410", "short_name": "410", "types": ["street_number"]},
        {"long_name": "Martínez", "short_name": "Martínez", "types": ["locality"]},
        {
            "long_name": "Buenos Aires",
            "short_name": "BA",
            "types": ["administrative_area_level_1"],
        },
        {"long_name": "Argentina", "short_name": "AR", "types": ["country"]},
        {"long_name": "1640", "short_name": "1640", "types": ["postal_code"]},
        {"long_name": "Martínez", "short_name": "Martínez", "types": ["neighborhood"]},
    ],
}


class PropertyMapsUnitTests(unittest.TestCase):
    def test_parse_components_from_mock_place(self):
        parsed = parse_address_components(MOCK_PLACE["address_components"])
        self.assertEqual(parsed["locality"], "Martínez")
        self.assertEqual(parsed["neighborhood"], "Martínez")
        self.assertEqual(parsed["administrative_area"], "Buenos Aires")
        self.assertEqual(parsed["postal_code"], "1640")
        self.assertIn("Santa Fe", parsed["street_address"])

    def test_apply_place_requires_real_coords(self):
        resolved = apply_place_to_location(MOCK_PLACE)
        self.assertEqual(resolved["geocode_status"], GEOCODE_RESOLVED)
        self.assertEqual(resolved["google_place_id"], MOCK_PLACE["place_id"])
        self.assertAlmostEqual(resolved["latitude"], -34.4941)
        self.assertAlmostEqual(resolved["longitude"], -58.4972)
        empty = apply_place_to_location({"formatted_address": "Italia 1341"})
        self.assertIsNone(empty["latitude"])
        self.assertEqual(empty["geocode_status"], GEOCODE_MANUAL)

    def test_manual_edit_clears_stale_coords(self):
        existing = {
            "address": "Av. Santa Fe 410",
            "google_place_id": "ChIJold",
            "latitude": -34.49,
            "longitude": -58.49,
            "geocode_status": GEOCODE_RESOLVED,
        }
        payload = location_from_form(
            {"address": "Italia 1341"},
            existing,
        )
        self.assertIsNone(payload["latitude"])
        self.assertIsNone(payload["google_place_id"])
        self.assertEqual(payload["geocode_status"], GEOCODE_STALE)

    def test_posted_stale_coords_are_ignored_when_address_changes(self):
        existing = {
            "address": "Av. Santa Fe 410",
            "google_place_id": "ChIJold",
            "latitude": -34.49,
            "longitude": -58.49,
        }
        payload = location_from_form(
            {
                "address": "Italia 1341",
                "google_place_id": "ChIJold",
                "latitude": "-34.49",
                "longitude": "-58.49",
            },
            existing,
        )
        self.assertIsNone(payload["latitude"])
        self.assertEqual(payload["geocode_status"], GEOCODE_STALE)

    def test_maps_links(self):
        row = apply_place_to_location(MOCK_PLACE)
        open_url = build_open_maps_url(row)
        directions = build_directions_url(row)
        self.assertIn("query_place_id=ChIJmockSantaFe410", open_url)
        self.assertIn("destination_place_id=ChIJmockSantaFe410", directions)
        self.assertTrue(directions.startswith("https://www.google.com/maps/dir/"))

    def test_provider_is_disabled_without_keys(self):
        provider = get_maps_provider()
        self.assertIsInstance(provider, DisabledMapsProvider)
        self.assertFalse(provider.is_configured())
        self.assertEqual(provider.browser_key(), "")


class PropertyMapsHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="maps-test")
        create_tables()
        create_tables()
        cls.org = add_organization("Maps Org")
        cls.other_org = add_organization("Maps Other")
        cls.password = "Password1"
        password_hash = hash_password(cls.password)
        cls.agent = add_agent("Maps Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Maps Other Agent", "Alto", cls.org)
        cls.foreign_agent = add_agent("Foreign Agent", "Alto", cls.other_org)
        cls.admin = add_user("maps_admin", password_hash, ROLE_ADMIN, cls.org)
        cls.agent_user = add_user(
            "maps_agent",
            password_hash,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.other_agent_user = add_user(
            "maps_other_agent",
            password_hash,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.foreign_admin = add_user(
            "maps_foreign_admin",
            password_hash,
            ROLE_ADMIN,
            cls.other_org,
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

    def _form(self, **extra):
        data = {
            "address": "Italia 1341",
            "jurisdiction": "CABA",
            "property_type": "apartment",
            "listing_price": "180000",
            "listing_purpose": "sale",
            "agent_id": str(self.agent),
            "listing_currency": "USD",
            "commercial_status": "available",
        }
        data.update(extra)
        return data

    def _place_form(self, **extra):
        data = self._form(
            address="Av. Santa Fe 410",
            neighborhood="Martínez",
            google_place_id=MOCK_PLACE["place_id"],
            formatted_address=MOCK_PLACE["formatted_address"],
            locality="Martínez",
            administrative_area="Buenos Aires",
            country="Argentina",
            postal_code="1640",
            latitude=str(MOCK_PLACE["latitude"]),
            longitude=str(MOCK_PLACE["longitude"]),
        )
        data.update(extra)
        return data

    def test_01_manual_address_works_without_google(self):
        self._login("maps_admin")
        created = self.client.post(
            "/properties/new",
            data=self._form(),
            follow_redirects=True,
        )
        self.assertEqual(created.status_code, 200)
        row = get_property_record(1, self.org) or get_property_record(
            self._latest_property_id(self.org),
            self.org,
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["address"], "Italia 1341")
        self.assertIsNone(row["latitude"])
        self.assertIsNone(row["google_place_id"])
        self.assertEqual(row["geocode_status"], GEOCODE_MANUAL)

    def _latest_property_id(self, organization_id):
        connection = get_connection()
        try:
            row = connection.execute(
                """
                SELECT id FROM properties
                WHERE organization_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (organization_id,),
            ).fetchone()
        finally:
            connection.close()
        return row[0] if row else None

    def test_02_selected_place_persists_location(self):
        self._login("maps_admin")
        self.client.post("/properties/new", data=self._place_form(), follow_redirects=True)
        property_id = self._latest_property_id(self.org)
        row = get_property_record(property_id, self.org)
        self.assertEqual(
            row["formatted_address"],
            MOCK_PLACE["formatted_address"],
        )
        self.assertEqual(row["google_place_id"], MOCK_PLACE["place_id"])
        self.assertAlmostEqual(row["latitude"], MOCK_PLACE["latitude"])
        self.assertAlmostEqual(row["longitude"], MOCK_PLACE["longitude"])
        self.assertEqual(row["locality"], "Martínez")
        self.assertEqual(row["neighborhood"], "Martínez")
        self.assertEqual(row["geocode_status"], GEOCODE_RESOLVED)

    def test_03_address_edit_invalidates_stale_geocode(self):
        property_id = add_property(
            "Av. Santa Fe 410",
            "CABA",
            self.org,
            agent_id=self.agent,
            google_place_id="ChIJold",
            latitude=-34.49,
            longitude=-58.49,
            geocode_status=GEOCODE_RESOLVED,
        )
        update_property(
            property_id,
            "Italia 1341",
            "CABA",
            self.org,
            agent_id=self.agent,
        )
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["address"], "Italia 1341")
        self.assertIsNone(row["latitude"])
        self.assertIsNone(row["google_place_id"])
        self.assertEqual(row["geocode_status"], GEOCODE_STALE)

    def test_04_detail_with_coords_shows_map_when_configured(self):
        property_id = add_property(
            "Av. Santa Fe 410",
            "CABA",
            self.org,
            agent_id=self.agent,
            formatted_address=MOCK_PLACE["formatted_address"],
            google_place_id=MOCK_PLACE["place_id"],
            latitude=MOCK_PLACE["latitude"],
            longitude=MOCK_PLACE["longitude"],
            locality="Martínez",
            geocode_status=GEOCODE_RESOLVED,
        )
        self._login("maps_admin")
        with patch(
            "modules.maps.config.maps_is_configured",
            return_value=True,
        ), patch(
            "modules.maps.config.maps_browser_key",
            return_value="browser-test-key",
        ):
            page = self.client.get(f"/properties/{property_id}")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("property-location__map", body)
        self.assertIn("Abrir en Google Maps", body)
        self.assertIn("Cómo llegar", body)
        self.assertIn("query_place_id=ChIJmockSantaFe410", body)
        self.assertIn("destination_place_id=ChIJmockSantaFe410", body)

    def test_05_detail_without_coords_shows_fallback(self):
        property_id = add_property(
            "Italia 1341",
            "CABA",
            self.org,
            agent_id=self.agent,
        )
        self._login("maps_admin")
        page = self.client.get(f"/properties/{property_id}")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("property-location__map", body)
        self.assertIn("Google Maps no está configurado", body)

    def test_06_unconfigured_maps_does_not_break_form(self):
        self._login("maps_admin")
        page = self.client.get("/properties/new")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="address"', body)
        self.assertNotIn("maps.googleapis.com/maps/api/js?key=AIza", body)

    def test_07_other_org_rejected(self):
        property_id = add_property(
            "Italia 1341",
            "CABA",
            self.org,
            agent_id=self.agent,
        )
        self._login("maps_foreign_admin")
        page = self.client.get(f"/properties/{property_id}")
        self.assertEqual(page.status_code, 404)

    def test_08_agent_scope_respected(self):
        property_id = add_property(
            "Italia 1341",
            "CABA",
            self.org,
            agent_id=self.agent,
        )
        self._login("maps_other_agent")
        page = self.client.get(f"/properties/{property_id}")
        self.assertEqual(page.status_code, 302)
        self.assertIn(page.headers.get("Location", ""), ("/", "/dashboard"))

    def test_09_agenda_visit_with_property_shows_directions(self):
        property_id = add_property(
            "Av. Santa Fe 410",
            "CABA",
            self.org,
            agent_id=self.agent,
            google_place_id=MOCK_PLACE["place_id"],
            latitude=MOCK_PLACE["latitude"],
            longitude=MOCK_PLACE["longitude"],
            formatted_address=MOCK_PLACE["formatted_address"],
        )
        tz = organization_timezone(self.org)
        due = (now_utc() + timedelta(hours=2)).astimezone(tz)
        task = create_task(
            self.org,
            self.agent,
            {
                "title": "Visita Santa Fe",
                "task_type": "visit",
                "priority": "normal",
                "due_date": due.date().isoformat(),
                "due_time": due.strftime("%H:%M"),
                "property_id": property_id,
            },
            created_by_user_id=self.agent_user,
        )
        decorated = decorate_task(task, tz=tz, now=now_utc())
        self.assertTrue(decorated["directions_url"])
        self.assertIn("destination_place_id=", decorated["directions_url"])
        self._login("maps_agent")
        agenda = self.client.get("/agenda")
        self.assertIn("Cómo llegar", agenda.get_data(as_text=True))
        home = self.client.get("/")
        self.assertIn("Cómo llegar", home.get_data(as_text=True))

    def test_10_agenda_without_property_hides_directions(self):
        tz = organization_timezone(self.org)
        due = (now_utc() + timedelta(hours=3)).astimezone(tz)
        task = create_task(
            self.org,
            self.agent,
            {
                "title": "Llamada con Ro",
                "task_type": "call",
                "priority": "normal",
                "due_date": due.date().isoformat(),
                "due_time": due.strftime("%H:%M"),
            },
            created_by_user_id=self.agent_user,
        )
        decorated = decorate_task(task, tz=tz, now=now_utc())
        self.assertIsNone(decorated["directions_url"])

    def test_11_mobile_render_200(self):
        property_id = add_property(
            "Italia 1341",
            "CABA",
            self.org,
            agent_id=self.agent,
        )
        self._login("maps_agent")
        detail = self.client.get(
            f"/properties/{property_id}",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"},
        )
        form = self.client.get("/properties/new")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(form.status_code, 200)
        self.assertIn("Ubicación", detail.get_data(as_text=True))

    def test_12_migration_is_idempotent(self):
        migrate_property_maps_sqlite()
        migrate_property_maps_sqlite()
        connection = get_connection()
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(properties)").fetchall()
            }
        finally:
            connection.close()
        self.assertIn("latitude", columns)
        self.assertIn("google_place_id", columns)
        self.assertIn("geocode_status", columns)


if __name__ == "__main__":
    unittest.main()
