"""FASE 5E — Daily visit routes. Haversine order, no GPS, no Directions API."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_route_planning.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GOOGLE_MAPS_BROWSER_KEY", None)
os.environ.pop("GOOGLE_MAPS_SERVER_KEY", None)
os.environ["MAPS_PROVIDER"] = "none"

from modules.agent_tasks import create_task
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.properties_repository import STATUS_APPROVED
from modules.jrh_ai_classify import classify_intent
from modules.jrh_ai_intents import BUILD_DAILY_ROUTE, QUERY_NEXT_VISIT
from modules.jrh_ai_service import ask_jrh
from modules.maps.config import GEOCODE_RESOLVED
from modules.maps.links import build_route_directions_url
from modules.maps.provider import MapProvider
from modules.route_planning import (
    build_daily_route,
    get_next_visit,
    is_visit_task,
    list_visits_for_day,
    nearest_neighbor_order,
    require_route_agent,
)
from web_app import app

ITALIA = (-34.4940, -58.5060)
SAN_ISIDRO = (-34.4700, -58.5080)
NUNEZ = (-34.5480, -58.4620)
WEST = (-34.5000, -58.5300)
EAST = (-34.5000, -58.4800)
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"


class RoutePlanningTests(unittest.TestCase):
    _day_offset = 0

    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="route-tests")
        create_tables()
        cls.org = add_organization("Route Org")
        cls.other_org = add_organization("Route Other")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Route Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Route Other Agent", "Alto", cls.org)
        cls.foreign_agent = add_agent("Route Foreign", "Alto", cls.other_org)
        cls.admin = add_user("route_admin", pwd, ROLE_ADMIN, cls.org)
        cls.agent_user = add_user(
            "route_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
        )
        cls.other_user = add_user(
            "route_other",
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
        cls.italia = cls._prop("Italia 1341", *ITALIA, neighborhood="Martínez")
        cls.libertador = cls._prop(
            "Libertador 15000",
            *SAN_ISIDRO,
            neighborhood="San Isidro",
        )
        cls.cabildo = cls._prop("Cabildo 3200", *NUNEZ, neighborhood="Núñez")
        cls.plain = cls._prop("Sin coords", None, None)
        cls.olivos = cls._prop("Maipu 100", -34.5100, -58.4900, neighborhood="Olivos")

    def setUp(self):
        RoutePlanningTests._day_offset += 1
        base = datetime(2026, 9, 9, tzinfo=timezone.utc).date()
        self.day = base + timedelta(days=self._day_offset)
        self.day_iso = self.day.isoformat()
        self.now = datetime(
            self.day.year,
            self.day.month,
            self.day.day,
            12,
            0,
            tzinfo=timezone.utc,
        )

    @classmethod
    def _prop(cls, address, lat, lng, *, neighborhood="Martínez"):
        kwargs = {
            "address": address,
            "jurisdiction": "PBA",
            "organization_id": cls.org,
            "agent_id": cls.agent_id,
            "status": STATUS_APPROVED,
            "property_type": "apartment",
            "neighborhood": neighborhood,
        }
        if lat is not None:
            kwargs["latitude"] = lat
            kwargs["longitude"] = lng
            kwargs["geocode_status"] = GEOCODE_RESOLVED
        return add_property(**kwargs)

    def _visit(self, property_id, time_text, *, date=None, agent_id=None, title=None, **extra):
        payload = {
            "title": title or "Visita",
            "task_type": "visit",
            "priority": "normal",
            "due_date": date or self.day_iso,
            "due_time": time_text,
            "property_id": property_id,
            "duration_minutes": 60,
        }
        payload.update(extra)
        return create_task(
            self.org,
            agent_id if agent_id is not None else self.agent_id,
            payload,
            created_by_user_id=self.agent_user,
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

    def test_next_visit_is_the_upcoming_one(self):
        first = self._visit(self.italia, "14:00")
        self._visit(self.libertador, "16:00")
        nxt = get_next_visit(
            self.org,
            self.agent_id,
            now=self.now,
        )
        self.assertEqual(nxt["id"], first["id"])
        self.assertIn("Italia", nxt.get("place_label") or "")

    def test_other_agent_visits_excluded(self):
        mine = self._visit(self.italia, "14:00")
        self._visit(self.libertador, "16:00", agent_id=self.other_agent)
        visits = list_visits_for_day(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        ids = {item["id"] for item in visits}
        self.assertIn(mine["id"], ids)
        self.assertEqual(len(ids), 1)

    def test_other_org_excluded(self):
        foreign_prop = add_property(
            "Otra org",
            "CABA",
            self.other_org,
            agent_id=self.foreign_agent,
            status=STATUS_APPROVED,
            latitude=ITALIA[0],
            longitude=ITALIA[1],
            geocode_status=GEOCODE_RESOLVED,
        )
        create_task(
            self.other_org,
            self.foreign_agent,
            {
                "title": "Visita extranjera",
                "task_type": "visit",
                "priority": "normal",
                "due_date": self.day_iso,
                "due_time": "14:00",
                "property_id": foreign_prop,
            },
        )
        visits = list_visits_for_day(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        self.assertFalse(visits)

    def test_fixed_times_keep_chronological_order(self):
        late = self._visit(self.cabildo, "18:00")
        early = self._visit(self.italia, "14:00")
        mid = self._visit(self.libertador, "16:00")
        view = build_daily_route(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        ids = [stop["id"] for stop in view["stops"]]
        self.assertEqual(ids, [early["id"], mid["id"], late["id"]])
        self.assertTrue(view["schedule_locked"])
        self.assertFalse(view["travel_duration_estimated"])
        self.assertIsNone(view["stops"][1].get("travel_duration_label"))

    def test_flexible_order_is_deterministic_nearest_neighbor(self):
        west = {"id": 1, "latitude": WEST[0], "longitude": WEST[1]}
        east = {"id": 2, "latitude": EAST[0], "longitude": EAST[1]}
        mid = {"id": 3, "latitude": ITALIA[0], "longitude": ITALIA[1]}
        ordered = nearest_neighbor_order([east, west, mid])
        self.assertEqual([item["id"] for item in ordered], [1, 3, 2])
        again = nearest_neighbor_order([mid, east, west])
        self.assertEqual([item["id"] for item in again], [1, 3, 2])

    def test_missing_coords_reported(self):
        self._visit(self.italia, "14:00")
        self._visit(self.plain, "16:00")
        view = build_daily_route(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        self.assertEqual(len(view["stops"]), 1)
        self.assertEqual(len(view["unlocated"]), 1)
        self.assertIn("Sin coords", view["unlocated"][0].get("place_label") or "")

    def test_conflicting_times_detected(self):
        self._visit(self.italia, "16:00", title="Martínez")
        self._visit(self.cabildo, "16:00", title="Belgrano")
        view = build_daily_route(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        self.assertTrue(view["conflicts"])
        self.assertEqual(view["conflicts"][0]["kind"], "same_time")

    def test_google_overlay_is_not_a_visit(self):
        self.assertFalse(
            is_visit_task({"task_type": "visit", "source": "google", "id": 9})
        )
        self.assertFalse(is_visit_task({"task_type": "meeting", "source": "jrh"}))
        task = self._visit(self.italia, "14:00")
        view = build_daily_route(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        self.assertEqual(view["stop_count"], 1)
        self.assertEqual(view["stops"][0]["id"], task["id"])

    def test_maps_url_contains_stops(self):
        self._visit(self.italia, "14:00")
        self._visit(self.libertador, "16:00")
        self._visit(self.cabildo, "18:00")
        view = build_daily_route(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        parsed = urlparse(view["maps_url"])
        self.assertEqual(parsed.netloc, "www.google.com")
        self.assertNotIn("key=", view["maps_url"])
        query = parse_qs(parsed.query)
        self.assertIn(str(ITALIA[0]), query.get("origin", [""])[0])
        self.assertIn(str(NUNEZ[0]), query.get("destination", [""])[0])
        self.assertTrue(query.get("waypoints"))

    def test_manual_reorder_does_not_change_agenda_time(self):
        first = self._visit(self.italia, "14:00")
        second = self._visit(self.libertador, "16:00")
        before = {first["id"]: first["due_at"], second["id"]: second["due_at"]}
        view = build_daily_route(
            self.org,
            self.agent_id,
            local_date=self.day,
            order_ids=[second["id"], first["id"]],
            now=self.now,
        )
        self.assertEqual(
            [stop["id"] for stop in view["stops"]],
            [second["id"], first["id"]],
        )
        self.assertEqual(view["original_due_at"][first["id"]], before[first["id"]])
        self.assertEqual(view["original_due_at"][second["id"]], before[second["id"]])
        self.assertTrue(view["manual"])

    def test_jrh_tomorrow_uses_correct_date(self):
        pivot = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)
        tomorrow = datetime(2027, 3, 2).date()
        tomorrow_iso = tomorrow.isoformat()
        self._visit(self.italia, "14:00", date=tomorrow_iso)
        self._visit(self.libertador, "16:00", date=tomorrow_iso)
        parsed = classify_intent("armame el recorrido de mañana")
        self.assertEqual(parsed["intent"], BUILD_DAILY_ROUTE)
        result = ask_jrh(
            "armame el recorrido de mañana",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            now=pivot,
        )
        self.assertEqual(result["intent"], BUILD_DAILY_ROUTE)
        titles = " ".join(card.get("title") or "" for card in result.get("cards") or [])
        self.assertIn("Italia", titles)
        self.assertIn("Libertador", titles)
        href_args = (result.get("actions") or [{}])[0].get("href_args") or {}
        self.assertEqual(href_args.get("date"), tomorrow_iso)

    def test_jrh_next_visit(self):
        self._visit(self.italia, "14:00")
        parsed = classify_intent("cuál es mi próxima visita?")
        self.assertEqual(parsed["intent"], QUERY_NEXT_VISIT)
        result = ask_jrh(
            "cuál es mi próxima visita?",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            now=self.now,
        )
        self.assertEqual(result["intent"], QUERY_NEXT_VISIT)
        self.assertIn("Italia", result.get("message") or "")

    def test_staff_admin_blocked(self):
        with self.assertRaises(Exception):
            require_route_agent(self.admin_record)
        client = self._login(self.admin, ROLE_ADMIN, self.org)
        self.assertEqual(client.get("/agenda/route").status_code, 403)
        self.assertEqual(client.post("/agenda/route/reorder").status_code, 403)

    def test_no_traffic_duration_invented(self):
        self._visit(self.italia, "14:00")
        self._visit(self.libertador, "16:00")
        view = build_daily_route(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        self.assertFalse(view["travel_duration_estimated"])
        blob = str(view)
        self.assertNotIn("25 minutos", blob)
        self.assertNotIn("tardar", blob.lower())

    def test_mobile_route_renders_200(self):
        self._visit(self.italia, "14:00")
        client = self._login(self.agent_user, ROLE_AGENT, self.org, self.agent_id)
        response = client.get(
            f"/agenda/route?date={self.day_iso}",
            headers={"User-Agent": MOBILE_UA},
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Italia", html)
        self.assertIn("Ver mapa", html)

    def test_no_google_api_in_planner(self):
        root = Path(__file__).resolve().parents[1] / "modules"
        planner = (root / "route_planning.py").read_text(encoding="utf-8")
        self.assertNotIn("googleapis", planner)
        self.assertNotIn("Distance Matrix", planner)
        self.assertIsNone(MapProvider().driving_route([]))
        url = build_route_directions_url(
            [
                {"latitude": ITALIA[0], "longitude": ITALIA[1]},
                {"latitude": NUNEZ[0], "longitude": NUNEZ[1]},
            ]
        )
        self.assertNotIn("key=", url)

    def test_meetings_are_not_visits(self):
        create_task(
            self.org,
            self.agent_id,
            {
                "title": "Reunión",
                "task_type": "meeting",
                "priority": "normal",
                "due_date": self.day_iso,
                "due_time": "15:00",
                "property_id": self.italia,
            },
        )
        visits = list_visits_for_day(
            self.org,
            self.agent_id,
            local_date=self.day,
            now=self.now,
        )
        self.assertFalse(visits)

    def test_missing_new_visit_asks_to_schedule(self):
        self._visit(self.italia, "14:00")
        result = ask_jrh(
            "tengo una visita nueva a las 16 en Olivos, dónde la meto?",
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.agent_id,
            now=self.now,
        )
        self.assertEqual(result["intent"], BUILD_DAILY_ROUTE)
        self.assertIn("agendar", (result.get("message") or "").lower())

    def test_reorder_http_does_not_change_due_at(self):
        first = self._visit(self.italia, "14:00")
        second = self._visit(self.libertador, "16:00")
        client = self._login(self.agent_user, ROLE_AGENT, self.org, self.agent_id)
        response = client.post(
            "/agenda/route/reorder",
            data={"date": self.day_iso, "order": f"{second['id']},{first['id']}"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        from modules.database.agent_tasks_repository import get_agent_task

        self.assertEqual(
            get_agent_task(first["id"], self.org)["due_at"],
            first["due_at"],
        )


if __name__ == "__main__":
    unittest.main()
