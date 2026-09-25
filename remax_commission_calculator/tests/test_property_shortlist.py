"""Property shortlist and the agent-sent WhatsApp draft."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_property_shortlist.db")

from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contacts import create_agent_contact, load_contact
from modules.database import (
    add_agent,
    add_organization,
    add_property,
    add_user,
    create_tables,
)
from modules.database.connection import get_connection
from modules.database.contacts_repository import (
    list_property_interactions,
    record_property_interaction,
)
from modules.jrh_ai_intents import SHARE_PROPERTY_SHORTLIST
from modules.jrh_ai_provider import interpret_with_rules
from modules.jrh_ai_service import SESSION_DRAFT_KEY, ask_jrh, confirm_jrh_action
from modules.organization_time import now_utc, to_utc_iso
from modules.property_shortlist import (
    ShortlistError,
    draft_whatsapp_message,
    public_share_url,
    select_properties,
)
from web_app import app


def _set_external_url(property_id, url):
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE properties SET external_url = ? WHERE id = ?",
            (url, property_id),
        )
        connection.commit()
    finally:
        connection.close()


class ShortlistMessageTests(unittest.TestCase):
    def test_public_urls_reject_app_paths(self):
        self.assertEqual(public_share_url("/properties/4"), "")
        self.assertEqual(public_share_url("http://localhost/properties/4"), "")
        self.assertEqual(
            public_share_url("https://www.remax.com.ar/listings/alvear-450"),
            "https://www.remax.com.ar/listings/alvear-450",
        )

    def test_message_keeps_order_and_hides_score(self):
        message = draft_whatsapp_message(
            {"name": "Martín Pérez"},
            [
                {
                    "address": "Alvear 450",
                    "neighborhood": "Martínez",
                    "price_label": "USD 190.000",
                    "rooms": 4,
                    "bedrooms": 2,
                    "parking": False,
                    "public_url": "https://www.remax.com.ar/listings/alvear-450",
                    "score": 88,
                },
                {
                    "address": "Av. Santa Fe 2100",
                    "neighborhood": "Martínez",
                    "price_label": "USD 205.000",
                    "rooms": 3,
                    "bedrooms": 2,
                    "parking": True,
                    "public_url": "",
                    "score": 86,
                },
            ],
        )
        self.assertLess(
            message.index("Alvear 450"),
            message.index("Santa Fe 2100"),
        )
        self.assertIn("https://www.remax.com.ar/listings/alvear-450", message)
        self.assertNotIn("/properties/", message)
        self.assertNotIn("88%", message)
        self.assertNotIn("86%", message)
        self.assertIn("cochera", message.casefold())
        self.assertIn("Hola Martín", message)


class ShortlistRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="shortlist-test")
        create_tables()
        cls.org = add_organization("Shortlist Org")
        cls.other = add_organization("Shortlist Other")
        cls.agent = add_agent("Short Agent", "Alto", cls.org)
        cls.other_agent = add_agent("Other Agent", "Alto", cls.other)
        cls.password = "Password1"
        cls.user = add_user(
            "short_agent",
            hash_password(cls.password),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.contact = create_agent_contact(
            cls.org,
            cls.agent,
            {
                "name": "Martín Pérez",
                "phone": "5491155550000",
                "preferences": {
                    "areas": ["Martínez"],
                    "budget": {"max": 250000, "currency": "USD"},
                    "property_types": ["departamento"],
                    "purpose": "sale",
                },
            },
        )
        cls.alvear = add_property(
            "Alvear 450",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent,
            neighborhood="Martínez",
            property_type="apartment",
            listing_price=190000,
            listing_currency="USD",
            listing_purpose="sale",
            rooms=4,
            bedrooms=2,
        )
        cls.santa = add_property(
            "Av. Santa Fe 2100",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent,
            neighborhood="Martínez",
            property_type="apartment",
            listing_price=205000,
            listing_currency="USD",
            listing_purpose="sale",
            rooms=3,
            bedrooms=2,
            parking_spaces=1,
        )
        _set_external_url(cls.alvear, "https://www.remax.com.ar/listings/alvear-450")
        _set_external_url(cls.santa, "https://www.remax.com.ar/listings/santa-fe-2100")
        cls.sold = add_property(
            "Vendida 10",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            commercial_status="sold",
            listing_price=100000,
            listing_currency="USD",
        )
        cls.foreign = add_property(
            "Ajena 20",
            "CABA",
            cls.other,
            agent_id=cls.other_agent,
            listing_price=100000,
            listing_currency="USD",
        )
        cls.discarded = add_property(
            "Descartada 30",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent,
            neighborhood="Martínez",
            property_type="apartment",
            listing_price=180000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        record_property_interaction(
            cls.org,
            cls.agent,
            contact_id=cls.contact["id"],
            property_id=cls.discarded,
            interaction_type="discarded",
        )

    def setUp(self):
        self.client = app.test_client()
        self.client.post(
            "/login",
            data={"username": "short_agent", "password": self.password},
            follow_redirects=True,
        )

    def test_select_one_then_several_preserves_order(self):
        one = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist",
            data={"property_id": str(self.santa)},
            follow_redirects=True,
        )
        body = one.get_data(as_text=True)
        self.assertIn("1 propiedad seleccionada", body)
        self.assertIn("Av. Santa Fe 2100", body)
        self.assertIn("https://www.remax.com.ar/listings/santa-fe-2100", body)

        ordered = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist",
            data={"property_id": [str(self.alvear), str(self.santa)]},
            follow_redirects=True,
        )
        page = ordered.get_data(as_text=True)
        self.assertLess(page.index("Alvear 450"), page.index("Santa Fe 2100"))
        self.assertNotIn("88%", page)
        self.assertIn("Compatibilidad", page)

    def test_rejects_too_many_unavailable_foreign_and_discarded(self):
        extra = [
            add_property(
                f"Extra {index}",
                "CABA",
                self.org,
                agent_id=self.agent,
                listing_price=100000,
                listing_currency="USD",
            )
            for index in range(4)
        ]
        too_many = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist",
            data={"property_id": [str(self.alvear), str(self.santa), *[str(item) for item in extra]]},
            follow_redirects=True,
        )
        self.assertIn("hasta 5", too_many.get_data(as_text=True))
        sold = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist",
            data={"property_id": str(self.sold)},
            follow_redirects=True,
        )
        self.assertIn("no está disponible", sold.get_data(as_text=True))
        foreign = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist",
            data={"property_id": str(self.foreign)},
            follow_redirects=True,
        )
        self.assertIn("No encontramos", foreign.get_data(as_text=True))
        discarded = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist",
            data={"property_id": str(self.discarded)},
            follow_redirects=True,
        )
        self.assertIn("descartada", discarded.get_data(as_text=True))

    def test_open_records_one_timeline_and_does_not_duplicate_shared(self):
        self.client.post(
            f"/contacts/{self.contact['id']}/shortlist",
            data={"property_id": [str(self.alvear), str(self.santa)]},
        )
        opened = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist/open",
            data={
                "property_id": [str(self.alvear), str(self.santa)],
                "message": "Hola Martín\n\n1. Alvear 450\nhttps://www.remax.com.ar/listings/alvear-450",
            },
            follow_redirects=True,
        )
        page = opened.get_data(as_text=True)
        self.assertIn("Abrir WhatsApp", page)
        self.assertIn("wa.me/", page)
        self.assertIn("Agendar seguimiento", page)
        self.assertNotIn("88%", page)
        rows = list_property_interactions(self.org, self.contact["id"], limit=20)
        shared = [row for row in rows if row["interaction_type"] == "shared"]
        self.assertEqual(len(shared), 2)
        again = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist/open",
            data={
                "property_id": [str(self.alvear), str(self.santa)],
                "message": "Hola Martín de nuevo",
            },
            follow_redirects=True,
        )
        self.assertEqual(again.status_code, 200)
        rows = list_property_interactions(self.org, self.contact["id"], limit=20)
        shared = [row for row in rows if row["interaction_type"] == "shared"]
        self.assertEqual(len(shared), 2)
        contact = load_contact(self.org, self.contact["id"], agent_id=self.agent)
        notes = [
            line for line in (contact.get("notes") or "").splitlines()
            if "property_shortlist_shared" in line
        ]
        self.assertEqual(len(notes), 2)
        self.assertIn("Alvear 450", notes[-1])
        self.assertIn("Santa Fe 2100", notes[-1])
        detail = self.client.get(f"/contacts/{self.contact['id']}")
        self.assertIn("Ya enviada", detail.get_data(as_text=True))

    def test_follow_up_uses_existing_cadence_date(self):
        response = self.client.post(
            f"/contacts/{self.contact['id']}/shortlist/follow-up",
            data={"days": "3"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        contact = load_contact(self.org, self.contact["id"], agent_id=self.agent)
        self.assertTrue(contact.get("next_follow_up_at"))

    def test_direct_select_rejects_other_agent_property(self):
        peer = add_property(
            "Del otro 1",
            "CABA",
            self.org,
            agent_id=add_agent("Peer", "Alto", self.org),
            listing_price=150000,
            listing_currency="USD",
        )
        with self.assertRaises(ShortlistError) as caught:
            select_properties(
                self.org,
                self.contact["id"],
                [peer],
                agent_id=self.agent,
            )
        self.assertEqual(caught.exception.code, "forbidden")


class ShortlistJrhTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="shortlist-jrh")
        create_tables()
        cls.org = add_organization("Shortlist JRH")
        cls.agent = add_agent("JRH Agent", "Alto", cls.org)
        cls.user_id = add_user(
            "short_jrh",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        cls.user = {
            "id": cls.user_id,
            "role": ROLE_AGENT,
            "agent_id": cls.agent,
            "organization_id": cls.org,
        }
        cls.contact = create_agent_contact(
            cls.org,
            cls.agent,
            {
                "name": "Lucía Gómez",
                "phone": "5491166660000",
                "preferences": {
                    "areas": ["Belgrano"],
                    "property_types": ["departamento"],
                    "purpose": "sale",
                },
            },
        )
        cls.alvear = add_property(
            "Alvear 450",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            neighborhood="Belgrano",
            property_type="apartment",
            listing_price=190000,
            listing_currency="USD",
            listing_purpose="sale",
            rooms=3,
            bedrooms=2,
        )
        cls.santa = add_property(
            "Av. Santa Fe 2100",
            "CABA",
            cls.org,
            agent_id=cls.agent,
            neighborhood="Belgrano",
            property_type="apartment",
            listing_price=205000,
            listing_currency="USD",
            listing_purpose="sale",
            rooms=3,
            bedrooms=2,
            parking_spaces=1,
        )

    def _ask(self, prompt, session=None):
        return ask_jrh(
            prompt,
            organization_id=self.org,
            user=self.user,
            agent_id=self.agent,
            language="es",
            session=session if session is not None else {},
        )

    def test_phrases_preview_confirm_and_cancel(self):
        for phrase in (
            "Mandale a Lucía las primeras tres.",
            "Preparame estas propiedades para Lucía.",
            "Armame un WhatsApp con las mejores opciones para Lucía.",
            "Mandale Alvear y Santa Fe a Lucía.",
        ):
            parsed = interpret_with_rules(phrase)
            self.assertEqual(parsed["intent"], SHARE_PROPERTY_SHORTLIST, phrase)

        session = {}
        preview = self._ask("Mandale Alvear y Santa Fe a Lucía.", session)
        self.assertEqual(preview["intent"], SHARE_PROPERTY_SHORTLIST)
        self.assertTrue(preview["confirm_required"])
        self.assertFalse(preview["wrote"])
        self.assertIn("Alvear", preview["cards"][0]["detail"])
        self.assertNotIn("%", preview["cards"][0]["detail"])
        before = list_property_interactions(self.org, self.contact["id"], limit=20)
        session.pop(SESSION_DRAFT_KEY, None)
        self.assertEqual(
            list_property_interactions(self.org, self.contact["id"], limit=20),
            before,
        )

        session = {}
        preview = self._ask("Mandale Alvear y Santa Fe a Lucía.", session)
        confirmed = confirm_jrh_action(
            organization_id=self.org,
            user=self.user,
            agent_id=self.agent,
            session=session,
            language="es",
        )
        self.assertTrue(confirmed["wrote"])
        self.assertTrue(confirmed["actions"][0]["href"].startswith("https://wa.me/"))
        shared = [
            row for row in list_property_interactions(self.org, self.contact["id"], limit=20)
            if row["interaction_type"] == "shared"
        ]
        self.assertEqual(len(shared), 2)
        contact = load_contact(self.org, self.contact["id"], agent_id=self.agent)
        self.assertEqual(
            (contact.get("notes") or "").count("property_shortlist_shared"),
            1,
        )

    def test_homonyms_ask_for_clarification(self):
        create_agent_contact(
            self.org,
            self.agent,
            {"name": "Sofía Uno", "phone": "5491177770001"},
        )
        create_agent_contact(
            self.org,
            self.agent,
            {"name": "Sofía Dos", "phone": "5491177770002"},
        )
        result = self._ask("Mandale a Sofía las primeras tres.")
        self.assertEqual(result["status"], "needs_attention")
        self.assertGreaterEqual(len(result["candidates"]), 2)


class SharedFollowUpDailyTests(unittest.TestCase):
    def test_due_follow_up_after_share_is_a_daily_candidate(self):
        from modules.contact_follow_up import REASON_SHARED, build_daily_follow_up_list

        now = now_utc()
        contact = {
            "id": 9,
            "name": "Martín Pérez",
            "phone": "1",
            "commercial_stage": "contacted",
            "follow_up_cadence": "medium",
            "follow_up_priority": "potential",
            "next_follow_up_at": to_utc_iso(now - timedelta(days=1)),
            "last_interacted_at": "",
            "notes": "[2026-09-20|property_shortlist_shared] Se compartieron 3 propiedades: Alvear 450, Santa Fe 2100 y Libertador 800.",
            "archived_at": None,
        }
        rows = build_daily_follow_up_list([contact], now=now)
        self.assertEqual(rows[0]["reason"], REASON_SHARED)


if __name__ == "__main__":
    unittest.main()
