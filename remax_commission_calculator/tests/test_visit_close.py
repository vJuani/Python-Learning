"""Structured visit close, property history, and JRH preview."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_UNSET = object()
_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_visit_close.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_tasks import AgentTaskError, complete_task, create_task, load_editable_task
from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contact_follow_up import build_daily_follow_up_list
from modules.contacts import create_agent_contact, decorate_contact
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.agent_tasks_repository import get_agent_task, list_agent_tasks
from modules.database.contacts_repository import (
    get_contact,
    list_property_interactions,
    update_contact,
)
from modules.jrh_ai_intents import LOG_VISIT_OUTCOME
from modules.jrh_ai_provider import MockAIIntentProvider
from modules.jrh_ai_service import ask_jrh, confirm_jrh_action
from modules.organization_time import UTC, organization_timezone, to_utc_iso
from modules.database.connection import get_connection
from modules.database.properties_repository import get_property_record
from modules.database.property_commercial_events_repository import (
    list_property_commercial_events,
)
from modules.visit_close import VisitCloseError, apply_visit_close, learn_need_from_text
from modules.visit_outcome import normalize_visit_outcome
from web_app import app


class VisitCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="visit-close-test")
        create_tables()
        cls.org = add_organization("Visit Close Org")
        cls.other_org = add_organization("Visit Close Other")
        cls.agent = add_agent("Visit Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Other Visit Agent", "Alto", cls.org)
        cls.user = add_user(
            "visit_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.tz = organization_timezone(cls.org)
        cls.viewer = {
            "id": cls.user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.agent,
        }
        cls.property_id = add_property(
            "Santamarina 1335",
            "CABA",
            cls.org,
            agent_id=cls.agent,
        )

    def _now(self):
        return datetime(2026, 9, 24, 15, 0, tzinfo=self.tz).astimezone(UTC)

    def _contact(self, name="Martín Pérez", *, agent=None, org=None):
        return create_agent_contact(
            org or self.org,
            agent or self.agent,
            {"name": name, "phone": "1155550000", "status": "lead", "source": "manual"},
        )

    def _visit(self, contact, *, title="Visita Santamarina", when=None):
        local = (when or self._now()).astimezone(self.tz)
        return create_task(
            self.org,
            self.agent,
            {
                "title": title,
                "task_type": "visit",
                "due_date": local.date().isoformat(),
                "due_time": local.strftime("%H:%M"),
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": self.property_id,
            },
            created_by_user_id=self.user,
        )

    def _close(self, contact, outcome, *, task=None):
        visit = task or self._visit(contact)
        if visit.get("status") != "completed":
            visit = complete_task(
                self.org,
                visit["id"],
                agent_id=self.agent,
                actor_user_id=self.user,
            )
        return apply_visit_close(
            visit,
            outcome,
            organization_id=self.org,
            agent_id=self.agent,
            actor_user_id=self.user,
            now=self._now(),
            tz=self.tz,
            language="es",
        )

    def _kinds(self, contact):
        history = decorate_contact(
            get_contact(contact["id"], self.org),
            organization_id=self.org,
            language="es",
        )["history"]
        return [
            event["kind"]
            for group in history
            for event in group["events"]
        ]

    def _relations(self, contact):
        return {
            row["interaction_type"]
            for row in list_property_interactions(self.org, contact["id"])
        }

    def test_legacy_outcome_keeps_interest_and_gains_result(self):
        outcome = normalize_visit_outcome(
            {"interest": "positive", "note": "Bien", "objection": "chica"}
        )
        self.assertEqual(outcome["interest"], "positive")
        self.assertEqual(outcome["result"], "liked")
        self.assertEqual(outcome["objections"], ["chica"])

    def test_results_and_timeline(self):
        expectations = {
            "liked": {"interested", "visited"},
            "interested": {"interested", "visited"},
            "disliked": {"discarded", "visited"},
            "second_visit": {"interested", "visited"},
            "other": {"visited"},
        }
        for result, relations in expectations.items():
            contact = self._contact(f"Result {result}")
            closed = self._close(contact, {"result": result, "note": "ok", "next_step": "none"})
            self.assertEqual(closed["outcome"]["result"], result)
            self.assertIn("visit_outcome", self._kinds(contact))
            self.assertEqual(self._relations(contact), relations)
            self.assertIsNone(closed["created_task"])

    def test_no_show_does_not_mark_lost(self):
        contact = self._contact("No Show")
        closed = self._close(
            contact,
            {"result": "no_show", "note": "No vino", "next_step": "call"},
        )
        stored = get_contact(contact["id"], self.org)
        self.assertNotEqual(stored["commercial_stage"], "lost")
        self.assertEqual(self._relations(contact), set())
        self.assertEqual(closed["created_task"]["task_type"], "call")

    def test_call_and_whatsapp_create_tasks_and_none_does_not(self):
        contact = self._contact("Pasos")
        call = self._close(
            contact,
            {"result": "liked", "next_step": "call", "note": "ok"},
        )
        self.assertEqual(call["created_task"]["task_type"], "call")
        whatsapp = self._close(
            contact,
            {"result": "liked", "next_step": "whatsapp", "note": "ok"},
        )
        self.assertEqual(whatsapp["created_task"]["task_type"], "follow_up")
        self.assertIn("WhatsApp", whatsapp["created_task"]["title"])
        quiet = self._close(
            contact,
            {"result": "liked", "next_step": "none", "note": "ok"},
        )
        self.assertIsNone(quiet["created_task"])

    def test_second_visit_does_not_duplicate(self):
        contact = self._contact("Segunda")
        self._visit(contact, title="Visita pendiente", when=self._now() + timedelta(days=3))
        before = list_agent_tasks(
            self.org, agent_id=self.agent, contact_id=contact["id"], task_type="visit"
        )
        closed = self._close(
            contact,
            {"result": "second_visit", "next_step": "second_visit", "note": "otra"},
        )
        after = list_agent_tasks(
            self.org, agent_id=self.agent, contact_id=contact["id"], task_type="visit"
        )
        self.assertTrue(closed["duplicate_visit"])
        self.assertEqual(len(after), len(before) + 1)

    def test_negotiate_and_interested_raise_stage(self):
        contact = self._contact("Negocia")
        update_contact(contact["id"], self.org, commercial_stage="visit_scheduled")
        self._close(contact, {"result": "negotiate", "next_step": "none", "note": "oferta"})
        self.assertEqual(get_contact(contact["id"], self.org)["commercial_stage"], "negotiating")
        followed = self._contact("Sigue")
        update_contact(followed["id"], self.org, commercial_stage="visit_scheduled")
        self._close(followed, {"result": "interested", "next_step": "none", "note": "quiere"})
        self.assertEqual(get_contact(followed["id"], self.org)["commercial_stage"], "following")

    def test_need_confirm_and_skip(self):
        learned = learn_need_from_text(
            "Le gustó pero quiere cochera. El presupuesto máximo es USD 220.000. No quiere más planta baja."
        )
        self.assertIn("cochera", learned["features"])
        self.assertEqual(learned["budget"]["max"], 220000)
        self.assertIn("planta baja", learned["observations"])
        contact = self._contact("Busca")
        closed = self._close(
            contact,
            {
                "result": "liked",
                "next_step": "none",
                "note": "Le gustó pero quiere cochera. El presupuesto máximo es USD 220.000.",
            },
        )
        self.assertTrue(closed["need_preview"]["has_changes"])
        self.assertFalse(get_contact(contact["id"], self.org).get("preferences_json"))
        apply_visit_close(
            closed["task"],
            closed["outcome"],
            organization_id=self.org,
            agent_id=self.agent,
            actor_user_id=self.user,
            now=self._now(),
            tz=self.tz,
            save_need=True,
        )
        stored = get_contact(contact["id"], self.org)
        self.assertIn("cochera", stored["preferences_json"])
        self.assertIn("220000", stored["preferences_json"])

    def test_other_org_and_other_agent_cannot_close(self):
        contact = self._contact("Ajeno", agent=self.other_agent)
        visit = create_task(
            self.org,
            self.other_agent,
            {
                "title": "Visita ajena",
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "11:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
            },
            created_by_user_id=self.user,
        )
        with self.assertRaises(AgentTaskError):
            load_editable_task(self.org, visit["id"], agent_id=self.agent)
        self.assertIsNone(get_agent_task(visit["id"], self.other_org))

    def test_daily_post_visit_reasons(self):
        now = datetime.now(UTC) + timedelta(minutes=1)
        missing = self._contact("Sin cierre")
        visit = complete_task(
            self.org,
            self._visit(missing)["id"],
            agent_id=self.agent,
            actor_user_id=self.user,
        )
        rows = build_daily_follow_up_list(
            [get_contact(missing["id"], self.org)],
            now=now,
            visits=[visit],
        )
        self.assertEqual(rows[0]["reason"], "visit_outcome_missing")
        interested = self._contact("Interes sin paso")
        closed = self._close(
            interested,
            {"result": "interested", "note": "quiere ver más"},
        )
        rows = build_daily_follow_up_list(
            [get_contact(interested["id"], self.org)],
            now=now,
            visits=[closed["task"]],
        )
        self.assertEqual(rows[0]["reason"], "visit_next_step_missing")
        later = self._contact("Mas adelante")
        scheduled = self._close(
            later,
            {
                "result": "liked",
                "next_step": "call",
                "next_step_at": to_utc_iso(now + timedelta(days=5)),
                "note": "viernes",
            },
        )
        self.assertEqual(
            build_daily_follow_up_list(
                [get_contact(later["id"], self.org)],
                now=now,
                visits=[scheduled["task"]],
            ),
            [],
        )

    def test_jrh_preview_confirm_and_cancel(self):
        contact = self._contact("Martín Pérez")
        self._visit(contact)
        session = {}
        preview = ask_jrh(
            "La visita con Martín Pérez salió bien, le gustó pero quiere cochera. Llamalo el viernes.",
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            language="es",
            session=session,
            provider=MockAIIntentProvider(),
            now=self._now(),
        )
        self.assertEqual(preview["intent"], LOG_VISIT_OUTCOME)
        self.assertTrue(preview["confirm_required"])
        self.assertFalse(preview["wrote"])
        blob = " ".join(card["title"] for card in preview["cards"])
        self.assertIn("Le gustó", blob)
        self.assertIn("Cochera", blob)
        self.assertIn("Llamar", blob)
        self.assertFalse(get_contact(contact["id"], self.org).get("preferences_json"))
        session.pop("jrh_ai_draft", None)
        cancelled = confirm_jrh_action(
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            session=session,
        )
        self.assertFalse(cancelled["wrote"])
        session = {}
        ask_jrh(
            "La visita con Martín Pérez salió bien, le gustó pero quiere cochera. Llamalo el viernes.",
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            language="es",
            session=session,
            provider=MockAIIntentProvider(),
            now=self._now(),
        )
        confirmed = confirm_jrh_action(
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            session=session,
            language="es",
        )
        self.assertTrue(confirmed["wrote"])
        stored = get_contact(contact["id"], self.org)
        self.assertIn("cochera", stored["preferences_json"] or "")
        self.assertIn("visit_outcome", self._kinds(contact))
        calls = [
            task
            for task in list_agent_tasks(self.org, agent_id=self.agent, contact_id=contact["id"])
            if task.get("task_type") == "call"
        ]
        self.assertEqual(len(calls), 1)

    def test_jrh_property_history_answers(self):
        liked = self._contact("Lucía Gómez")
        self._close(liked, {"result": "liked", "next_step": "none", "note": "ok"})
        discarded = self._contact("Pedro Díaz")
        self._close(discarded, {"result": "disliked", "next_step": "none", "note": "no"})
        liked_answer = ask_jrh(
            "Cuál le gustó a Lucía Gómez?",
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            language="es",
            session={},
            provider=MockAIIntentProvider(),
            now=self._now(),
        )
        self.assertEqual(liked_answer["intent"], "QUERY_CONTACT_PROPERTIES")
        self.assertIn("Santamarina 1335", liked_answer["message"])
        discarded_answer = ask_jrh(
            "Qué propiedades descartó Pedro Díaz?",
            organization_id=self.org,
            user=self.viewer,
            agent_id=self.agent,
            language="es",
            session={},
            provider=MockAIIntentProvider(),
            now=self._now(),
        )
        self.assertIn("Santamarina 1335", discarded_answer["message"])

    def test_close_page_renders_choices(self):
        contact = self._contact("Pantalla")
        visit = self._visit(contact)
        client = app.test_client()
        client.post(
            "/login",
            data={"username": "visit_agent", "password": "Password1"},
            follow_redirects=True,
        )
        page = client.get(f"/agenda/{visit['id']}/visit-close")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("¿Cómo salió la visita?", body)
        self.assertIn("Le gustó", body)
        self.assertIn("No se presentó", body)
        self.assertIn("WhatsApp", body)
        self.assertIn("Sin próximo paso", body)
        self.assertIn('name="result"', body)
        self.assertIn('name="note"', body)
        self.assertIn('name="objections"', body)
        self.assertIn('name="next_step"', body)
        self.assertIn('name="next_step_date"', body)
        self.assertIn('name="next_step_time"', body)
        self.assertIn('name="next_step_note"', body)

    def test_close_page_shows_the_new_outcome_sheet(self):
        contact = self._contact("Pantalla UI")
        visit = self._visit(contact)
        client = app.test_client()
        client.post(
            "/login",
            data={"username": "visit_agent", "password": "Password1"},
            follow_redirects=True,
        )
        body = client.get(f"/agenda/{visit['id']}/visit-close").get_data(as_text=True)
        self.assertIn("visit-close__check", body)
        self.assertIn('name="result" value="interested"', body)
        self.assertIn("Comentario de la visita", body)
        self.assertIn("Objeciones o dudas", body)
        self.assertIn("Guardar resultado", body)
        self.assertIn("JRH va a registrar:", body)
        self.assertIn("Visita realizada", body)
        self.assertIn('data-visit-summary', body)
        self.assertIn('data-visit-when', body)
        self.assertIn('data-visit-show-unless="none"', body)
        self.assertIn('value="none"', body)
        self.assertRegex(body, r'data-visit-when[^>]*hidden|hidden[^>]*data-visit-when')
        css = Path("static/css/agenda-page.css").read_text(encoding="utf-8")
        self.assertIn("width: min(760px, calc(100vw - 2rem))", css)
        self.assertIn("width: 95vw", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", css)
        self.assertIn("Reservó", body)
        self.assertIn("data-visit-reservation", body)
        self.assertIn('name="reservation_amount"', body)
        self.assertIn('name="agreed_property_price"', body)
        self.assertIn('value="prepare_operation"', body)
        self.assertIn("dialog.visit-close:not([open])", css)
        self.assertIn("display: none", css)

    def _operation_count(self):
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM operations WHERE organization_id = ?",
                (self.org,),
            )
            return cursor.fetchone()[0]
        finally:
            connection.close()

    def _priced_property(self, address="Calle Test 1234", *, agent=_UNSET, org=None, price=185000):
        return add_property(
            address,
            "CABA",
            org or self.org,
            agent_id=self.agent if agent is _UNSET else agent,
            listing_price=price,
            listing_currency="USD",
        )

    def test_agenda_hides_the_visit_sheet_after_save(self):
        contact = self._contact("Cierre visible")
        visit = self._visit(contact)
        client = app.test_client()
        client.post(
            "/login",
            data={"username": "visit_agent", "password": "Password1"},
            follow_redirects=True,
        )
        saved = client.post(
            f"/agenda/{visit['id']}/visit-close",
            data={"result": "liked", "next_step": "none", "note": "ok"},
            follow_redirects=True,
        )
        body = saved.get_data(as_text=True)
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.request.path.endswith("/agenda"))
        self.assertIn('id="visit-close-sheet"', body)
        self.assertNotRegex(body, r'<dialog id="visit-close-sheet"[^>]*\bopen\b')
        css = Path("static/css/agenda-page.css").read_text(encoding="utf-8")
        self.assertIn("dialog.visit-close:not([open])", css)
        self.assertRegex(
            css,
            r"dialog\.visit-close:not\(\[open\]\)\s*\{\s*display:\s*none;",
        )

    def test_reserved_updates_price_status_and_does_not_open_an_operation(self):
        property_id = self._priced_property()
        contact = self._contact("Reserva")
        visit = create_task(
            self.org,
            self.agent,
            {
                "title": "Visita Calle Test",
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "11:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": property_id,
            },
            created_by_user_id=self.user,
        )
        before_ops = self._operation_count()
        closed = self._close(
            contact,
            {
                "result": "reserved",
                "reservation_amount": "2.000",
                "reservation_currency": "USD",
                "agreed_property_price": "178.000",
                "agreed_property_currency": "USD",
                "note": "seña",
            },
            task=visit,
        )
        stored = json.loads(closed["task"]["outcome_json"])
        self.assertEqual(stored["result"], "reserved")
        self.assertEqual(stored["reservation_amount"], 2000)
        self.assertEqual(stored["reservation_currency"], "USD")
        self.assertEqual(stored["agreed_property_price"], 178000)
        self.assertEqual(stored["agreed_property_currency"], "USD")
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["commercial_status"], "reserved")
        self.assertEqual(int(row["listing_price"]), 178000)
        self.assertNotEqual(int(row["listing_price"]), 2000)
        notes = get_contact(contact["id"], self.org)["notes"]
        self.assertIn("Reservó Calle Test 1234", notes)
        self.assertIn("Reserva: USD 2.000", notes)
        self.assertIn("Valor acordado: USD 178.000", notes)
        event = list_property_commercial_events(self.org, property_id)[0]
        self.assertEqual(event["previous_commercial_status"], "available")
        self.assertEqual(event["commercial_status"], "reserved")
        self.assertEqual(int(event["previous_price"]), 185000)
        self.assertEqual(int(event["listing_price"]), 178000)
        self.assertEqual(event["actor_user_id"], self.user)
        self.assertEqual(event["agent_id"], self.agent)
        self.assertEqual(event["organization_id"], self.org)
        self.assertTrue(event["created_at"])
        self.assertEqual(closed["created_task"]["task_type"], "follow_up")
        self.assertIn("Preparar operación", closed["created_task"]["title"])
        self.assertEqual(self._operation_count(), before_ops)

    def test_reservation_amount_does_not_replace_the_price(self):
        property_id = self._priced_property("Calle Sin Acuerdo")
        contact = self._contact("Solo seña")
        visit = create_task(
            self.org,
            self.agent,
            {
                "title": "Visita sin acuerdo",
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "12:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": property_id,
            },
            created_by_user_id=self.user,
        )
        self._close(
            contact,
            {
                "result": "reserved",
                "reservation_amount": 2000,
                "reservation_currency": "USD",
                "next_step": "none",
                "note": "seña",
            },
            task=visit,
        )
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["commercial_status"], "reserved")
        self.assertEqual(int(row["listing_price"]), 185000)

    def test_reservation_rolls_back_when_the_property_update_fails(self):
        property_id = self._priced_property("Calle Rollback")
        contact = self._contact("Rollback")
        visit = create_task(
            self.org,
            self.agent,
            {
                "title": "Visita rollback",
                "task_type": "visit",
                "due_date": "2026-09-24",
                "due_time": "13:00",
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "property_id": property_id,
            },
            created_by_user_id=self.user,
        )
        visit = complete_task(
            self.org,
            visit["id"],
            agent_id=self.agent,
            actor_user_id=self.user,
        )
        from unittest.mock import patch

        with patch(
            "modules.visit_close._commit_property_reservation",
            side_effect=RuntimeError("property write failed"),
        ):
            with self.assertRaises(RuntimeError):
                apply_visit_close(
                    visit,
                    {
                        "result": "reserved",
                        "reservation_amount": 2000,
                        "agreed_property_price": 178000,
                        "note": "falla",
                    },
                    organization_id=self.org,
                    agent_id=self.agent,
                    actor_user_id=self.user,
                    now=self._now(),
                    tz=self.tz,
                    language="es",
                )
        self.assertFalse(get_agent_task(visit["id"], self.org).get("outcome_json"))
        row = get_property_record(property_id, self.org)
        self.assertEqual(row["commercial_status"], "available")
        self.assertEqual(int(row["listing_price"]), 185000)
        self.assertEqual(list_property_commercial_events(self.org, property_id), [])

    def test_reservation_stays_inside_the_organization_and_the_owning_agent(self):
        foreign = self._priced_property("Ajena org", org=self.other_org, agent=None)
        contact = self._contact("Aislada")
        visit = complete_task(
            self.org,
            self._visit(contact)["id"],
            agent_id=self.agent,
            actor_user_id=self.user,
        )
        visit["property_id"] = foreign
        with self.assertRaises(VisitCloseError) as other_org:
            apply_visit_close(
                visit,
                {"result": "reserved", "reservation_amount": 2000, "note": "no"},
                organization_id=self.org,
                agent_id=self.agent,
                actor_user_id=self.user,
                now=self._now(),
                tz=self.tz,
            )
        self.assertEqual(other_org.exception.message_key, "visit_close_err_property")
        self.assertFalse(get_agent_task(visit["id"], self.org).get("outcome_json"))
        self.assertNotEqual(
            get_property_record(foreign, self.other_org)["commercial_status"],
            "reserved",
        )
        owned_by_other = self._priced_property("De otro agente", agent=self.other_agent)
        visit["property_id"] = owned_by_other
        with self.assertRaises(VisitCloseError) as other_agent:
            apply_visit_close(
                visit,
                {"result": "reserved", "note": "no"},
                organization_id=self.org,
                agent_id=self.agent,
                actor_user_id=self.user,
                now=self._now(),
                tz=self.tz,
            )
        self.assertEqual(
            other_agent.exception.message_key,
            "visit_close_err_property_forbidden",
        )
        kept = get_property_record(owned_by_other, self.org)
        self.assertEqual(kept["commercial_status"], "available")
        self.assertEqual(int(kept["listing_price"]), 185000)
