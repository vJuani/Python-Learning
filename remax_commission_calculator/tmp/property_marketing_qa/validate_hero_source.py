"""Single-photo check: neutral background + the hero only, through the real load/payload path.

Usage: python tmp/property_marketing_qa/validate_hero_source.py [path/to/original.jpg]
"""

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageChops, ImageStat  # noqa: E402

from modules.marketing_photo_fit import FULL_BLEED_PHOTO_MAX  # noqa: E402
from modules.marketing_render_html import close_html_renderer, screenshot_poster  # noqa: E402
from modules.property_marketing import _load_listing_photos, _photo_payload  # noqa: E402

QA = ROOT / "tmp" / "property_marketing_qa"
DEFAULT_ORIGINAL = QA / "originals" / "hero_original.jpg"
MARGIN = 60


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ORIGINAL
    if not path.is_file():
        raise SystemExit(f"original not found: {path}")
    loaded = _load_listing_photos([{"id": 1, "path": str(path), "source": "local", "is_cover": True}])
    row, payload, meta = loaded[0]
    hero = _photo_payload(row, payload, meta, FULL_BLEED_PHOTO_MAX, hero=True)
    width, height = hero["display_width"], hero["display_height"]
    canvas = (1080, height + 2 * MARGIN)
    left = (canvas[0] - width) // 2
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html, body, #poster {{ margin: 0; width: {canvas[0]}px; height: {canvas[1]}px; background: #e9ecef; overflow: hidden; }}
#poster {{ position: relative; }}
img {{ position: absolute; left: {left}px; top: {MARGIN}px; width: {width}px; height: {height}px;
  object-fit: contain; object-position: center; transform: none; filter: none; }}
</style></head><body><div id="poster"><img src="{hero['src']}" alt=""></div></body></html>"""
    png, _metrics = screenshot_poster(html, size=canvas)
    native_w, native_h = hero["width"], hero["height"]
    native_html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html, body, #poster {{ margin: 0; width: {native_w}px; height: {native_h}px; overflow: hidden; }}
img {{ display: block; width: {native_w}px; height: {native_h}px; object-fit: contain; transform: none; filter: none; }}
</style></head><body><div id="poster"><img src="{hero['src']}" alt=""></div></body></html>"""
    native_png, _native_metrics = screenshot_poster(native_html, size=(native_w, native_h))
    close_html_renderer()
    native_shot = Image.open(io.BytesIO(native_png)).convert("RGB")
    native_diff = ImageChops.difference(native_shot, Image.open(path).convert("RGB")).getbbox()
    out = QA / "qa_hero_source_only.png"
    out.write_bytes(png)

    shot = Image.open(io.BytesIO(png)).convert("RGB")
    region = shot.crop((left, MARGIN, left + width, MARGIN + height))
    original = Image.open(path).convert("RGB")
    expected = original if original.size == (width, height) else original.resize((width, height), Image.Resampling.LANCZOS)
    diff = ImageStat.Stat(ImageChops.difference(region, expected)).mean
    audit = hero["source_audit"]
    report = {
        "source_path": audit["source_path"],
        "original_dimensions": [audit["original_width"], audit["original_height"]],
        "byte_size": audit["byte_size"],
        "sha256_source": audit["sha256"],
        "sha256_loaded": audit["loaded_sha256"],
        "sha256_data_uri": audit["data_uri_sha256"],
        "hashes_match": audit["match"],
        "max_available_dimensions": [hero["max_width"], hero["max_height"]],
        "scale_factor": round(hero["scale_factor"], 4),
        "rendered_dimensions": [width, height],
        "crop": False,
        "upscale": width > audit["original_width"] or height > audit["original_height"],
        "pixel_match": native_diff is None,
        "pixel_match_note": "native-size render compared pixel by pixel with the source file",
        "downscaled_mean_abs_diff_vs_lanczos": [round(v, 2) for v in diff],
        "png": str(out),
        "canvas": list(canvas),
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
