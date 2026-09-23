"""Render the 3 master replicas, measure DOM boxes in reference px, build side-by-sides."""

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from modules.property_marketing import (  # noqa: E402
    TEMPLATE_CLEAN_GRID,
    TEMPLATE_LIFESTYLE_DARK,
    TEMPLATE_PREMIUM_HERO,
    render_property_marketing,
)
from tests.test_property_marketing import _fixture_context  # noqa: E402

QA = ROOT / "tmp" / "property_marketing_qa"
REFS = ROOT / "marketing_style_references" / "property_templates_v1"

JOBS = (
    (TEMPLATE_PREMIUM_HERO, "JRH_template_05_premium_hero.png", "qa_master_premium_hero.png", "Coordiná tu visita"),
    (TEMPLATE_LIFESTYLE_DARK, "JRH_template_03_lifestyle_dark.png", "qa_master_lifestyle_dark.png", "Coordiná tu visita"),
    (TEMPLATE_CLEAN_GRID, "JRH_template_02_clean_grid.png", "qa_master_clean_grid.png", "Escribime ahora"),
)

SELECTORS = (
    ".brand__logo", ".brand__mark", ".brand__sub", ".zone", ".brand__tagline",
    ".hero", ".thumb--1", ".thumb--2", ".thumb--3", ".kicker",
    ".address", ".copy", ".rule", ".facts", ".price", ".cta", ".quote", ".aside",
    ".agent__photo", ".agent__meta h2", ".agent__meta p", ".agent-contact", ".legal",
)

MEASURE_JS = """
(selectors) => {
  const out = {};
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(el);
    const t = range.getBoundingClientRect();
    out[sel] = {box: [r.left, r.top, r.width, r.height], text: [t.left, t.top, t.width, t.height]};
  }
  return out;
}
"""


PHOTO_AUDIT_JS = """
() => {
  const imgs = [...document.querySelectorAll('img')];
  const srcCount = {};
  for (const i of imgs) srcCount[i.src] = (srcCount[i.src] || 0) + 1;
  const bgWithData = [...document.querySelectorAll('*')].filter(
    (el) => getComputedStyle(el).backgroundImage.includes('data:image')).length;
  return {
    background_images_from_data_uri: bgWithData,
    photos: [...document.querySelectorAll('.photo-original__image')].map((i) => {
      const cs = getComputedStyle(i);
      const r = i.getBoundingClientRect();
      const box = i.parentElement.getBoundingClientRect();
      return {
        cls: i.className,
        natural: [i.naturalWidth, i.naturalHeight],
        rendered: [Math.round(r.width * 100) / 100, Math.round(r.height * 100) / 100],
        box: [Math.round(box.width * 100) / 100, Math.round(box.height * 100) / 100],
        fully_visible: r.left >= box.left - 0.01 && r.top >= box.top - 0.01 && r.right <= box.right + 0.01 && r.bottom <= box.bottom + 0.01,
        filter: cs.filter, transform: cs.transform, mask: cs.maskImage || cs.webkitMaskImage,
        object_fit: cs.objectFit, object_position: cs.objectPosition, opacity: cs.opacity,
        same_src_in_document: srcCount[i.src],
      };
    }),
  };
}
"""


def main():
    packed = _fixture_context()
    report = {}
    rendered = []
    for template, ref_name, out_name, cta in JOBS:
        packed["art"] = {"cta": cta, "subheadline": packed["facts"]["benefit_line"]}
        options = dict(packed["options"], layout_template=template)
        result = render_property_marketing(packed, "post", options=options, art=packed["art"], language="es")
        rendered.append((template, ref_name, out_name, result))
    from modules import marketing_render_html

    marketing_render_html.close_html_renderer()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for template, ref_name, out_name, result in rendered:
            png = result["png_bytes"]
            (QA / out_name).write_bytes(png)
            master = Image.open(io.BytesIO(png)).convert("RGB")
            ref = Image.open(REFS / ref_name).convert("RGB")
            unit = master.width / float(ref.width)

            page = browser.new_page(viewport={"width": master.width, "height": master.height}, device_scale_factor=1)
            page.set_content(result["html"], wait_until="load")
            page.wait_for_timeout(150)
            boxes = page.evaluate(MEASURE_JS, list(SELECTORS))
            photos = page.evaluate(PHOTO_AUDIT_JS)
            page.close()
            report.setdefault("_photos", {})[template] = photos
            in_ref = {
                sel: {k: [round(v / unit, 1) for v in vals] for k, vals in data.items()}
                for sel, data in boxes.items()
            }
            report[template] = {"master": list(master.size), "reference": list(ref.size), "boxes_ref_px": in_ref}
            view = result["render_context"]
            report.setdefault("_fit", {})[template] = [
                {
                    "role": role,
                    "original_dimensions": [p["width"], p["height"]],
                    "max_available_dimensions": [p["max_width"], p["max_height"]],
                    "scale_factor": round(p["scale_factor"], 4),
                    "rendered_dimensions": [p["display_width"], p["display_height"]],
                    "crop": False,
                    "upscale": p["display_width"] > p["width"] or p["display_height"] > p["height"],
                }
                for role, p in [("hero", view["hero"])] + [(f"thumb_{i + 1}", s) for i, s in enumerate(view["secondary"])]
            ]

            ref_h = round(ref.height * master.width / float(ref.width))
            ref_big = Image.new("RGB", master.size, (24, 24, 24))
            ref_big.paste(ref.resize((master.width, ref_h), Image.Resampling.LANCZOS), (0, 0))
            gap = 40
            side = Image.new("RGB", (master.width * 2 + gap, max(master.height, ref_h) + 70), (24, 24, 24))
            side.paste(ref_big, (0, 70))
            side.paste(master, (master.width + gap, 70))
            draw = ImageDraw.Draw(side)
            draw.text((20, 24), f"REFERENCIA {ref_name} ({ref.width}x{ref.height} -> {master.width}x{master.height})", fill=(255, 255, 255))
            draw.text((master.width + gap + 20, 24), f"MASTER {out_name} ({master.width}x{master.height})", fill=(255, 255, 255))
            side.save(QA / out_name.replace("qa_master_", "compare_master_"))

            blend = Image.blend(ref_big, master, 0.5)
            blend.save(QA / out_name.replace("qa_master_", "overlay_master_"))
        browser.close()
    (QA / "master_boxes.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"_fit": report["_fit"], "_photos": report["_photos"]}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
