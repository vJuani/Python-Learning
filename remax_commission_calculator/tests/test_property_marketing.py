"""Official property marketing templates: HTML/CSS + Playwright, no Pillow fallback."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from shutil import copyfile

from PIL import Image, ImageDraw

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ.setdefault("APP_ENV", "development")

from modules.config import BASE_DIR
from modules.marketing_context import MarketingError
from modules.marketing_photo_fit import CLEAN_GRID_MASTER, FULL_BLEED_MASTER
from modules.marketing_render_html import HTML_RENDERER
from modules.marketing_service import _render_local_visual
from modules.marketing_photo_selector import photo_scene_label, select_photos_for_item
from modules.property_marketing import (
    PACKAGED_BALLOON,
    TEMPLATE_CLEAN_GRID,
    TEMPLATE_LIFESTYLE_DARK,
    TEMPLATE_PREMIUM_HERO,
    PropertyMarketingError,
    render_property_marketing,
    select_property_template,
)


def _visible_html(html):
    import re
    text = str(html or "")
    start = text.find("<article")
    text = text[start:] if start >= 0 else text
    return re.sub(r'src="data:[^"]+"', 'src="data:"', text)


QA_DIR = BASE_DIR / "tmp" / "property_marketing_qa"
QA_ASSETS = QA_DIR / "assets"
# Untouched listing original (full facade). assets/hero.jpg is a crop of an old flyer screenshot.
QA_ORIGINAL_HERO = QA_DIR / "originals" / "hero_original.jpg"
V2_REFERENCE = (
    BASE_DIR / "static" / "marketing_style_references" / "modern_commercial_v2_reference.png"
)


def _ensure_qa_photo_assets():
    QA_ASSETS.mkdir(parents=True, exist_ok=True)
    wanted = {
        "hero.jpg": (560, 90, 1070, 420),
        "interior_2.jpg": (42, 672, 348, 812),
        "interior_3.jpg": (372, 672, 682, 812),
        "interior_4.jpg": (706, 672, 1036, 812),
    }
    missing = [name for name in wanted if not (QA_ASSETS / name).is_file()]
    if not missing:
        return QA_ASSETS
    if not V2_REFERENCE.is_file():
        raise FileNotFoundError("QA listing photos missing and v2 reference is not available")
    flyer = Image.open(V2_REFERENCE).crop((21, 26, 1101, 1376))
    for name, box in wanted.items():
        flyer.crop(box).convert("RGB").save(QA_ASSETS / name, quality=94)
    agent = flyer.crop((28, 1000, 236, 1288)).convert("RGBA")
    agent.save(QA_ASSETS / "agent.png")
    return QA_ASSETS


def _photo(path, source, label=""):
    del label
    path.parent.mkdir(parents=True, exist_ok=True)
    copyfile(source, path)
    return path


def _agent_png(path):
    source = _ensure_qa_photo_assets() / "agent.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        copyfile(source, path)
        return path
    image = Image.new("RGBA", (420, 620), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((110, 20, 310, 230), fill=(72, 48, 36, 255))
    draw.rectangle((90, 210, 330, 600), fill=(36, 64, 92, 255))
    image.save(path, "PNG")
    return path


def _fixture_context(*, photos=4, include_agent=True, bathrooms=2, instagram="@josebarreiro", email="jose.barreiro@example.com", legal=True):
    files = {}
    qa = _ensure_qa_photo_assets()
    sources = (
        QA_ORIGINAL_HERO if QA_ORIGINAL_HERO.is_file() else qa / "hero.jpg",
        qa / "interior_2.jpg",
        qa / "interior_3.jpg",
        qa / "interior_4.jpg",
    )
    labels = ("fachada", "living", "cocina", "bano")
    photo_rows = []
    for index in range(photos):
        name = f"photo_{index}.jpg"
        path = _photo(_PRIVATE_ROOT / name, sources[index % len(sources)], label=labels[index % len(labels)])
        files[name] = path
        photo_rows.append(
            {
                "id": index + 1,
                "label": labels[index % len(labels)],
                "is_cover": index == 0,
                "storage_key": name,
                "path": str(path),
            }
        )
    agent_path = _agent_png(_PRIVATE_ROOT / "agent.png")
    files["agent"] = agent_path
    facts = {
        "property_id": 1640,
        "organization_id": 7,
        "title": "Italia 1341",
        "neighborhood": "Rosario",
        "locality": "Santa Fe",
        "purpose": "sale",
        "kicker": "EN VENTA",
        "rooms": 4,
        "bedrooms": 3,
        "bathrooms": bathrooms,
        "total_m2": 131,
        "price_label": "USD 320.000",
        "brand_name": "RE/MAX Data House",
        "office_name": "RE/MAX Data House",
        "benefit_line": "Diseño, confort y una ubicación inmejorable.",
        "used_demo_fallback": False,
        "legal_complete": bool(legal),
    }
    if legal:
        facts["legal_broker_name"] = "Maria Eugenia Conti"
        facts["legal_broker_license"] = "C.I. Mat. Nº 1234"
        facts["legal_broker_college"] = "COCIR Santa Fe"
        facts["legal_footer_line"] = (
            "Martillero responsable: Maria Eugenia Conti | C.I. Mat. Nº 1234 | COCIR Santa Fe"
        )
    agent = None
    if include_agent:
        agent = {
            "name": "José Barreiro",
            "title": "Asesor Inmobiliario",
            "whatsapp": "+54 9 11 2513 1361",
            "instagram": instagram,
            "email": email,
            "whatsapp_enabled": True,
            "instagram_enabled": bool(instagram),
            "photo_path": str(agent_path),
        }
    return {
        "facts": facts,
        "photos": photo_rows,
        "agent": agent,
        "language": "es",
        "files": files,
        "art": {"cta": "Escribime ahora"},
        "options": {
            "include_agent": include_agent,
            "show_agent_photo": include_agent,
            "show_price": True,
        },
    }


def _other_property_context():
    qa = _ensure_qa_photo_assets()
    files = {}
    sources = (
        (qa / "interior_2.jpg", "living"),
        (qa / "interior_2.jpg", "living"),
        (qa / "interior_2.jpg", "living"),
        (qa / "interior_3.jpg", "cocina"),
        (qa / "interior_4.jpg", "bano"),
    )
    photo_rows = []
    for index, (source, label) in enumerate(sources):
        name = f"libertador_{index}.jpg"
        path = _photo(_PRIVATE_ROOT / name, source, label=label)
        files[name] = path
        photo_rows.append(
            {
                "id": 20 + index,
                "label": label,
                "is_cover": index == 0,
                "storage_key": name,
                "path": str(path),
            }
        )
    return {
        "facts": {
            "property_id": 220,
            "organization_id": 7,
            "title": "Libertador 1000",
            "neighborhood": "Martínez",
            "locality": "Buenos Aires",
            "purpose": "sale",
            "kicker": "EN VENTA",
            "rooms": 3,
            "bedrooms": 2,
            "bathrooms": 1,
            "total_m2": 89,
            "price_label": "USD 180.000",
            "brand_name": "RE/MAX Data House",
            "office_name": "RE/MAX Data House",
            "benefit_line": "Luz, amplitud y una ubicación privilegiada.",
            "used_demo_fallback": False,
            "legal_complete": True,
            "legal_broker_name": "Maria Eugenia Conti",
            "legal_broker_license": "C.I. Mat. Nº 1234",
            "legal_broker_college": "COCIR Santa Fe",
            "legal_footer_line": (
                "Martillero responsable: Maria Eugenia Conti | C.I. Mat. Nº 1234 | COCIR Santa Fe"
            ),
        },
        "photos": photo_rows,
        "agent": None,
        "language": "es",
        "files": files,
        "art": {
            "cta": "Escribime ahora",
            "subheadline": "Luz, amplitud y una ubicación privilegiada.",
        },
        "options": {
            "include_agent": False,
            "show_agent_photo": False,
            "show_price": True,
        },
    }


class PropertyMarketingTests(unittest.TestCase):
    def _render(self, packed, template):
        options = dict(packed["options"])
        options["layout_template"] = template
        return render_property_marketing(
            packed,
            "post",
            options=options,
            art=packed.get("art") or {},
            language="es",
        )

    def test_01_png_size_and_renderer(self):
        result = self._render(_fixture_context(), TEMPLATE_CLEAN_GRID)
        image = Image.open(io.BytesIO(result["png_bytes"]))
        self.assertEqual(image.size, tuple(result["render_context"]["canvas_size"]))
        self.assertEqual(image.width, CLEAN_GRID_MASTER[0])
        self.assertEqual(result["template_used"], TEMPLATE_CLEAN_GRID)
        self.assertEqual(result["renderer_used"], HTML_RENDERER)
        self.assertEqual(result["post_process"], "html_playwright")
        self.assertIn("data:image/jpeg", result["html"])
        images = (result.get("metrics") or {}).get("images") or []
        hero_loads = [item for item in images if "hero__photo" in str(item.get("className") or "")]
        self.assertTrue(hero_loads)
        self.assertTrue(all(item.get("loaded") for item in hero_loads))
        self.assertGreater(hero_loads[0].get("natural_width") or 0, 100)

    def test_02_no_internal_ids_or_jrh(self):
        result = self._render(_fixture_context(), TEMPLATE_CLEAN_GRID)
        html = _visible_html(result["html"])
        self.assertIn("Italia 1341", html)
        self.assertNotIn("B1640", html)
        self.assertNotIn("Cód", html)
        self.assertNotIn("property_id", html)
        self.assertNotIn("JRH", html)
        self.assertNotIn("jrh", html)

    def test_03_missing_fields_adapt(self):
        packed = _fixture_context(include_agent=True, bathrooms=None, instagram="", email="", legal=False)
        packed["options"]["include_agent"] = True
        result = self._render(packed, TEMPLATE_CLEAN_GRID)
        html = _visible_html(result["html"])
        self.assertNotIn("Baños", html)
        self.assertNotIn("None", html)
        self.assertNotIn("N/A", html)
        self.assertNotIn("agent-contact__item--instagram", html)
        self.assertNotIn("agent-contact__item--email", html)
        self.assertNotIn("mailto:", html)
        self.assertIn("poster--no-broker", html)

    def test_04_auto_select(self):
        many = _fixture_context(photos=4)
        self.assertEqual(select_property_template("automatic", many["photos"], many["facts"]), TEMPLATE_CLEAN_GRID)
        weak = _fixture_context(photos=1)
        weak["photos"][0]["label"] = "interior"
        weak["photos"][0]["is_cover"] = True
        self.assertEqual(
            select_property_template("automatic", weak["photos"], weak["facts"]),
            TEMPLATE_CLEAN_GRID,
        )
        facade = [{"id": 1, "label": "fachada pileta"}]
        self.assertEqual(select_property_template("automatic", facade, {}), TEMPLATE_LIFESTYLE_DARK)
        strong = _fixture_context(photos=1)
        self.assertEqual(
            select_property_template("automatic", strong["photos"], strong["facts"]),
            TEMPLATE_PREMIUM_HERO,
        )

    def test_05_failure_does_not_use_pillow(self):
        packed = _fixture_context()
        with mock.patch(
            "modules.property_marketing.screenshot_poster",
            side_effect=RuntimeError("boom"),
        ), mock.patch(
            "modules.marketing_service.stamp_branding_overlay",
            side_effect=AssertionError("pillow fallback is forbidden"),
        ):
            with self.assertRaises(PropertyMarketingError) as raised:
                self._render(packed, TEMPLATE_CLEAN_GRID)
            self.assertEqual(raised.exception.message_key, "PROPERTY_MARKETING_RENDER_FAILED")
            with self.assertRaises(MarketingError):
                _render_local_visual(
                    packed,
                    "post",
                    options={"layout_template": TEMPLATE_CLEAN_GRID, **packed["options"]},
                    art=packed.get("art") or {},
                    language="es",
                )

    def test_06_packaged_balloon_exists(self):
        self.assertTrue(PACKAGED_BALLOON.is_file())
        with Image.open(PACKAGED_BALLOON) as logo:
            self.assertEqual(logo.mode, "RGBA")
            self.assertGreater(logo.size[0], 200)
            self.assertGreater(logo.size[1], 200)

    def test_07_write_qa_pngs(self):
        QA_DIR.mkdir(parents=True, exist_ok=True)
        mapping = (
            (TEMPLATE_CLEAN_GRID, "qa_master_clean_grid.png", "Escribime ahora", CLEAN_GRID_MASTER),
            (TEMPLATE_LIFESTYLE_DARK, "qa_master_lifestyle_dark.png", "Coordiná tu visita", FULL_BLEED_MASTER),
            (TEMPLATE_PREMIUM_HERO, "qa_master_premium_hero.png", "Coordiná tu visita", FULL_BLEED_MASTER),
        )
        packed = _fixture_context()
        for template, name, cta, size in mapping:
            packed["art"] = {
                "cta": cta,
                "subheadline": packed["facts"]["benefit_line"],
            }
            result = self._render(packed, template)
            out = QA_DIR / name
            out.write_bytes(result["png_bytes"])
            self.assertTrue(out.is_file())
            with Image.open(out) as image:
                self.assertEqual(image.size, tuple(result["render_context"]["canvas_size"]))
                self.assertEqual(image.width, size[0])
                sample = image.convert("RGB").resize((48, 48))
                colors = sample.getcolors(maxcolors=4096)
            self.assertTrue(colors is None or len(colors) > 40)
            self.assertNotIn("B1640", _visible_html(result["html"]))
            self.assertNotIn("JRH", _visible_html(result["html"]))

    def test_08_avoids_duplicate_rooms(self):
        packed = _other_property_context()
        chosen = select_photos_for_item(packed["photos"], fmt="post", index=0, limit=4)
        scenes = [photo_scene_label(item) for item in chosen]
        self.assertEqual(scenes[0], "living")
        self.assertEqual(sorted(scenes[1:]), ["bano", "cocina"])
        self.assertEqual(len(scenes), 3)
        self.assertEqual(len(set(scenes)), 3)
        self.assertEqual(
            select_property_template("automatic", packed["photos"], packed["facts"]),
            TEMPLATE_CLEAN_GRID,
        )

    def test_09_other_property_auto_qa(self):
        packed = _other_property_context()
        result = self._render(packed, "automatic")
        self.assertEqual(result["template_used"], TEMPLATE_CLEAN_GRID)
        html = _visible_html(result["html"])
        self.assertIn("Libertador 1000", html)
        self.assertNotIn("Italia 1341", html)
        self.assertEqual(len(result.get("render_context", {}).get("secondary") or []), 2)
        QA_DIR.mkdir(parents=True, exist_ok=True)
        out = QA_DIR / "qa_auto_libertador.png"
        out.write_bytes(result["png_bytes"])
        with Image.open(out) as image:
            self.assertEqual(image.size, tuple(result["render_context"]["canvas_size"]))

    def test_10_photo_fit_and_agent_contacts(self):
        from modules.marketing_photo_fit import COVER_MAX_CROP, choose_photo_fit

        fit, crop = choose_photo_fit((1080, 1350), (1080, 1350))
        self.assertEqual(fit, "cover")
        self.assertEqual(crop, 0)
        fit, crop = choose_photo_fit((1600, 900), (1080, 1350))
        self.assertEqual(fit, "contain")
        self.assertGreater(crop, COVER_MAX_CROP)
        fit, crop = choose_photo_fit((800, 1000), (1080, 1350))
        self.assertEqual(fit, "contain")

        result = self._render(_fixture_context(), TEMPLATE_LIFESTYLE_DARK)
        html = result["html"]
        self.assertIn("photo-original__image", html)
        self.assertNotIn("__fill", html)
        self.assertNotIn("photo-bg", html)
        self.assertIn("https://wa.me/5491125131361", html)
        self.assertIn("https://www.instagram.com/josebarreiro/", html)
        self.assertIn("mailto:jose.barreiro@example.com", html)
        self.assertIn("agent-contact__item--whatsapp", html)
        self.assertIn("agent-contact__item--instagram", html)
        self.assertIn("agent-contact__item--email", html)
        self.assertNotIn(">None<", html)
        self.assertEqual(result["render_context"]["hero"]["fit"], "original")
        contacts = result["render_context"]["agent"]["contacts"]
        self.assertEqual([item["kind"] for item in contacts], ["whatsapp", "instagram", "email"])
        self.assertEqual(
            [item["value"] for item in contacts],
            ["+54 9 11 2513 1361", "@josebarreiro", "jose.barreiro@example.com"],
        )
        visible = _visible_html(html).replace("<wbr>", "")
        self.assertIn("jose.barreiro@<wbr>example.com<", html)
        self.assertNotIn("&lt;wbr&gt;", html)
        for value in ("+54 9 11 2513 1361", "@josebarreiro", "jose.barreiro@example.com"):
            self.assertIn(f'agent-contact__value">{value}<', html.replace("<wbr>", ""))
            self.assertIn(value, visible)
        for bad in ("None", "null", "undefined"):
            self.assertNotIn(f'agent-contact__value">{bad}', html)

    def test_11_hero_keeps_original_bytes_and_caps_scale(self):
        import base64

        from modules.marketing_photo_fit import plan_original_photo
        from modules.marketing_photo_selector import score_listing_photo

        plan = plan_original_photo((800, 600), (1080, 523))
        self.assertEqual((plan["display_width"], plan["display_height"]), (697, 523))
        self.assertEqual(plan["crop"], 0.0)
        self.assertEqual(plan["visual_transform"], "downscale_to_fit")
        small = plan_original_photo((510, 330), (1080, 523))
        self.assertEqual((small["display_width"], small["display_height"]), (510, 330))
        self.assertEqual(small["scale_factor"], 1.0)
        self.assertEqual(small["visual_transform"], "none")

        small = {"id": 1, "is_cover": True, "label": "fachada", "width": 800, "height": 600}
        large = {"id": 2, "label": "fachada", "width": 2500, "height": 1800}
        self.assertGreater(
            score_listing_photo(large, hero=True),
            score_listing_photo(small, hero=True),
        )

        packed = _fixture_context()
        result = self._render(packed, TEMPLATE_PREMIUM_HERO)
        hero = result["render_context"]["hero"]
        self.assertFalse(hero["recompressed"])
        self.assertLessEqual(hero["scale_factor"], 1.0)
        self.assertEqual(hero["crop"], 0.0)
        self.assertTrue(str(hero["src"]).startswith("data:image/"))
        source = next(item for item in packed["photos"] if item["id"] == hero["id"])
        original = Path(source["path"]).read_bytes()
        encoded = str(hero["src"]).split(",", 1)[1]
        self.assertEqual(len(base64.b64decode(encoded)), len(original))
        self.assertEqual(result["png_bytes"][:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(io.BytesIO(result["png_bytes"])) as image:
            self.assertEqual(image.size, tuple(result["render_context"]["canvas_size"]))
            self.assertEqual(image.width, FULL_BLEED_MASTER[0])
        css = (BASE_DIR / "static" / "css" / "marketing-render" / "property_premium_hero.css").read_text(encoding="utf-8")
        self.assertNotIn("transform: scale(1.", css)
        frame_css = (BASE_DIR / "static" / "css" / "marketing-render" / "property_photo_frame.css").read_text(encoding="utf-8")
        self.assertNotIn("blur(", frame_css)
        self.assertNotIn("scale(", frame_css)
        for name in ("property_clean_grid.css", "property_lifestyle_dark.css", "property_premium_hero.css"):
            template_css = (BASE_DIR / "static" / "css" / "marketing-render" / name).read_text(encoding="utf-8")
            self.assertNotIn("blur(", template_css)
            self.assertNotIn("hero__shade", template_css)
        html = result["html"]
        self.assertEqual(html.count(encoded[:120]), 1)

    def test_12_large_photos_fill_space_without_upscale(self):
        from modules.marketing_photo_fit import (
            CLEAN_GRID_HERO_MAX,
            CLEAN_GRID_THUMB_MAX,
            FULL_BLEED_PHOTO_MAX,
            plan_original_photo,
        )
        from modules.property_marketing import _photo_layout

        hero = plan_original_photo((1440, 1024), FULL_BLEED_PHOTO_MAX)
        self.assertEqual((hero["display_width"], hero["display_height"]), (990, 704))
        premium = _photo_layout(TEMPLATE_PREMIUM_HERO, FULL_BLEED_MASTER, hero, [], FULL_BLEED_PHOTO_MAX)
        self.assertEqual((premium["hero"]["width"], premium["hero"]["height"]), (990, 704))

        grid_hero = plan_original_photo((1440, 1024), CLEAN_GRID_HERO_MAX)
        self.assertEqual(grid_hero["display_width"], CLEAN_GRID_HERO_MAX[0])
        sources = ((1600, 1067), (1200, 1600), (1440, 960))
        thumbs = [plan_original_photo(size, CLEAN_GRID_THUMB_MAX) for size in sources]
        heights = set()
        for (width, height), photo in zip(sources, thumbs):
            self.assertLessEqual(photo["display_width"], width)
            self.assertLessEqual(photo["display_height"], height)
            self.assertAlmostEqual(photo["display_width"] / photo["display_height"], width / height, delta=0.01)
            heights.add(photo["display_height"])
        self.assertGreater(len(heights), 1)
        grid = _photo_layout(TEMPLATE_CLEAN_GRID, CLEAN_GRID_MASTER, grid_hero, thumbs, CLEAN_GRID_HERO_MAX)
        for box, photo in zip(grid["thumbs"], thumbs):
            self.assertEqual((box["width"], box["height"]), (photo["display_width"], photo["display_height"]))


if __name__ == "__main__":
    unittest.main()
