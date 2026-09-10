"""FASE 6A — JRH Marketing IA. Isolated temp DB. No live RedREMAX HTTP."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_marketing_ia.db")
os.environ.pop("DATABASE_URL", None)
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)

from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.agents_repository import update_agent_profile_photo
from modules.database.connection import get_connection
from modules.database.marketing_repository import get_marketing_asset
from modules.database.properties_repository import get_property_record, update_property
from modules.database.property_media_repository import STRATEGY_COPY, upsert_property_media
from modules.database.users_repository import get_user_by_id
from modules.jrh_ai_classify import classify_intent, detect_marketing_content
from modules.jrh_ai_intents import CREATE_TASK, START_MARKETING_CONTENT
from modules.marketing_context import (
    FORBIDDEN_FACT_KEYS,
    PHOTO_LIMIT,
    build_property_marketing_context,
    context_to_snapshot,
)
from modules.marketing_copy import _sanitize_ai_copy, generate_marketing_copy
from modules.marketing_renderer import FORMAT_SIZES, render_marketing_image
from modules.marketing_service import (
    can_view_asset,
    generate_marketing_proposals,
    resolve_asset_file,
)
from modules.property_sync.media import get_property_media_for_generation
from web_app import app

QA_DIR = Path(__file__).resolve().parent / "qa_marketing"


def _write_photo(path, color, size=(1400, 1000), rooms=False):
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    if rooms:
        draw.rectangle((80, 80, size[0] - 80, size[1] - 80), outline=(255, 255, 255), width=8)
        draw.rectangle((120, 160, 520, 520), fill=(color[0] + 20, color[1] + 20, color[2] + 20))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=92)
    return path


def _write_agent_png(path):
    image = Image.new("RGBA", (400, 520), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 40, 280, 200), fill=(10, 22, 51, 255))
    draw.rounded_rectangle((80, 220, 320, 500), 40, fill=(13, 71, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return path


class MarketingIaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="marketing-test", SESSION_COOKIE_SECURE=False)
        create_tables()
        cls.org = add_organization("Marketing Org")
        cls.other_org = add_organization("Other Org")
        cls.agent_id = add_agent("José Barreiro", "Alto", cls.org)
        cls.other_agent_id = add_agent("Otro Agente", "Alto", cls.org)
        cls.admin_id = add_user(
            "mkt_admin",
            hash_password("Password1"),
            ROLE_ADMIN,
            cls.org,
            email="mkt.admin@example.com",
        )
        cls.agent_user_id = add_user(
            "mkt_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="José",
            last_name="Barreiro",
            phone="1140000000",
            email="jose.barreiro@example.com",
        )
        cls.other_user_id = add_user(
            "mkt_other",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.other_agent_id,
            first_name="Otro",
            email="otro@example.com",
        )
        photo_key = f"organizations/{cls.org}/agents/{cls.agent_id}/photo.png"
        _write_agent_png(_PRIVATE_ROOT / photo_key)
        update_agent_profile_photo(
            cls.agent_id,
            cls.org,
            profile_photo_key=photo_key,
            profile_photo_mime="image/png",
            profile_photo_width=400,
            profile_photo_height=520,
        )
        cls.property_id = add_property(
            "Santamarina 1335",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent_id,
            property_type="apartment",
            listing_price=90000,
            listing_purpose="sale",
            listing_currency="USD",
            neighborhood="Victoria",
            locality="Victoria",
            rooms=2,
            bedrooms=1,
            bathrooms=1,
            covered_m2=42,
            description="Departamento de 2 ambientes en Victoria.",
            created_by_user_id=cls.admin_id,
        )
        colors = (
            (62, 74, 92),
            (118, 96, 78),
            (86, 104, 92),
            (90, 90, 108),
            (130, 110, 96),
            (70, 80, 88),
            (100, 88, 76),
        )
        for index, color in enumerate(colors):
            rel = f"organizations/{cls.org}/properties/{cls.property_id}/media/p{index}.jpg"
            _write_photo(_PRIVATE_ROOT / rel, color, rooms=True)
            upsert_property_media(
                cls.org,
                cls.property_id,
                source="manual",
                external_media_id=f"local-{index}",
                original_url=None,
                storage_key=rel,
                storage_strategy=STRATEGY_COPY,
                position=index,
                is_cover=index == 0,
                content_type="image/jpeg",
            )
        cls.bare_id = add_property(
            "Sin Fotos 100",
            "Buenos Aires",
            cls.org,
            agent_id=cls.agent_id,
            listing_price=100000,
            listing_purpose="sale",
            created_by_user_id=cls.admin_id,
        )
        cls.other_prop = add_property(
            "Otra 200",
            "Buenos Aires",
            cls.org,
            agent_id=cls.other_agent_id,
            listing_price=50000,
            listing_purpose="sale",
            created_by_user_id=cls.admin_id,
        )

    def _property(self):
        return get_property_record(self.property_id, self.org)

    def _user(self, user_id):
        return get_user_by_id(user_id)

    def _form(self, **overrides):
        data = {
            "format": "story",
            "style": "elegant",
            "tone": "professional",
            "show_price": "1",
            "include_agent": "1",
            "show_agent_photo": "1",
            "show_features": "1",
        }
        data.update(overrides)
        return data

    def test_01_marketing_context_facts(self):
        context = build_property_marketing_context(self._property())
        facts = context["facts"]
        self.assertEqual(facts["title"], "Santamarina 1335")
        self.assertIn("Victoria", facts["locality"] or facts["location_line"])
        self.assertEqual(facts["listing_price"], 90000)
        self.assertEqual(facts["price_label"], "USD 90.000")
        self.assertEqual(facts["purpose_label"], "Venta")
        self.assertEqual(facts["rooms"], 2)
        self.assertEqual(facts["agent_id"], self.agent_id)
        self.assertTrue(facts["price_policy"]["default_show"])

    def test_02_real_photos_only(self):
        context = build_property_marketing_context(self._property())
        self.assertGreaterEqual(context["photo_count"], 1)
        for photo in context["photos"]:
            self.assertTrue(photo.get("storage_key"))
            self.assertIsNone(photo.get("generated_url"))

    def test_03_cover_first(self):
        media = get_property_media_for_generation(self._property(), limit=5)
        self.assertTrue(media[0]["is_cover"])
        context = build_property_marketing_context(self._property())
        self.assertEqual(context["cover_id"], media[0]["id"])
        self.assertEqual(context["photos"][0]["id"], media[0]["id"])

    def test_04_max_five_photos(self):
        media = get_property_media_for_generation(self._property(), limit=5)
        self.assertLessEqual(len(media), PHOTO_LIMIT)
        self.assertEqual(len(media), 5)
        context = build_property_marketing_context(self._property())
        self.assertEqual(context["photo_count"], 5)

    def test_05_agent_branding_from_property(self):
        context = build_property_marketing_context(self._property())
        agent = context["agent"]
        self.assertEqual(agent["agent_id"], self.agent_id)
        self.assertIn("José", agent["name"])
        self.assertTrue(agent["has_photo"])
        self.assertTrue(agent["photo_path"])

    def test_06_other_agent_not_leaked(self):
        context = build_property_marketing_context(self._property())
        self.assertNotEqual(context["agent"]["agent_id"], self.other_agent_id)
        self.assertNotIn("Otro", context["agent"]["name"])
        other = get_property_record(self.other_prop, self.org)
        other_ctx = build_property_marketing_context(other)
        self.assertEqual(other_ctx["facts"]["agent_id"], self.other_agent_id)

    def test_07_no_clients_data(self):
        context = build_property_marketing_context(self._property())
        blob = json.dumps(context).lower()
        self.assertNotIn("clientsdata", blob)
        self.assertNotIn("clients_data", blob)
        for key in context["facts"]:
            self.assertNotIn(key.lower(), FORBIDDEN_FACT_KEYS)

    def test_08_no_documents(self):
        context = build_property_marketing_context(self._property())
        facts = context["facts"]
        self.assertNotIn("documents", facts)
        self.assertNotIn("commission", facts)
        self.assertNotIn("clientsData", facts)
        snapshot = json.dumps(context_to_snapshot(context)["facts"]).lower()
        self.assertNotIn("document", snapshot)
        self.assertNotIn('"commission"', snapshot)

    def test_09_story_dimensions(self):
        result = generate_marketing_proposals(
            self.org, self._user(self.agent_user_id), property_id=self.property_id, form=self._form()
        )
        path = resolve_asset_file(result["assets"][0])
        image = Image.open(path)
        self.assertEqual(image.size, FORMAT_SIZES["story"])

    def test_10_post_dimensions(self):
        result = generate_marketing_proposals(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            form=self._form(format="post"),
        )
        image = Image.open(resolve_asset_file(result["assets"][0]))
        self.assertEqual(image.size, FORMAT_SIZES["post"])

    def test_11_status_dimensions(self):
        result = generate_marketing_proposals(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            form=self._form(format="status"),
        )
        image = Image.open(resolve_asset_file(result["assets"][0]))
        self.assertEqual(image.size, FORMAT_SIZES["status"])

    def test_12_three_variants(self):
        result = generate_marketing_proposals(
            self.org, self._user(self.agent_user_id), property_id=self.property_id, form=self._form()
        )
        templates = [item["template"] for item in result["assets"]]
        self.assertEqual(templates, ["editorial", "visual", "minimal"])
        self.assertEqual(len({item["id"] for item in result["assets"]}), 3)

    def test_13_snapshot_immutable(self):
        result = generate_marketing_proposals(
            self.org, self._user(self.agent_user_id), property_id=self.property_id, form=self._form()
        )
        asset = result["assets"][0]
        old_price = asset["property_snapshot"]["facts"]["listing_price"]
        row = self._property()
        update_property(
            row["id"],
            row["address"],
            row["jurisdiction"],
            self.org,
            agent_id=row["agent_id"],
            listing_price=85000,
        )
        reloaded = get_marketing_asset(asset["id"], self.org)
        self.assertEqual(reloaded["property_snapshot"]["facts"]["listing_price"], old_price)
        self.assertEqual(old_price, 90000)
        live = get_property_record(self.property_id, self.org)
        self.assertEqual(live["listing_price"], 85000)
        update_property(
            row["id"],
            row["address"],
            row["jurisdiction"],
            self.org,
            agent_id=row["agent_id"],
            listing_price=90000,
        )

    def test_14_no_invented_facts(self):
        context = build_property_marketing_context(self._property())
        cleaned = _sanitize_ai_copy(
            {
                "headline": "Vista al río en Victoria",
                "subheadline": "La mejor propiedad de Argentina",
                "description": "3 dormitorios con pileta olímpica",
                "cta": "Escribime",
                "caption": "Vista al río",
                "hashtags": ["#lujoextremo", "#Victoria"],
            },
            context,
        )
        self.assertNotIn("Vista al río", cleaned["headline"])
        self.assertNotIn("mejor propiedad", cleaned["subheadline"].lower())
        copy = generate_marketing_copy(context, tone="professional")
        self.assertLessEqual(len(copy["hashtags"]), 6)
        self.assertNotIn("90.000", copy["headline"])

    def test_15_image_failure_safe(self):
        context = build_property_marketing_context(self._property())
        context["photos"] = [
            {"storage_key": "missing/nope.jpg", "original_url": None},
            context["photos"][0],
        ]
        payload, size = render_marketing_image(
            context,
            generate_marketing_copy(context),
            fmt="story",
            template="editorial",
            style="elegant",
            options={"show_price": True, "include_agent": True, "show_agent_photo": True},
        )
        self.assertGreater(len(payload), 1000)
        self.assertEqual(size, FORMAT_SIZES["story"])

    def test_16_missing_agent_photo_safe(self):
        context = build_property_marketing_context(self._property())
        context["agent"]["photo_path"] = None
        context["agent"]["has_photo"] = False
        payload, _size = render_marketing_image(
            context,
            generate_marketing_copy(context),
            fmt="story",
            template="editorial",
            style="elegant",
            options={"show_price": True, "include_agent": True, "show_agent_photo": True},
        )
        self.assertGreater(len(payload), 1000)

    def test_17_png_generation(self):
        result = generate_marketing_proposals(
            self.org, self._user(self.agent_user_id), property_id=self.property_id, form=self._form()
        )
        path = resolve_asset_file(result["assets"][0])
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".png")
        self.assertFalse(str(result["assets"][0]["storage_key"]).startswith("data:"))

    def test_18_permissions(self):
        result = generate_marketing_proposals(
            self.org, self._user(self.agent_user_id), property_id=self.property_id, form=self._form()
        )
        asset = result["assets"][0]
        self.assertTrue(can_view_asset(self._user(self.agent_user_id), asset))
        self.assertTrue(can_view_asset(self._user(self.admin_id), asset))
        self.assertFalse(can_view_asset(self._user(self.other_user_id), asset))
        client = app.test_client()
        guest = client.get("/marketing")
        self.assertEqual(guest.status_code, 403)
        with client.session_transaction() as sess:
            sess["user_id"] = self.other_user_id
        denied = client.get(f"/marketing/assets/{asset['id']}")
        self.assertEqual(denied.status_code, 403)
        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user_id
        ok = client.get("/marketing")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("Crear contenido".encode("utf-8"), ok.data)

    def test_19_jrh_property_context(self):
        self.assertEqual(detect_marketing_content("creame una historia de Santamarina"), "story")
        parsed = classify_intent("creame una historia de Santamarina")
        self.assertEqual(parsed["intent"], START_MARKETING_CONTENT)
        self.assertGreaterEqual(parsed["confidence"], 0.96)
        self.assertNotEqual(parsed["intent"], CREATE_TASK)
        with_context = classify_intent(
            "haceme un post de esta propiedad",
            context={"last_entity": {"kind": "property", "id": self.property_id}},
        )
        self.assertEqual(with_context["intent"], START_MARKETING_CONTENT)
        self.assertEqual(with_context["entities"].get("previous_kind"), "property")
        self.assertEqual(with_context["entities"].get("previous_id"), self.property_id)

    def test_20_mobile_render_and_qa(self):
        QA_DIR.mkdir(parents=True, exist_ok=True)
        for fmt in ("story", "post", "status"):
            result = generate_marketing_proposals(
                self.org,
                self._user(self.agent_user_id),
                property_id=self.property_id,
                form=self._form(format=fmt),
            )
            expected = FORMAT_SIZES[fmt]
            for asset in result["assets"]:
                path = resolve_asset_file(asset)
                with Image.open(path) as image:
                    self.assertEqual(image.size, expected)
                dest = QA_DIR / f"{fmt}_{asset['template']}.png"
                dest.write_bytes(path.read_bytes())
        self.assertTrue((QA_DIR / "story_editorial.png").is_file())
        self.assertTrue((QA_DIR / "post_visual.png").is_file())
        self.assertTrue((QA_DIR / "status_minimal.png").is_file())

    def test_21_private_price_default_hidden(self):
        connection = get_connection()
        connection.execute(
            "UPDATE properties SET external_metadata_json = ? WHERE id = ? AND organization_id = ?",
            (json.dumps({"external_price_exposure": "private"}), self.property_id, self.org),
        )
        connection.commit()
        connection.close()
        context = build_property_marketing_context(self._property())
        self.assertTrue(context["facts"]["price_policy"]["private"])
        self.assertFalse(context["facts"]["price_policy"]["default_show"])
        connection = get_connection()
        connection.execute(
            "UPDATE properties SET external_metadata_json = NULL WHERE id = ? AND organization_id = ?",
            (self.property_id, self.org),
        )
        connection.commit()
        connection.close()

    def test_22_snapshot_helper_keeps_facts(self):
        context = build_property_marketing_context(self._property())
        snapshot = context_to_snapshot(context)
        self.assertEqual(snapshot["facts"]["listing_price"], 90000)
        self.assertEqual(snapshot["photo_count"], 5)


if __name__ == "__main__":
    unittest.main()
