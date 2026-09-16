"""Extract official JRH IA mascot crops from the Codex brand sheet.

Does not redraw the character. Crops the approved poses, knocks out the
sheet background, and hue-shifts green to violet only when a dark variant
is missing from the board.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "static" / "images" / "Imagen de Codex 15 sept 2026, 05_02_35 p.m..png"
DEST = BASE / "static" / "branding" / "jrh-ai"

# Full-sheet boxes (1448x1086) around the official characters only.
BOXES = {
    "hero-light": (255, 68, 638, 592),
    "hero-dark": (742, 72, 1136, 598),
    "avatar-light": (1165, 112, 1430, 340),
    "launcher-light": (1182, 448, 1378, 650),
}


def _dist(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _sat(r, g, b) -> float:
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx == 0:
        return 0.0
    return (mx - mn) / mx


def _lum(rgb) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _is_accent(rgb) -> bool:
    r, g, b = rgb
    hue, sat, _val = _rgb_to_hsv(r, g, b)
    if sat < 0.28:
        return False
    return (70 <= hue <= 175) or (230 <= hue <= 310)


def _sheet_bg(im: Image.Image):
    w, h = im.size
    pix = im.load()
    samples = []
    for x, y in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3), (w // 2, 2), (2, h // 2)):
        samples.append(pix[x, y][:3])
    samples.sort()
    return samples[len(samples) // 2]


def _extract_character(im: Image.Image, *, mode: str) -> Image.Image:
    """Keep the character by growing from its body; punch out the sheet."""
    rgba = im.convert("RGBA")
    w, h = rgba.size
    pix = rgba.load()
    bg = _sheet_bg(rgba)

    if mode == "light":
        thresh, max_sat = 42, 0.20
    else:
        thresh, max_sat = 36, 0.22

    def is_sheet(rgb) -> bool:
        return _dist(rgb, bg) <= thresh and _sat(*rgb) <= max_sat and not _is_accent(rgb)

    core = []
    for y in range(h):
        for x in range(w):
            rgb = pix[x, y][:3]
            if _lum(rgb) >= 188 or _is_accent(rgb):
                core.append((x, y))

    if not core:
        return _flood_from_edges(rgba, bg, thresh, max_sat)

    radius = max(18, int(max(w, h) * 0.085))
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

    white_top = min(y for _x, y in core)
    white_cx = sum(x for x, _y in core) / len(core)

    for y in range(h):
        for x in range(w):
            r, g, b, _a = pix[x, y]
            rgb = (r, g, b)
            dcore = dist_map.get((x, y))
            if dcore is None:
                pix[x, y] = (r, g, b, 0)
                continue
            hat = (white_top - 52) <= y <= (white_top + 10) and abs(x - white_cx) < w * 0.28
            if is_sheet(rgb) and not hat and dcore > 5:
                pix[x, y] = (r, g, b, 0)
            elif is_sheet(rgb) and not hat:
                pix[x, y] = (r, g, b, max(0, int(255 * (1 - dcore / 8))))

    return rgba


def _flood_from_edges(rgba, bg, thresh, max_sat):
    """Fallback knockout if no body core is found."""
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
        r, g, b, a = pix[x, y]
        if _dist((r, g, b), bg) > thresh or _sat(r, g, b) > max_sat:
            continue
        pix[x, y] = (r, g, b, 0)
        q.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    return rgba


def _keep_largest(im: Image.Image, min_keep: int = 80) -> Image.Image:
    """Drop leftover labels as small disconnected blobs."""
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
    largest = set(components[0])
    lx = sum(p[0] for p in components[0]) / len(components[0])
    ly = sum(p[1] for p in components[0]) / len(components[0])
    for cells in components[1:]:
        if len(cells) < min_keep:
            continue
        cx = sum(p[0] for p in cells) / len(cells)
        cy = sum(p[1] for p in cells) / len(cells)
        if abs(cx - lx) + abs(cy - ly) < max(w, h) * 0.35 and len(cells) > len(components[0]) * 0.04:
            keep.add(id(cells))
            largest.update(cells)

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


def _circularize(im: Image.Image) -> Image.Image:
    """Keep the approved launcher disc; fade pixels outside it."""
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    square = im.crop((left, top, left + side, top + side)).convert("RGBA")
    pix = square.load()
    cx = cy = (side - 1) / 2
    radius = side / 2 - 0.5
    for y in range(side):
        for x in range(side):
            r, g, b, a = pix[x, y]
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d > radius:
                pix[x, y] = (r, g, b, 0)
            elif d > radius - 1.25:
                fade = int(a * (radius - d) / 1.25)
                pix[x, y] = (r, g, b, fade)
    return square


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


def _green_to_violet(im: Image.Image, *, darken_fill: bool = False) -> Image.Image:
    """Hue-shift Irish green accents to JRH dark violet. Geometry unchanged."""
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
                # 140° green -> ~270° violet, keep relative spread.
                new_h = (hue - 140) * 0.45 + 270
                new_h %= 360
                new_s = min(1.0, sat * 1.05)
                new_v = val
                if darken_fill and sat >= 0.35 and val <= 0.55:
                    new_v = max(0.16, val * 0.55)
                    new_s = min(1.0, sat * 0.85)
                nr, ng, nb = _hsv_to_rgb(new_h, new_s, new_v)
                pix[x, y] = (nr, ng, nb, a)
    return out


def _checker(im: Image.Image, path: Path, cell: int = 16) -> None:
    w, h = im.size
    bg = Image.new("RGB", (w, h), (220, 220, 220))
    pix = bg.load()
    for y in range(h):
        for x in range(w):
            if ((x // cell) + (y // cell)) % 2:
                pix[x, y] = (245, 245, 245)
    bg.paste(im, mask=im.split()[-1])
    bg.save(path)


def _save(im: Image.Image, name: str) -> None:
    dest = DEST / name
    im.save(dest, format="PNG", optimize=False, compress_level=1)
    _checker(im, DEST / f"_preview-{name}")
    print(f"wrote {dest.name} {im.size}")


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"missing source sheet: {SOURCE}")
    DEST.mkdir(parents=True, exist_ok=True)
    sheet = Image.open(SOURCE).convert("RGB")

    light_hero = _trim(_keep_largest(_flood_alpha(sheet.crop(BOXES["hero-light"]), mode="light")))
    dark_hero = _trim(_keep_largest(_flood_alpha(sheet.crop(BOXES["hero-dark"]), mode="dark")))
    avatar = _trim(_keep_largest(_flood_alpha(sheet.crop(BOXES["avatar-light"]), mode="light")))
    launcher = _circularize(_trim(_flood_alpha(sheet.crop(BOXES["launcher-light"]), mode="light"), pad=2))

    _save(light_hero, "jrh-ia-hero-light.png")
    _save(dark_hero, "jrh-ia-hero-dark.png")
    _save(avatar, "jrh-ia-avatar-light.png")
    _save(_green_to_violet(avatar), "jrh-ia-avatar-dark.png")
    _save(launcher, "jrh-ia-launcher-light.png")
    _save(_green_to_violet(launcher, darken_fill=True), "jrh-ia-launcher-dark.png")


if __name__ == "__main__":
    main()
