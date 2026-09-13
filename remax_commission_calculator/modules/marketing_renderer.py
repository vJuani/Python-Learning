"""Deterministic Pillow marketing renderer. Matches JRH flyer language."""

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
SOFT = (244, 247, 252)
INK = (17, 28, 51)
MUTED = (91, 107, 124)
GOLD = (196, 164, 92)
LINE = (226, 232, 240)

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


def fit_contain(image, width, height, *, fill=NAVY):
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


def fit_contain_safe(image, size, fmt=None, *, fill=NAVY):
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
            images.append(ImageEnhance.Contrast(image.convert("RGB")).enhance(1.04))
        except Exception:
            continue
    return images


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
    image = _open_image(path)
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


def _u(width, value):
    return max(1, int(value * width / 1080))


def _paste_logo(canvas, logo_path, *, box, xy):
    image = _logo_rgba(str(logo_path)) if logo_path else None
    if image is None:
        return False
    image = image.copy()
    image.thumbnail(box, Image.Resampling.LANCZOS)
    canvas.paste(image, xy, image)
    return True


def _wordmark(draw, xy, brand, *, fill=NAVY, size=36):
    x, y = xy
    draw.text((x, y), brand or "JRH One", font=font(size, bold=True), fill=fill)


def _header(canvas, facts, *, accent, invert=False):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    pad = _u(width, 48)
    if invert:
        draw.rounded_rectangle(
            (0, 0, width, _u(width, 118)),
            0,
            fill=NAVY,
        )
        fill = WHITE
    else:
        fill = NAVY
    placed = _paste_logo(
        canvas,
        facts.get("organization_logo"),
        box=(_u(width, 220), _u(width, 64)),
        xy=(pad, _u(width, 28)),
    )
    if not placed:
        _wordmark(draw, (pad, _u(width, 34)), facts.get("brand_name"), fill=fill, size=_u(width, 34))
    purpose = (facts.get("purpose_label") or "").upper()
    if purpose:
        badge_w = _text_width(draw, purpose, font(_u(width, 20), bold=True)) + _u(width, 48)
        bx = width - pad - badge_w
        by = _u(width, 36)
        draw.rounded_rectangle(
            (bx, by, bx + badge_w, by + _u(width, 46)),
            _u(width, 23),
            fill=NAVY if not invert else ELECTRIC,
        )
        draw.text(
            (bx + _u(width, 24), by + _u(width, 10)),
            purpose,
            font=font(_u(width, 20), bold=True),
            fill=WHITE,
        )


def _feature_icons(draw, chips, xy, *, width, color=ELECTRIC, text_fill=INK):
    if not chips:
        return
    x, y = xy
    gap = _u(width, 28)
    icon = _u(width, 22)
    used = font(_u(width, 22))
    for chip in chips[:4]:
        _draw_icon(draw, (x, y + 2), color, icon)
        draw.text((x + icon + 8, y), chip, font=used, fill=text_fill)
        x += icon + 8 + _text_width(draw, chip, used) + gap


def _draw_icon(draw, xy, color, size):
    x, y = xy
    draw.rounded_rectangle((x, y, x + size, y + size), 6, outline=color, width=2)
    draw.line((x + 4, y + size * 0.55, x + size - 4, y + size * 0.55), fill=color, width=2)


