"""Deterministic Pillow marketing renderer. Matches JRH flyer language."""

from __future__ import annotations

import io
import logging
import re
from collections import deque
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from modules.marketing_branding import usable_brand_name
from modules.marketing_visual_spec import theme_palette
from modules.property_sync.media import load_original_media_bytes

logger = logging.getLogger(__name__)


NAVY = (10, 22, 51)
ELECTRIC = (13, 71, 255)
WHITE = (255, 255, 255)
IVORY = (250, 247, 241)
SOFT = (247, 244, 238)
INK = (17, 28, 51)
MUTED = (91, 107, 124)
GOLD = (196, 164, 92)
LINE = (226, 228, 222)
WA_GREEN = (37, 211, 102)
IG_PINK = (193, 53, 132)
IG_DOT = (255, 255, 255)

FORMAT_SIZES = {
    "story": (1080, 1920),
    "status": (1080, 1920),
    "post": (1080, 1350),
    "flyer": (1240, 1754),
}

STYLE_ACCENT = {
    "elegant": ELECTRIC,
    "modern": ELECTRIC,
    "minimal": ELECTRIC,
}


def _font_path(bold=False, italic=False):
    if italic:
        candidates = (
            Path(r"C:\Windows\Fonts\segoeuiz.ttf"),
            Path(r"C:\Windows\Fonts\segoesc.ttf"),
            Path(r"C:\Windows\Fonts\calibrii.ttf"),
        )
    elif bold:
        candidates = (
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        )
    else:
        candidates = (
            Path(r"C:\Windows\Fonts\segoeui.ttf"),
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        )
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def font(size, *, bold=False, italic=False):
    path = _font_path(bold=bold, italic=italic)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _open_image(source):
    if source is None:
        return None
    try:
        if isinstance(source, (bytes, bytearray)):
            image = Image.open(io.BytesIO(source))
        else:
            path = Path(source)
            if not path.is_file():
                return None
            image = Image.open(path)
        image.load()
        return ImageOps.exif_transpose(image)
    except Exception:
        return None


def _as_rgba(image):
    if image.mode == "RGBA":
        return image
    return image.convert("RGBA")


def fit_cover(image, width, height, *, focus=(0.5, 0.42)):
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return image.resize((width, height), Image.Resampling.LANCZOS)
    scale = max(width / src_w, height / src_h)
    resized = image.resize(
        (max(1, int(src_w * scale)), max(1, int(src_h * scale))),
        Image.Resampling.LANCZOS,
    )
    fx = 0.5 if focus is None else max(0.0, min(1.0, float(focus[0])))
    fy = 0.5 if focus is None else max(0.0, min(1.0, float(focus[1])))
    left = int(resized.width * fx - width / 2)
    top = int(resized.height * fy - height / 2)
    left = max(0, min(left, resized.width - width))
    top = max(0, min(top, resized.height - height))
    return resized.crop((left, top, left + width, top + height))


