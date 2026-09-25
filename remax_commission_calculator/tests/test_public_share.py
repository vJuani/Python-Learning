"""Public property links and public shortlists. No login on /p or /s."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_PATH"] = str(Path(_TMP.name) / "public_share.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config, get_private_upload_root
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.connection import get_connection
from modules.database.organization_settings_repository import ensure_organization_settings
from modules.database.property_media_repository import (
    MEDIA_PHOTO,
    STRATEGY_COPY,
    upsert_property_media,
)
from modules.database.properties_repository import update_property_status
from modules.database.public_share_repository import get_property_link_by_token
from modules.jrh_ai_provider import interpret_with_rules
from modules.jrh_ai_intents import SHARE_PROPERTY_SHORTLIST
from modules.jrh_ai_service import SESSION_DRAFT_KEY, ask_jrh, confirm_jrh_action
from modules.public_share import (
    KIND_PROPERTY,
    ensure_property_link,
    new_token,
    opens_for,
    revoke_property_link,
    share_target,
    valid_token,
)
from web_app import app


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
    b"\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


class PublicShareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="public-share")
        create_tables()
        cls.org = add_organization("Public QA")
        cls.other = add_organization("Other Office")
        cls.agent = add_agent("Ana López", "Alto", cls.org)
        cls.user_id = add_user(
            "public_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent,
        )
        ensure_organization_settings(cls.org, "Inmobiliaria Norte")
        connection = get_connection()
        try:
            connection.execute(
                """
                UPDATE organization_settings
                SET legal_footer_line = ?, display_name = ?
                WHERE organization_id = ?
                """,
                ("Mat. 1234 CUCICBA — Ana López", "Inmobiliaria Norte", cls.org),
            )
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
        cls.contact = create_agent_contact(
            cls.org,
            cls.agent,
            {
                "name": "Martín Pérez Secreto",
                "phone": "5491155550000",
                "preferences": {
                    "areas": ["Martínez"],
                    "property_types": ["departamento"],
                    "purpose": "sale",
                    "budget": {"max": 250000, "currency": "USD"},
                },
            },
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
            rooms=4,
            bedrooms=2,
            bathrooms=2,
            total_m2=120,
            parking_spaces=1,
            description="Balcón al jardín",
            external_id="SECRET-EXT-99",
        )
        cls.other_property = add_property(
            "Calle Ajena 9",
            "CABA",
            cls.other,
            listing_price=1,
            listing_currency="USD",
        )
        folder = get_private_upload_root() / "qa"
        folder.mkdir(parents=True, exist_ok=True)
        photo = folder / "cover.png"
        photo.write_bytes(_PNG)
        upsert_property_media(
            cls.org,
            cls.property_id,
            source="manual",
            media_type=MEDIA_PHOTO,
            storage_key="qa/cover.png",
            storage_strategy=STRATEGY_COPY,
            position=0,
            is_cover=True,
            content_type="image/png",
            content_hash="public-share-cover",
        )
        cls.link = ensure_property_link(cls.org, cls.property_id, agent_id=cls.agent)
        cls.fragile = add_property(
            "Libertador 800",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent,
            neighborhood="Palermo",
            property_type="apartment",
            listing_price=180000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        cls.fragile_link = ensure_property_link(cls.org, cls.fragile, agent_id=cls.agent)
        cls.client = app.test_client()

    def test_token_is_unguessable_and_stable(self):
        again = ensure_property_link(self.org, self.property_id, agent_id=self.agent)
        self.assertEqual(again["token"], self.link["token"])
        self.assertTrue(valid_token(self.link["token"]))
        self.assertGreaterEqual(len(self.link["token"]), 32)
        self.assertNotEqual(self.link["token"], str(self.property_id))
        self.assertFalse(self.link["token"].isdigit())
        self.assertNotEqual(new_token(), new_token())

    def test_public_page_without_login_hides_internal_data(self):
        from modules.contact_follow_up import record_timeline_note
        from modules.organization_time import organization_timezone

        record_timeline_note(
            self.contact,
            "NOTA-INTERNA-SECRETA",
            kind="note",
            now=datetime.now(timezone.utc),
            tz=organization_timezone(self.org),
        )
        before = opens_for(self.org, KIND_PROPERTY, self.link["id"])
        response = self.client.get(f"/p/{self.link['token']}")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alvear 450", body)
        self.assertIn("Balcón al jardín", body)
        self.assertIn("Mat. 1234 CUCICBA", body)
        self.assertIn("Inmobiliaria Norte", body)
        self.assertIn("og:title", body)
        self.assertIn("og:description", body)
        self.assertIn("og:image", body)
        self.assertIn("Consultar por WhatsApp", body)
        self.assertIn("wa.me/", body)
        self.assertNotIn("NOTA-INTERNA-SECRETA", body)
        self.assertNotIn("SECRET-EXT-99", body)
        self.assertNotIn("Martín Pérez Secreto", body)
        self.assertNotIn(f"/properties/{self.property_id}", body)
        self.assertNotIn("organization_id", body)
        self.assertNotIn("total_commission", body)
        self.assertNotIn("googletagmanager", body)
        self.assertEqual(
            opens_for(self.org, KIND_PROPERTY, self.link["id"]),
            before + 1,
        )

    def test_photo_belongs_to_the_property(self):
        photo = self.client.get(f"/p/{self.link['token']}/photo/0")
        self.assertEqual(photo.status_code, 200)
        self.assertIn("image/", photo.content_type)
        self.assertTrue(photo.data.startswith(b"\x89PNG"))
        missing = self.client.get(f"/p/{self.link['token']}/photo/4")
        self.assertEqual(missing.status_code, 404)

    def test_invalid_revoked_unpublished_and_sold(self):
        self.assertEqual(self.client.get("/p/1").status_code, 404)
        self.assertEqual(self.client.get(f"/p/{new_token()}").status_code, 404)
        foreign = self.client.get(f"/p/{self.link['token']}")
        self.assertNotIn("Calle Ajena 9", foreign.get_data(as_text=True))

        revoke_property_link(self.org, self.fragile)
        revoked = self.client.get(f"/p/{self.fragile_link['token']}")
        self.assertEqual(revoked.status_code, 410)
        self.assertNotIn("Libertador 800", revoked.get_data(as_text=True))

        restored = ensure_property_link(self.org, self.fragile, agent_id=self.agent)
        update_property_status(self.fragile, self.org, "pending")
        hidden = self.client.get(f"/p/{restored['token']}")
        self.assertEqual(hidden.status_code, 404)
        self.assertNotIn("Libertador 800", hidden.get_data(as_text=True))

        update_property_status(self.fragile, self.org, "approved")
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE properties SET commercial_status = ? WHERE id = ?",
                ("sold", self.fragile),
            )
            connection.commit()
        finally:
            connection.close()
        sold = self.client.get(f"/p/{restored['token']}")
        sold_body = sold.get_data(as_text=True)
        self.assertEqual(sold.status_code, 200)
        self.assertIn("ya no se encuentra disponible", sold_body)
        self.assertNotIn(">Venta<", sold_body)

    def test_share_url_falls_back_to_public_token(self):
        from modules.database.properties_repository import get_property_record

        record = get_property_record(self.property_id, self.org)
        url, kind = share_target(record, "https://app.example", agent_id=self.agent)
        self.assertEqual(kind, "jrh")
        self.assertIn("/p/", url)
        self.assertNotIn("/properties/", url)
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE properties SET external_url = ? WHERE id = ?",
                ("https://www.remax.com.ar/listings/alvear-450", self.property_id),
            )
            connection.commit()
        finally:
            connection.close()
        record = get_property_record(self.property_id, self.org)
        portal, portal_kind = share_target(record, "https://app.example", agent_id=self.agent)
        self.assertEqual(portal_kind, "portal")
        self.assertIn("remax.com.ar", portal)
        connection = get_connection()
        try:
            connection.execute(
                "UPDATE properties SET external_url = NULL WHERE id = ?",
                (self.property_id,),
            )
            connection.commit()
        finally:
            connection.close()

    def test_public_shortlist_hides_the_contact_and_expires(self):
        from modules.public_share import ensure_public_shortlist

        self.client.post(
            "/login",
            data={"username": "public_agent", "password": "Password1"},
            follow_redirects=True,
        )
        row = ensure_public_shortlist(
            self.org,
            [self.property_id],
            contact_id=self.contact["id"],
            agent_id=self.agent,
            days=30,
        )
        guest = app.test_client()
        page = guest.get(f"/s/{row['token']}")
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Opciones seleccionadas para vos", body)
        self.assertIn("Alvear 450", body)
        self.assertIn("Ver propiedad", body)
        self.assertNotIn("Martín Pérez Secreto", body)
        self.assertNotIn("Compatibilidad", body)
        self.assertNotIn("contact_id", body)
        self.assertNotEqual(row["token"], str(self.contact["id"]))

        connection = get_connection()
        try:
            connection.execute(
                "UPDATE public_shortlists SET expires_at = ? WHERE token = ?",
                ("2000-01-01T00:00:00+00:00", row["token"]),
            )
            connection.commit()
        finally:
            connection.close()
        expired = guest.get(f"/s/{row['token']}")
        self.assertEqual(expired.status_code, 410)
        self.assertNotIn("Alvear 450", expired.get_data(as_text=True))

    def test_collection_message_and_jrh_preview(self):
        for phrase in (
            "Armame un link con estas tres para Martín Pérez Secreto.",
            "Generame una selección para Martín Pérez Secreto.",
        ):
            parsed = interpret_with_rules(phrase)
            self.assertEqual(parsed["intent"], SHARE_PROPERTY_SHORTLIST, phrase)
        user = {
            "id": self.user_id,
            "role": ROLE_AGENT,
            "agent_id": self.agent,
            "organization_id": self.org,
        }
        session = {}
        preview = ask_jrh(
            "Armame un link con estas tres para Martín Pérez Secreto.",
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session=session,
        )
        self.assertTrue(preview["confirm_required"])
        self.assertFalse(preview["wrote"])
        detail = preview["cards"][0]["detail"]
        self.assertIn("/s/", detail)
        self.assertNotIn("%", detail)
        self.assertNotIn("Martín Pérez Secreto", detail.split("Hola", 1)[-1])
        session.pop(SESSION_DRAFT_KEY, None)
        self.assertIsNone(get_property_link_by_token("missing"))
        preview = ask_jrh(
            "Armame un link con estas tres para Martín Pérez Secreto.",
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session=session,
        )
        confirmed = confirm_jrh_action(
            organization_id=self.org,
            user=user,
            agent_id=self.agent,
            language="es",
            session=session,
        )
        self.assertTrue(confirmed["wrote"])
        self.assertIn("wa.me", confirmed["actions"][0]["href"])


if __name__ == "__main__":
    unittest.main()
