"""Brochure photos stay whole and logos keep their own ratio."""

from __future__ import annotations

import io
import unittest
from pathlib import Path

from PIL import Image
from reportlab.lib.units import mm

from modules.pdf_images import uncropped_raster
from modules.pdf_property_brochure import (
    _COLLAGE_GAP,
    _CONTACT_ICON_FILES,
    build_property_brochure_pdf,
    collage_frames,
    contained_draw_box,
    logo_draw_size,
)
from modules.property_marketing import ICON_EMAIL, ICON_IG, ICON_WA


def _ratio(width, height):
    return width / height


class BrochureLayoutTests(unittest.TestCase):
    def test_icons_are_the_web_svg_files(self):
        self.assertEqual(
            _CONTACT_ICON_FILES["whatsapp"].read_text(encoding="utf-8").strip(),
            ICON_WA,
        )
        self.assertEqual(
            _CONTACT_ICON_FILES["instagram"].read_text(encoding="utf-8").strip(),
            ICON_IG,
        )
        self.assertEqual(
            _CONTACT_ICON_FILES["mail"].read_text(encoding="utf-8").strip(),
            ICON_EMAIL,
        )
        self.assertTrue(str(_CONTACT_ICON_FILES["whatsapp"]).endswith(
            str(Path("static") / "assets" / "icons" / "whatsapp.svg")
        ))

    def test_horizontal_logo_keeps_its_ratio(self):
        width, height = logo_draw_size(800, 200, 42 * mm, 14 * mm)
        self.assertAlmostEqual(_ratio(width, height), 4.0, places=3)
        self.assertLessEqual(width, 42 * mm + 0.01)
        self.assertLessEqual(height, 14 * mm + 0.01)

    def test_vertical_logo_keeps_its_ratio(self):
        width, height = logo_draw_size(200, 800, 42 * mm, 14 * mm)
        self.assertAlmostEqual(_ratio(width, height), 0.25, places=3)
        self.assertLessEqual(width, 42 * mm + 0.01)
        self.assertLessEqual(height, 14 * mm + 0.01)

    def test_small_logo_is_not_upscaled(self):
        width, height = logo_draw_size(48, 48, 42 * mm, 14 * mm, min_dpi=144)
        native = 48 * 72 / 144
        self.assertAlmostEqual(width, native, places=2)
        self.assertAlmostEqual(height, native, places=2)
        self.assertLess(width, 20 * mm)

    def test_horizontal_photo_stays_complete(self):
        offset_x, offset_y, draw_w, draw_h = contained_draw_box(900, 300, 240, 180)
        self.assertAlmostEqual(_ratio(draw_w, draw_h), 3.0, places=4)
        self.assertAlmostEqual(draw_w / 900, draw_h / 300, places=4)
        self.assertGreater(offset_y, 0)
        self.assertLessEqual(offset_x + draw_w, 240 + 0.01)
        self.assertLessEqual(offset_y + draw_h, 180 + 0.01)

    def test_vertical_photo_stays_complete(self):
        offset_x, offset_y, draw_w, draw_h = contained_draw_box(300, 900, 240, 180)
        self.assertAlmostEqual(_ratio(draw_w, draw_h), 1 / 3, places=4)
        self.assertAlmostEqual(draw_w / 300, draw_h / 900, places=4)
        self.assertGreater(offset_x, 0)
        self.assertLessEqual(offset_x + draw_w, 240 + 0.01)
        self.assertLessEqual(offset_y + draw_h, 180 + 0.01)

    def test_raster_never_changes_the_photo_ratio(self):
        wide = io.BytesIO()
        Image.new("RGB", (640, 200), (180, 40, 40)).save(wide, format="PNG")
        tall = io.BytesIO()
        Image.new("RGB", (200, 640), (40, 80, 180)).save(tall, format="PNG")
        wide_out = uncropped_raster(wide.getvalue(), 300, 300)
        tall_out = uncropped_raster(tall.getvalue(), 300, 300)
        self.assertEqual(wide_out["source_size"], (640, 200))
        self.assertEqual(tall_out["source_size"], (200, 640))
        for result in (wide_out, tall_out):
            src_w, src_h = result["source_size"]
            out_w, out_h = result["size"]
            self.assertLessEqual(abs(out_w * src_h - out_h * src_w), max(src_w, src_h))
        self.assertLessEqual(wide_out["size"][0], 300)
        self.assertLessEqual(tall_out["size"][1], 300)

    def test_four_photos_fill_cells_that_match_their_ratios(self):
        sizes = [(1600, 900), (800, 1200), (1000, 750), (2000, 800)]
        frames, height = collage_frames(sizes, 480)
        self.assertEqual(len(frames), 4)
        image_area = 0.0
        for (src_w, src_h), frame in zip(sizes, frames):
            x, y, w, h = frame
            self.assertAlmostEqual(w / h, src_w / src_h, places=2)
            image_area += w * h
            self.assertGreaterEqual(x, -0.01)
            self.assertGreaterEqual(y, -0.01)
            self.assertLessEqual(x + w, 480 + 0.5)
            self.assertLessEqual(y + h, height + 0.5)
        hero = frames[3]
        bottom = frames[0]
        self.assertEqual(hero[0], 0)
        self.assertGreater(hero[2], frames[1][2])
        self.assertAlmostEqual(bottom[2], 480, places=1)
        self.assertGreater(bottom[1], hero[1])
        self.assertAlmostEqual(frames[1][0] - (hero[0] + hero[2]), _COLLAGE_GAP, places=2)
        self.assertGreater(image_area / (480 * height), 0.85)

    def test_portrait_column_is_narrower_than_the_wide_hero(self):
        sizes = [(1800, 800), (700, 1100), (900, 700)]
        frames, _height = collage_frames(sizes, 480)
        hero = frames[0]
        portrait = frames[1]
        self.assertEqual(hero[0], 0)
        self.assertLess(portrait[2], hero[2])
        self.assertAlmostEqual(portrait[2] / portrait[3], 700 / 1100, places=2)

    def test_brochure_with_mixed_photos_is_a_pdf(self):
        wide = io.BytesIO()
        Image.new("RGB", (800, 240), (210, 60, 50)).save(wide, format="JPEG", quality=95)
        tall = io.BytesIO()
        Image.new("RGB", (240, 800), (50, 90, 180)).save(tall, format="JPEG", quality=95)
        logo = io.BytesIO()
        Image.new("RGBA", (640, 160), (15, 118, 110, 255)).save(logo, format="PNG")
        pdf_bytes = build_property_brochure_pdf({
            "title": "Calle Completa 20",
            "price": "USD 120.000,00",
            "description": "La foto entra completa.",
            "gallery": [wide.getvalue(), tall.getvalue()],
            "organization": {
                "name": "Achard Propiedades QA",
                "logo": logo.getvalue(),
                "accent_color": "#0f766e",
            },
            "powered_by": "Powered by JRH One",
        })
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        draw_w, draw_h = logo_draw_size(640, 160, 42 * mm, 14 * mm)
        self.assertAlmostEqual(_ratio(draw_w, draw_h), 4.0, places=3)
        self.assertLess(draw_h, 14 * mm)


if __name__ == "__main__":
    unittest.main()