def fit_contain(image, width, height, *, fill=IVORY):
    src_w, src_h = image.size
    canvas = Image.new("RGB", (width, height), fill)
    if src_w <= 0 or src_h <= 0:
        return canvas
    scale = min(width / src_w, height / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    resized = image.convert("RGB").resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas.paste(resized, ((width - new_w) // 2, (height - new_h) // 2))
    return canvas


def fit_contain_safe(image, size, fmt=None, *, fill=IVORY):
    from modules.marketing_visual_spec import format_from_size, safe_rect

    width, height = size
    fmt = fmt or format_from_size(size)
    left, top, right, bottom = safe_rect(fmt, size)
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)
    inner = fit_contain(image, box_w, box_h, fill=fill)
    canvas = Image.new("RGB", (width, height), fill)
    canvas.paste(inner, (left, top))
    return canvas


def load_property_photo_pairs(photo_rows, *, cache=None):
    """Load each listing photo independently so a failed fetch cannot shift later rows."""
    pairs = []
    cache = cache if cache is not None else {}
    for item in photo_rows or []:
        try:
            payload = load_original_media_bytes(item, cache=cache, cache_dir=True)
            image = _open_image(payload)
            if image is None:
                logger.error(
                    "original_photo_fetch_failed source_type=%s original_url=%s path=%s",
                    (item or {}).get("source_type") or (item or {}).get("source"),
                    (item or {}).get("original_url"),
                    (item or {}).get("storage_key") or (item or {}).get("path"),
                )
                continue
            pairs.append(
                (item, ImageEnhance.Contrast(image.convert("RGB")).enhance(1.04))
            )
        except Exception:
            logger.exception(
                "original_photo_fetch_failed source_type=%s original_url=%s path=%s",
                (item or {}).get("source_type") or (item or {}).get("source"),
                (item or {}).get("original_url"),
                (item or {}).get("storage_key") or (item or {}).get("path"),
            )
            continue
    return pairs


def load_property_photos(photo_rows, *, cache=None):
    return [image for _row, image in load_property_photo_pairs(photo_rows, cache=cache)]


def load_agent_photo(path):
    image = _open_image(path)
    if image is None:
        return None
    return _as_rgba(image)


def _text_width(draw, text, used_font):
    return draw.textbbox((0, 0), text, font=used_font)[2]


def _wrap(draw, text, used_font, max_width):
    words = str(text or "").split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if _text_width(draw, trial, used_font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


@lru_cache(maxsize=12)
def _logo_rgba(path):
    source = str(path or "")
    if source.lower().endswith(".svg"):
        try:
            import cairosvg

            png = cairosvg.svg2png(url=source)
            image = Image.open(io.BytesIO(png))
            image.load()
            return _as_rgba(image).copy()
        except Exception:
            return None
    image = _open_image(source)
    if image is None:
        return None
    return _as_rgba(image).copy()


def _rounded_mask(width, height, radius):
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=max(1, radius), fill=255
    )
    return mask


def _circle_mask(diameter):
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, diameter - 2, diameter - 2), fill=255)
    return mask


def paste_rounded(canvas, image, xy, size, radius=28):
    if image is None:
        return
    fitted = fit_cover(image.convert("RGB"), size[0], size[1]).convert("RGBA")
    fitted.putalpha(_rounded_mask(size[0], size[1], radius))
    canvas.paste(fitted, xy, fitted)


def paste_circle(canvas, image, xy, diameter):
    if image is None:
        return
    fitted = fit_cover(image.convert("RGBA"), diameter, diameter)
    fitted.putalpha(_circle_mask(diameter))
    canvas.paste(fitted, xy, fitted)


def paste_cover_rounded(canvas, image, xy, size, *, radius=16, focus=(0.5, 0.40)):
    if image is None:
        return
    fitted = fit_cover(image.convert("RGB"), size[0], size[1], focus=focus).convert("RGBA")
    fitted.putalpha(_rounded_mask(size[0], size[1], radius))
    canvas.paste(fitted, xy, fitted)


def _luma(color):
    red, green, blue = color[:3]
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _trim_alpha(image, pad=2):
    alpha = image.split()[-1] if image.mode in {"RGBA", "LA"} else None
    bbox = alpha.getbbox() if alpha is not None else image.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(image.width, right + pad)
    bottom = min(image.height, bottom + pad)
    return image.crop((left, top, right, bottom))


