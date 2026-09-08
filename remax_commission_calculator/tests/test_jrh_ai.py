"""Phase 4E transversal JRH assistant. Mocks only — no live LLM calls."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_jrh_ai.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.agent_account import create_movement
from modules.agent_tasks import create_task
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
from modules.jrh_ai_intents import (
    CREATE_TASK,
    FALLBACK,
    QUERY_AGENDA,
    QUERY_AGENT_ACCOUNT,
    QUERY_PENDINGS,
    QUERY_PROPERTIES,
    QUERY_PROPERTY_NEEDS,
    START_INVOICE,
    START_AGENT_PAYMENT,
)
from datetime import datetime, timezone

from modules.jrh_ai_classify import (
    extract_entities,
    normalize_location,
    parse_price_amount,
    resolve_agenda_date,
)
from modules.jrh_ai_provider import (
    MockAIIntentProvider,
    get_intent_provider,
    interpret_prompt,
    interpret_with_rules,
)
from modules.jrh_ai_resolver import resolve_agents
from modules.jrh_ai_service import ask_jrh, confirm_jrh_action
from modules.organization_time import now_utc, organization_timezone
from web_app import app


def _task_count(organization_id, agent_id=None):
    connection = get_connection()
    try:
        if agent_id is None:
            row = connection.execute(
                "SELECT COUNT(*) FROM agent_tasks WHERE organization_id = ?",
                (organization_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(*) FROM agent_tasks "
                "WHERE organization_id = ? AND agent_id = ?",
                (organization_id, agent_id),
            ).fetchone()
        return row[0]
    finally:
        connection.close()


def _movement_count(organization_id):
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM agent_account_movements "
            "WHERE organization_id = ?",
            (organization_id,),
        ).fetchone()[0]
    finally:
        connection.close()


class JrhAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="jrh-ai-test")
        create_tables()
        cls.org = add_organization("JRH AI Org")
        cls.other_org = add_organization("JRH AI Other Org")
        cls.password = "Password1"
        pwd = hash_password(cls.password)

        cls.barreiro = add_agent("José Luis Barreiro", "Alto", cls.org)
        cls.jose_martinez = add_agent("José Martínez", "Alto", cls.org)
        cls.pablo = add_agent("Pablo Gómez", "Alto", cls.org)
        cls.own_agent = add_agent("Home Agent", "Alto", cls.org)
        cls.quiroga = add_agent("Laura Quiroga", "Alto", cls.org)
        cls.foreign_barreiro = add_agent("José Luis Barreiro", "Alto", cls.other_org)

        cls.admin = add_user(
            "jrh_ai_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            email="jrh_ai_admin@example.com",
        )
        cls.agent_user = add_user(
            "jrh_ai_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.own_agent,
            email="jrh_ai_agent@example.com",
        )
        cls.barreiro_user = add_user(
            "jrh_ai_barreiro",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.barreiro,
            email="jrh_ai_barreiro@example.com",
        )
        cls.foreign_user = add_user(
            "jrh_ai_foreign",
            pwd,
            ROLE_AGENT,
            cls.other_org,
            agent_id=cls.foreign_barreiro,
            email="jrh_ai_foreign@example.com",
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
            "agent_id": cls.own_agent,
        }
        cls.barreiro_record = {
            "id": cls.barreiro_user,
            "role": ROLE_AGENT,
            "organization_id": cls.org,
            "agent_id": cls.barreiro,
        }

        cls.fee = cls._charge(
            cls.org,
            cls.barreiro,
            description="Fee septiembre",
            charge_category="fee",
            amount="65",
            created_by_user_id=cls.admin,
        )
        cls.jrh_fee = cls._charge(
            cls.org,
            cls.barreiro,
            description="JRH One septiembre",
            charge_category="jrh_subscription",
            amount="10",
            created_by_user_id=cls.admin,
        )
        cls.pablo_fee = cls._charge(
            cls.org,
            cls.pablo,
            description="Fee septiembre",
            charge_category="fee",
            amount="65",
            created_by_user_id=cls.admin,
        )
        cls.own_fee = cls._charge(
            cls.org,
            cls.own_agent,
            description="Fee septiembre",
            charge_category="fee",
            amount="65",
            created_by_user_id=cls.admin,
        )
        cls._charge(
            cls.other_org,
            cls.foreign_barreiro,
            description="Fee extranjera",
            charge_category="fee",
            amount="99",
            created_by_user_id=cls.foreign_user,
        )

        add_property(
            "Av. Libertador 4200",
            "CABA",
            cls.org,
            agent_id=cls.own_agent,
            neighborhood="Núñez",
            rooms=3,
            listing_price=220000,
            property_type="apartment",
        )
        add_property(
            "Cabildo 1000",
            "CABA",
            cls.org,
            agent_id=cls.own_agent,
            neighborhood="Belgrano",
            rooms=2,
            listing_price=400000,
            property_type="apartment",
        )
        add_property(
            "Av. Cabildo 2500",
            "CABA",
            cls.org,
            agent_id=cls.own_agent,
            neighborhood="Belgrano",
            rooms=3,
            listing_price=210000,
            property_type="apartment",
        )
        add_property(
            "Foreign Capital 100",
            "CABA",
            cls.other_org,
            agent_id=cls.foreign_barreiro,
            neighborhood="Palermo",
            rooms=3,
            listing_price=180000,
            property_type="apartment",
        )

        cls.martin = create_agent_contact(
            cls.org,
            cls.own_agent,
            {
                "name": "Martín Pérez",
                "preferences": {
                    "areas": ["Núñez"],
                    "budget": {"max": 250000, "currency": "USD"},
                    "property_types": ["departamento"],
                    "rooms": 3,
                },
            },
        )

        tz = organization_timezone(cls.org)
        today = now_utc().astimezone(tz)
        create_task(
            cls.org,
            cls.own_agent,
            {
                "title": "Visita Libertador",
                "task_type": "visit",
                "due_date": today.date().isoformat(),
                "due_time": "16:30",
                "contact_name": "Martín",
            },
            created_by_user_id=cls.agent_user,
        )

    @staticmethod
    def _charge(
        organization_id,
        agent_id,
        *,
        description,
        charge_category="fee",
        amount="65",
        created_by_user_id=None,
    ):
        return create_movement(
            organization_id,
            agent_id,
            {
                "movement_type": "charge",
                "charge_category": charge_category,
                "currency": "USD",
                "amount": amount,
                "vat_mode": "add_vat",
                "vat_rate": "21",
                "description": description,
                "movement_date": "2026-09-02",
                "period_label": "Septiembre 2026",
            },
            created_by_user_id=created_by_user_id,
        )

    def _ask(self, prompt, *, user=None, agent_id=None, session=None, **kwargs):
        return ask_jrh(
            prompt,
            organization_id=self.org,
            user=user or self.admin_record,
            agent_id=agent_id,
            language="es",
            session=session if session is not None else {},
            **kwargs,
        )

    def _login(self, username):
        client = app.test_client()
        mapping = {
            "jrh_ai_admin": (self.admin, ROLE_ADMIN, self.org),
            "jrh_ai_agent": (self.agent_user, ROLE_AGENT, self.org),
        }
        uid, role, org = mapping[username]
        with client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["role"] = role
            sess["organization_id"] = org
        return client

    def test_01_que_debe_barreiro_interprets_account(self):
        parsed = interpret_with_rules("qué debe Barreiro")
        self.assertEqual(parsed["intent"], QUERY_AGENT_ACCOUNT)
        self.assertEqual(parsed["entities"].get("agent_name"), "Barreiro")
        result = self._ask("qué debe Barreiro")
        self.assertEqual(result["intent"], QUERY_AGENT_ACCOUNT)
        self.assertEqual(result["status"], "ready")
        self.assertIn("Barreiro", result["summary"])

    def test_02_resolve_barreiro_inside_org(self):
        matches = resolve_agents(
            self.org,
            "Barreiro",
            user=self.admin_record,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], self.barreiro)
        self.assertIn("José Luis", matches[0]["name"])

    def test_03_ambiguous_name_asks(self):
        result = self._ask("qué debe José")
        self.assertEqual(result["intent"], QUERY_AGENT_ACCOUNT)
        self.assertEqual(result["status"], "needs_attention")
        names = {item["name"] for item in result["candidates"]}
        self.assertIn("José Luis Barreiro", names)
        self.assertIn("José Martínez", names)

    def test_04_other_org_never_appears(self):
        matches = resolve_agents(
            self.org,
            "Barreiro",
            user=self.admin_record,
        )
        self.assertEqual([item["id"] for item in matches], [self.barreiro])
        result = self._ask("qué debe Barreiro")
        candidate_ids = {item.get("id") for item in result.get("candidates") or []}
        self.assertNotIn(self.foreign_barreiro, candidate_ids)
        self.assertNotEqual(result.get("entity", {}).get("id"), self.foreign_barreiro)

    def test_05_que_tengo_hoy_uses_agenda(self):
        parsed = interpret_with_rules("qué tengo hoy")
        self.assertEqual(parsed["intent"], QUERY_AGENDA)
        result = self._ask(
            "qué tengo hoy",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_AGENDA)
        titles = [card["title"] for card in result["cards"]]
        self.assertTrue(any("Libertador" in title for title in titles))

    def test_06_pendiente_uses_pending_center(self):
        parsed = interpret_with_rules("qué tengo pendiente")
        self.assertEqual(parsed["intent"], QUERY_PENDINGS)
        result = self._ask(
            "qué tengo pendiente",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_PENDINGS)
        self.assertEqual(result["status"], "ready")
        hrefs = [action.get("href_name") for action in result["actions"]]
        self.assertIn("pendings_center", hrefs)

    def test_07_property_search_uses_backend_filters(self):
        parsed = interpret_with_rules(
            "mostrame departamentos de Núñez hasta 250 mil"
        )
        self.assertEqual(parsed["intent"], QUERY_PROPERTIES)
        self.assertEqual(parsed["entities"].get("neighborhood"), "Núñez")
        self.assertEqual(parsed["entities"].get("max_price"), 250000)
        result = self._ask(
            "buscame departamentos en Núñez hasta 250 mil",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_PROPERTIES)
        self.assertGreaterEqual(len(result["cards"]), 1)
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertIn("Libertador", titles)
        self.assertNotIn("Cabildo", titles)

    def test_08_need_uses_property_match(self):
        parsed = interpret_with_rules("qué tengo para Martín")
        self.assertEqual(parsed["intent"], QUERY_PROPERTY_NEEDS)
        result = self._ask(
            "qué tengo para Martín",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_PROPERTY_NEEDS)
        self.assertEqual(result["status"], "ready")
        self.assertIn("Martín", result["summary"])
        hrefs = [action.get("href_name") for action in result["actions"]]
        self.assertIn("contacts_property_matches", hrefs)

    def test_09_invoice_fee_resolves_charge(self):
        result = self._ask("quiero facturar el fee de Barreiro")
        self.assertEqual(result["intent"], START_INVOICE)
        self.assertTrue(result["confirm_required"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["entity"].get("kind"), "charge")
        self.assertEqual(result["entity"].get("id"), self.fee["id"])
        hrefs = [action.get("href_name") for action in result["actions"]]
        self.assertIn("billing_prepare_charge", hrefs)

    def test_10_ambiguous_invoice_asks_selection(self):
        result = self._ask("facturame lo de Barreiro")
        self.assertEqual(result["intent"], START_INVOICE)
        self.assertEqual(result["status"], "needs_attention")
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertFalse(result["wrote"])

    def test_11_financial_action_does_not_write_without_confirm(self):
        before = _movement_count(self.org)
        result = self._ask("quiero facturar el fee de Barreiro")
        self.assertTrue(result["confirm_required"])
        self.assertFalse(result["wrote"])
        self.assertEqual(_movement_count(self.org), before)
        payment = self._ask("registrame este pago")
        self.assertEqual(payment["intent"], START_AGENT_PAYMENT)
        self.assertTrue(payment["confirm_required"])
        self.assertFalse(payment["wrote"])
        self.assertEqual(_movement_count(self.org), before)

    def test_12_confirm_uses_real_task_service(self):
        session = {
            "jrh_ai_draft": {
                "intent": CREATE_TASK,
                "title": "Visita confirmada JRH",
                "task_type": "visit",
                "due_date": "2026-09-08",
                "due_time": "18:00",
                "contact_name": "Martín",
            }
        }
        before = _task_count(self.org, self.own_agent)
        result = confirm_jrh_action(
            organization_id=self.org,
            user=self.agent_record,
            agent_id=self.own_agent,
            session=session,
            language="es",
        )
        self.assertEqual(result["intent"], CREATE_TASK)
        self.assertTrue(result["wrote"])
        self.assertEqual(_task_count(self.org, self.own_agent), before + 1)
        self.assertNotIn("jrh_ai_draft", session)

        invoice_session = {"jrh_ai_draft": {"intent": START_INVOICE, "charge_id": self.fee["id"]}}
        before_mov = _movement_count(self.org)
        invoice = confirm_jrh_action(
            organization_id=self.org,
            user=self.admin_record,
            agent_id=None,
            session=invoice_session,
            language="es",
        )
        self.assertFalse(invoice["wrote"])
        self.assertEqual(_movement_count(self.org), before_mov)

    def test_13_agent_cannot_see_other_agent(self):
        result = self._ask(
            "qué debe Barreiro",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_AGENT_ACCOUNT)
        self.assertEqual(result["status"], "needs_attention")
        matches = resolve_agents(
            self.org,
            "Barreiro",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(matches, [])

    def test_14_context_facturamelo_resolves_previous_charge(self):
        session = {}
        first = self._ask("qué debe Pablo", session=session)
        self.assertEqual(first["intent"], QUERY_AGENT_ACCOUNT)
        self.assertEqual(first["entity"].get("kind"), "charge")
        second = self._ask("facturámelo", session=session)
        self.assertEqual(second["intent"], START_INVOICE)
        self.assertTrue(second["confirm_required"])
        self.assertEqual(second["entity"].get("id"), self.pablo_fee["id"])

    def test_15_low_confidence_asks(self):
        class LowConfidenceProvider:
            def interpret(self, prompt, *, context=None, language="es"):
                return {
                    "intent": QUERY_AGENT_ACCOUNT,
                    "entities": {"agent_name": "Barreiro"},
                    "confidence": 0.4,
                    "provider": "mock",
                    "model": "rules",
                }

        result = self._ask(
            "barreiro algo",
            provider=LowConfidenceProvider(),
        )
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("Barreiro", result["message"])

    def test_16_provider_is_mock(self):
        provider = get_intent_provider()
        self.assertIsInstance(provider, MockAIIntentProvider)
        parsed = interpret_prompt("qué debe Barreiro")
        self.assertEqual(parsed["provider"], "mock")
        self.assertEqual(parsed["intent"], QUERY_AGENT_ACCOUNT)

    def test_17_fallback_does_not_execute(self):
        result = self._ask("asdfgh qwerty")
        self.assertEqual(result["intent"], FALLBACK)
        self.assertFalse(result["wrote"])
        self.assertFalse(result.get("confirm_required"))
        hrefs = [action.get("href_name") for action in result["actions"]]
        self.assertIn("pendings_center", hrefs)
        self.assertIn("agenda_index", hrefs)

    def test_18_ui_renders_200(self):
        client = self._login("jrh_ai_admin")
        page = client.get("/jrh")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Preguntale a JRH", body)
        self.assertIn("data-jrh-ask", body)
        home = client.get("/")
        self.assertIn("Preguntale a JRH", home.get_data(as_text=True))

    def test_19_mobile_renders_200(self):
        client = self._login("jrh_ai_agent")
        page = client.get("/jrh?mobile=1")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("is-mobile", body)
        self.assertIn("Preguntale a JRH", body)

    def test_19b_legacy_interpret_get_uses_new_ui(self):
        client = self._login("jrh_ai_agent")
        page = client.get("/jrh/interpret", follow_redirects=True)
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("jrh-studio", body)
        self.assertNotIn("jrh-ask__form", body)
        self.assertNotIn("QUERY_", body)

    def test_19c_agent_sees_acm_quick_action(self):
        client = self._login("jrh_ai_agent")
        body = client.get("/jrh").get_data(as_text=True)
        self.assertIn("data-jrh-acm", body)
        self.assertIn("Crear ACM", body)

    def test_19d_staff_does_not_see_acm_action(self):
        client = self._login("jrh_ai_admin")
        body = client.get("/jrh").get_data(as_text=True)
        self.assertNotIn("data-jrh-acm", body)
        self.assertIn("jrh-studio", body)

    def test_19e_conversation_and_dark_motion_hooks(self):
        client = self._login("jrh_ai_agent")
        page = client.get("/jrh")
        body = page.get_data(as_text=True)
        self.assertIn("jrh-ask-page.css", body)
        css = Path(__file__).resolve().parents[1].joinpath("static", "css", "jrh-ask-page.css").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("[data-theme=\"dark\"]", css)
        asked = client.post(
            "/jrh",
            data={"prompt": "mostrame qué propiedades disponibles tengo en capital"},
        )
        asked_body = asked.get_data(as_text=True)
        self.assertEqual(asked.status_code, 200)
        self.assertIn("jrh-chat", asked_body)
        self.assertIn("Libertador", asked_body)
        self.assertNotIn("QUERY_PROPERTIES", asked_body)
        self.assertNotIn("Intent detected", asked_body)

    def test_20_no_obvious_n_plus_one(self):
        for index in range(6):
            add_property(
                f"Núñez extra {index}",
                "CABA",
                self.org,
                agent_id=self.own_agent,
                neighborhood="Núñez",
                rooms=3,
                listing_price=180000 + index,
                property_type="apartment",
            )
        from modules.database import connection as connection_module

        original = connection_module.get_connection
        calls = {"count": 0}

        def counting_connection(*args, **kwargs):
            calls["count"] += 1
            return original(*args, **kwargs)

        connection_module.get_connection = counting_connection
        try:
            result = self._ask(
                "mostrame departamentos de Núñez hasta 250 mil",
                user=self.agent_record,
                agent_id=self.own_agent,
            )
        finally:
            connection_module.get_connection = original

        self.assertEqual(result["intent"], QUERY_PROPERTIES)
        self.assertGreaterEqual(len(result["cards"]), 1)
        self.assertLessEqual(calls["count"], 8)

    def test_21_inventory_phrases_are_query_properties(self):
        phrases = (
            "mostrame que propiedades disponibles tengo en capital",
            "qué propiedades tengo en CABA",
            "mostrame lo disponible",
            "buscame deptos en Núñez",
            "hay casas en zona norte?",
            "departamentos hasta 250 mil",
            "qué alquileres tenemos en capital",
        )
        for phrase in phrases:
            parsed = interpret_with_rules(phrase)
            self.assertEqual(
                parsed["intent"],
                QUERY_PROPERTIES,
                phrase,
            )

    def test_22_agenda_query_is_not_create(self):
        for phrase in (
            "qué visitas tengo mañana",
            "qué tengo agendado hoy",
            "tengo algo el jueves?",
            "qué tengo agendado el jueves?",
            "el jueves tengo algo reservado?",
        ):
            parsed = interpret_with_rules(phrase)
            self.assertEqual(parsed["intent"], QUERY_AGENDA, phrase)

    def test_23_create_task_needs_explicit_signal(self):
        for phrase in (
            "agendame una visita mañana a las 18",
            "recordame llamar a Juan",
            "agendame algo el jueves",
            "agendame una visita el jueves a las 17",
        ):
            parsed = interpret_with_rules(phrase)
            self.assertEqual(parsed["intent"], CREATE_TASK, phrase)

    def test_24_unclear_prompt_is_fallback_not_agenda(self):
        for phrase in ("haceme algo con Juan", "asdfgh qwerty"):
            parsed = interpret_with_rules(phrase)
            self.assertEqual(parsed["intent"], FALLBACK, phrase)
            result = self._ask(phrase)
            self.assertEqual(result["intent"], FALLBACK)
            self.assertFalse(result["wrote"])

    def test_25_capital_aliases_normalize_to_caba(self):
        for raw in ("capital", "CABA", "Capital Federal"):
            location = normalize_location(raw)
            self.assertEqual(location.get("jurisdiction"), "CABA", raw)
        parsed = interpret_with_rules(
            "mostrame que propiedades disponibles tengo en capital"
        )
        self.assertEqual(parsed["entities"].get("jurisdiction"), "CABA")
        self.assertEqual(parsed["entities"].get("availability"), "available")
        result = self._ask(
            "mostrame que propiedades disponibles tengo en capital",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_PROPERTIES)
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertIn("Libertador", titles)
        self.assertNotIn("Foreign Capital", titles)

    def test_26_price_aliases_and_agent_scope(self):
        self.assertEqual(parse_price_amount("hasta 250 mil"), 250000)
        self.assertEqual(parse_price_amount("250k"), 250000)
        self.assertEqual(parse_price_amount("250 lucas"), 250000)
        self.assertEqual(parse_price_amount("250.000"), 250000)
        other_agent_property = add_property(
            "Privada Otras Manos 9",
            "CABA",
            self.org,
            agent_id=self.barreiro,
            neighborhood="Núñez",
            rooms=3,
            listing_price=190000,
            property_type="apartment",
        )
        result = self._ask(
            "qué propiedades tengo en CABA",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertNotIn("Privada Otras Manos", titles)
        self.assertNotEqual(result.get("entity", {}).get("id"), other_agent_property)

    def test_27_capital_available_answers_with_filtered_cards(self):
        parsed = interpret_with_rules(
            "mostrame qué propiedades disponibles tengo en capital"
        )
        self.assertEqual(parsed["intent"], QUERY_PROPERTIES)
        self.assertEqual(parsed["entities"].get("jurisdiction"), "CABA")
        self.assertEqual(parsed["entities"].get("availability"), "available")
        result = self._ask(
            "mostrame qué propiedades disponibles tengo en capital",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_PROPERTIES)
        self.assertIn("Encontré", result["message"])
        self.assertIn("CABA", result["message"])
        self.assertNotIn("Te llevo al listado", result["message"])
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertIn("Libertador", titles)
        self.assertNotIn("Foreign Capital", titles)
        action = result["actions"][0]
        self.assertEqual(action["href_args"].get("jurisdiction"), "CABA")
        self.assertEqual(action["href_args"].get("commercial_status"), "available")

    def test_28_nunez_apartments(self):
        parsed = interpret_with_rules("qué deptos tenemos en Núñez")
        self.assertEqual(parsed["intent"], QUERY_PROPERTIES)
        self.assertEqual(parsed["entities"].get("neighborhood"), "Núñez")
        self.assertEqual(parsed["entities"].get("property_type"), "apartment")
        result = self._ask(
            "qué deptos tenemos en Núñez",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertIn("Libertador", titles)
        self.assertNotIn("Cabildo", titles)

    def test_29_belgrano_max_price(self):
        parsed = interpret_with_rules("hay algo en Belgrano hasta 250 mil?")
        self.assertEqual(parsed["intent"], QUERY_PROPERTIES)
        self.assertEqual(parsed["entities"].get("neighborhood"), "Belgrano")
        self.assertEqual(parsed["entities"].get("max_price"), 250000)
        result = self._ask(
            "hay algo en Belgrano hasta 250 mil?",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertIn("Cabildo 2500", titles)
        self.assertNotIn("Cabildo 1000", titles)

    def test_30_view_all_preserves_filters(self):
        result = self._ask(
            "mostrame qué propiedades disponibles tengo en capital",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        href_args = result["actions"][0]["href_args"]
        self.assertEqual(href_args.get("jurisdiction"), "CABA")
        self.assertEqual(href_args.get("commercial_status"), "available")
        self.assertEqual(result["data"].get("filters"), href_args)

    def test_31_thursday_query_is_not_create(self):
        for phrase in (
            "tengo algo el jueves?",
            "qué tengo agendado el jueves?",
        ):
            parsed = interpret_with_rules(phrase)
            self.assertEqual(parsed["intent"], QUERY_AGENDA, phrase)
            result = self._ask(
                phrase,
                user=self.agent_record,
                agent_id=self.own_agent,
            )
            self.assertEqual(result["intent"], QUERY_AGENDA, phrase)
            self.assertNotEqual(result["intent"], CREATE_TASK)

    def test_32_thursday_date_resolves(self):
        monday = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
        resolved = resolve_agenda_date(
            extract_entities("tengo algo el jueves?"),
            monday,
        )
        self.assertEqual(resolved["start"].isoformat(), "2026-09-10")
        thursday = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        same = resolve_agenda_date(extract_entities("el jueves tengo algo?"), thursday)
        self.assertEqual(same["start"].isoformat(), "2026-09-10")
        nxt = resolve_agenda_date(extract_entities("próximo jueves"), thursday)
        self.assertEqual(nxt["start"].isoformat(), "2026-09-17")

    def test_33_empty_agenda_is_not_create(self):
        result = self._ask(
            "tengo algo el jueves?",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_AGENDA)
        self.assertIn("nada agendado", result["message"])
        hrefs = [action.get("href_name") for action in result["actions"]]
        self.assertIn("agenda_compose", hrefs)
        self.assertFalse(result.get("confirm_required"))

    def test_34_thursday_agenda_lists_existing_task(self):
        tz = organization_timezone(self.org)
        thursday = resolve_agenda_date(
            extract_entities("tengo algo el jueves?"),
            now_utc().astimezone(tz),
        )["start"]
        create_task(
            self.org,
            self.own_agent,
            {
                "title": "Llamar a Martín",
                "task_type": "call",
                "due_date": thursday.isoformat(),
                "due_time": "10:00",
                "contact_name": "Martín",
            },
            created_by_user_id=self.agent_user,
        )
        result = self._ask(
            "qué tengo agendado el jueves?",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_AGENDA)
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertIn("Martín", titles)
        self.assertNotIn("nada agendado", result["message"])

    def test_35_agent_own_fee(self):
        parsed = interpret_with_rules("debo fee?")
        self.assertEqual(parsed["intent"], QUERY_AGENT_ACCOUNT)
        self.assertTrue(parsed["entities"].get("self"))
        result = self._ask(
            "q debo de fee?",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], QUERY_AGENT_ACCOUNT)
        self.assertIn("Fee", result["message"] + str(result["cards"]))
        self.assertEqual(result["entity"].get("id"), self.own_fee["id"])

    def test_36_staff_barreiro_fee(self):
        parsed = interpret_with_rules("qué debe Barreiro de fee?")
        self.assertEqual(parsed["intent"], QUERY_AGENT_ACCOUNT)
        self.assertEqual(parsed["entities"].get("agent_name"), "Barreiro")
        result = self._ask("qué debe Barreiro de fee?")
        self.assertEqual(result["intent"], QUERY_AGENT_ACCOUNT)
        titles = " ".join(card["title"] for card in result["cards"])
        self.assertIn("Fee", titles)
        self.assertNotIn("JRH One", titles)

    def test_37_no_fee_pending(self):
        result = self._ask("qué debe Quiroga de fee?")
        self.assertEqual(result["intent"], QUERY_AGENT_ACCOUNT)
        self.assertIn("No tenés Fee pendiente", result["message"])

    def test_38_invoice_vague_many_candidates(self):
        result = self._ask(
            "facturame lo q tengo",
            user=self.barreiro_record,
            agent_id=self.barreiro,
        )
        self.assertEqual(result["intent"], START_INVOICE)
        self.assertEqual(result["status"], "needs_attention")
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertFalse(result["wrote"])
        self.assertIn("cargos", result["message"])

    def test_39_invoice_vague_one_preview(self):
        result = self._ask(
            "facturame lo que tengo",
            user=self.agent_record,
            agent_id=self.own_agent,
        )
        self.assertEqual(result["intent"], START_INVOICE)
        self.assertTrue(result["confirm_required"])
        self.assertFalse(result["wrote"])
        self.assertEqual(result["entity"].get("id"), self.own_fee["id"])
        hrefs = [action.get("href_name") for action in result["actions"]]
        self.assertIn("billing_prepare_charge", hrefs)

    def test_40_facturame_eso_uses_context(self):
        session = {}
        first = self._ask(
            "q debo de fee?",
            user=self.agent_record,
            agent_id=self.own_agent,
            session=session,
        )
        self.assertEqual(first["entity"].get("kind"), "charge")
        second = self._ask(
            "facturame eso",
            user=self.agent_record,
            agent_id=self.own_agent,
            session=session,
        )
        self.assertEqual(second["intent"], START_INVOICE)
        self.assertTrue(second["confirm_required"])
        self.assertFalse(second["wrote"])
        self.assertEqual(second["entity"].get("id"), self.own_fee["id"])

    def test_41_home_and_ask_http_answer_properties(self):
        client = self._login("jrh_ai_agent")
        page = client.post(
            "/jrh",
            data={"prompt": "mostrame qué propiedades disponibles tengo en capital"},
        )
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Encontré", body)
        self.assertIn("Libertador", body)
        self.assertNotIn("QUERY_PROPERTIES", body)
        self.assertNotIn("Te llevo al listado", body)
        home = client.post(
            "/jrh/interpret",
            data={"prompt": "mostrame qué propiedades disponibles tengo en capital"},
        )
        home_body = home.get_data(as_text=True)
        self.assertEqual(home.status_code, 200)
        self.assertIn("Encontré", home_body)
        self.assertNotIn("Entendí 1 cosa", home_body)
        self.assertNotIn("Te llevo al listado", home_body)


if __name__ == "__main__":
    unittest.main()
