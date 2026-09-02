"""Regenerate static/brand/login-card-mark.png from email-footer source."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

BRAND_DIR = Path(__file__).resolve().parents[1] / "static" / "brand"
OUTPUT = BRAND_DIR / "login-card-mark.png"
SOURCE = BRAND_DIR / "email-footer.png"


def content_bbox(im: Image.Image, white_thresh: int = 248) -> tuple[int, int, int, int]:
    w, h = im.size
    px = im.load()
    minx, miny, maxx, maxy = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 16:
                continue
            if r >= white_thresh and g >= white_thresh and b >= white_thresh:
                continue
            minx = min(minx, x)
            miny = min(miny, y)
            maxx = max(maxx, x)
            maxy = max(maxy, y)
    return minx, miny, maxx, maxy


def main() -> None:
    src = Image.open(SOURCE).convert("RGBA")
    # Left logo panel only; stop before contact/email glyphs (~x=450).
    crop = src.crop((75, 60, 468, 660))
    minx, miny, maxx, maxy = content_bbox(crop)
    content_w = maxx - minx + 1
    content_h = maxy - miny + 1
    margin = int(max(content_w, content_h) * 0.18)
    side = max(content_w, content_h) + margin * 2

    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    offset_x = margin + (max(content_w, content_h) - content_w) // 2 - minx
    offset_y = margin + (max(content_w, content_h) - content_h) // 2 - miny
    canvas.paste(crop, (offset_x, offset_y), crop)

    if side != 640:
        canvas = canvas.resize((640, 640), Image.Resampling.LANCZOS)

    canvas.save(OUTPUT, optimize=True)
    bb = content_bbox(canvas)
    print(f"Wrote {OUTPUT} ({canvas.size[0]}x{canvas.size[1]})")
    print(f"Content bbox: {bb}")
    print(
        "Margins L={} R={} T={} B={}".format(
            bb[0],
            canvas.size[0] - 1 - bb[2],
            bb[1],
            canvas.size[1] - 1 - bb[3],
        )
    )


if __name__ == "__main__":
    main()