def prepare_agent_cutout(image):
    """Keep a clean person cutout. Drop black or edge-connected studio plates."""
    if image is None:
        return None
    rgba = _as_rgba(image)
    alpha = rgba.getchannel("A")
    total = rgba.size[0] * rgba.size[1]
    if total <= 0:
        return None
    transparent = sum(alpha.histogram()[:200])
    if transparent > total * 0.08:
        return _trim_alpha(rgba)
    pixels = rgba.load()
    width, height = rgba.size
    edge = []
    step_x = max(1, width // 24)
    step_y = max(1, height // 24)
    for x in range(0, width, step_x):
        edge.append(pixels[x, 0][:3])
        edge.append(pixels[x, height - 1][:3])
    for y in range(0, height, step_y):
        edge.append(pixels[0, y][:3])
        edge.append(pixels[width - 1, y][:3])
    if not edge:
        return _trim_alpha(rgba)
    dark_ratio = sum(1 for color in edge if _luma(color) < 42) / len(edge)
    avg = tuple(sum(color[i] for color in edge) // len(edge) for i in range(3))
    uniform = all(abs(color[i] - avg[i]) < 28 for color in edge for i in range(3))
    if dark_ratio < 0.5 and not uniform:
        return _trim_alpha(rgba)

    marked = bytearray(width * height)
    queue = deque()

    def is_background(x, y):
        red, green, blue, _alpha = pixels[x, y]
        if dark_ratio >= 0.5:
            return _luma((red, green, blue)) < 58 and max(red, green, blue) < 78
        return (
            abs(red - avg[0]) + abs(green - avg[1]) + abs(blue - avg[2]) < 54
        )

    def push(x, y):
        if x < 0 or y < 0 or x >= width or y >= height:
            return
        index = y * width + x
        if marked[index] or not is_background(x, y):
            return
        marked[index] = 1
        queue.append((x, y))

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)
    while queue:
        x, y = queue.popleft()
        push(x - 1, y)
        push(x + 1, y)
        push(x, y - 1)
        push(x, y + 1)

    kept = total - marked.count(1)
    if kept < total * 0.08:
        return _trim_alpha(rgba)
    for y in range(height):
        row = y * width
        for x in range(width):
            if marked[row + x]:
                pixels[x, y] = (0, 0, 0, 0)
    return _trim_alpha(rgba)


def paste_agent_cutout(canvas, image, xy, *, height):
    if image is None or height <= 1:
        return False
    cutout = prepare_agent_cutout(image)
    if cutout is None:
        return False
    scale = height / float(cutout.height or 1)
    size = (max(1, int(cutout.width * scale)), max(1, int(height)))
    fitted = cutout.resize(size, Image.Resampling.LANCZOS)
    canvas.paste(fitted, xy, fitted)
    return True


def compose_photo_grid(canvas, photos, *, box, radius=16, gap=10, fmt=None):
    """Hero + equal secondary cells. Cover-crop with a slight interior bias."""
    photos = [item for item in (photos or []) if item is not None]
    if not photos:
        return
    left, top, width, height = box
    extras = photos[1:3]
    if not extras:
        paste_cover_rounded(
            canvas,
            photos[0],
            (left, top),
            (width, height),
            radius=radius,
            focus=(0.5, 0.38),
        )
        return
    stack_ratio = height / float(width or 1)
    split = fmt == "post" or stack_ratio < 0.82
    if split:
        hero_w = int(width * 0.64)
        side_w = width - hero_w - gap
        side_h = (height - gap * (len(extras) - 1)) // len(extras)
        paste_cover_rounded(
            canvas,
            photos[0],
            (left, top),
            (hero_w, height),
            radius=radius,
            focus=(0.5, 0.38),
        )
        for index, photo in enumerate(extras):
            paste_cover_rounded(
                canvas,
                photo,
                (left + hero_w + gap, top + index * (side_h + gap)),
                (side_w, side_h),
                radius=max(8, radius - 4),
                focus=(0.5, 0.40),
            )
        return
    thumb_w = (width - gap * (len(extras) - 1)) // len(extras)
    thumb_h = min(int(thumb_w * 0.66), int(height * 0.34))
    hero_h = max(int(width * 0.42), height - thumb_h - gap)
    if hero_h + gap + thumb_h > height:
        thumb_h = max(120, height - gap - int(width * 0.42))
        hero_h = height - thumb_h - gap
    paste_cover_rounded(
        canvas,
        photos[0],
        (left, top),
        (width, hero_h),
        radius=radius,
        focus=(0.5, 0.38),
    )
    thumb_top = top + hero_h + gap
    for index, photo in enumerate(extras):
        paste_cover_rounded(
            canvas,
            photo,
            (left + index * (thumb_w + gap), thumb_top),
            (thumb_w, thumb_h),
            radius=max(8, radius - 4),
            focus=(0.5, 0.40),
        )


def _u(width, value):
    return max(1, int(value * width / 1080))


def _paste_logo(canvas, logo_path, *, box, xy):
    image = _logo_rgba(str(logo_path)) if logo_path else None
    if image is None:
        return None
    image = image.copy()
    image.thumbnail(box, Image.Resampling.LANCZOS)
    canvas.paste(image, xy, image)
    return image.size


def _wordmark(draw, xy, brand, *, fill=NAVY, size=36):
    x, y = xy
    draw.text((x, y), brand or "RE/MAX Data House", font=font(size, bold=True), fill=fill)


def _office_brand(facts):
    return (
        usable_brand_name(
            facts.get("brand_name"),
            facts.get("office_name"),
            facts.get("organization_name"),
        )
        or "RE/MAX Data House"
    )


def _office_logo(facts):
    """Exact office logo file. Never invents or redraws a mark."""
    from modules.organization_marketing_logo import (
        resolve_stored_logo_file,
        scan_organization_logo,
    )

    facts = facts or {}
    for key in (
        "marketing_logo_url",
        "marketing_logo",
        "marketing_logo_path",
        "logo_path",
        "organization_logo",
        "office_logo",
    ):
        resolved = resolve_stored_logo_file(facts.get(key))
        if resolved:
            return str(resolved)
    scanned = scan_organization_logo(facts.get("organization_id"))
    return str(scanned) if scanned else None


def _header(canvas, facts, *, accent, invert=False, kicker="", fill=None, mute=None):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    pad = _u(width, 40)
    if invert:
        draw.rounded_rectangle(
            (0, 0, width, _u(width, 118)),
            0,
            fill=NAVY,
        )
        fill = fill or WHITE
        mute = mute or SOFT
    else:
        fill = fill or NAVY
        mute = mute or MUTED
    placed = _paste_logo(
        canvas,
        _office_logo(facts),
        box=(_u(width, 64), _u(width, 64)),
        xy=(pad, _u(width, 20)),
    )
    text_x = pad + (placed[0] + _u(width, 14) if placed else 0)
    wordmark = facts.get("wordmark_text")
    if wordmark is None:
        wordmark = _office_brand(facts)
    if wordmark and (facts.get("show_wordmark", True) or not placed):
        _wordmark(
            draw,
            (text_x, _u(width, 34)),
            wordmark,
            fill=fill,
            size=_u(width, 26),
        )
    label = " ".join(str(kicker or facts.get("kicker") or "").split())
    if label:
        used = font(_u(width, 16))
        tw = _text_width(draw, label, used)
        draw.text((width - pad - tw, _u(width, 42)), label, font=used, fill=mute)


def _parse_chip(chip):
    text = str(chip or "").strip()
    match = re.match(r"^(\d+(?:[.,]\d+)?)\s*(.*)$", text)
    if not match:
        return "", text
    return match.group(1), match.group(2).strip()


def _chip_kind(chip, index):
    text = str(chip or "").lower()
    if "m²" in text or "m2" in text or "metro" in text:
        return "area"
    if "baño" in text or "bath" in text:
        return "bath"
    if "dorm" in text or "hab" in text or "bed" in text:
        return "bed"
    if "amb" in text or "room" in text or "ambience" in text:
        return "rooms"
    return ("rooms", "bed", "bath", "area")[min(index, 3)]


def _icon_rooms(draw, box, color):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 2, y0 + 4, x1 - 2, y1 - 2), 3, outline=color, width=2)
    mid_x = (x0 + x1) // 2
    mid_y = (y0 + y1) // 2 + 1
    draw.line((mid_x, y0 + 4, mid_x, y1 - 2), fill=color, width=2)
    draw.line((x0 + 2, mid_y, x1 - 2, mid_y), fill=color, width=2)