def _cta_pill(draw, xy, text, *, width, accent=ELECTRIC, min_w=None):
    if not text:
        return
    used = font(_u(width, 26), bold=True)
    label = f"{text}  →"
    tw = _text_width(draw, label, used)
    pad_x = _u(width, 36)
    pill_w = max(min_w or 0, tw + pad_x * 2)
    x, y = xy
    h = _u(width, 64)
    draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, fill=accent)
    draw.text((x + (pill_w - tw) // 2, y + _u(width, 16)), label, font=used, fill=WHITE)


def _agent_block(canvas, agent, *, xy, width, show_photo=True, circular=False, size=180):
    if not agent:
        return
    x, y = xy
    photo = load_agent_photo(agent.get("photo_path")) if show_photo else None
    photo_h = size if circular else int(size * 1.18)
    if photo is not None:
        if circular:
            paste_circle(canvas, photo, (x, y), size)
        else:
            paste_rounded(canvas, photo, (x, y), (size, photo_h), radius=_u(width, 28))
        text_y = y + photo_h + _u(width, 12)
    else:
        text_y = y
    draw = ImageDraw.Draw(canvas)
    name = agent.get("name") or ""
    title = agent.get("title") or ""
    draw.text((x, text_y), name, font=font(_u(width, 24), bold=True), fill=NAVY)
    if title:
        draw.text(
            (x, text_y + _u(width, 32)),
            title,
            font=font(_u(width, 16)),
            fill=MUTED,
        )


def _address_block(draw, facts, copy, *, xy, width, max_width, light=False):
    x, y = xy
    title_fill = WHITE if light else NAVY
    mute = SOFT if light else MUTED
    kicker = (copy.get("headline") or facts.get("type_label") or "").upper()
    if kicker:
        draw.text((x, y), kicker, font=font(_u(width, 20), bold=True), fill=ELECTRIC if not light else WHITE)
        y += _u(width, 34)
    title = facts.get("title") or copy.get("headline") or ""
    title_font = font(_u(width, 64), bold=True)
    for line in _wrap(draw, title, title_font, max_width)[:2]:
        draw.text((x, y), line, font=title_font, fill=title_fill)
        y += _u(width, 70)
    loc = facts.get("location_line") or facts.get("locality") or ""
    if loc:
        draw.text((x, y + 4), f"●  {loc}", font=font(_u(width, 24)), fill=mute)
        y += _u(width, 40)
    return y


def _price(draw, facts, options, *, xy, width, fill=NAVY, size=72):
    if not (options.get("show_price") and facts.get("price_label")):
        return
    draw.text(xy, facts["price_label"], font=font(_u(width, size), bold=True), fill=fill)


def render_editorial(size, photos, facts, copy, agent, options, style):
    """Hero + overlay title, two thumbs, agent card. Reference flyer 1."""
    width, height = size
    canvas = Image.new("RGBA", size, WHITE)
    draw = ImageDraw.Draw(canvas)
    accent = STYLE_ACCENT.get(style, ELECTRIC)
    pad = _u(width, 48)
    _header(canvas, facts, accent=accent)
    hero_top = _u(width, 130)
    hero_h = int(height * 0.42)
    if photos:
        paste_rounded(
            canvas,
            photos[0],
            (pad, hero_top),
            (width - pad * 2, hero_h),
            radius=_u(width, 36),
        )
        tint = Image.new("RGBA", (width - pad * 2, hero_h), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(tint)
        fade_h = int(hero_h * 0.48)
        for index in range(fade_h):
            ratio = index / max(1, fade_h - 1)
            tdraw.line(
                [(0, hero_h - fade_h + index), (width - pad * 2, hero_h - fade_h + index)],
                fill=(10, 22, 51, int(20 + 200 * ratio)),
            )
        alpha = _rounded_mask(width - pad * 2, hero_h, _u(width, 36))
        tint_alpha = tint.split()[-1]
        merged = Image.new("L", tint.size, 0)
        merged.paste(tint_alpha, (0, 0), alpha)
        tint.putalpha(merged)
        canvas.paste(tint, (pad, hero_top), tint)
        overlay_draw = ImageDraw.Draw(canvas)
        _address_block(
            overlay_draw,
            facts,
            copy,
            xy=(pad + _u(width, 36), hero_top + hero_h - _u(width, 280)),
            width=width,
            max_width=width - pad * 2 - _u(width, 80),
            light=True,
        )
        if options.get("show_features", True):
            _feature_icons(
                overlay_draw,
                facts.get("chips") or [],
                (pad + _u(width, 36), hero_top + hero_h - _u(width, 70)),
                width=width,
                color=WHITE,
                text_fill=WHITE,
            )
    y = hero_top + hero_h + _u(width, 36)
    _price(draw, facts, options, xy=(pad, y), width=width, size=78)
    y += _u(width, 100)
    thumbs_h = _u(width, 220)
    remaining = photos[1:3]
    if remaining:
        thumb_w = (width - pad * 2 - _u(width, 240) - _u(width, 24)) // max(1, len(remaining))
        for index, photo in enumerate(remaining):
            paste_rounded(
                canvas,
                photo,
                (pad + index * (thumb_w + 12), y),
                (thumb_w, thumbs_h),
                radius=_u(width, 24),
            )
    if options.get("include_agent") and agent:
        _agent_block(
            canvas,
            agent,
            xy=(width - pad - _u(width, 200), y - _u(width, 10)),
            width=width,
            show_photo=options.get("show_agent_photo", True),
            size=_u(width, 200),
        )
    y += thumbs_h + _u(width, 36)
    _cta_pill(
        draw,
        (pad, y),
        copy.get("cta") or "Consultá por esta propiedad",
        width=width,
        accent=accent,
        min_w=width - pad * 2 - _u(width, 280),
    )
    footer_top = height - _u(width, 110)
    draw.rectangle((0, footer_top, width, height), fill=NAVY)
    _paste_logo(
        canvas,
        facts.get("organization_logo"),
        box=(_u(width, 180), _u(width, 50)),
        xy=(pad, footer_top + _u(width, 28)),
    )
    draw.text(
        (pad + _u(width, 200), footer_top + _u(width, 40)),
        facts.get("brand_name") or "JRH One",
        font=font(_u(width, 22), bold=True),
        fill=WHITE,
    )
    return canvas.convert("RGB")


def render_visual(size, photos, facts, copy, agent, options, style):
    """Asymmetric collage + price + agent. Reference flyer 3."""
    width, height = size
    canvas = Image.new("RGBA", size, WHITE)
    draw = ImageDraw.Draw(canvas)
    accent = STYLE_ACCENT.get(style, ELECTRIC)
    pad = _u(width, 48)
    _header(canvas, facts, accent=accent)
    top = _u(width, 136)
    gallery_h = int(height * 0.38)
    gallery = photos[:3]
    if len(gallery) == 1:
        paste_rounded(
            canvas, gallery[0], (pad, top), (width - pad * 2, gallery_h), radius=_u(width, 32)
        )
    elif gallery:
        left_w = int((width - pad * 2 - 16) * 0.62)
        right_w = width - pad * 2 - 16 - left_w
        paste_rounded(
            canvas, gallery[0], (pad, top), (left_w, gallery_h), radius=_u(width, 28)
        )
        extra = gallery[1:3]
        stack_h = (gallery_h - 16) // max(1, len(extra))
        for index, image in enumerate(extra):
            paste_rounded(
                canvas,
                image,
                (pad + left_w + 16, top + index * (stack_h + 16)),
                (right_w, stack_h),
                radius=_u(width, 24),
            )
    y = _address_block(
        draw,
        facts,
        copy,
        xy=(pad, top + gallery_h + _u(width, 28)),
        width=width,
        max_width=width - pad * 2 - (_u(width, 280) if agent else 0),
    )
    if options.get("show_features", True):
        _feature_icons(draw, facts.get("chips") or [], (pad, y + 8), width=width)
        y += _u(width, 56)
    _price(draw, facts, options, xy=(pad, y + 8), width=width, size=82)
    if options.get("include_agent") and agent:
        _agent_block(
            canvas,
            agent,
            xy=(width - pad - _u(width, 220), y - _u(width, 20)),
            width=width,
            show_photo=options.get("show_agent_photo", True),
            size=_u(width, 210),
        )
    y += _u(width, 130)
    _cta_pill(
        draw,
        (pad, min(y, height - _u(width, 160))),
        copy.get("cta") or "Escribinos",
        width=width,
        accent=accent,
    )
    return canvas.convert("RGB")


def render_minimal(size, photos, facts, copy, agent, options, style):
    """One hero, lots of air, chips, price + CTA, circular agent. Reference flyer 8."""
    width, height = size
    canvas = Image.new("RGBA", size, WHITE)
    draw = ImageDraw.Draw(canvas)
    accent = STYLE_ACCENT.get(style, ELECTRIC)
    pad = _u(width, 52)
    _header(canvas, facts, accent=accent)
    top = _u(width, 136)
    hero_h = int(height * 0.36)
    if photos:
        paste_rounded(
            canvas, photos[0], (pad, top), (width - pad * 2, hero_h), radius=_u(width, 36)
        )
    y = top + hero_h + _u(width, 28)
    purpose = " · ".join(
        part
        for part in (
            (facts.get("type_label") or "").upper(),
            (facts.get("purpose_label") or "").upper(),
        )
        if part
    )
    if purpose:
        draw.text((pad, y), purpose, font=font(_u(width, 20), bold=True), fill=MUTED)
        y += _u(width, 32)
    title_font = font(_u(width, 58), bold=True)
    for line in _wrap(draw, facts.get("title") or "", title_font, width - pad * 2)[:2]:
        draw.text((pad, y), line, font=title_font, fill=NAVY)
        y += _u(width, 64)
    loc = facts.get("location_line") or ""
    if loc:
        draw.text((pad, y), f"●  {loc}", font=font(_u(width, 24)), fill=MUTED)
        y += _u(width, 40)
    script = copy.get("headline") or ""
    if script:
        draw.text((pad, y), script, font=font(_u(width, 28), italic=True), fill=ELECTRIC)
        y += _u(width, 46)
    if options.get("show_features", True):
        _feature_icons(draw, facts.get("chips") or [], (pad, y), width=width)
        y += _u(width, 58)
    _price(draw, facts, options, xy=(pad, y), width=width, size=70)
    price_w = _text_width(draw, facts.get("price_label") or "", font(_u(width, 70), bold=True))
    _cta_pill(
        draw,
        (pad + price_w + _u(width, 28), y + _u(width, 12)),
        copy.get("cta") or "Consultá",
        width=width,
        accent=accent,
    )
    y += _u(width, 110)
    thumbs = photos[1:3]
    thumb_h = _u(width, 200)
    if thumbs:
        thumb_w = _u(width, 250)
        for index, photo in enumerate(thumbs):
            paste_rounded(
                canvas,
                photo,
                (pad + index * (thumb_w + 16), y),
                (thumb_w, thumb_h),
                radius=_u(width, 24),
            )
    if options.get("include_agent") and agent:
        _agent_block(
            canvas,
            agent,
            xy=(width - pad - _u(width, 200), y),
            width=width,
            show_photo=options.get("show_agent_photo", True),
            circular=True,
            size=_u(width, 168),
        )
    return canvas.convert("RGB")


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
