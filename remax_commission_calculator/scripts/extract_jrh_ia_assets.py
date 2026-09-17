"""Do not regenerate the official JRH IA bot from the brand sheet.

Canonical UI files live at:

    static/branding/jrh-ai/jrh_ia_bot_light.png
    static/branding/jrh-ai/jrh_ia_bot_dark.png

This script is leftover crop tooling. Running it must not overwrite
those two files.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "static" / "images" / "jrh-ia-mascot-sheet.png"
DEST = BASE / "static" / "branding" / "jrh-ai"

# Two-panel full-body sheet (772x567). Include hat, hands, legs, hover ring.
BOXES = {
    "hero-light": (6, 12, 276, 510),
    "hero-dark": (442, 54, 762, 498),
}


def _dist(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _sat(r, g, b) -> float:
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx == 0:
        return 0.0
    return (mx - mn) / mx


def _rgb_to_hsv(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    df = mx - mn
    if df == 0:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / df) + 360) % 360
    elif mx == g:
        h = (60 * ((b - r) / df) + 120) % 360
    else:
        h = (60 * ((r - g) / df) + 240) % 360
    s = 0.0 if mx == 0 else df / mx
    return h, s, mx


def _hsv_to_rgb(h, s, v):
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if h < 60:
        rp, gp, bp = c, x, 0
    elif h < 120:
        rp, gp, bp = x, c, 0
    elif h < 180:
        rp, gp, bp = 0, c, x
    elif h < 240:
        rp, gp, bp = 0, x, c
    elif h < 300:
        rp, gp, bp = x, 0, c
    else:
        rp, gp, bp = c, 0, x
    return (
        int(round((rp + m) * 255)),
        int(round((gp + m) * 255)),
        int(round((bp + m) * 255)),
    )


def _is_accent(rgb) -> bool:
    r, g, b = rgb
    hue, sat, _val = _rgb_to_hsv(r, g, b)
    if sat < 0.22:
        return False
    return (70 <= hue <= 175) or (230 <= hue <= 310)


def _sheet_bg(im: Image.Image):
    w, h = im.size
    pix = im.load()
    samples = []
    for x, y in (
        (2, 2),
        (w - 3, 2),
        (2, h - 3),
        (w - 3, h - 3),
        (w // 2, 2),
        (2, h // 2),
    ):
        samples.append(pix[x, y][:3])
    samples.sort()
    return samples[len(samples) // 2]


def _lum(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _knockout_sheet(im: Image.Image, *, mode: str) -> Image.Image:
    """Keep pixels near the character body; punch sheet/panel everywhere else."""
    rgba = im.convert("RGBA")
    w, h = rgba.size
    pix = rgba.load()
    bg = _sheet_bg(rgba)
    thresh = 44 if mode == "light" else 70
    max_sat = 0.22 if mode == "light" else 0.35

    def is_sheet(rgb) -> bool:
        if _is_accent(rgb):
            return False
        if mode == "dark" and _lum(rgb) <= 38 and _sat(*rgb) <= 0.28:
            return True
        return _dist(rgb, bg) <= thresh and _sat(*rgb) <= max_sat

    core = []
    for y in range(h):
        for x in range(w):
            rgb = pix[x, y][:3]
            if is_sheet(rgb) and not _is_accent(rgb):
                continue
            if _lum(rgb) >= 170 or _is_accent(rgb):
                core.append((x, y))

    if not core:
        return _flood_from_edges(rgba, is_sheet)

    radius = max(22, int(max(w, h) * 0.11))
    dist_map = {}
    q = deque()
    for x, y in core:
        dist_map[(x, y)] = 0
        q.append((x, y))
    while q:
        x, y = q.popleft()
        d = dist_map[(x, y)]
        if d >= radius:
            continue
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or ny < 0 or nx >= w or ny >= h:
                continue
            if (nx, ny) in dist_map:
                continue
            dist_map[(nx, ny)] = d + 1
            q.append((nx, ny))

    for y in range(h):
        for x in range(w):
            r, g, b, _a = pix[x, y]
            rgb = (r, g, b)
            dcore = dist_map.get((x, y))
            if dcore is None:
                pix[x, y] = (r, g, b, 0)
                continue
            if is_sheet(rgb) and dcore > 4:
                pix[x, y] = (r, g, b, 0)
            elif is_sheet(rgb):
                pix[x, y] = (r, g, b, max(0, int(255 * (1 - dcore / 6))))

    if mode == "dark":
        _punch_dark_panel(rgba, dist_map)
    return _flood_from_edges(rgba, is_sheet)


def _punch_dark_panel(im: Image.Image, dist_map: dict) -> None:
    """Remove leftover navy card pixels that are not on the body."""
    pix = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            if a == 0 or _is_accent((r, g, b)):
                continue
            dcore = dist_map.get((x, y), 999)
            if _lum((r, g, b)) <= 78 and _sat(r, g, b) <= 0.42 and dcore > 2:
                pix[x, y] = (r, g, b, 0)


def _flood_from_edges(rgba, is_sheet):
    w, h = rgba.size
    pix = rgba.load()
    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))
    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        i = y * w + x
        if seen[i]:
            continue
        seen[i] = 1
        r, g, b, _a = pix[x, y]
        if not is_sheet((r, g, b)):
            continue
        pix[x, y] = (r, g, b, 0)
        q.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    return rgba


def _keep_largest(im: Image.Image, min_keep: int = 40) -> Image.Image:
    """Drop leftover labels as small disconnected blobs. Keep nearby hat."""
    w, h = im.size
    pix = im.load()
    seen = bytearray(w * h)
    components = []

    def neighbors(x, y):
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                yield nx, ny

    for y in range(h):
        for x in range(w):
            i = y * w + x
            if seen[i] or pix[x, y][3] < 16:
                continue
            q = deque([(x, y)])
            seen[i] = 1
            cells = []
            while q:
                cx, cy = q.popleft()
                cells.append((cx, cy))
                for nx, ny in neighbors(cx, cy):
                    ni = ny * w + nx
                    if seen[ni] or pix[nx, ny][3] < 16:
                        continue
                    seen[ni] = 1
                    q.append((nx, ny))
            components.append(cells)

    if not components:
        return im

    components.sort(key=len, reverse=True)
    keep = {id(components[0])}
    lx = sum(p[0] for p in components[0]) / len(components[0])
    ly = sum(p[1] for p in components[0]) / len(components[0])
    for cells in components[1:]:
        if len(cells) < min_keep:
            continue
        cx = sum(p[0] for p in cells) / len(cells)
        cy = sum(p[1] for p in cells) / len(cells)
        if cy < h * 0.11 or cy > h * 0.90 or cx > w * 0.86:
            continue
        near = abs(cx - lx) + abs(cy - ly) < max(w, h) * 0.18
        tall_hat = cy < ly and abs(cx - lx) < w * 0.22 and len(cells) > len(components[0]) * 0.03
        if near or tall_hat:
            keep.add(id(cells))

    keep_cells = set()
    for cells in components:
        if id(cells) in keep:
            keep_cells.update(cells)

    out = im.copy()
    opix = out.load()
    for y in range(h):
        for x in range(w):
            if (x, y) not in keep_cells:
                r, g, b, _a = opix[x, y]
                opix[x, y] = (r, g, b, 0)
    return out


def _wipe_caption_bands(im: Image.Image) -> Image.Image:
    """Clear leftover sheet copy; keep hat, glow, and body accents."""
    w, h = im.size
    pix = im.load()
    top_band = int(h * 0.22)
    bottom_band = int(h * 0.92)
    right_band = int(w * 0.90)
    left_caption = int(w * 0.48)
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            if a == 0:
                continue
            if y < top_band and x < left_caption:
                pix[x, y] = (r, g, b, 0)
                continue
            if _is_accent((r, g, b)):
                continue
            if y > bottom_band or x > right_band:
                pix[x, y] = (r, g, b, 0)
    return im


def _trim(im: Image.Image, pad: int = 8) -> Image.Image:
    bbox = im.getbbox()
    if not bbox:
        return im
    l, t, r, b = bbox
    l = max(0, l - pad)
    t = max(0, t - pad)
    r = min(im.width, r + pad)
    b = min(im.height, b + pad)
    return im.crop((l, t, r, b))


def _bust(im: Image.Image) -> Image.Image:
    """Head-to-chest crop for small avatars; keep the house hat."""
    w, h = im.size
    bottom = max(int(h * 0.62), 1)
    return _trim(im.crop((0, 0, w, bottom)), pad=6)


def _green_to_violet(im: Image.Image, *, darken_fill: bool = False) -> Image.Image:
    out = im.convert("RGBA")
    pix = out.load()
    w, h = out.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            if a == 0:
                continue
            hue, sat, val = _rgb_to_hsv(r, g, b)
            if 70 <= hue <= 175 and sat >= 0.18:
                new_h = ((hue - 140) * 0.45 + 270) % 360
                new_s = min(1.0, sat * 1.05)
                new_v = val
                if darken_fill and sat >= 0.35 and val <= 0.55:
                    new_v = max(0.16, val * 0.55)
                    new_s = min(1.0, sat * 0.85)
                nr, ng, nb = _hsv_to_rgb(new_h, new_s, new_v)
                pix[x, y] = (nr, ng, nb, a)
    return out


def _checker(im: Image.Image, path: Path, *, dark: bool = False) -> None:
    w, h = im.size
    a, b = ((28, 28, 32), (44, 44, 50)) if dark else ((220, 220, 220), (245, 245, 245))
    bg = Image.new("RGB", (w, h), a)
    pix = bg.load()
    cell = 16
    for y in range(h):
        for x in range(w):
            if ((x // cell) + (y // cell)) % 2:
                pix[x, y] = b
    bg.paste(im, mask=im.split()[-1])
    bg.save(path)


def _save(im: Image.Image, name: str) -> None:
    dest = DEST / name
    im.save(dest, format="PNG", optimize=False, compress_level=1)
    print(f"wrote {dest.name} {im.size} mode={im.mode}")


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing source sheet: {SOURCE}")
    DEST.mkdir(parents=True, exist_ok=True)
    sheet = Image.open(SOURCE).convert("RGB")

    light_hero = _trim(
        _wipe_caption_bands(_keep_largest(_knockout_sheet(sheet.crop(BOXES["hero-light"]), mode="light"))),
        pad=12,
    )
    dark_hero = _trim(
        _wipe_caption_bands(_keep_largest(_knockout_sheet(sheet.crop(BOXES["hero-dark"]), mode="dark"))),
        pad=12,
    )
    avatar_light = _bust(light_hero)
    avatar_dark = _bust(dark_hero)

    _save(light_hero, "jrh-ia-hero-light.png")
    _save(dark_hero, "jrh-ia-hero-dark.png")
    _save(avatar_light, "jrh-ia-avatar-light.png")
    _save(avatar_dark, "jrh-ia-avatar-dark.png")
    _save(light_hero, "jrh-ia-launcher-light.png")
    _save(dark_hero, "jrh-ia-launcher-dark.png")


if __name__ == "__main__":
    main()
