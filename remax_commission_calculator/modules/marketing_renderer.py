"""Deterministic Pillow marketing renderer. LLM never positions pixels."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from modules.property_sync.media import resolve_media_filesystem_path
from modules.property_sync.remote_media import fetch_allowed_image_bytes


NAVY = (10, 22, 51)
ELECTRIC = (13, 71, 255)
WHITE = (255, 255, 255)
SOFT = (238, 243, 255)
INK = (51, 65, 92)
MUTED = (91, 107, 124)
GOLD = (214, 196, 160)

FORMAT_SIZES = {
    "story": (1080, 1920),
    "status": (1080, 1920),
    "post": (1080, 1350),
    "flyer": (1240, 1754),
}

STYLE_ACCENT = {
    "elegant": GOLD,
    "modern": ELECTRIC,
    "minimal": ELECTRIC,
}


def _font_path(bold=False):
    candidates = (
        (
            Path(r"C:\Windows\Fonts\segoeuib.ttf")
            if bold
            else Path(r"C:\Windows\Fonts\segoeui.ttf")
        ),
        Path(r"C:\Windows\Fonts\arialbd.ttf") if bold else Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if bold
        else Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def font(size, *, bold=False):
    path = _font_path(bold)
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


def fit_cover(image, width, height):
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return image.resize((width, height), Image.Resampling.LANCZOS)
    scale = max(width / src_w, height / src_h)
    resized = image.resize(
        (max(1, int(src_w * scale)), max(1, int(src_h * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def load_property_photos(photo_rows, *, cache=None):
    images = []
    cache = cache if cache is not None else {}
    for item in photo_rows or []:
        try:
            path = resolve_media_filesystem_path(item)
            image = _open_image(path) if path else None
            if image is None:
                remote = fetch_allowed_image_bytes(item.get("original_url"), cache=cache)
                image = _open_image(remote)
            if image is None:
                continue
            images.append(image.convert("RGB"))
        except Exception:
            continue
    return images


def load_agent_photo(path):
    image = _open_image(path)
    if image is None:
        return None
    return _as_rgba(image)


def _vertical_fade(width, height, start_alpha=0, end_alpha=220, color=NAVY):
    gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)
    for index in range(height):
        ratio = index / max(1, height - 1)
        alpha = int(start_alpha + (end_alpha - start_alpha) * ratio)
        draw.line([(0, index), (width, index)], fill=(*color, alpha))
    return gradient


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


def _draw_text(draw, xy, text, used_font, fill=WHITE, shadow=True):
    x, y = xy
    if shadow:
        draw.text((x + 1, y + 2), text, font=used_font, fill=(0, 0, 0, 110))
    draw.text((x, y), text, font=used_font, fill=fill)


@lru_cache(maxsize=12)
def _logo_rgba(path):
    image = _open_image(path)
    if image is None:
        return None
    return _as_rgba(image).copy()


def _paste_logo(canvas, logo_path, *, box, xy=(48, 48)):
    image = _logo_rgba(str(logo_path)) if logo_path else None
    if image is None:
        return
    image = image.copy()
    image.thumbnail(box, Image.Resampling.LANCZOS)
    canvas.paste(image, xy, image)


def _paste_agent(canvas, agent, *, xy, size=168, show_photo=True):
    if not agent:
        return
    x, y = xy
    photo = load_agent_photo(agent.get("photo_path")) if show_photo else None
    if photo is not None:
        photo = fit_cover(photo, size, int(size * 1.15))
        canvas.paste(photo, (x, y), photo if photo.mode == "RGBA" else None)
        text_x = x + size + 22
    else:
        text_x = x
    draw = ImageDraw.Draw(canvas)
    name_font = font(28, bold=True)
    meta_font = font(22)
    _draw_text(draw, (text_x, y + 18), agent.get("name") or "", name_font)
    contact = agent.get("phone") or agent.get("email") or ""
    if contact:
        _draw_text(draw, (text_x, y + 58), contact, meta_font, fill=SOFT)


def _facts_line(facts):
    return "  ·  ".join(facts.get("chips") or [])


def _draw_price_block(draw, facts, options, *, xy, accent, large=False):
    x, y = xy
    if options.get("show_price") and facts.get("price_label"):
        _draw_text(draw, (x, y), facts["price_label"], font(86 if large else 64, bold=True))
        y += 96 if large else 74
    purpose = (facts.get("purpose_label") or "").upper()
    if purpose:
        _draw_text(draw, (x, y), purpose, font(26, bold=True), fill=accent)
        y += 40
    return y


def _base_canvas(size, color=NAVY):
    return Image.new("RGBA", size, (*color, 255))


def render_editorial(size, photos, facts, copy, agent, options, style):
    width, height = size
    canvas = _base_canvas(size)
    cover = photos[0] if photos else None
    if cover is not None:
        photo = fit_cover(cover, width, height)
        photo = ImageEnhance.Contrast(photo).enhance(1.06)
        canvas.paste(photo.convert("RGBA"), (0, 0))
    top = _vertical_fade(width, 180, 90, 0)
    canvas.alpha_composite(top, (0, 0))
    fade = _vertical_fade(width, int(height * 0.42), 0, 210)
    canvas.alpha_composite(fade, (0, height - fade.height))
    draw = ImageDraw.Draw(canvas)
    accent = STYLE_ACCENT.get(style, ELECTRIC)
    _paste_logo(canvas, facts.get("organization_logo"), box=(220, 72))
    y = _draw_price_block(
        draw, facts, options, xy=(56, int(height * 0.52)), accent=accent, large=True
    )
    title_font = font(54, bold=True)
    for line in _wrap(draw, copy.get("headline") or facts.get("title"), title_font, width - 120)[:3]:
        _draw_text(draw, (56, y), line, title_font)
        y += 64
    loc = facts.get("location_line") or facts.get("locality") or ""
    if loc:
        _draw_text(draw, (56, y + 8), loc, font(30), fill=SOFT)
        y += 50
    chips = _facts_line(facts)
    if chips and options.get("show_features", True):
        _draw_text(draw, (56, y + 8), chips, font(26), fill=SOFT)
    if options.get("include_agent") and agent:
        _paste_agent(
            canvas,
            agent,
            xy=(56, height - 230),
            show_photo=options.get("show_agent_photo", True),
        )
    return canvas.convert("RGB")


def render_visual(size, photos, facts, copy, agent, options, style):
    width, height = size
    canvas = _base_canvas(size, NAVY)
    draw = ImageDraw.Draw(canvas)
    accent = STYLE_ACCENT.get(style, ELECTRIC)
    margin = 36
    gallery = photos[:3] or photos[:1]
    top = 150
    body_h = int(height * 0.58)
    if len(gallery) == 1:
        photo = fit_cover(gallery[0], width - margin * 2, body_h)
        canvas.paste(photo.convert("RGBA"), (margin, top))
    else:
        left_w = int((width - margin * 2 - 16) * 0.62)
        right_w = width - margin * 2 - 16 - left_w
        left = fit_cover(gallery[0], left_w, body_h)
        canvas.paste(left.convert("RGBA"), (margin, top))
        stack_h = (body_h - 16) // 2
        extra = gallery[1:3]
        for index, image in enumerate(extra):
            tile = fit_cover(image, right_w, stack_h)
            canvas.paste(
                tile.convert("RGBA"),
                (margin + left_w + 16, top + index * (stack_h + 16)),
            )
    _paste_logo(canvas, facts.get("organization_logo"), box=(200, 64))
    purpose = (facts.get("purpose_label") or "").upper()
    if purpose:
        _draw_text(draw, (width - 280, 56), purpose, font(24, bold=True), fill=accent)
    y = top + body_h + 36
    if options.get("show_price") and facts.get("price_label"):
        _draw_text(draw, (margin, y), facts["price_label"], font(58, bold=True))
        y += 70
    for line in _wrap(draw, copy.get("headline") or facts.get("title"), font(40, bold=True), width - 80)[:2]:
        _draw_text(draw, (margin, y), line, font(40, bold=True))
        y += 50
    if options.get("show_features", True):
        chips = _facts_line(facts)
        if chips:
            _draw_text(draw, (margin, y + 6), chips, font(24), fill=SOFT)
    if options.get("include_agent") and agent:
        _paste_agent(
            canvas,
            agent,
            xy=(margin, height - 210),
            size=140,
            show_photo=options.get("show_agent_photo", True),
        )
    return canvas.convert("RGB")


def render_minimal(size, photos, facts, copy, agent, options, style):
    width, height = size
    canvas = Image.new("RGB", size, WHITE)
    draw = ImageDraw.Draw(canvas)
    accent = STYLE_ACCENT.get(style, ELECTRIC)
    pad = 72 if style == "minimal" else 56
    photo_h = int(height * 0.56)
    if photos:
        photo = fit_cover(photos[0], width - pad * 2, photo_h)
        canvas.paste(photo, (pad, pad))
    y = pad + photo_h + 40
    navy_draw = ImageDraw.Draw(canvas)
    purpose = (facts.get("purpose_label") or "").upper()
    if purpose:
        navy_draw.text((pad, y), purpose, font=font(22, bold=True), fill=accent)
        y += 36
    title_font = font(48, bold=True)
    for line in _wrap(draw, copy.get("headline") or facts.get("title"), title_font, width - pad * 2)[:2]:
        navy_draw.text((pad, y), line, font=title_font, fill=NAVY)
        y += 56
    if options.get("show_price") and facts.get("price_label"):
        navy_draw.text((pad, y + 8), facts["price_label"], font=font(56, bold=True), fill=NAVY)
        y += 72
    loc = facts.get("location_line") or ""
    if loc:
        navy_draw.text((pad, y), loc, font=font(26), fill=INK)
        y += 40
    if options.get("show_features", True) and facts.get("chips"):
        navy_draw.text((pad, y), _facts_line(facts), font=font(24), fill=MUTED)
    if options.get("include_agent") and agent:
        name = agent.get("name") or ""
        contact = agent.get("phone") or agent.get("email") or ""
        navy_draw.text((pad, height - 140), name, font=font(26, bold=True), fill=NAVY)
        if contact:
            navy_draw.text((pad, height - 100), contact, font=font(22), fill=INK)
        photo = (
            load_agent_photo(agent.get("photo_path"))
            if options.get("show_agent_photo", True)
            else None
        )
        if photo is not None:
            fitted = fit_cover(photo, 120, 140)
            canvas.paste(fitted, (width - pad - 120, height - 200), fitted)
    logo = _open_image(facts.get("organization_logo"))
    if logo is not None:
        logo = _as_rgba(logo)
        logo.thumbnail((180, 56), Image.Resampling.LANCZOS)
        canvas.paste(logo, (width - pad - logo.width, 36), logo)
    return canvas


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
    renderer = RENDERERS.get(template) or render_editorial
    image = renderer(size, photos, facts, copy or {}, agent, options or {}, style)
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
