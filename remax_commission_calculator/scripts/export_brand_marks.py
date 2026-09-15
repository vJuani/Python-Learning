"""Export canonical JRH One brand marks from the six source lockups."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.config import BASE_DIR

SOURCE = BASE_DIR / "static" / "images"
OUT = BASE_DIR / "static" / "brand"
ICONS = BASE_DIR / "static" / "icons"

WHITE = (255, 255, 255)
NAVY = (12, 21, 33)


def knockout(im: Image.Image, bg: tuple[int, int, int], thresh: float) -> Image.Image:
    im = im.convert("RGBA")
    pixels = im.load()
    width, height = im.size
    br, bgc, bb = bg
    fade_start = thresh * 0.35
    fade_span = max(1.0, thresh * 0.65)
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            dist = ((red - br) ** 2 + (green - bgc) ** 2 + (blue - bb) ** 2) ** 0.5
            if dist >= thresh:
                continue
            faded = int(max(0.0, min(255.0, (dist - fade_start) / fade_span * 255.0)))
            pixels[x, y] = (red, green, blue, min(alpha, faded))
    return im


def crop_not_background(im: Image.Image, bg: tuple[int, int, int], thresh: float, pad: int = 8) -> Image.Image:
    rgb = im.convert("RGB")
    pixels = rgb.load()
    width, height = rgb.size
    br, bgc, bb = bg
    left, top, right, bottom = width, height, 0, 0
    found = False
    for y in range(height):
        for x in range(width):
            red, green, blue = pixels[x, y]
            dist = ((red - br) ** 2 + (green - bgc) ** 2 + (blue - bb) ** 2) ** 0.5
            if dist < thresh:
                continue
            found = True
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if not found:
        return im.convert("RGBA")
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(width, right + 1 + pad)
    bottom = min(height, bottom + 1 + pad)
    return im.convert("RGBA").crop((left, top, right, bottom))


def crop_alpha(im: Image.Image, pad: int = 16) -> Image.Image:
    bbox = im.getbbox()
    if bbox is None:
        return im
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(im.width, right + pad)
    bottom = min(im.height, bottom + pad)
    return im.crop((left, top, right, bottom))


def recolor_opaque(im: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    im = im.convert("RGBA")
    pixels = im.load()
    width, height = im.size
    red_t, green_t, blue_t = color
    for y in range(height):
        for x in range(width):
            _red, _green, _blue, alpha = pixels[x, y]
            if alpha > 16:
                pixels[x, y] = (red_t, green_t, blue_t, alpha)
    return im


def save(im: Image.Image, name: str) -> None:
    path = OUT / name
    im.save(path, "PNG", optimize=True)
    print(f"wrote {path.relative_to(BASE_DIR)} {im.size}")


def resize_square(im: Image.Image, size: int) -> Image.Image:
    fitted = im.copy()
    fitted.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(
        fitted,
        ((size - fitted.width) // 2, (size - fitted.height) // 2),
        fitted,
    )
    return canvas


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)

    primary_light = crop_alpha(knockout(Image.open(SOURCE / "1.png"), WHITE, 32))
    horizontal_light = crop_alpha(knockout(Image.open(SOURCE / "2..png"), WHITE, 32))
    isotype_light = crop_alpha(knockout(Image.open(SOURCE / "3.png"), WHITE, 28), pad=24)
    app_icon = crop_not_background(Image.open(SOURCE / "4.png"), WHITE, 22, pad=6)
    dark_green = crop_alpha(knockout(Image.open(SOURCE / "5.png"), NAVY, 42))
    dark_violet = crop_alpha(knockout(Image.open(SOURCE / "6.png"), NAVY, 42))
    isotype_dark = recolor_opaque(isotype_light, (255, 255, 255))

    save(primary_light, "logo-primary-light.png")
    save(horizontal_light, "logo-horizontal-light.png")
    save(isotype_light, "isotype-light.png")
    save(app_icon, "app-icon.png")
    save(dark_green, "logo-dark-green.png")
    save(dark_violet, "logo-dark-violet.png")
    save(dark_green, "logo-horizontal-dark-green.png")
    save(dark_violet, "logo-horizontal-dark-violet.png")
    save(isotype_dark, "isotype-dark-green.png")
    save(isotype_dark, "isotype-dark-violet.png")
    save(horizontal_light, "brand-logo-light.png")
    save(dark_green, "brand-logo-dark.png")
    save(app_icon, "brand-icon.png")
    save(primary_light, "login-header-lockup.png")

    for size, name in ((192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")):
        resize_square(app_icon, size).save(ICONS / name, "PNG", optimize=True)
        print(f"wrote static/icons/{name}")

    maskable = Image.new("RGBA", (512, 512), (14, 79, 56, 255))
    inner = resize_square(app_icon, 410)
    maskable.paste(inner, ((512 - inner.width) // 2, (512 - inner.height) // 2), inner)
    maskable.save(ICONS / "icon-maskable-512.png", "PNG", optimize=True)
    print("wrote static/icons/icon-maskable-512.png")


if __name__ == "__main__":
    main()
