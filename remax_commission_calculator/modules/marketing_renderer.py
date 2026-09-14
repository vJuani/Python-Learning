"""Deterministic Pillow marketing renderer. Matches JRH flyer language."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

from modules.marketing_branding import usable_brand_name
from modules.marketing_visual_spec import theme_palette
from modules.property_sync.media import resolve_media_filesystem_path
from modules.property_sync.remote_media import fetch_allowed_image_bytes


NAVY = (10, 22, 51)
ELECTRIC = (13, 71, 255)
WHITE = (255, 255, 255)
IVORY = (250, 247, 241)
SOFT = (247, 244, 238)
INK = (17, 28, 51)
MUTED = (91, 107, 124)
GOLD = (196, 164, 92)
LINE = (226, 228, 222)

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


def _visible_agent_contacts(agent):
    """WhatsApp and Instagram only. Hide a missing channel. Never show phone."""
    agent = agent or {}
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    lines = []
    if whatsapp:
        lines.append(whatsapp)
    if instagram:
        lines.append(instagram)
    return lines


def _cta_pill(draw, xy, text, *, width, accent=ELECTRIC, min_w=None):
    if not text:
        return
    used = font(_u(width, 20), bold=True)
    label = text
    tw = _text_width(draw, label, used)
    pad_x = _u(width, 28)
    pill_w = max(min_w or 0, tw + pad_x * 2)
    x, y = xy
    h = _u(width, 50)
    draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, outline=accent, width=2)
    draw.text((x + (pill_w - tw) // 2, y + _u(width, 13)), label, font=used, fill=accent)


def _agent_block(canvas, agent, *, xy, width, show_photo=True, circular=False, size=180, text_beside=False, ink=NAVY, mute=MUTED):
    if not agent:
        return
    x, y = xy
    photo = load_agent_photo(agent.get("photo_path")) if show_photo else None
    photo_h = size if circular else int(size * 1.18)
    draw = ImageDraw.Draw(canvas)
    name = agent.get("name") or ""
    title = agent.get("title") or ""
    contacts = _visible_agent_contacts(agent)
    name_font = font(_u(width, 22), bold=True)
    title_font = font(_u(width, 14))
    contact_font = font(_u(width, 13))
    text_w = 0
    for line, used in (
        (name, name_font),
        (title, title_font),
        *[(item, contact_font) for item in contacts],
    ):
        if line:
            text_w = max(text_w, _text_width(draw, line, used))
    if photo is not None:
        if circular:
            paste_circle(canvas, photo, (x, y), size)
        else:
            paste_rounded(canvas, photo, (x, y), (size, photo_h), radius=_u(width, 22))
        if text_beside:
            text_x = x - text_w - _u(width, 18)
            text_y = y + _u(width, 10)
        else:
            text_x = x
            text_y = y + photo_h + _u(width, 16)
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
        draw.text((text_x, text_y), contact, font=contact_font, fill=mute)
        text_y += _u(width, 20)


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
        title_font = font(_u(width, 46), bold=True)
        for line in _wrap(draw, headline, title_font, max_width)[:2]:
            draw.text((x, y), line, font=title_font, fill=title_fill)
            y += _u(width, 52)
    street = copy.get("street") or facts.get("title") or ""
    if street:
        draw.text((x, y + 2), street, font=font(_u(width, 30), bold=True), fill=title_fill)
        y += _u(width, 40)
    zone = copy.get("zone") or facts.get("zone_line") or facts.get("locality") or ""
    if zone:
        draw.text((x, y + 2), zone, font=font(_u(width, 20)), fill=mute)
        y += _u(width, 34)
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
    """Large hero, two thumbs, commercial title below the photo."""
    width, height = size
    colors = theme_palette(style)
    canvas = Image.new("RGBA", size, (*colors["field"], 255))
    draw = ImageDraw.Draw(canvas)
    accent = colors["accent"]
    pad = _u(width, 40)
    _header(
        canvas,
        facts,
        accent=accent,
        kicker=copy.get("kicker"),
        fill=colors["ink"],
        mute=colors["mute"],
    )
    hero_top = _u(width, 104)
    hero_h = int(height * 0.44)
    if photos:
        paste_rounded(
            canvas,
            photos[0],
            (pad, hero_top),
            (width - pad * 2, hero_h),
            radius=_u(width, 28),
        )
    y = hero_top + hero_h + _u(width, 22)
    thumbs_h = _u(width, 168)
    remaining = photos[1:3]
    if remaining:
        gap = _u(width, 14)
        thumb_w = (width - pad * 2 - gap) // max(1, len(remaining))
        for index, photo in enumerate(remaining):
            paste_rounded(
                canvas,
                photo,
                (pad + index * (thumb_w + gap), y),
                (thumb_w, thumbs_h),
                radius=_u(width, 18),
            )
        y += thumbs_h + _u(width, 24)
    y = _address_block(
        draw,
        facts,
        copy,
        xy=(pad, y),
        width=width,
        max_width=width - pad * 2,
        light=colors["theme"] == "blue",
    )
    if options.get("show_features", True):
        _feature_icons(
            draw,
            facts.get("chips") or [],
            (pad, y + 6),
            width=width,
            color=accent,
            text_fill=colors["ink"],
        )
        y += _u(width, 52)
    _price(draw, facts, options, xy=(pad, y + 4), width=width, size=70, fill=colors["ink"])
    y += _u(width, 92)
    _cta_pill(
        draw,
        (pad, min(y, height - _u(width, 240))),
        copy.get("cta") or "Contáctanos",
        width=width,
        accent=accent,
        min_w=_u(width, 220),
    )
    if options.get("include_agent") and agent:
        _agent_block(
            canvas,
            agent,
            xy=(width - pad - _u(width, 128), height - _u(width, 248)),
            width=width,
            show_photo=options.get("show_agent_photo", True),
            size=_u(width, 128),
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
