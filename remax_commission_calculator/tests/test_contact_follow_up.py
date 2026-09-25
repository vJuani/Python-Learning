"""Daily follow-up list, cadence, and JRH natural-language updates."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_follow_up.db")

from modules.agent_tasks import complete_task, create_task
from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contact_follow_up import (
    apply_follow_up_plan,
    build_daily_follow_up_list,
    detect_follow_up_command,
    mark_contacted,
    resolve_follow_up_when,
)
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.contacts_repository import get_contact, update_contact
from modules.database.notifications_repository import list_notifications
from modules.follow_up_daily import digest_window_open, dispatch_daily_follow_ups
from modules.jrh_ai_classify import classify_intent
from modules.jrh_ai_intents import LOG_CONTACT_FOLLOW_UP
from modules.jrh_ai_provider import MockAIIntentProvider
from modules.jrh_ai_service import SESSION_DRAFT_KEY, ask_jrh, confirm_jrh_action
from modules.organization_time import UTC, now_utc, organization_timezone, parse_utc_iso, to_utc_iso
from web_app import app


class ContactFollowUpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="follow-up-test")
        create_tables()
        cls.org = add_organization("Follow Up Org")
        cls.password = "Password1"
        cls.agent = add_agent("Follow Agent", "Alto", cls.org)
        cls.user = add_user(
            "follow_agent",
            hash_password(cls.password),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.tz = organization_timezone(cls.org)

    def setUp(self):
        self.client = app.test_client()

    def _local(self, hour, minute, day=24):
        local = datetime(2026, 9, day, hour, minute, tzinfo=self.tz)
        return local.astimezone(UTC)

    def _contact(self, name, **fields):
        contact = create_agent_contact(
            self.org,
            self.agent,
            {"name": name, "phone": "1155550000", "status": "lead", "source": "manual"},
        )
        if fields:
            contact = update_contact(contact["id"], self.org, **fields)
        return contact

    def test_parse_examples(self):
        now = self._local(12, 0)
        juan = detect_follow_up_command(
            "Hablé con Juan, por ahora no vende. Llamarlo en dos meses."
        )
        self.assertEqual(juan["contact_name"], "Juan")
        self.assertEqual(juan["follow_up_priority"], "long_term")
        self.assertEqual(juan["follow_up_cadence"], "manual")
        juan = resolve_follow_up_when(juan, now=now, tz=self.tz)
        self.assertEqual(parse_utc_iso(juan["next_follow_up_at"]).astimezone(self.tz).date().isoformat(), "2026-11-24")

        lucia = detect_follow_up_command(
            "Lucía sigue buscando, hablale la semana que viene."
        )
        self.assertEqual(lucia["contact_name"], "Lucía")
        self.assertEqual(lucia["delay"], {"days": 7})
        lucia = resolve_follow_up_when(lucia, now=now, tz=self.tz)
        self.assertEqual(
            parse_utc_iso(lucia["next_follow_up_at"]).astimezone(self.tz).date().isoformat(),
            "2026-10-01",
        )

        martin = detect_follow_up_command("Martín ya compró.")
        self.assertTrue(martin["stop_automatic"])
        self.assertEqual(martin["commercial_stage"], "converted")
        self.assertEqual(
            classify_intent("Martín ya compró.")["intent"],
            LOG_CONTACT_FOLLOW_UP,
        )
        self.assertEqual(
            classify_intent("buscame a Juan")["intent"],
            "QUERY_CONTACT",
        )

    def test_natural_language_hides_contact_until_the_date(self):
        now = self._local(12, 0)
        contact = self._contact("Juan")
        plan = resolve_follow_up_when(
            detect_follow_up_command(
                "Hablé con Juan, por ahora no vende. Llamarlo en dos meses."
            ),
            now=now,
            tz=self.tz,
        )
        updated = apply_follow_up_plan(contact, plan, now=now, tz=self.tz)
        self.assertEqual(updated["follow_up_priority"], "long_term")
        self.assertEqual(updated["follow_up_cadence"], "manual")
        self.assertIn("Hablé con Juan", updated["notes"])
        self.assertEqual(
            build_daily_follow_up_list([updated], now=now),
            [],
        )
        later = parse_utc_iso(updated["next_follow_up_at"]) + timedelta(hours=1)
        rows = build_daily_follow_up_list([updated], now=later)
        self.assertEqual(rows[0]["reason"], "long_term_due")

    def test_converted_stops_automatic_follow_up(self):
        now = self._local(12, 0)
        contact = self._contact("Martín")
        user = {
            "id": self.user,
            "role": ROLE_AGENT,
            "agent_id": self.agent,
        }
        session = {}
        result = ask_jrh(
            "Martín ya compró.",
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session=session,
            provider=MockAIIntentProvider(),
            now=now,
        )
        self.assertEqual(result["intent"], LOG_CONTACT_FOLLOW_UP)
        self.assertTrue(result["confirm_required"])
        self.assertFalse(result["wrote"])
        self.assertNotEqual(
            get_contact(contact["id"], self.org)["commercial_stage"],
            "converted",
        )
        confirmed = confirm_jrh_action(
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            session=session,
            language="es",
        )
        self.assertTrue(confirmed["wrote"])
        self.assertNotIn(SESSION_DRAFT_KEY, session)
        stored = get_contact(contact["id"], self.org)
        self.assertEqual(stored["commercial_stage"], "converted")
        self.assertEqual(stored["follow_up_cadence"], "none")
        self.assertFalse(stored.get("next_follow_up_at"))
        self.assertEqual(build_daily_follow_up_list([stored], now=now), [])

    def test_daily_list_priority_and_one_push(self):
        morning = self._local(9, 10)
        early = self._local(7, 0)
        self._contact(
            "Vencido",
            commercial_stage="contacted",
            follow_up_cadence="medium",
            follow_up_interval_days=7,
            follow_up_priority="follow_up",
            next_follow_up_at=to_utc_iso(morning - timedelta(days=8)),
        )
        self._contact(
            "Alta",
            commercial_stage="contacted",
            follow_up_cadence="high",
            follow_up_interval_days=3,
            follow_up_priority="potential",
            next_follow_up_at=to_utc_iso(morning - timedelta(hours=2)),
        )
        self._contact(
            "Media",
            commercial_stage="contacted",
            follow_up_cadence="medium",
            follow_up_interval_days=7,
            follow_up_priority="follow_up",
            next_follow_up_at=to_utc_iso(morning - timedelta(hours=2)),
        )
        self._contact("Nuevo")
        self._contact(
            "Dormido",
            commercial_stage="contacted",
            follow_up_cadence="manual",
            follow_up_priority="long_term",
            next_follow_up_at=to_utc_iso(morning + timedelta(days=10)),
        )
        self.assertFalse(digest_window_open(early, self.tz, "09:00"))
        self.assertTrue(digest_window_open(morning, self.tz, "09:00"))
        quiet = dispatch_daily_follow_ups(self.org, now=early)
        self.assertEqual(quiet["created"], 0)
        self.assertEqual(list_notifications(self.user, self.org), [])

        sent = dispatch_daily_follow_ups(self.org, now=morning)
        self.assertEqual(sent["created"], 1)
        self.assertEqual(sent["individuals"], 2)
        notes = list_notifications(self.user, self.org)
        kinds = [item["kind"] for item in notes]
        self.assertEqual(kinds.count("crm_daily_follow_up"), 1)
        self.assertEqual(kinds.count("crm_follow_up_due"), 2)
        digest = next(item for item in notes if item["kind"] == "crm_daily_follow_up")
        self.assertIn("4", digest["payload"]["title"])
        self.assertEqual(digest["payload"]["url"], "/contacts/follow-ups")

        again = dispatch_daily_follow_ups(self.org, now=morning + timedelta(minutes=10))
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["individuals"], 0)
        self.assertEqual(len(list_notifications(self.user, self.org)), 3)

    def test_visit_without_follow_up_and_mark_contacted(self):
        now = now_utc()
        contact = self._contact(
            "Visita Hecha",
            commercial_stage="contacted",
            follow_up_cadence="medium",
            follow_up_interval_days=7,
            follow_up_priority="follow_up",
        )
        local = now.astimezone(self.tz)
        task = create_task(
            self.org,
            self.agent,
            {
                "title": "Visita",
                "task_type": "visit",
                "due_date": local.date().isoformat(),
                "due_time": local.strftime("%H:%M"),
                "contact_id": contact["id"],
                "contact_name": contact["name"],
            },
            created_by_user_id=self.user,
        )
        complete_task(
            self.org,
            task["id"],
            agent_id=self.agent,
            actor_user_id=self.user,
        )
        rows = build_daily_follow_up_list(
            [get_contact(contact["id"], self.org)],
            now=now_utc(),
            visits=[
                {
                    "contact_id": contact["id"],
                    "task_type": "visit",
                    "status": "completed",
                    "completed_at": to_utc_iso(now),
                    "due_at": to_utc_iso(now - timedelta(hours=1)),
                }
            ],
        )
        self.assertEqual(rows[0]["reason"], "visit_outcome_missing")
        marked = mark_contacted(get_contact(contact["id"], self.org), now=now_utc())
        self.assertEqual(marked["commercial_stage"], "contacted")
        self.assertTrue(marked["next_follow_up_at"])
        self.assertEqual(
            build_daily_follow_up_list([marked], now=now_utc()),
            [],
        )

    def test_follow_up_page_actions(self):
        contact = self._contact("Página")
        self.client.get("/logout", follow_redirects=True)
        self.client.post(
            "/login",
            data={"username": "follow_agent", "password": self.password},
            follow_redirects=True,
        )
        page = self.client.get("/contacts/follow-ups")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Página", body)
        self.assertIn("WhatsApp", body)
        self.assertIn("Marcar contactado", body)
        self.assertIn("Posponer", body)
        self.assertIn("Agendar", body)
        self.assertIn("Agregar nota", body)
        noted = self.client.post(
            f"/contacts/{contact['id']}/follow-up/note",
            data={"note": "Llamar después de las 18"},
            follow_redirects=True,
        )
        self.assertEqual(noted.status_code, 200)
        stored = get_contact(contact["id"], self.org)
        self.assertIn("Llamar después de las 18", stored["notes"])
        postponed = self.client.post(
            f"/contacts/{contact['id']}/follow-up/postpone",
            data={"days": "7"},
            follow_redirects=True,
        )
        self.assertEqual(postponed.status_code, 200)
        self.assertNotIn("Página", postponed.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
