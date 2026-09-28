"""Public inquiries become CRM contacts. Opening a link does not."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "inbound.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.contact_follow_up import (
    REASON_INBOUND,
    REASON_NEW,
    build_daily_follow_up_list,
    business_deadline,
    mark_contacted,
)
from modules.contacts import ContactError, load_contact
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.contacts_repository import get_contact
from modules.database.notifications_repository import find_notification_by_event_key
from modules.database.organization_settings_repository import ensure_organization_settings
from modules.follow_up_daily import collect_agent_follow_ups
from modules.inbound_inquiry import reset_inquiry_rate_limits
from modules.jrh_ai_provider import interpret_with_rules
from modules.jrh_ai_intents import CREATE_TASK, QUERY_CONTACT
from modules.jrh_ai_service import ask_jrh, confirm_jrh_action
from modules.notifications.events import emit_event
from modules.organization_time import now_utc, organization_timezone, to_utc_iso
from modules.public_share import ensure_property_link, ensure_public_shortlist, revoke_property_link
from web_app import app


TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class InboundInquiryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="inbound-inquiry")
        create_tables()
        cls.org = add_organization("Inbound QA")
        cls.other = add_organization("Other Inbound")
        ensure_organization_settings(cls.org, "Oficina Norte")
        ensure_organization_settings(cls.other, "Oficina Sur")
        cls.agent = add_agent("Ana López", "Alto", cls.org)
        cls.other_agent = add_agent("Bruno Díaz", "Alto", cls.org)
        cls.foreign_agent = add_agent("Carla Sur", "Alto", cls.other)
        cls.user_id = add_user(
            "inbound_ana",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE users
                SET phone = ?, first_name = ?, last_name = ?
                WHERE id = ?
                """,
                ("5491112345678", "Ana", "López", cls.user_id),
            )
            connection.commit()
        finally:
            connection.close()
        cls.other_user_id = add_user(
            "inbound_bruno",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent,
        )
        cls.property_id = add_property(
            "Alvear 450",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent,
            neighborhood="Martínez",
            property_type="apartment",
            listing_price=190000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        cls.link = ensure_property_link(cls.org, cls.property_id, agent_id=cls.agent)
        cls.secret = _insert_contact(
            cls.org,
            cls.agent,
            "Martín Pérez Secreto",
            "5491155550000",
            "martin@example.com",
        )
        cls.shortlist = ensure_public_shortlist(
            cls.org,
            [cls.property_id],
            contact_id=cls.secret,
            agent_id=cls.agent,
        )
        cls.client = app.test_client()

    def setUp(self):
        reset_inquiry_rate_limits()

    def _count(self, organization_id):
        connection = get_connection()
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM contacts WHERE organization_id = ?",
                (organization_id,),
            ).fetchone()
        finally:
            connection.close()
        return row[0]

    def _form_token(self, path):
        page = self.client.get(path)
        body = page.get_data(as_text=True)
        match = re.search(r'name="form_token" value="([^"]+)"', body)
        return page, body, match.group(1) if match else ""

    def _post(self, path, token, **fields):
        payload = {
            "name": "Lucía Gómez",
            "phone": "11 5555-1111",
            "email": "",
            "message": "¿Sigue disponible?",
            "form_token": token,
        }
        payload.update(fields)
        return self.client.post(path, data=payload)

    def test_get_does_not_create_a_contact_and_keeps_whatsapp_clean(self):
        before = self._count(self.org)
        page, body, _token = self._form_token(f"/p/{self.link['token']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Enviar consulta", body)
        self.assertIn("wa.me/", body)
        self.assertIn("Alvear%20450", body)
        self.assertNotIn("ref=", body.split("wa.me/", 1)[-1].split('"', 1)[0])
        self.assertNotIn("organization_id", body)
        self.assertNotIn("Martín Pérez Secreto", body)
        self.assertEqual(self._count(self.org), before)

    def test_new_inquiry_creates_contact_property_source_and_one_push(self):
        before = self._count(self.org)
        _page, _body, token = self._form_token(f"/p/{self.link['token']}")
        response = self._post(
            f"/p/{self.link['token']}/inquiry",
            token,
            name="Lucía Gómez",
            phone="11 5555-1111",
            email="Lucia@Example.com",
            message="¿Sigue disponible? ¿Se puede visitar el sábado?",
            organization_id=str(self.other),
            agent_id=str(self.foreign_agent),
            property_id="1",
            contact_id=str(self.secret),
            shortlist_id="1",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Recibimos tu consulta.", response.get_data(as_text=True))
        self.assertNotIn("ya existe", response.get_data(as_text=True).lower())
        self.assertEqual(self._count(self.org), before + 1)
        contact = _latest_contact(self.org)
        self.assertEqual(contact["agent_id"], self.agent)
        self.assertEqual(contact["organization_id"], self.org)
        self.assertEqual(contact["commercial_stage"], "new")
        self.assertEqual(contact["source"], "public_property")
        self.assertEqual(contact["source_type"], "public_property")
        self.assertEqual(contact["email_normalized"], "lucia@example.com")
        self.assertEqual(contact["phone_normalized"], "541155551111")
        self.assertTrue(contact["last_interacted_at"])
        row = _latest_inquiry(contact["id"])
        self.assertEqual(row["interaction_type"], "inquiry_received")
        self.assertEqual(row["property_id"], self.property_id)
        self.assertEqual(row["source"], "public_property")
        self.assertIsNone(row["shortlist_id"])
        self.assertIn("sábado", row["message"])
        event_key = f"inbound_lead:{row['id']}"
        self.assertIsNotNone(
            find_notification_by_event_key(self.org, event_key, user_id=self.user_id)
        )
        again = emit_event(
            "crm.inbound_lead",
            {
                "organization_id": self.org,
                "user_id": self.user_id,
                "agent_id": self.agent,
                "type": "inbound_lead_received",
                "title": "Nueva consulta por Alvear 450",
                "body": "Lucía dejó una consulta.",
                "url": f"/contacts/{contact['id']}#inquiry-{row['id']}",
                "event_key": event_key,
                "entity_type": "contact",
                "entity_id": contact["id"],
            },
        )
        self.assertTrue(again["deduped"])
        self.assertFalse(again["created"])

    def test_phone_formats_and_email_do_not_duplicate(self):
        _page, _body, token = self._form_token(f"/p/{self.link['token']}")
        first = self._post(
            f"/p/{self.link['token']}/inquiry",
            token,
            name="Martín Pérez",
            phone="11 5555-0000",
            email="Martin@Example.com",
            message="Sigo interesado.",
        )
        self.assertEqual(first.status_code, 200)
        _page, _body, token = self._form_token(f"/p/{self.link['token']}")
        second = self._post(
            f"/p/{self.link['token']}/inquiry",
            token,
            name="Otra persona",
            phone="+54 9 11 5555-0000",
            email="martin@example.com",
            message="Otro mensaje.",
        )
        self.assertEqual(second.status_code, 200)
        matches = _contacts_with_phone(self.org, "55550000")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], self.secret)
        self.assertEqual(matches[0]["agent_id"], self.agent)

    def test_email_phone_conflict_does_not_merge(self):
        email_only = _insert_contact(
            self.org,
            self.agent,
            "Elena Ruiz",
            "5491144440000",
            "elena@example.com",
        )
        before = self._count(self.org)
        _page, _body, token = self._form_token(f"/p/{self.link['token']}")
        response = self._post(
            f"/p/{self.link['token']}/inquiry",
            token,
            name="Alguien Nuevo",
            phone="11 2222-3333",
            email="elena@example.com",
            message="Quiero visitar.",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("elena", response.get_data(as_text=True).lower())
        self.assertEqual(self._count(self.org), before + 1)
        created = _latest_contact(self.org)
        self.assertNotEqual(created["id"], email_only)
        self.assertEqual(created["email"], "")
        inquiry = _latest_inquiry(created["id"])
        self.assertEqual(inquiry["attention"], "email_phone_conflict")
        self.assertEqual(inquiry["related_contact_id"], email_only)
        self.assertEqual(inquiry["visitor_email"], "elena@example.com")
        stored = get_contact(email_only, self.org)
        self.assertEqual(stored["phone_normalized"], "5491144440000")

    def test_existing_contact_of_another_agent_keeps_ownership(self):
        owned = _insert_contact(
            self.org,
            self.other_agent,
            "Dueño Ajeno",
            "5491166660000",
            "",
        )
        before = self._count(self.org)
        _page, _body, token = self._form_token(f"/p/{self.link['token']}")
        response = self._post(
            f"/p/{self.link['token']}/inquiry",
            token,
            name="Dueño Ajeno",
            phone="11 6666-0000",
            message="Vi la propiedad de Ana.",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count(self.org), before)
        stored = get_contact(owned, self.org)
        self.assertEqual(stored["agent_id"], self.other_agent)
        inquiry = _latest_inquiry(owned)
        self.assertEqual(inquiry["agent_id"], self.other_agent)
        self.assertEqual(inquiry["listing_agent_id"], self.agent)
        self.assertEqual(inquiry["attention"], "other_agent_listing")
        event_key = f"inbound_lead:{inquiry['id']}"
        self.assertIsNotNone(
            find_notification_by_event_key(
                self.org, event_key, user_id=self.other_user_id
            )
        )
        self.assertIsNone(
            find_notification_by_event_key(self.org, event_key, user_id=self.user_id)
        )
        with self.assertRaises(ContactError):
            load_contact(self.org, owned, agent_id=self.agent)

    def test_other_organization_is_isolated(self):
        foreign_property = add_property(
            "Calle Ajena 9",
            "CABA",
            self.other,
            agent_id=self.foreign_agent,
            listing_price=1,
            listing_currency="USD",
            listing_purpose="sale",
        )
        foreign_link = ensure_property_link(
            self.other, foreign_property, agent_id=self.foreign_agent
        )
        before_home = self._count(self.org)
        _page, _body, token = self._form_token(f"/p/{foreign_link['token']}")
        response = self._post(
            f"/p/{foreign_link['token']}/inquiry",
            token,
            name="Martín Pérez",
            phone="11 5555-0000",
            email="martin@example.com",
            message="Consulta en otra oficina.",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count(self.org), before_home)
        foreign_contacts = _contacts_with_phone(self.other, "55550000")
        self.assertEqual(len(foreign_contacts), 1)
        self.assertEqual(foreign_contacts[0]["agent_id"], self.foreign_agent)
        self.assertEqual(foreign_contacts[0]["organization_id"], self.other)

    def test_shortlist_records_source_without_assuming_its_contact(self):
        before = self._count(self.org)
        _page, body, token = self._form_token(f"/s/{self.shortlist['token']}")
        self.assertNotIn("Martín Pérez Secreto", body)
        self.assertNotIn("contact_id", body)
        response = self._post(
            f"/s/{self.shortlist['token']}/inquiry",
            token,
            name="Visitante Nuevo",
            phone="11 7777-8888",
            message="Me interesan las opciones.",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._count(self.org), before + 1)
        contact = _latest_contact(self.org)
        self.assertNotEqual(contact["id"], self.secret)
        self.assertEqual(contact["source"], "public_shortlist")
        self.assertEqual(contact["agent_id"], self.agent)
        inquiry = _latest_inquiry(contact["id"])
        self.assertEqual(inquiry["shortlist_id"], self.shortlist["id"])
        self.assertEqual(inquiry["source"], "public_shortlist")
        self.assertIsNone(inquiry["property_id"])

    def test_spam_invalid_revoked_expired_and_unavailable(self):
        _page, _body, token = self._form_token(f"/p/{self.link['token']}")
        before = self._count(self.org)
        honeypot = self._post(
            f"/p/{self.link['token']}/inquiry",
            token,
            company_website="https://spam.example",
        )
        self.assertEqual(honeypot.status_code, 200)
        self.assertIn("Recibimos tu consulta.", honeypot.get_data(as_text=True))
        self.assertEqual(self._count(self.org), before)
        bad = self._post(f"/p/{self.link['token']}/inquiry", "not-a-token")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self._count(self.org), before)
        missing = self.client.post("/p/no-such-token/inquiry", data={"name": "A"})
        self.assertEqual(missing.status_code, 404)

        sold_id = add_property(
            "Libertador 800",
            "Buenos Aires",
            self.org,
            agent_id=self.agent,
            listing_price=1,
            listing_currency="USD",
            listing_purpose="sale",
        )
        sold_link = ensure_property_link(self.org, sold_id, agent_id=self.agent)
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE properties SET commercial_status = ? WHERE id = ?",
                ("sold", sold_id),
            )
            connection.commit()
        finally:
            connection.close()
        sold = self.client.get(f"/p/{sold_link['token']}")
        self.assertEqual(sold.status_code, 200)
        self.assertNotIn("Enviar consulta", sold.get_data(as_text=True))
        sold_post = self.client.post(
            f"/p/{sold_link['token']}/inquiry",
            data={"name": "Nadie", "phone": "1155551111", "message": "Hola"},
        )
        self.assertEqual(sold_post.status_code, 404)

        revoke_id = add_property(
            "Santa Fe 100",
            "Buenos Aires",
            self.org,
            agent_id=self.agent,
            listing_price=1,
            listing_currency="USD",
            listing_purpose="sale",
        )
        revoked_link = ensure_property_link(self.org, revoke_id, agent_id=self.agent)
        revoke_property_link(self.org, revoke_id)
        revoked = self.client.post(f"/p/{revoked_link['token']}/inquiry", data={"name": "A"})
        self.assertEqual(revoked.status_code, 410)

        expired = ensure_public_shortlist(
            self.org,
            [self.property_id],
            agent_id=self.agent,
        )
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE public_shortlists SET expires_at = ? WHERE token = ?",
                ("2000-01-01T00:00:00", expired["token"]),
            )
            connection.commit()
        finally:
            connection.close()
        gone = self.client.post(f"/s/{expired['token']}/inquiry", data={"name": "A"})
        self.assertEqual(gone.status_code, 410)
        self.assertEqual(self._count(self.org), before)

        reset_inquiry_rate_limits()
        for index in range(5):
            _page, _body, form_token = self._form_token(f"/p/{self.link['token']}")
            ok = self._post(
                f"/p/{self.link['token']}/inquiry",
                form_token,
                name=f"Persona {index}",
                phone=f"11 3000-000{index}",
                message="Consulta real.",
            )
            self.assertEqual(ok.status_code, 200, index)
        _page, _body, form_token = self._form_token(f"/p/{self.link['token']}")
        limited = self._post(
            f"/p/{self.link['token']}/inquiry",
            form_token,
            name="Persona extra",
            phone="11 3000-0009",
            message="Una de más.",
        )
        self.assertEqual(limited.status_code, 429)
        self.assertNotIn("30000009", _phones(self.org))

    def test_daily_inbound_unanswered_clears_when_contacted(self):
        moment = datetime(2026, 9, 30, 10, 0, tzinfo=TZ)
        later = datetime(2026, 9, 30, 15, 0, tzinfo=TZ)
        self.assertEqual(
            business_deadline(moment, 4, TZ),
            datetime(2026, 9, 30, 14, 0, tzinfo=TZ),
        )
        _page, _body, token = self._form_token(f"/p/{self.link['token']}")
        self._post(
            f"/p/{self.link['token']}/inquiry",
            token,
            name="Sin Respuesta",
            phone="11 9090-1010",
            message="¿Sigue en venta?",
        )
        contact = _latest_contact(self.org)
        inquiry = _latest_inquiry(contact["id"])
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE contact_property_interactions
                SET created_at = ?
                WHERE id = ?
                """,
                (to_utc_iso(moment), inquiry["id"]),
            )
            connection.execute(
                """
                UPDATE contacts
                SET last_interacted_at = ?
                WHERE id = ?
                """,
                (to_utc_iso(moment), contact["id"]),
            )
            connection.commit()
        finally:
            connection.close()
        rows = [
            row
            for row in collect_agent_follow_ups(self.org, self.agent, now=later)
            if row["contact_id"] == contact["id"]
        ]
        self.assertEqual([row["reason"] for row in rows], [REASON_INBOUND])
        self.assertNotIn(REASON_NEW, [row["reason"] for row in rows])
        recent = build_daily_follow_up_list(
            [
                {
                    **get_contact(contact["id"], self.org),
                    "latest_inquiry": {"created_at": to_utc_iso(later - timedelta(hours=1))},
                }
            ],
            now=later,
        )
        self.assertFalse(any(row["reason"] == REASON_INBOUND for row in recent))
        mark_contacted(get_contact(contact["id"], self.org), now=now_utc())
        cleared = [
            row
            for row in collect_agent_follow_ups(self.org, self.agent, now=later)
            if row["contact_id"] == contact["id"]
        ]
        self.assertEqual(cleared, [])

    def test_query_contact_and_schedule_from_an_inquiry(self):
        street_id = add_property(
            "Santamarina 1335",
            "Buenos Aires",
            self.org,
            agent_id=self.agent,
            listing_price=1,
            listing_currency="USD",
            listing_purpose="sale",
        )
        street_link = ensure_property_link(self.org, street_id, agent_id=self.agent)
        _page, _body, token = self._form_token(f"/p/{street_link['token']}")
        self._post(
            f"/p/{street_link['token']}/inquiry",
            token,
            name="José Pérez",
            phone="11 1212-3434",
            message="Quería consultar por Santamarina.",
        )
        contact = _latest_contact(self.org)
        for phrase in (
            "Qué consultas nuevas tengo?",
            "Mostrame los contactos nuevos de hoy",
            "Quién preguntó por Santamarina?",
            "Quién consultó por Santamarina?",
        ):
            parsed = interpret_with_rules(phrase)
            self.assertEqual(parsed["intent"], QUERY_CONTACT, phrase)
        user = {
            "id": self.user_id,
            "role": ROLE_AGENT,
            "agent_id": self.agent,
            "organization_id": self.org,
        }
        listed = ask_jrh(
            "Quién preguntó por Santamarina?",
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session={},
        )
        self.assertEqual(listed["intent"], QUERY_CONTACT)
        self.assertEqual(listed["status"], "ready")
        self.assertEqual(listed["cards"][0]["title"], "José Pérez")
        self.assertFalse(listed["wrote"])
        schedule_phrase = "Agendame una llamada con el que preguntó por Santamarina"
        self.assertEqual(interpret_with_rules(schedule_phrase)["intent"], CREATE_TASK)
        session = {}
        preview = ask_jrh(
            schedule_phrase,
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session=session,
        )
        self.assertTrue(preview["confirm_required"])
        self.assertFalse(preview["wrote"])
        self.assertEqual(preview["data"]["draft"]["contact_id"], contact["id"])
        self.assertEqual(preview["data"]["draft"]["task_type"], "call")
        confirmed = confirm_jrh_action(
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session=session,
        )
        self.assertTrue(confirmed["wrote"])
        _page, _body, token = self._form_token(f"/p/{street_link['token']}")
        self._post(
            f"/p/{street_link['token']}/inquiry",
            token,
            name="Otra Consulta",
            phone="11 1212-9999",
            message="También pregunto.",
        )
        ambiguous = ask_jrh(
            schedule_phrase,
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session={},
        )
        self.assertEqual(ambiguous["status"], "needs_attention")
        self.assertGreaterEqual(len(ambiguous["candidates"]), 2)
        self.assertFalse(ambiguous["wrote"])


def _insert_contact(organization_id, agent_id, name, phone, email):
    from modules.contacts import create_agent_contact

    contact = create_agent_contact(
        organization_id,
        agent_id,
        {
            "name": name,
            "phone": phone,
            "email": email,
            "source": "manual",
        },
    )
    return contact["id"]


def _latest_contact(organization_id):
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id FROM contacts
            WHERE organization_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (organization_id,),
        ).fetchone()
    finally:
        connection.close()
    return get_contact(row[0], organization_id)


def _latest_inquiry(contact_id):
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT
                id, interaction_type, property_id, shortlist_id, source,
                message, agent_id, listing_agent_id, attention,
                related_contact_id, visitor_email
            FROM contact_property_interactions
            WHERE contact_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (contact_id,),
        ).fetchone()
    finally:
        connection.close()
    return {
        "id": row[0],
        "interaction_type": row[1],
        "property_id": row[2],
        "shortlist_id": row[3],
        "source": row[4],
        "message": row[5],
        "agent_id": row[6],
        "listing_agent_id": row[7],
        "attention": row[8],
        "related_contact_id": row[9],
        "visitor_email": row[10],
    }


def _contacts_with_phone(organization_id, suffix):
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, agent_id, organization_id, phone_normalized
            FROM contacts
            WHERE organization_id = ?
                AND phone_normalized LIKE ?
            """,
            (organization_id, f"%{suffix}"),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row[0],
            "agent_id": row[1],
            "organization_id": row[2],
            "phone_normalized": row[3],
        }
        for row in rows
    ]


def _phones(organization_id):
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT phone_normalized FROM contacts WHERE organization_id = ?",
            (organization_id,),
        ).fetchall()
    finally:
        connection.close()
    return " ".join(row[0] or "" for row in rows)


if __name__ == "__main__":
    unittest.main()