def _icon_bed(draw, box, color):
    x0, y0, x1, y1 = box
    draw.line((x0 + 2, y1 - 3, x1 - 2, y1 - 3), fill=color, width=2)
    draw.rounded_rectangle((x0 + 2, y0 + 11, x1 - 2, y1 - 6), 3, outline=color, width=2)
    draw.arc((x0 + 3, y0 + 3, x0 + (x1 - x0) * 0.55, y0 + 16), 200, 360, fill=color, width=2)


def _icon_bath(draw, box, color):
    x0, y0, x1, y1 = box
    draw.arc((x0 + 3, y0 + 8, x1 - 3, y1 - 1), 0, 180, fill=color, width=2)
    draw.line((x0 + 3, y0 + (y1 - y0) // 2 + 2, x1 - 3, y0 + (y1 - y0) // 2 + 2), fill=color, width=2)
    draw.line((x1 - 7, y0 + 3, x1 - 7, y0 + 10), fill=color, width=2)
    draw.ellipse((x1 - 11, y0 + 2, x1 - 3, y0 + 8), outline=color, width=2)


def _icon_area(draw, box, color):
    x0, y0, x1, y1 = box
    draw.rectangle((x0 + 3, y0 + 3, x1 - 3, y1 - 3), outline=color, width=2)
    tick = max(3, (x1 - x0) // 5)
    draw.line((x0 + 3, y0 + 3, x0 + 3 + tick, y0 + 3), fill=color, width=2)
    draw.line((x0 + 3, y0 + 3, x0 + 3, y0 + 3 + tick), fill=color, width=2)
    draw.line((x1 - 3 - tick, y1 - 3, x1 - 3, y1 - 3), fill=color, width=2)
    draw.line((x1 - 3, y1 - 3 - tick, x1 - 3, y1 - 3), fill=color, width=2)


FEATURE_ICON_PAINTERS = {
    "rooms": _icon_rooms,
    "bed": _icon_bed,
    "bath": _icon_bath,
    "area": _icon_area,
}


def _icon_whatsapp(draw, xy, size):
    x, y = xy
    draw.ellipse((x, y, x + size, y + size), fill=WA_GREEN)
    pad = max(2, size // 6)
    draw.arc((x + pad, y + pad, x + size - pad, y + size - pad + 1), 200, 40, fill=WHITE, width=max(2, size // 10))
    draw.polygon(
        (
            (x + size * 0.28, y + size * 0.70),
            (x + size * 0.18, y + size * 0.88),
            (x + size * 0.46, y + size * 0.74),
        ),
        fill=WA_GREEN,
    )
    draw.line(
        (x + size * 0.30, y + size * 0.36, x + size * 0.42, y + size * 0.62),
        fill=WHITE,
        width=max(2, size // 10),
    )


def _icon_instagram(draw, xy, size):
    x, y = xy
    draw.rounded_rectangle((x, y, x + size, y + size), max(4, size // 4), fill=IG_PINK)
    cx = x + size / 2
    cy = y + size / 2
    r = size * 0.22
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=WHITE, width=max(2, size // 10))
    dot = max(2, size // 8)
    draw.ellipse((x + size * 0.68, y + size * 0.18, x + size * 0.68 + dot, y + size * 0.18 + dot), fill=WHITE)


def _feature_icons(draw, chips, xy, *, width, color=ELECTRIC, text_fill=INK, mute=MUTED):
    if not chips:
        return
    x, y = xy
    icon = _u(width, 28)
    number_font = font(_u(width, 24), bold=True)
    label_font = font(_u(width, 13))
    gap = _u(width, 22)
    for index, chip in enumerate(chips[:4]):
        kind = _chip_kind(chip, index)
        number, label = _parse_chip(chip)
        painter = FEATURE_ICON_PAINTERS.get(kind, _icon_area)
        painter(draw, (x, y, x + icon, y + icon), color)
        text_x = x + icon + 8
        if number:
            draw.text((text_x, y - 2), number, font=number_font, fill=text_fill)
            draw.text((text_x, y + _u(width, 22)), label or chip, font=label_font, fill=mute)
            text_w = max(
                _text_width(draw, number, number_font),
                _text_width(draw, label or chip, label_font),
            )
        else:
            draw.text((text_x, y + 4), chip, font=label_font, fill=text_fill)
            text_w = _text_width(draw, chip, label_font)
        x += icon + 8 + text_w + gap


def _draw_icon(draw, xy, color, size):
    _icon_area(draw, (xy[0], xy[1], xy[0] + size, xy[1] + size), color)


def _visible_agent_contacts(agent):
    """WhatsApp and Instagram only. Hide a missing channel. Never show phone."""
    agent = agent or {}
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    contacts = []
    if whatsapp:
        contacts.append({"channel": "whatsapp", "label": whatsapp})
    if instagram:
        contacts.append({"channel": "instagram", "label": instagram})
    return contacts


def _cta_pill(draw, xy, text, *, width, accent=ELECTRIC, min_w=None, filled=True, text_fill=WHITE):
    if not text:
        return
    used = font(_u(width, 20), bold=True)
    label = text
    tw = _text_width(draw, label, used)
    pad_x = _u(width, 28)
    pill_w = max(min_w or 0, tw + pad_x * 2)
    x, y = xy
    h = _u(width, 50)
    if filled:
        draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, fill=accent)
        draw.text((x + (pill_w - tw) // 2, y + _u(width, 13)), label, font=used, fill=text_fill)
        return
    draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, outline=accent, width=2)
    draw.text((x + (pill_w - tw) // 2, y + _u(width, 13)), label, font=used, fill=accent)


def _agent_block(canvas, agent, *, xy, width, show_photo=True, circular=False, size=180, text_beside=False, ink=NAVY, mute=MUTED):
    if not agent:
        return
    x, y = xy
    photo = load_agent_photo(agent.get("photo_path")) if show_photo else None
    photo_h = int(size * 1.28)
    draw = ImageDraw.Draw(canvas)
    name = agent.get("name") or ""
    title = agent.get("title") or ""
    contacts = _visible_agent_contacts(agent)
    name_font = font(_u(width, 22), bold=True)
    title_font = font(_u(width, 14))
    contact_font = font(_u(width, 14))
    icon = _u(width, 20)
    text_w = 0
    for line, used in (
        (name, name_font),
        (title, title_font),
        *[(item["label"], contact_font) for item in contacts],
    ):
        if line:
            extra = icon + 8 if used is contact_font else 0
            text_w = max(text_w, _text_width(draw, line, used) + extra)
    if photo is not None:
        if circular:
            paste_circle(canvas, photo, (x, y), size)
        else:
            paste_agent_cutout(canvas, photo, (x, y), height=photo_h)
        if text_beside:
            text_x = x - text_w - _u(width, 16)
            text_y = y + _u(width, 18)
        else:
            text_x = x
            text_y = y + photo_h + _u(width, 12)
    else:
        text_x = x
        text_y = y
    if name:
        draw.text((text_x, text_y), name, font=name_font, fill=ink)
        text_y += _u(width, 28)
    if title:
        draw.text((text_x, text_y), title, font=title_font, fill=mute)
        text_y += _u(width, 22)
    for contact in contacts:
        if contact["channel"] == "whatsapp":
            _icon_whatsapp(draw, (text_x, text_y + 1), icon)
        else:
            _icon_instagram(draw, (text_x, text_y + 1), icon)
        draw.text((text_x + icon + 8, text_y), contact["label"], font=contact_font, fill=ink)
        text_y += _u(width, 24)


def _address_block(draw, facts, copy, *, xy, width, max_width, light=False):
    x, y = xy
    title_fill = WHITE if light else NAVY
    mute = SOFT if light else MUTED
    headline = (
        copy.get("headline")
        or facts.get("operation_title")
        or facts.get("type_label")
        or ""
    ).upper()
    if headline:
        title_font = font(_u(width, 48), bold=True)
        for line in _wrap(draw, headline, title_font, max_width)[:2]:
            draw.text((x, y), line, font=title_font, fill=title_fill)
            y += _u(width, 54)
    zone = copy.get("zone") or facts.get("zone_line") or facts.get("locality") or ""
    if zone:
        draw.text((x, y + 2), zone, font=font(_u(width, 28), bold=True), fill=title_fill)
        y += _u(width, 36)
    street = copy.get("street") or facts.get("title") or ""
    if street and street.casefold() not in zone.casefold():
        draw.text((x, y + 2), street, font=font(_u(width, 20)), fill=mute)
        y += _u(width, 28)
    bajada = copy.get("subheadline") or facts.get("benefit_line") or ""
    if bajada:
        bajada_font = font(_u(width, 18))
        for line in _wrap(draw, bajada, bajada_font, max_width)[:2]:
            draw.text((x, y + 2), line, font=bajada_font, fill=mute)
            y += _u(width, 24)
    return y


def _legal_footer(canvas, facts, *, line=LINE, mute=MUTED):
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    pad = _u(width, 40)
    bar_h = _u(width, 52)
    top = height - bar_h
    draw.line((pad, top, width - pad, top), fill=line, width=1)
    legal = (
        facts.get("legal_footer_line")
        or facts.get("broker_footer_text")
        or ""
    )
    if legal:
        draw.text(
            (pad, top + _u(width, 16)),
            legal,
            font=font(_u(width, 12)),
            fill=MUTED,
        )


def _price(draw, facts, options, *, xy, width, fill=NAVY, size=72):
    if not (options.get("show_price") and facts.get("price_label")):
        return
    draw.text(xy, facts["price_label"], font=font(_u(width, size), bold=True), fill=fill)


def render_editorial(size, photos, facts, copy, agent, options, style):
    """Modern listing flyer: hero-led photo grid, cutout agent, contact icons."""
    return render_modern_listing(size, photos, facts, copy or {}, agent, options or {}, style)


def render_modern_listing(size, photos, facts, copy, agent, options, style, *, fallback_hero=None):
    width, height = size
    colors = theme_palette(style)
    canvas = Image.new("RGBA", size, (*colors["field"], 255))
    draw = ImageDraw.Draw(canvas)
    accent = colors["accent"]
    pad = _u(width, 40)
    facts = facts or {}
    copy = copy or {}
    options = options or {}
    photos = [item for item in (photos or []) if item is not None]
    if not photos and fallback_hero is not None:
        photos = [fallback_hero]
    _header(
        canvas,
        facts,
        accent=accent,
        kicker=copy.get("kicker"),
        fill=colors["ink"],
        mute=colors["mute"],
    )
    show_agent = bool(options.get("include_agent") and agent)
    show_photo = bool(show_agent and options.get("show_agent_photo", True) and (agent or {}).get("photo_path"))
    fmt = "story"
    if size == FORMAT_SIZES.get("post"):
        fmt = "post"
    elif size == FORMAT_SIZES.get("flyer"):
        fmt = "flyer"
    legal_h = _u(width, 46)
    contact_h = _u(width, 132) if show_agent else _u(width, 58)
    feat_h = _u(width, 54) if options.get("show_features", True) and (facts.get("chips") or copy.get("attributes")) else 0
    price_h = _u(width, 58) if options.get("show_price", True) and facts.get("price_label") else 0
    bajada_h = _u(width, 28) if (copy.get("subheadline") or facts.get("benefit_line")) else 0
    content_h = (
        _u(width, 16) + _u(width, 118) + _u(width, 72) + bajada_h + price_h + feat_h + contact_h + legal_h
    )
    photo_top = _u(width, 88)
    photo_h = max(_u(width, 320), height - photo_top - content_h)
    compose_photo_grid(
        canvas,
        photos,
        box=(pad, photo_top, width - pad * 2, photo_h),
        radius=_u(width, 16),
        gap=_u(width, 10),
        fmt=fmt,
    )
    y = photo_top + photo_h + _u(width, 16)
    reserve = _u(width, 220) if show_photo else 0
    y = _address_block(
        draw,
        facts,
        copy,
        xy=(pad, y),
        width=width,
        max_width=width - pad * 2 - reserve,
        light=colors["theme"] == "blue",
    )
    if options.get("show_price", True):
        _price(draw, facts, options, xy=(pad, y), width=width, size=56, fill=colors["ink"])
        y += price_h
    if options.get("show_features", True):
        _feature_icons(
            draw,
            facts.get("chips") or copy.get("attributes") or [],
            (pad, y + 2),
            width=width,
            color=accent,
            text_fill=colors["ink"],
            mute=colors["mute"],
        )
        y += feat_h
    _cta_pill(
        draw,
        (pad, y + 2),
        copy.get("cta") or "Contáctanos",
        width=width,
        accent=accent,
        min_w=_u(width, 220),
        filled=True,
        text_fill=WHITE,
    )
    if show_agent:
        cutout_w = _u(width, 150)
        _agent_block(
            canvas,
            agent,
            xy=(width - pad - cutout_w, height - legal_h - _u(width, 200)),
            width=width,
            show_photo=show_photo,
            size=cutout_w,
            text_beside=True,
            ink=colors["ink"],
            mute=colors["mute"],
        )
    _legal_footer(canvas, facts, line=colors["line"], mute=colors["mute"])
    return canvas.convert("RGB")


def render_visual(size, photos, facts, copy, agent, options, style):
    """Same locked layout as Light/Blue Premium."""
    return render_editorial(size, photos, facts, copy, agent, options, style)


def render_minimal(size, photos, facts, copy, agent, options, style):
    """Same locked layout as Light/Blue Premium."""
    return render_editorial(size, photos, facts, copy, agent, options, style)


RENDERERS = {
    "editorial": render_editorial,
    "visual": render_visual,
    "minimal": render_minimal,
}


def render_marketing_image(context, copy, *, fmt, template, style, options):
    size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    photos = load_property_photos((context or {}).get("photos") or [])
    facts = (context or {}).get("facts") or {}
    agent = (context or {}).get("agent") if options.get("include_agent") else None
    del template
    image = render_editorial(size, photos, facts, copy or {}, agent, options or {}, style)
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), size


def png_to_pdf_bytes(png_bytes):
    image = Image.open(io.BytesIO(png_bytes))
    page = A4
    packet = io.BytesIO()
    c = pdf_canvas.Canvas(packet, pagesize=page)
    width, height = page
    c.drawImage(
        ImageReader(image),
        0,
        0,
        width=width,
        height=height,
        preserveAspectRatio=True,
        anchor="c",
    )
    c.save()
    packet.seek(0)
    return packet.getvalue()
