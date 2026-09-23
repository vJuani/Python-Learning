"""HTML/CSS Playwright Marketing IA renderer — modern_commercial_v3."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

_TEST_TMP = tempfile.TemporaryDirectory()
_PRIVATE_ROOT = Path(_TEST_TMP.name) / "uploads"
_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PRIVATE_UPLOAD_ROOT"] = str(_PRIVATE_ROOT)
os.environ["MARKETING_HTML_DEBUG"] = "1"
os.environ.setdefault("APP_ENV", "development")

from modules.config import BASE_DIR
from modules.marketing_context import MarketingError
from modules.marketing_render_html import (
    DEBUG_DIR,
    HTML_RENDERER,
    HTML_TEMPLATE,
    POSTER_SIZE,
    build_marketing_render_context,
    render_html_visual,
    render_marketing_html,
    screenshot_poster,
)
from modules.marketing_renderer import FORMAT_SIZES
from modules.marketing_service import _render_local_visual


def _photo(path, color, size=(1400, 1000), label=""):
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, size[0] - 40, size[1] - 40), outline=(255, 255, 255), width=10)
    if label:
        draw.rectangle((60, 60, 360, 140), fill=(20, 28, 44))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=92)
    return path


def _agent_png(path):
    image = Image.new("RGBA", (420, 620), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((110, 20, 310, 230), fill=(48, 56, 70, 255))
    draw.rectangle((90, 210, 330, 600), fill=(48, 56, 70, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return path


def _logo_png(path):
    image = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 12, 68, 72), fill=(227, 24, 55, 255))
    draw.rectangle((80, 22, 230, 58), fill=(12, 24, 46, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return path


def _italia_context(*, rental=False, include_agent=True, show_price=True, long_copy=False):
    root = _PRIVATE_ROOT
    hero = _photo(root / "hero.jpg", (18, 78, 28), label="fachada")
    living = _photo(root / "living.jpg", (168, 46, 186), label="living")
    cocina = _photo(root / "cocina.jpg", (28, 58, 210), label="cocina")
    jardin = _photo(root / "jardin.jpg", (232, 198, 22), label="jardin")
    agent_path = _agent_png(root / "agent.png")
    logo_path = _logo_png(root / "logo.png")
    purpose = "rental" if rental else "sale"
    facts = {
        "title": "Italia 1341",
        "postal_code": "B1640",
        "neighborhood": "Martínez",
        "locality": "Martínez",
        "jurisdiction": "Buenos Aires",
        "purpose": purpose,
        "operation_type": purpose,
        "property_type": "house",
        "type_label": "Casa",
        "rooms": 4,
        "bedrooms": 3,
        "bathrooms": 2,
        "total_m2": 131,
        "covered_m2": 131,
        "price_label": None if not show_price else "USD 320.000",
        "listing_price": None if not show_price else 320000,
        "listing_currency": "USD",
        "kicker": "EN ALQUILER" if rental else "EN VENTA",
        "brand_name": "RE/MAX Data House",
        "office_name": "RE/MAX Data House",
        "logo_path": str(logo_path),
        "marketing_logo_path": str(logo_path),
        "legal_footer_line": "Corredor Público Mauro Marvisi CUCI CBA 1762 / CMPPSI 5574",
        "amenities": ["jardín", "entorno residencial", "excelente conectividad"],
        "benefit_line": "Un hogar moderno, funcional y con excelente conectividad.",
    }
    photos = [
        {"id": 1, "label": "fachada", "is_cover": True, "storage_key": "hero.jpg"},
        {"id": 2, "label": "living", "storage_key": "living.jpg"},
        {"id": 3, "label": "cocina", "storage_key": "cocina.jpg"},
        {"id": 4, "label": "jardin", "storage_key": "jardin.jpg"},
    ]
    agent = None
    if include_agent:
        agent = {
            "name": "José Barreiro",
            "title": "Agente inmobiliario",
            "whatsapp": "+54 9 11 2513 1361",
            "instagram": "@josebarreiro_remax",
            "email": "juanignacioruiz2001@gmail.com",
            "photo_path": str(agent_path),
        }
    headline = (
        "Viví la amplitud extraordinaria y el confort exclusivo de esta residencia única en Martínez"
        if long_copy
        else ""
    )
    return {
        "facts": facts,
        "photos": photos,
        "agent": agent,
        "language": "es",
        "files": {
            "hero": hero,
            "living": living,
            "cocina": cocina,
            "jardin": jardin,
            "agent": agent_path,
            "logo": logo_path,
        },
        "art": {"headline": headline, "cta": "Consultame disponibilidad" if rental else ""},
        "options": {
            "include_agent": include_agent,
            "show_agent_photo": include_agent,
            "show_price": show_price,
        },
    }


class MarketingHtmlRendererTests(unittest.TestCase):
    def _render(self, packed, **option_overrides):
        options = dict(packed["options"])
        options.update(option_overrides)
        return render_html_visual(
            packed,
            "post",
            options=options,
            art=packed.get("art") or {},
            language="es",
        )

    def test_01_png_size_template_and_renderer(self):
        result = self._render(_italia_context())
        image = Image.open(io.BytesIO(result["png_bytes"]))
        self.assertEqual(image.size, (1080, 1350))
        self.assertEqual(image.size, POSTER_SIZE)
        self.assertEqual(image.size, FORMAT_SIZES["post"])
        self.assertEqual(result["template_used"], HTML_TEMPLATE)
        self.assertEqual(result["renderer_used"], HTML_RENDERER)
        self.assertEqual(result["layout_version"], HTML_TEMPLATE)

    def test_02_photos_agent_logo_load(self):
        result = self._render(_italia_context())
        html = result["html"]
        metrics = result["metrics"]
        self.assertIn("data:image/jpeg;base64,", html)
        self.assertIn("data:image/png;base64,", html)
        self.assertGreaterEqual(html.count("data:image/jpeg;base64,"), 4)
        self.assertTrue(metrics["hasHero"])
        self.assertTrue(metrics["hasAgent"])
        self.assertTrue(metrics["hasLogo"])
        self.assertTrue(result["agent_photo_composited"])
        self.assertTrue(result["logo_stamped"])
        self.assertGreaterEqual(result["listing_photos"], 4)

    def test_03_no_overflow_or_scrollbar(self):
        result = self._render(_italia_context(long_copy=True))
        metrics = result["metrics"]
        self.assertEqual(metrics["width"], 1080)
        self.assertEqual(metrics["height"], 1350)
        self.assertLessEqual(metrics["scrollWidth"], metrics["clientWidth"] + 1)
        self.assertLessEqual(metrics["scrollHeight"], metrics["clientHeight"] + 1)
        self.assertEqual(metrics["overflow"], [])

    def test_04_offline_document(self):
        result = self._render(_italia_context())
        html = result["html"]
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn("@font-face", html)
        self.assertIn("data:font/", html)

    def test_05_failure_does_not_use_pillow(self):
        packed = _italia_context()
        with mock.patch(
            "modules.marketing_render_html.screenshot_poster",
            side_effect=MarketingError("marketing_err_html_render_failed", 500),
        ), mock.patch(
            "modules.property_marketing.screenshot_poster",
            side_effect=MarketingError("marketing_err_html_render_failed", 500),
        ), mock.patch(
            "modules.marketing_service.stamp_branding_overlay",
            side_effect=AssertionError("pillow fallback is forbidden"),
        ):
            with self.assertRaises(MarketingError) as raised:
                self._render(packed)
            self.assertEqual(raised.exception.message_key, "marketing_err_html_render_failed")
            with self.assertRaises(MarketingError) as raised_local:
                _render_local_visual(
                    packed,
                    "post",
                    options=packed["options"],
                    art=packed.get("art") or {},
                    language="es",
                )
            self.assertEqual(raised_local.exception.message_key, "marketing_err_html_render_failed")

    def test_06_agent_false_adapts_layout(self):
        result = self._render(_italia_context(include_agent=False))
        self.assertIn("poster--no-agent", result["html"])
        self.assertNotIn("José Barreiro", result["html"])
        self.assertFalse(result["metrics"]["hasAgent"])
        self.assertIn("cta__btn", result["html"])
        self.assertIn("→", result["html"])

    def test_07_price_missing_adapts_layout(self):
        result = self._render(_italia_context(show_price=False))
        self.assertIn("poster--no-price", result["html"])
        self.assertNotIn("320.000", result["html"])
        self.assertNotIn('class="price"', result["html"])

    def test_08_rental_adapts_operation_and_cta(self):
        result = self._render(_italia_context(rental=True))
        self.assertIn("EN ALQUILER", result["html"])
        self.assertNotIn("EN VENTA", result["html"])
        self.assertIn("Consultame disponibilidad", result["html"])

    def test_09_debug_dumps_and_visual_qa(self):
        result = self._render(_italia_context())
        self.assertTrue((DEBUG_DIR / "render.html").is_file())
        self.assertTrue((DEBUG_DIR / "render.png").is_file())
        self.assertTrue((DEBUG_DIR / "render_context.json").is_file())
        out = DEBUG_DIR / "modern_commercial_v3.png"
        out.write_bytes(result["png_bytes"])
        reference = (
            BASE_DIR
            / "static"
            / "marketing_style_references"
            / "modern_commercial_v2_reference.png"
        )
        self.assertTrue(reference.is_file())
        rendered = Image.open(io.BytesIO(result["png_bytes"])).convert("RGB")
        ref = Image.open(reference).convert("RGB").resize(POSTER_SIZE, Image.Resampling.LANCZOS)
        compare = Image.new("RGB", (POSTER_SIZE[0] * 2 + 40, POSTER_SIZE[1]), (24, 24, 24))
        compare.paste(ref, (0, 0))
        compare.paste(rendered, (POSTER_SIZE[0] + 40, 0))
        compare.save(DEBUG_DIR / "v3_fixture_vs_reference.png")
        html = result["html"]
        self.assertEqual(result["render_context"]["headline_lines"][0], "Viví la amplitud")
        self.assertIn("Martínez", result["render_context"]["headline_lines"][-1])
        self.assertIn("USD", html)
        self.assertIn("320.000", html)
        self.assertNotIn("navbar", html.lower())
        self.assertNotIn("sidebar", html.lower())

    def test_10_preview_html_is_isolated(self):
        packed = _italia_context()
        ctx = build_marketing_render_context(
            packed,
            fmt="post",
            options=packed["options"],
            art=packed.get("art") or {},
            language="es",
        )
        html = render_marketing_html(ctx)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn('id="poster"', html)
        self.assertNotIn("base.html", html)
        png, metrics = screenshot_poster(html)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (1080, 1350))
        self.assertEqual(metrics["width"], 1080)

    def test_11_skips_marketing_asset_and_uses_gallery_2(self):
        packed = _italia_context()
        dorm = _photo(_PRIVATE_ROOT / "dorm.jpg", (210, 32, 28), label="dormitorio")
        packed["photos"] = [
            {
                "id": 99,
                "label": "fachada",
                "is_cover": True,
                "source": "marketing",
                "storage_key": "tmp/marketing_html_render/italia_1341_v3.png",
            },
            packed["photos"][0],
            packed["photos"][1],
            {"id": 5, "label": "dormitorio", "storage_key": "dorm.jpg"},
        ]
        packed["files"]["dorm"] = dorm
        ctx = build_marketing_render_context(
            packed,
            fmt="post",
            options=packed["options"],
            art=packed.get("art") or {},
            language="es",
        )
        html = render_marketing_html(ctx)
        self.assertFalse(ctx["hero_is_generated_marketing_asset"])
        self.assertEqual(ctx["photo_origins"]["hero_photo"]["source_type"], "original_property_photo")
        self.assertEqual(len(ctx["secondary"]), 2)
        self.assertIn('class="gallery gallery--2"', html)
        self.assertEqual(ctx["photo_origins"]["secondary_photos"][2]["source_type"], "missing")


if __name__ == "__main__":
    unittest.main()
