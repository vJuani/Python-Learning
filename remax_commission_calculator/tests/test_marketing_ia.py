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
from modules.marketing_art_director import plan_item
from modules.marketing_copy import _sanitize_ai_copy, generate_marketing_copy
from modules.marketing_image_provider import MarketingImageError, MockMarketingImageProvider
from modules.marketing_renderer import FORMAT_SIZES, render_marketing_image
from modules.marketing_photo_selector import select_photos_for_item
from modules.marketing_request import DEFAULT_PROMPT, expand_items, parse_marketing_request
from modules.marketing_service import (
    asset_download_name,
    can_view_asset,
    cleanup_expired_marketing_assets,
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
        self.assertEqual(parsed["copy_density"], "low")
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
        original = MockMarketingImageProvider.generate_background

        def flaky(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise MarketingImageError("forced")
            return original(self, **kwargs)

        MockMarketingImageProvider.generate_background = flaky
        try:
            result = start_marketing_batch(
                self.org,
                self._user(self.agent_user_id),
                property_id=self.property_id,
                prompt="Haceme 3 historias diferentes.",
            )
        finally:
            MockMarketingImageProvider.generate_background = original
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
        self.assertIn("¿Qué querés que JRH haga?".encode("utf-8"), ok.data)
        self.assertNotIn("Elegí el estilo".encode("utf-8"), ok.data)
        self.assertNotIn("Historial".encode("utf-8"), ok.data)
        create = client.get(f"/marketing/new?property_id={self.property_id}")
        self.assertEqual(create.status_code, 200)
        self.assertIn("Santamarina 1335".encode("utf-8"), create.data)
        self.assertIn("Generar con IA".encode("utf-8"), create.data)
        self.assertNotIn(b'name="format"', create.data)
        self.assertNotIn(b'name="style"', create.data)
        home = client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Crear contenido con IA".encode("utf-8"), home.data)
        self.assertNotIn(b">Marketing</span>", home.data)
        detail = client.get(f"/properties/{self.property_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Crear con JRH IA".encode("utf-8"), detail.data)

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
        original = MockMarketingImageProvider.generate_background
        calls = {"n": 0}

        def flaky(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise MarketingImageError("forced")
            return original(self, **kwargs)

        MockMarketingImageProvider.generate_background = flaky
        try:
            result = start_marketing_batch(
                self.org,
                self._user(self.agent_user_id),
                property_id=self.property_id,
                prompt="Haceme 3 historias diferentes.",
            )
        finally:
            MockMarketingImageProvider.generate_background = original
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


if __name__ == "__main__":
    unittest.main()
