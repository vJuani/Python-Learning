"""FASE 6A — JRH Marketing IA. Isolated temp DB. No live RedREMAX HTTP."""

from __future__ import annotations

import io
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

from modules.agent_branding import get_agent_presentation_asset
from modules.agent_photo import resolve_agent_photo_path
from modules.auth import ROLE_ADMIN, ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.database.agents_repository import get_agent_record, update_agent_profile_photo
from modules.database.connection import get_connection
from modules.database.marketing_repository import get_marketing_asset, update_marketing_asset
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
from jinja2 import Environment
from modules.marketing_art_director import STORY_DIRECTIONS, plan_item
from modules.marketing_copy import _sanitize_ai_copy, generate_marketing_copy, summarize_listing_copy
from modules.marketing_composer import compose_marketing_image
from modules.marketing_image_provider import MarketingImageError, MockMarketingImageProvider, get_marketing_image_model
from modules.openai_image_service import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    build_marketing_image_prompt,
    generate_marketing_images,
    resolve_generation_plan,
)
from modules.marketing_qa import resolve_qa_file, run_raw_story_qa
from modules.marketing_image_provider import sha256_bytes
from modules.marketing_quality import validate_creative
from modules.marketing_visual_spec import (
    EDITORIAL_PREMIUM,
    LUXURY_MINIMAL,
    MODERN_COMMERCIAL,
    SAFE_AREA,
    STYLE_BRIEFS,
    STYLE_REFERENCE_LABEL,
    approved_style_path,
    build_visual_brief,
    normalize_style,
)
from modules.marketing_references import collect_reference_images
from modules.marketing_renderer import FORMAT_SIZES, fit_contain_safe, render_marketing_image
from modules.marketing_photo_selector import select_photos_for_item
from modules.marketing_request import DEFAULT_PROMPT, agent_presentation_config, expand_items, parse_marketing_request
from modules.marketing_service import (
    asset_download_name,
    can_view_asset,
    cleanup_expired_marketing_assets,
    discard_marketing_asset,
    ensure_json_serializable,
    generate_marketing_proposals,
    resolve_asset_file,
    start_marketing_batch,
    vary_marketing_asset,
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
            "prompt": "Haceme 3 historias, todas diferentes, premium, sin descripción larga y usando mi foto.",
            "format": "story",
            "show_price": "1",
            "include_agent": "1",
        }
        data.update(overrides)
        return data

    def _pack_prompt(self):
        return (
            "Haceme:\n"
            "3 opciones de historia de Instagram/WhatsApp,\n"
            "3 posts de Instagram,\n"
            "3 flyers.\n"
            "Quiero que los 9 sean diferentes.\n"
            "No pongas descripciones largas.\n"
            "Usá las fotos reales de la propiedad y mi foto.\n"
            "Que se vean premium."
        )

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
        presentation = get_agent_presentation_asset(self.agent_id, self.org, agent_login_only=True)
        acm_path = resolve_agent_photo_path(get_agent_record(self.agent_id, self.org))
        self.assertEqual(agent["photo_path"], str(acm_path))
        self.assertEqual(presentation["photo_path"], str(acm_path))

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

    def test_10_post_and_flyer_dimensions(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 1 post y 1 flyer, diferentes y premium.",
        )
        formats = {item["format"]: item for item in result["assets"]}
        self.assertEqual(Image.open(resolve_asset_file(formats["post"])).size, FORMAT_SIZES["post"])
        self.assertEqual(Image.open(resolve_asset_file(formats["flyer"])).size, FORMAT_SIZES["flyer"])

    def test_11_parser_pack_and_variation(self):
        parsed = parse_marketing_request(self._pack_prompt())
        self.assertEqual(parsed["story_count"], 3)
        self.assertEqual(parsed["post_count"], 3)
        self.assertEqual(parsed["flyer_count"], 3)
        self.assertTrue(parsed["with_agent"])
        self.assertEqual(parsed["copy_density"], "very_low")
        vary = parse_marketing_request("más minimalista", variation=True)
        self.assertEqual(vary["story_count"], 0)
        self.assertEqual(vary["post_count"], 0)
        self.assertEqual(vary["flyer_count"], 0)
        no_photo = parse_marketing_request("Haceme 3 historias sin mi foto")
        self.assertTrue(no_photo["with_agent"])
        self.assertFalse(no_photo["with_agent_photo"])
        no_agent = parse_marketing_request("Haceme 3 historias sin agente")
        self.assertFalse(no_agent["with_agent"])
        self.assertEqual(parse_marketing_request("haceme 3 estados de WhatsApp")["story_count"], 3)
        self.assertEqual(parse_marketing_request("quiero 2 publicaciones")["post_count"], 2)
        self.assertEqual(parse_marketing_request("armame 1 volante")["flyer_count"], 1)
        vague = parse_marketing_request("haceme algo lindo")
        self.assertEqual(vague["story_count"], 1)
        self.assertEqual(vague["post_count"], 1)
        self.assertEqual(vague["flyer_count"], 1)
        huge = parse_marketing_request("haceme 50 historias")
        self.assertEqual(huge["story_count"], 12)
        self.assertEqual(len(expand_items(huge)), 12)

    def test_12_unique_directions_and_order(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt=self._pack_prompt(),
        )
        self.assertEqual(len(result["assets"]), 9)
        formats = [item["format"] for item in result["assets"]]
        self.assertEqual(formats, ["story"] * 3 + ["post"] * 3 + ["flyer"] * 3)
        for group in result["groups"]:
            directions = [
                (item.get("options") or {}).get("visual_direction") for item in group["assets"]
            ]
            self.assertEqual(len(directions), len(set(directions)), directions)
        self.assertEqual([group["format"] for group in result["groups"]], ["story", "post", "flyer"])

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

    def test_15_image_failure_isolated(self):
        calls = {"n": 0}
        original = MockMarketingImageProvider.generate_creative

        def flaky(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise MarketingImageError("forced")
            return original(self, **kwargs)

        MockMarketingImageProvider.generate_creative = flaky
        try:
            result = start_marketing_batch(
                self.org,
                self._user(self.agent_user_id),
                property_id=self.property_id,
                prompt="Haceme 3 historias diferentes.",
            )
        finally:
            MockMarketingImageProvider.generate_creative = original
        statuses = [item["pipeline_status"] for item in result["assets"]]
        self.assertIn("failed", statuses)
        self.assertIn("completed", statuses)
        self.assertEqual(len(result["assets"]), 3)
        failed = next(item for item in result["assets"] if item["failed"])
        self.assertIsNone(resolve_asset_file(failed))

    def test_16_missing_agent_photo_safe(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 1 historia sin mi foto.",
        )
        path = resolve_asset_file(result["assets"][0])
        self.assertTrue(path.is_file())
        self.assertFalse((result["assets"][0].get("options") or {}).get("show_agent_photo"))

    def test_17_png_generation_and_facts(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 1 flyer premium usando mi foto.",
        )
        asset = result["assets"][0]
        path = resolve_asset_file(asset)
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".png")
        self.assertFalse(str(asset["storage_key"]).startswith("data:"))
        facts = asset["property_snapshot"]["facts"]
        self.assertEqual(facts["title"], "Santamarina 1335")
        self.assertEqual(facts["price_label"], "USD 90.000")
        self.assertEqual((asset.get("copy_snapshot") or {}).get("description"), "")

    def test_18_permissions_and_one_prompt_ui(self):
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
        ok = client.get("/marketing", follow_redirects=True)
        self.assertEqual(ok.status_code, 200)
        self.assertIn("JRH IA".encode("utf-8"), ok.data)
        self.assertIn("Tipo de pieza".encode("utf-8"), ok.data)
        self.assertNotIn("Historial".encode("utf-8"), ok.data)
        create = client.get(f"/marketing/new?property_id={self.property_id}")
        self.assertEqual(create.status_code, 200)
        self.assertIn("Santamarina 1335".encode("utf-8"), create.data)
        self.assertIn("Generar con IA".encode("utf-8"), create.data)
        self.assertIn(b'name="piece_type"', create.data)
        self.assertIn(b'name="style"', create.data)
        self.assertNotIn(b"<textarea", create.data)
        home = client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Crear pieza de marketing".encode("utf-8"), home.data)
        self.assertNotIn(b">Marketing</span>", home.data)
        detail = client.get(f"/properties/{self.property_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Crear con JRH IA".encode("utf-8"), detail.data)
        self.assertIn(b"data-mkt-open", detail.data)
        self.assertIn(b"data-mkt-dialog", detail.data)

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

    def test_20_qa_nine_outputs(self):
        QA_DIR.mkdir(parents=True, exist_ok=True)
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt=self._pack_prompt(),
        )
        self.assertEqual(len(result["assets"]), 9)
        names = []
        for index, asset in enumerate(result["assets"], start=1):
            path = resolve_asset_file(asset)
            self.assertIsNotNone(path)
            expected = FORMAT_SIZES[asset["format"]]
            with Image.open(path) as image:
                self.assertEqual(image.size, expected)
            dest = QA_DIR / f"{index:02d}_{asset['format']}_{asset['template']}.png"
            dest.write_bytes(path.read_bytes())
            names.append(dest.name)
        self.assertEqual(len(names), 9)
        self.assertTrue(any(name.startswith("01_story") for name in names))
        self.assertTrue(any(name.startswith("04_post") for name in names))
        self.assertTrue(any(name.startswith("07_flyer") for name in names))

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
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 1 post premium.",
        )
        self.assertFalse((result["assets"][0].get("options") or {}).get("show_price"))
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

    def test_23_idempotency_and_vary(self):
        token = "qa-idem-santamarina"
        first = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 2 posts diferentes.",
            idempotency_key=token,
        )
        second = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 2 posts diferentes.",
            idempotency_key=token,
        )
        self.assertEqual(first["generation_id"], second["generation_id"])
        self.assertEqual(len(first["assets"]), len(second["assets"]))
        varied = vary_marketing_asset(
            self.org,
            self._user(self.agent_user_id),
            first["assets"][0]["id"],
            "más minimalista",
        )
        self.assertEqual(len(varied["assets"]), 1)
        self.assertEqual(varied["assets"][0]["format"], "post")
        self.assertNotEqual(varied["generation_id"], first["generation_id"])

    def test_24_art_director_diversity(self):
        context = build_property_marketing_context(self._property())
        used = set()
        request = parse_marketing_request(DEFAULT_PROMPT)
        planned = [
            plan_item(context, request, fmt="story", index=index, used_directions=used)
            for index in range(1, 4)
        ]
        directions = [item["visual_direction"] for item in planned]
        self.assertEqual(len(set(directions)), 3)

    def test_25_dict_items_method_is_not_iterable(self):
        group = {
            "format": "story",
            "label": "Historias",
            "items": [{"id": 1}, {"id": 2}],
        }
        self.assertTrue(callable(group.items))
        with self.assertRaises(TypeError) as caught:
            for _asset in group.items:
                pass
        self.assertIn("not iterable", str(caught.exception))
        materialized = list(group.items())
        self.assertEqual(len(materialized), 3)
        values = list(group.values())
        self.assertEqual(values[-1], [{"id": 1}, {"id": 2}])

    def test_26_generation_page_does_not_iterate_dict_items(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 1 historia y 1 post.",
        )
        template = Environment().from_string(
            "{% for group in view.groups %}{% for asset in group.assets %}{{ asset.id }}{% endfor %}{% endfor %}"
        )
        rendered = template.render(view=result)
        self.assertTrue(rendered)
        broken = Environment().from_string(
            "{% for group in view.groups %}{% for asset in group.items %}{{ asset }}{% endfor %}{% endfor %}"
        )
        with self.assertRaises(TypeError):
            broken.render(view={"groups": [{"format": "story", "items": [1]}]})
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user_id
        page = client.get(f"/marketing/generation/{result['generation_id']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"mkt-proposal", page.data)
        self.assertIn(b"Historias", page.data)
        self.assertTrue(result["assets"][0].get("ready"))
        preview = client.get(f"/marketing/assets/{result['assets'][0]['id']}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "image/png")

    def test_27_art_direction_and_metadata_are_json_serializable(self):
        context = build_property_marketing_context(self._property())
        request = parse_marketing_request(DEFAULT_PROMPT)
        art = plan_item(context, request, fmt="story", index=1, used_directions=set())
        payload = ensure_json_serializable(
            {
                "art_direction": art,
                "copy_snapshot": {"headline": art.get("headline"), "hashtags": []},
                "property_snapshot": context_to_snapshot(context),
            },
            path="metadata",
        )
        json.dumps(payload)
        with self.assertRaises(TypeError):
            ensure_json_serializable({"features": {}.items}, path="features")
        with self.assertRaises(TypeError):
            ensure_json_serializable({"values": {}.values}, path="values")

    def test_28_failed_item_does_not_500_generation_page(self):
        original = MockMarketingImageProvider.generate_creative
        calls = {"n": 0}

        def flaky(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise MarketingImageError("forced")
            return original(self, **kwargs)

        MockMarketingImageProvider.generate_creative = flaky
        try:
            result = start_marketing_batch(
                self.org,
                self._user(self.agent_user_id),
                property_id=self.property_id,
                prompt="Haceme 3 historias diferentes.",
            )
        finally:
            MockMarketingImageProvider.generate_creative = original
        self.assertEqual(len(result["assets"]), 3)
        self.assertTrue(any(item.get("failed") for item in result["assets"]))
        self.assertTrue(any(item.get("ready") for item in result["assets"]))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user_id
        page = client.get(f"/marketing/generation/{result['generation_id']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("No pude generar esta propuesta.".encode("utf-8"), page.data)
        self.assertIn("Reintentar".encode("utf-8"), page.data)
        self.assertIn(b"mkt-proposal", page.data)


    def test_29_temporary_assets_filenames_and_cleanup(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 1 historia premium usando mi foto.",
        )
        asset = result["assets"][0]
        self.assertTrue(asset.get("temporary"))
        self.assertTrue(asset.get("expires_at"))
        name = asset_download_name(asset)
        self.assertTrue(name.startswith("santamarina-1335-story-"))
        self.assertTrue(name.endswith(".png"))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user_id
        download = client.get(f"/marketing/assets/{asset['id']}/download.png")
        self.assertEqual(download.status_code, 200)
        self.assertIn("santamarina-1335-story-", download.headers.get("Content-Disposition", ""))
        composer = client.get(
            f"/marketing/new?property_id={self.property_id}&generation_id={result['generation_id']}"
        )
        self.assertEqual(composer.status_code, 200)
        self.assertIn(b"mkt-proposal", composer.data)
        self.assertNotIn(b"BRIGHT_GEOMETRIC", composer.data)
        update_marketing_asset(asset["id"], self.org, expires_at="2000-01-01T00:00:00")
        removed = cleanup_expired_marketing_assets(self.org)
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(get_marketing_asset(asset["id"], self.org))

    def test_30_photo_selector_and_private_price_lock(self):
        context = build_property_marketing_context(self._property())
        first = select_photos_for_item(context["photos"], fmt="story", index=0)
        second = select_photos_for_item(context["photos"], fmt="story", index=1)
        self.assertTrue(first)
        self.assertEqual(first[0]["id"], context["photos"][0]["id"])
        self.assertNotEqual([item["id"] for item in first], [item["id"] for item in second])
        connection = get_connection()
        connection.execute(
            "UPDATE properties SET external_metadata_json = ? WHERE id = ? AND organization_id = ?",
            (json.dumps({"external_price_exposure": "private"}), self.property_id, self.org),
        )
        connection.commit()
        connection.close()
        locked = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme 1 post y poné el precio.",
        )
        self.assertFalse((locked["assets"][0].get("options") or {}).get("show_price"))
        connection = get_connection()
        connection.execute(
            "UPDATE properties SET external_metadata_json = NULL WHERE id = ? AND organization_id = ?",
            (self.property_id, self.org),
        )
        connection.commit()
        connection.close()

    def test_31_agent_data_prompt_sends_photo(self):
        parsed = parse_marketing_request("Haceme una historia premium, subilo con mis datos y mi foto.")
        config = agent_presentation_config(parsed["prompt"], parsed)
        self.assertTrue(config["show_agent"])
        self.assertTrue(config["show_photo"])
        self.assertTrue(config["show_name"])
        MockMarketingImageProvider.last_call = None
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme una historia premium, con mis datos y mi foto, sin descripción larga.",
        )
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["format"], "story")
        options = result["assets"][0].get("options") or {}
        self.assertTrue(options.get("include_agent"))
        self.assertTrue(options.get("show_agent_photo"))
        self.assertTrue(options.get("agent_photo_loaded"))
        self.assertTrue(options.get("agent_photo_sent_to_provider"))
        self.assertTrue(options.get("agent_photo_composited"))
        self.assertTrue((MockMarketingImageProvider.last_call or {}).get("agent_photo_sent_to_provider"))
        self.assertGreaterEqual((MockMarketingImageProvider.last_call or {}).get("property_refs") or 0, 1)
        path = resolve_asset_file(result["assets"][0])
        self.assertTrue(path and path.is_file())

    def test_32_no_agent_when_requested(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme un post sin mi foto ni mis datos.",
        )
        options = result["assets"][0].get("options") or {}
        self.assertFalse(options.get("include_agent"))
        self.assertFalse(options.get("show_agent_photo"))
        self.assertFalse(options.get("agent_photo_sent_to_provider"))

    def test_33_composer_layout_is_centered_workspace(self):
        css = Path(__file__).resolve().parent.parent.joinpath("static", "css", "marketing.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: none", css)
        self.assertIn("max-width: 72rem", css)
        self.assertIn(".mkt-prompt .btn-primary", css)
        self.assertIn("display: block !important", css)
        self.assertEqual(
            get_marketing_image_model(),
            os.environ.get("OPENAI_IMAGE_MODEL")
            or os.environ.get("MARKETING_IMAGE_MODEL")
            or "gpt-image-1",
        )
        context = build_property_marketing_context(self._property())
        packed = collect_reference_images(
            context,
            {"include_agent": True, "show_agent_photo": True},
        )
        self.assertTrue(packed["agent_photo_loaded"])
        self.assertGreaterEqual(packed["property_photo_count"], 1)
        self.assertTrue(any(item["role"] == "agent" for item in packed["references"]))

    def test_34_explicit_one_story_not_pack(self):
        parsed = parse_marketing_request("haceme UNA historia con mi foto y mis datos")
        self.assertTrue(parsed["explicit_formats"])
        self.assertEqual(parsed["story_count"], 1)
        self.assertEqual(parsed["post_count"], 0)
        self.assertEqual(parsed["flyer_count"], 0)
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme una historia premium con mi foto y mis datos, sin descripción larga.",
        )
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["format"], "story")
        self.assertTrue(result.get("single"))
        self.assertEqual([group["format"] for group in result["groups"]], ["story"])

    def test_35_explicit_two_posts_and_one_flyer(self):
        posts = parse_marketing_request("haceme dos posts")
        self.assertEqual(posts["story_count"], 0)
        self.assertEqual(posts["post_count"], 2)
        self.assertEqual(posts["flyer_count"], 0)
        flyer = parse_marketing_request("haceme un flyer")
        self.assertEqual(flyer["story_count"], 0)
        self.assertEqual(flyer["post_count"], 0)
        self.assertEqual(flyer["flyer_count"], 1)
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="haceme dos posts",
        )
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual({item["format"] for item in result["assets"]}, {"post"})

    def test_36_con_mis_datos_enables_photo_and_contact(self):
        config = agent_presentation_config("subilo con mis datos")
        self.assertTrue(config["show_agent"])
        self.assertTrue(config["show_photo"])
        self.assertTrue(config["show_name"])
        self.assertTrue(config["show_phone"])
        self.assertTrue(config["show_email"])
        photo = agent_presentation_config("usá mi foto")
        self.assertTrue(photo["show_photo"])
        no_face = agent_presentation_config("Haceme una historia sin mi foto pero con mis datos")
        self.assertTrue(no_face["show_agent"])
        self.assertFalse(no_face["show_photo"])
        self.assertTrue(no_face["show_name"])
        only_listing = agent_presentation_config("Haceme una historia sin mis datos")
        self.assertFalse(only_listing["show_agent"])
        self.assertFalse(only_listing["show_photo"])
        self.assertFalse(only_listing["show_name"])

    def test_37_agent_photo_is_property_agent_not_current_user(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.admin_id),
            property_id=self.property_id,
            prompt="Haceme una historia con mi foto y mis datos",
        )
        snapshot = result["assets"][0].get("agent_branding_snapshot") or {}
        self.assertEqual(snapshot.get("agent_id"), self.agent_id)
        self.assertEqual(snapshot.get("email"), "jose.barreiro@example.com")
        self.assertNotEqual(snapshot.get("email"), "mkt.admin@example.com")
        self.assertTrue((result["assets"][0].get("options") or {}).get("agent_photo_composited"))

    def test_38_quality_detects_missing_composited_agent(self):
        verdict = validate_creative(
            _quality_png(),
            size=FORMAT_SIZES["story"],
            options={"include_agent": True, "show_agent_photo": True, "agent_photo_loaded": True},
            references=[{"role": "property_hero"}, {"role": "agent"}],
            agent_photo_sent=True,
            agent_photo_composited=False,
        )
        self.assertFalse(verdict["ok"])
        self.assertIn("agent_not_composited", verdict["reasons"])

    def test_39_single_story_result_ui_hides_empty_format_tabs(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme una historia premium con mi foto y mis datos, sin descripción larga.",
        )
        self.assertTrue(result["single"])
        html = Path(__file__).resolve().parent.parent.joinpath("templates", "marketing", "new.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("view.single", html)
        self.assertIn("marketing_ready_single_", html)
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user_id
        page = client.get(f"/marketing/generation/{result['generation_id']}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Tu historia está lista.".encode("utf-8"), page.data)
        self.assertNotIn(b"Posts (0)", page.data)
        self.assertNotIn(b"Flyers (0)", page.data)
        self.assertNotIn(b"mkt-tabs", page.data)

    def test_40_no_agent_photo_when_data_only(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt="Haceme una historia sin mi foto pero con mis datos",
        )
        options = result["assets"][0].get("options") or {}
        self.assertTrue(options.get("include_agent"))
        self.assertFalse(options.get("show_agent_photo"))
        self.assertFalse(options.get("agent_photo_sent_to_provider"))
        self.assertFalse(options.get("agent_photo_composited"))
        snapshot = result["assets"][0].get("agent_branding_snapshot") or {}
        self.assertIn("José", snapshot.get("name") or "")

    def test_41_canonical_jrh_layout_matches_approved_model(self):
        context = build_property_marketing_context(self._property())
        with_photo = compose_marketing_image(
            context,
            {"cta": "Consultame"},
            fmt="story",
            options={"include_agent": True, "show_agent_photo": True, "show_name": True, "show_price": True},
        )
        png, size, meta = with_photo
        self.assertEqual(size, FORMAT_SIZES["story"])
        self.assertTrue(meta.get("agent_photo_composited"))
        image = Image.open(io.BytesIO(png)).convert("RGB")
        sample = image.resize((24, 40))
        dark = sum(1 for pixel in sample.getdata() if pixel[2] > pixel[0] and pixel[0] < 80)
        self.assertGreater(dark, 80)
        without = compose_marketing_image(
            context,
            {"cta": "Consultame"},
            fmt="story",
            options={"include_agent": True, "show_agent_photo": False, "show_name": True, "show_price": True},
        )
        self.assertFalse(without[2].get("agent_photo_composited"))
        QA_DIR.mkdir(parents=True, exist_ok=True)
        (QA_DIR / "canonical_story_with_agent.png").write_bytes(png)
        (QA_DIR / "canonical_story_no_photo.png").write_bytes(without[0])

    def test_42_visual_spec_and_three_compositions(self):
        self.assertTrue(approved_style_path() and approved_style_path().is_file())
        self.assertIn("VISUAL STYLE REFERENCE ONLY", STYLE_REFERENCE_LABEL)
        self.assertEqual(len(STORY_DIRECTIONS), 3)
        self.assertEqual(len(set(STORY_DIRECTIONS)), 3)
        context = build_property_marketing_context(self._property())
        packed = collect_reference_images(
            context,
            {"include_agent": True, "show_agent_photo": True},
        )
        self.assertTrue(any(item["role"] == "style" for item in packed["references"]))
        self.assertTrue(any("VISUAL STYLE REFERENCE ONLY" in (item.get("label") or "") for item in packed["references"]))
        used = set()
        request = parse_marketing_request(
            "Haceme UNA historia de Instagram premium, con mi foto y mis datos. Quiero poco texto."
        )
        self.assertEqual(request["story_count"], 1)
        planned = [
            plan_item(context, request, fmt="story", index=index, used_directions=used)
            for index in range(1, 4)
        ]
        directions = [item["visual_direction"] for item in planned]
        self.assertEqual(len(set(directions)), 3)
        self.assertTrue(all(item.get("visual_brief") for item in planned))
        QA_DIR.mkdir(parents=True, exist_ok=True)
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            prompt=(
                "Haceme UNA historia de Instagram premium, con mi foto y mis datos. "
                "Quiero poco texto. Que tenga el nivel visual de la referencia aprobada."
            ),
        )
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["format"], "story")
        options = result["assets"][0].get("options") or {}
        self.assertTrue(options.get("agent_photo_composited"))
        path = resolve_asset_file(result["assets"][0])
        self.assertTrue(path and path.is_file())
        generated = path.read_bytes()
        (QA_DIR / "qa_one_story_approved_level.png").write_bytes(generated)
        verdict = validate_creative(
            generated,
            size=FORMAT_SIZES["story"],
            options=options,
            references=packed["references"],
            agent_photo_sent=True,
            agent_photo_composited=True,
        )
        self.assertTrue(verdict["ok"], verdict)
        reference = Image.open(approved_style_path()).convert("RGB")
        output = Image.open(io.BytesIO(generated)).convert("RGB")
        pair = Image.new("RGB", (1080 * 2 + 40, 1920), (10, 22, 51))
        pair.paste(reference.resize((1080, 1920), Image.Resampling.LANCZOS), (0, 0))
        pair.paste(output.resize((1080, 1920), Image.Resampling.LANCZOS), (1120, 0))
        pair.save(QA_DIR / "qa_reference_vs_story.png")
        brief = build_visual_brief("story", "editorial_navy")
        self.assertEqual(brief["text_density"], "very_low")
        self.assertEqual(brief["quality_target"], "approved_jrh_story")
        self.assertEqual(brief["safe_area"]["left"], 80)
        self.assertEqual(brief["safe_area"]["bottom"], 180)

    def test_43_openai_service_prompt_and_plan(self):
        self.assertEqual(DEFAULT_OPENAI_IMAGE_MODEL, "gpt-image-1")
        self.assertEqual(get_marketing_image_model(), "gpt-image-1")
        self.assertEqual(
            [item["format"] for item in resolve_generation_plan(["story"], count=1)],
            ["story"],
        )
        pack = resolve_generation_plan(["story"], quantity="pack")
        self.assertEqual(len(pack), 9)
        self.assertEqual([item["format"] for item in pack].count("story"), 3)
        self.assertEqual([item["format"] for item in pack].count("post"), 3)
        self.assertEqual([item["format"] for item in pack].count("flyer"), 3)
        context = build_property_marketing_context(self._property())
        prompt = build_marketing_image_prompt(
            context,
            "story",
            include_agent=True,
            include_price=True,
            style="premium",
            cta="Consultame",
        )
        self.assertIn("finished premium real-estate", prompt)
        self.assertIn("Santamarina", prompt)
        self.assertIn("REAL agent portrait", prompt)
        self.assertIn("STRICT SAFE AREA", prompt)
        self.assertIn("left 80px", prompt)
        self.assertIn("bottom 180px", prompt)
        self.assertIn("vertical captions", prompt)
        self.assertIn("ALLOWED COPY ONLY", prompt)
        self.assertIn(STYLE_BRIEFS[EDITORIAL_PREMIUM][:24], prompt)

    def test_44_structured_one_story_and_pack_counts(self):
        one = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            formats=["story"],
            count=1,
            include_agent=True,
            include_price=True,
            style="premium",
            request_text="con mi foto",
        )
        self.assertEqual(len(one["assets"]), 1)
        self.assertEqual(one["assets"][0]["format"], "story")
        self.assertTrue((one["assets"][0].get("options") or {}).get("agent_photo_sent_to_provider"))
        generated = generate_marketing_images(
            self._property(),
            formats=["story"],
            count=1,
            include_agent=True,
            include_price=True,
        )
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["format"], "story")
        self.assertTrue(generated[0]["png_bytes"])
        pack = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            formats=["pack"],
            quantity="pack",
            include_agent=True,
        )
        self.assertEqual(len(pack["assets"]), 9)

    def test_45_discard_removes_session_asset(self):
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            formats=["story"],
            count=1,
        )
        asset = result["assets"][0]
        path = resolve_asset_file(asset)
        self.assertTrue(path and path.is_file())
        discarded = discard_marketing_asset(
            self.org, self._user(self.agent_user_id), asset["id"]
        )
        self.assertEqual(discarded["property_id"], self.property_id)
        self.assertFalse(path.is_file())
        self.assertIsNone(get_marketing_asset(asset["id"], self.org))

    def test_46_raw_story_qa_skips_legacy_compositor(self):
        don_bosco = add_property(
            "Don Bosco 477",
            "Buenos Aires",
            self.org,
            agent_id=self.agent_id,
            property_type="apartment",
            listing_price=120000,
            listing_purpose="sale",
            listing_currency="USD",
            created_by_user_id=self.admin_id,
        )
        rel = f"organizations/{self.org}/properties/{don_bosco}/media/cover.jpg"
        _write_photo(_PRIVATE_ROOT / rel, (70, 82, 96), rooms=True)
        upsert_property_media(
            self.org,
            don_bosco,
            source="manual",
            external_media_id="don-bosco-cover",
            original_url=None,
            storage_key=rel,
            storage_strategy=STRATEGY_COPY,
            position=0,
            is_cover=True,
            content_type="image/jpeg",
        )
        report = run_raw_story_qa(
            self.org,
            self._user(self.agent_user_id),
            address="Don Bosco 477",
        )
        self.assertEqual(report["property_address"], "Don Bosco 477")
        self.assertTrue(report["hashes_match"])
        self.assertEqual(report["raw_sha256"], report["final_sha256"])
        self.assertEqual(report["post_process"], "none")
        self.assertIsNone(report["post_process_fn"])
        raw_path = resolve_qa_file(self.org, report["run_id"], "raw_openai_output.png")
        final_path = resolve_qa_file(self.org, report["run_id"], "final_output.png")
        self.assertTrue(raw_path and raw_path.is_file())
        self.assertTrue(final_path and final_path.is_file())
        self.assertEqual(sha256_bytes(raw_path.read_bytes()), sha256_bytes(final_path.read_bytes()))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = self.agent_user_id
        page = client.get("/marketing/qa/raw-story")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Raw OpenAI Story", page.data)

    def test_47_safe_area_copy_and_layout_validation(self):
        self.assertEqual(SAFE_AREA["story"], {"left": 80, "right": 80, "top": 120, "bottom": 180})
        self.assertEqual(SAFE_AREA["post"], {"left": 70, "right": 70, "top": 70, "bottom": 70})
        self.assertEqual(SAFE_AREA["flyer"], {"left": 80, "right": 80, "top": 80, "bottom": 80})
        self.assertEqual(normalize_style("premium"), EDITORIAL_PREMIUM)
        self.assertEqual(normalize_style("modern"), MODERN_COMMERCIAL)
        self.assertEqual(normalize_style("minimal"), LUXURY_MINIMAL)
        self.assertNotEqual(STYLE_BRIEFS[EDITORIAL_PREMIUM], STYLE_BRIEFS[MODERN_COMMERCIAL])
        self.assertNotEqual(STYLE_BRIEFS[MODERN_COMMERCIAL], STYLE_BRIEFS[LUXURY_MINIMAL])
        context = build_property_marketing_context(self._property())
        copy = summarize_listing_copy(context["facts"], context.get("agent"))
        self.assertEqual(copy["street"], "Santamarina 1335")
        self.assertIn("Victoria", copy["zone"])
        long_copy = summarize_listing_copy(
            {
                "title": "Avenida del Libertador General San Martín 4450 piso 12",
                "locality": "Vicente López",
                "jurisdiction": "Buenos Aires",
            }
        )
        self.assertLessEqual(len(long_copy["street"]), 32)
        clipped = Image.new("RGB", FORMAT_SIZES["story"], (18, 28, 48))
        draw = ImageDraw.Draw(clipped)
        draw.rectangle((120, 200, 960, 1400), fill=(90, 100, 120))
        for y in range(0, 1920, 4):
            draw.rectangle((0, y, 48, y + 2), fill=(255, 255, 255))
        for x in range(0, 1080, 4):
            draw.rectangle((x, 1860, x + 2, 1919), fill=(255, 255, 255))
        buffer = io.BytesIO()
        clipped.save(buffer, format="PNG")
        verdict = validate_creative(
            buffer.getvalue(),
            size=FORMAT_SIZES["story"],
            options={"layout_engine": "openai_images", "format": "story"},
            references=[{"role": "property_hero"}],
            fmt="story",
        )
        self.assertFalse(verdict["ok"])
        self.assertTrue(
            {"unsafe_edges", "text_clipped", "agent_clipped"} & set(verdict["reasons"]),
            verdict["reasons"],
        )
        marker = Image.new("RGB", (1024, 1536), (30, 40, 60))
        ImageDraw.Draw(marker).rectangle((0, 0, 40, 1536), fill=(255, 0, 0))
        fitted = fit_contain_safe(marker, FORMAT_SIZES["story"], "story")
        self.assertEqual(fitted.size, FORMAT_SIZES["story"])
        left_edge = fitted.crop((0, 0, 80, 1920))
        reds = sum(1 for pixel in left_edge.getdata() if pixel[0] > 200 and pixel[1] < 40)
        self.assertEqual(reds, 0)
        result = start_marketing_batch(
            self.org,
            self._user(self.agent_user_id),
            property_id=self.property_id,
            formats=["story"],
            count=1,
            include_agent=True,
            style="premium",
        )
        options = result["assets"][0].get("options") or {}
        self.assertGreaterEqual(options.get("quality_score") or 0, 70)
        self.assertFalse(
            {"unsafe_edges", "text_clipped", "agent_clipped"} & set(options.get("quality_reasons") or [])
        )


def _quality_png():
    buffer = __import__("io").BytesIO()
    image = Image.new("RGB", FORMAT_SIZES["story"], (18, 28, 48))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 900, 1400), fill=(80, 90, 110))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()

