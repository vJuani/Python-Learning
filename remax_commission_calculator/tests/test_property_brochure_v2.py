"""Property brochure PDF V2 + contextual agent card. Does not touch ACM."""

from __future__ import annotations

import base64
import io
import os
import re
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_brochure_v2.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("DATABASE_URL", None)

from modules.agent_branding import get_agent_branding
from modules.agent_photo import save_agent_profile_photo
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config, get_private_upload_root
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.properties_repository import STATUS_APPROVED
from modules.database.property_media_repository import STRATEGY_COPY, upsert_property_media
from modules.database.users_repository import get_user_by_id
from modules.pdf_images import compose_on_color
from modules.property_agent_actions import get_property_agent_actions
from modules.property_brochure import generate_property_brochure
from modules.property_detail_view import compact_property_title, compact_property_unit
from web_app import app


def _jpeg(color, size=(640, 480)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _png_alpha():
    image = Image.new("RGBA", (160, 200), (12, 80, 200, 0))
    for y in range(40, 180):
        for x in range(40, 120):
            image.putpixel((x, y), (20, 40, 90, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_haystack(payload):
    parts = [payload]
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", payload, re.S):
        raw = match.group(1)
        try:
            raw = base64.a85decode(raw, adobe=True, ignorechars=b" \t\r\n")
        except Exception:
            pass
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        parts.append(raw)
    return b"\n".join(parts)


def _page_count(payload):
    return len(re.findall(rb"/Type\s*/Page(?!s)", payload))


class PropertyBrochureV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="brochure-v2")
        create_tables()
        Path(os.environ["PRIVATE_UPLOAD_ROOT"]).mkdir(parents=True, exist_ok=True)
        cls.org = add_organization("Ficha V2 Org")
        pwd = hash_password("Password1")
        cls.agent_id = add_agent("Jose Listing", "Alto", cls.org)
        cls.peer_id = add_agent("Peer Agent", "Alto", cls.org)
        cls.agent_user = add_user(
            "brochure_v2_agent",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            is_active=True,
            first_name="Jose",
            last_name="Listing",
            phone="1140001111",
            email="jose.listing@jrh.test",
        )
        cls.peer_user = add_user(
            "brochure_v2_peer",
            pwd,
            ROLE_AGENT,
            cls.org,
            agent_id=cls.peer_id,
            is_active=True,
            first_name="Peer",
            last_name="Agent",
            email="peer.agent@jrh.test",
        )
        cls.admin = add_user(
            "brochure_v2_admin",
            pwd,
            ROLE_ADMIN,
            cls.org,
            agent_id=cls.agent_id,
            is_active=True,
            first_name="Admin",
            last_name="Office",
            email="admin.wrong@jrh.test",
        )
        cls.prop = add_property(
            "Santamarina 1335 Piso 5 Dpto A (B1644), Victoria, Buenos Aires, Argentina",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            property_type="apartment",
            listing_price=90000,
            listing_currency="USD",
            listing_purpose="sale",
            neighborhood="Victoria",
            locality="Victoria",
            administrative_area="Buenos Aires",
            rooms=2,
            bedrooms=1,
            bathrooms=1,
            covered_m2=42,
            description="Departamento luminoso en Victoria.\n\nLiving comedor y cocina integrada.",
            features={"balcony": True, "pool": True},
        )
        cls.empty = add_property(
            "Sin Foto Brochure",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            listing_price=80000,
            listing_currency="USD",
            listing_purpose="sale",
        )
        cls.single = add_property(
            "Una Foto 10",
            "CABA",
            cls.org,
            agent_id=cls.agent_id,
            status=STATUS_APPROVED,
            listing_price=75000,
            listing_currency="USD",
        )
        root = get_private_upload_root()
        colors = ((30, 80, 160), (200, 90, 40), (40, 140, 80), (90, 40, 140), (180, 160, 40))
        for index, color in enumerate(colors):
            rel = f"brochure-v2-{index}.jpg"
            Image.new("RGB", (720, 540), color).save(root / rel, format="JPEG")
            upsert_property_media(
                cls.org,
                cls.prop,
                source="manual",
                external_media_id=f"b2-{index}",
                storage_key=rel,
                storage_strategy=STRATEGY_COPY,
                is_cover=index == 0,
                position=index,
            )
        Image.new("RGB", (720, 540), (20, 20, 80)).save(root / "brochure-single.jpg", format="JPEG")
        upsert_property_media(
            cls.org,
            cls.single,
            source="manual",
            external_media_id="single",
            storage_key="brochure-single.jpg",
            storage_strategy=STRATEGY_COPY,
            is_cover=True,
            position=0,
        )
        save_agent_profile_photo(cls.org, cls.agent_id, _png_alpha(), content_type="image/png")

    @classmethod
    def tearDownClass(cls):
        _TEST_TMP.cleanup()

    def _user(self, user_id):
        return get_user_by_id(user_id)

    def _html(self, user_id, property_id):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        response = client.get(f"/properties/{property_id}")
        return response, response.get_data(as_text=True)

    def test_01_five_photos_collage(self):
        result = generate_property_brochure(self.prop, self.org, True, self._user(self.agent_user))
        self.assertEqual(len(result["assets"]["gallery"]), 5)
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))
        self.assertLessEqual(_page_count(result["pdf_bytes"]), 2)
        self.assertNotIn("Galería", _pdf_haystack(result["pdf_bytes"]).decode("latin-1", "ignore"))

    def test_02_one_photo_adapts(self):
        result = generate_property_brochure(self.single, self.org, True, self._user(self.agent_user))
        self.assertEqual(len(result["assets"]["gallery"]), 1)
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))
        self.assertLessEqual(_page_count(result["pdf_bytes"]), 2)

    def test_03_no_photos_still_generates(self):
        result = generate_property_brochure(self.empty, self.org, True, self._user(self.agent_user))
        self.assertEqual(result["assets"]["gallery"], [])
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))

    def test_04_no_empty_gallery_page(self):
        one = generate_property_brochure(self.single, self.org, True, self._user(self.agent_user))
        five = generate_property_brochure(self.prop, self.org, True, self._user(self.agent_user))
        self.assertLessEqual(_page_count(one["pdf_bytes"]), 2)
        self.assertLessEqual(_page_count(five["pdf_bytes"]), 2)
        for result in (one, five):
            self.assertNotIn(b"Galer", _pdf_haystack(result["pdf_bytes"]))

    def test_05_price_formatting(self):
        result = generate_property_brochure(self.prop, self.org, True, self._user(self.agent_user))
        text = _pdf_haystack(result["pdf_bytes"]).decode("latin-1", "ignore")
        self.assertIn("USD 90.000,00", text)

    def test_06_real_main_data(self):
        self.assertEqual(
            compact_property_title({"address": "Santamarina 1335 Piso 5 Dpto A (B1644), Victoria"}),
            "Santamarina 1335",
        )
        self.assertIn("Piso 5", compact_property_unit({"address": "Santamarina 1335 Piso 5 Dpto A (B1644), Victoria"}))
        result = generate_property_brochure(self.prop, self.org, True, self._user(self.agent_user))
        text = _pdf_haystack(result["pdf_bytes"]).decode("latin-1", "ignore")
        self.assertIn("Santamarina 1335", text)
        self.assertIn("Victoria", text)
        self.assertIn("PROPIEDAD EN VENTA", text.upper())
        self.assertNotIn("AR.42.27", text)

    def test_07_correct_agent(self):
        result = generate_property_brochure(self.prop, self.org, True, self._user(self.admin))
        self.assertEqual(result["agent"]["name"], "Jose Listing")
        self.assertEqual(result["agent"]["agent_id"], self.agent_id)
        text = _pdf_haystack(result["pdf_bytes"]).decode("latin-1", "ignore")
        self.assertIn("Jose Listing", text)
        self.assertNotIn("Admin Office", text)

    def test_08_agent_email_correct(self):
        result = generate_property_brochure(self.prop, self.org, True, self._user(self.admin))
        self.assertEqual(result["agent"]["email"], "jose.listing@jrh.test")
        text = _pdf_haystack(result["pdf_bytes"]).decode("latin-1", "ignore")
        self.assertIn("jose.listing@jrh.test", text)
        self.assertNotIn("admin.wrong@jrh.test", text)
        branding = get_agent_branding(self.agent_id, self.org, agent_login_only=True)
        self.assertEqual(branding["email"], "jose.listing@jrh.test")

    def test_09_agent_photo_correct(self):
        result = generate_property_brochure(self.prop, self.org, True, self._user(self.agent_user))
        self.assertTrue(result["agent"]["has_photo"])
        self.assertTrue(result["agent"].get("photo_path"))

    def test_10_transparency_works(self):
        branding = get_agent_branding(self.agent_id, self.org, agent_login_only=True)
        from modules.agent_photo import resolve_agent_photo_path
        from modules.database.agents_repository import get_agent_record

        path = resolve_agent_photo_path(get_agent_record(self.agent_id, self.org))
        composed = compose_on_color(path, (10, 22, 51), max_width_px=80, max_height_px=100)
        self.assertIsNotNone(composed)
        image = Image.open(composed["buffer"])
        corner = image.getpixel((2, 2))
        white_distance = sum(abs(corner[i] - 255) for i in range(3))
        navy_distance = sum(abs(corner[i] - (10, 22, 51)[i]) for i in range(3))
        self.assertGreater(white_distance, 80)
        self.assertLess(navy_distance, 40)

    def test_11_without_agent_omits_contact(self):
        result = generate_property_brochure(self.prop, self.org, False, self._user(self.agent_user))
        text = _pdf_haystack(result["pdf_bytes"]).decode("latin-1", "ignore")
        self.assertFalse(result["include_agent_contact"])
        self.assertNotIn("jose.listing@jrh.test", text)
        self.assertNotIn("1140001111", text)
        self.assertIn("JRH One", text)

    def test_12_remote_image_failure_safe(self):
        from unittest.mock import patch

        with patch(
            "modules.property_sync.remote_media.fetch_allowed_image_bytes",
            return_value=None,
        ):
            result = generate_property_brochure(self.empty, self.org, True, self._user(self.agent_user))
        self.assertTrue(result["pdf_bytes"].startswith(b"%PDF"))

    def test_13_same_agent_hides_contact(self):
        _, html = self._html(self.agent_user, self.prop)
        card = re.search(r'data-pd-agent.*?</article>', html, re.S).group(0)
        self.assertNotIn("Contactar", card)
        self.assertIn("Ver mi perfil", card)
        self.assertIn('data-agent-viewer="owner"', html)

    def test_14_same_agent_sees_my_profile(self):
        actions = get_property_agent_actions(
            self._user(self.agent_user),
            {"agent_id": self.agent_id},
            branding={"phone": "1140001111", "email": "jose.listing@jrh.test"},
        )
        self.assertEqual(actions["viewer"], "owner")
        self.assertFalse(actions["show_contact"])
        self.assertEqual(actions["profile_key"], "property_agent_my_profile")

    def test_15_staff_sees_view_agent(self):
        _, html = self._html(self.admin, self.prop)
        card = re.search(r'data-pd-agent.*?</article>', html, re.S).group(0)
        self.assertIn("Ver agente", card)
        self.assertIn("Editar agente", card)
        self.assertNotIn("Contactar", card)

    def test_16_peer_agent_no_contact(self):
        actions = get_property_agent_actions(
            self._user(self.peer_user),
            {"agent_id": self.agent_id},
            branding={"phone": "1140001111", "email": "jose.listing@jrh.test"},
        )
        self.assertEqual(actions["viewer"], "peer")
        self.assertFalse(actions["show_contact"])
        self.assertEqual(actions["profile_key"], "property_agent_profile")

    def test_17_guest_contact_when_phone(self):
        actions = get_property_agent_actions(
            None,
            {"agent_id": self.agent_id},
            is_guest=True,
            branding={"phone": "+54 11 4000-1111", "email": "jose.listing@jrh.test"},
        )
        self.assertEqual(actions["viewer"], "guest")
        self.assertTrue(actions["show_contact"])
        self.assertIn("wa.me/541140001111", actions["contact_href"])
        none = get_property_agent_actions(None, {"agent_id": self.agent_id}, is_guest=True, branding={})
        self.assertFalse(none["show_contact"])

    def test_18_no_hardcoded_jose(self):
        source = Path(__file__).resolve().parents[1] / "modules" / "property_presentation.py"
        self.assertNotIn("Barreiro", source.read_text(encoding="utf-8"))
        self.assertNotIn("juanignacioruiz2001", source.read_text(encoding="utf-8"))
