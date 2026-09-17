"""modern_premium flyer. New layout matching the approved reference board."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from modules.marketing_language import (
    default_kicker,
    hero_stack_lines,
    marketing_label,
)
from modules.marketing_renderer import (
    IG_PINK,
    INK,
    IVORY,
    MUTED,
    NAVY,
    WA_GREEN,
    WHITE,
    _office_brand,
    _office_logo,
    _parse_chip,
    _paste_logo,
    _text_width,
    _u,
    _wrap,
    font,
    load_agent_photo,
    paste_agent_cutout,
    paste_cover_rounded,
)

MODERN_PREMIUM_V1 = "modern_premium_v1"
MODERN_PREMIUM_LAYOUT = MODERN_PREMIUM_V1
MODERN_PREMIUM_ALIASES = frozenset({MODERN_PREMIUM_V1, "modern_premium"})
LEGACY_TEMPLATES = frozenset({"legacy", "editorial", "modern-editorial-v1"})
MODERN_FORMATS = frozenset({"flyer", "post"})
RENDERER_USED = "pillow_modern_renderer"
LAYOUT_VERSION = "modern_premium_v1"
CARD = (255, 255, 255)
CARD_LINE = (220, 222, 216)
BANDS = {
    "header": 0.05,
    "hero": 0.36,
    "gallery": 0.14,
    "features": 0.08,
    "price_contact": 0.24,
    "cta_footer": 0.13,
}


def explicit_layout_choice(options=None):
    options = options or {}
    return str(options.get("template") or options.get("layout_template") or "").strip()


def resolve_layout_template(fmt, options=None):
    """Flyer/post always resolve to modern_premium_v1 unless legacy is explicit."""
    template = explicit_layout_choice(options)
    if template in LEGACY_TEMPLATES:
        return template
    if str(fmt or "") in MODERN_FORMATS or template in MODERN_PREMIUM_ALIASES:
        return MODERN_PREMIUM_V1
    return "modern-editorial-v1"


def uses_modern_premium(fmt, options=None):
    return resolve_layout_template(fmt, options) == MODERN_PREMIUM_V1


def _clip(text, limit):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    clipped = value[: limit - 1].rsplit(" ", 1)[0].strip()
    return clipped or value[:limit]


def _layout_bands(height):
    header = int(height * BANDS["header"])
    hero = int(height * BANDS["hero"])
    gallery = int(height * BANDS["gallery"])
    features = int(height * BANDS["features"])
    price_contact = int(height * BANDS["price_contact"])
    used = header + hero + gallery + features + price_contact
    return {
        "header": header,
        "hero": hero,
        "gallery": gallery,
        "features": features,
        "price_contact": price_contact,
        "cta_footer": max(1, height - used),
        "hero_top": header,
        "gallery_top": header + hero,
        "features_top": header + hero + gallery,
        "price_top": header + hero + gallery + features,
        "cta_top": header + hero + gallery + features + price_contact,
    }


def _hero_shade(width, height):
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = shade.load()
    band = max(1, int(width * 0.48))
    for x in range(band):
        fade = 1.0 - (x / float(band)) ** 1.35
        alpha = int(172 * fade)
        for y in range(height):
            pixels[x, y] = (8, 12, 22, alpha)
    return shade.filter(ImageFilter.GaussianBlur(radius=1.2))


def _icon_wa(draw, xy, size):
    x, y = xy
    draw.ellipse((x, y, x + size, y + size), fill=WHITE)
    inner = max(2, size // 8)
    draw.ellipse((x + inner, y + inner, x + size - inner, y + size - inner), fill=WA_GREEN)
    draw.polygon(
        (
            (x + size * 0.28, y + size * 0.70),
            (x + size * 0.16, y + size * 0.90),
            (x + size * 0.46, y + size * 0.74),
        ),
        fill=WA_GREEN,
    )
    draw.arc(
        (x + size * 0.28, y + size * 0.30, x + size * 0.72, y + size * 0.70),
        200,
        40,
        fill=WHITE,
        width=max(2, size // 10),
    )


def _icon_ig(draw, xy, size, *, fill=IG_PINK, stroke=WHITE):
    x, y = xy
    draw.rounded_rectangle((x, y, x + size, y + size), max(4, size // 4), fill=fill)
    cx, cy = x + size / 2, y + size / 2
    r = size * 0.20
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=stroke, width=max(2, size // 10))
    dot = max(2, size // 8)
    draw.ellipse((x + size * 0.68, y + size * 0.18, x + size * 0.68 + dot, y + size * 0.18 + dot), fill=stroke)


def _icon_sofa(draw, box, color):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 2, y0 + 10, x1 - 2, y1 - 6), 5, outline=color, width=2)
    draw.line((x0 + 2, y1 - 3, x1 - 2, y1 - 3), fill=color, width=2)
    mid = (x0 + x1) // 2
    draw.line((mid, y0 + 10, mid, y1 - 6), fill=color, width=2)


def _icon_bed(draw, box, color):
    x0, y0, x1, y1 = box
    draw.line((x0 + 2, y1 - 3, x1 - 2, y1 - 3), fill=color, width=2)
    draw.rounded_rectangle((x0 + 2, y0 + 12, x1 - 2, y1 - 6), 4, outline=color, width=2)
    draw.arc((x0 + 3, y0 + 2, x0 + (x1 - x0) * 0.58, y0 + 16), 200, 360, fill=color, width=2)


def _icon_bath(draw, box, color):
    x0, y0, x1, y1 = box
    draw.arc((x0 + 3, y0 + 8, x1 - 3, y1 - 1), 0, 180, fill=color, width=2)
    draw.line((x0 + 3, y0 + (y1 - y0) // 2 + 2, x1 - 3, y0 + (y1 - y0) // 2 + 2), fill=color, width=2)
    draw.line((x1 - 8, y0 + 3, x1 - 8, y0 + 11), fill=color, width=2)
    draw.ellipse((x1 - 12, y0 + 2, x1 - 4, y0 + 9), outline=color, width=2)


def _icon_area(draw, box, color):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 4, y0 + 3, x1 - 4, y1 - 3), 2, outline=color, width=2)
    draw.line((x0 + 7, y0 + 7, x1 - 7, y0 + 7), fill=color, width=2)


FEATURE_ICONS = (_icon_sofa, _icon_bed, _icon_bath, _icon_area)


def _draw_header(canvas, facts, *, language, band):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    pad = _u(width, 40)
    logo = _u(width, 40)
    top = max(8, (band["header"] - logo) // 2)
    placed = _paste_logo(canvas, _office_logo(facts), box=(logo, logo), xy=(pad, top))
    brand = facts.get("wordmark_text") or _office_brand(facts)
    text_x = pad + (placed[0] + _u(width, 10) if placed else 0)
    if brand:
        draw.text((text_x, top + 8), brand, font=font(_u(width, 20), bold=True), fill=INK)
    tagline = marketing_label("tagline", language)
    if tagline:
        used = font(_u(width, 12), bold=True)
        tw = _text_width(draw, tagline, used)
        draw.text((width - pad - tw, top + 12), tagline, font=used, fill=MUTED)


def _draw_hero(canvas, photos, facts, copy, *, language, band, fallback=None):
    width, _height = canvas.size
    pad = _u(width, 40)
    top = band["hero_top"]
    hero_h = band["hero"] - _u(width, 8)
    box = (width - pad * 2, hero_h)
    radius = _u(width, 20)
    hero = photos[0] if photos else fallback
    if hero is not None:
        paste_cover_rounded(canvas, hero, (pad, top), box, radius=radius, focus=(0.5, 0.36))
    shade = _hero_shade(box[0], hero_h)
    mask = Image.new("L", box, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box[0] - 1, hero_h - 1), radius=radius, fill=255)
    shade.putalpha(Image.composite(shade.split()[-1], Image.new("L", box, 0), mask))
    canvas.paste(shade, (pad, top), shade)
    draw = ImageDraw.Draw(canvas)
    x = pad + _u(width, 32)
    y = top + _u(width, 28)
    max_w = int(box[0] * 0.56)
    badge = default_kicker(language, facts)
    if badge:
        used = font(_u(width, 18), bold=True)
        tw = _text_width(draw, badge, used)
        bh = _u(width, 40)
        draw.rounded_rectangle((x, y, x + tw + _u(width, 32), y + bh), bh // 2, fill=WHITE)
        draw.text((x + _u(width, 16), y + _u(width, 8)), badge, font=used, fill=INK)
        y += bh + _u(width, 18)
    title_font = font(_u(width, 58), bold=True)
    for line in hero_stack_lines(language, facts):
        for wrapped in _wrap(draw, line, title_font, max_w)[:1]:
            draw.text((x, y), wrapped, font=title_font, fill=WHITE)
            y += _u(width, 62)
    bajada = _clip(copy.get("subheadline") or facts.get("benefit_line") or "", 62)
    if bajada:
        body = font(_u(width, 20))
        y += _u(width, 6)
        for line in _wrap(draw, bajada, body, max_w)[:2]:
            draw.text((x, y), line, font=body, fill=(230, 234, 240))
            y += _u(width, 28)
    street = _clip(copy.get("street") or facts.get("title") or "", 32)
    zone = _clip(copy.get("zone") or facts.get("zone_line") or "", 36)
    y += _u(width, 14)
    pin = 18
    pin_y = y + 2
    draw.ellipse((x, pin_y, x + pin, pin_y + pin), outline=WHITE, width=2)
    draw.polygon([(x + 9, pin_y + 26), (x + 2, pin_y + 16), (x + 16, pin_y + 16)], outline=WHITE)
    if street:
        draw.text((x + 28, y), street, font=font(_u(width, 22), bold=True), fill=WHITE)
        y += _u(width, 30)
    if zone:
        draw.text((x + 28, y), zone, font=font(_u(width, 18)), fill=(210, 216, 224))
    return band["gallery_top"]


def _draw_thumbs(canvas, photos, *, band):
    extras = photos[1:4]
    if not extras:
        return band["features_top"]
    width, _height = canvas.size
    pad = _u(width, 40)
    gap = _u(width, 12)
    inset = _u(width, 10)
    thumb_h = band["gallery"] - inset
    cell = (width - pad * 2 - gap * (len(extras) - 1)) // len(extras)
    radius = _u(width, 14)
    top = band["gallery_top"] + 2
    for index, photo in enumerate(extras):
        paste_cover_rounded(
            canvas,
            photo,
            (pad + index * (cell + gap), top),
            (cell, thumb_h),
            radius=radius,
            focus=(0.5, 0.40),
        )
    return band["features_top"]


def _draw_features(draw, chips, *, band, width):
    if not chips:
        return
    pad = _u(width, 40)
    x = pad
    y = band["features_top"] + _u(width, 8)
    h = band["features"] - _u(width, 16)
    inner_w = width - pad * 2
    draw.rounded_rectangle((x, y, x + inner_w, y + h), _u(width, 16), fill=CARD, outline=CARD_LINE, width=1)
    icon = _u(width, 32)
    number_font = font(_u(width, 24), bold=True)
    label_font = font(_u(width, 14))
    items = chips[:4]
    slot = inner_w // max(1, len(items))
    for index, chip in enumerate(items):
        number, label = _parse_chip(chip)
        painter = FEATURE_ICONS[min(index, 3)]
        fx = x + index * slot + _u(width, 18)
        fy = y + (h - icon) // 2
        painter(draw, (fx, fy, fx + icon, fy + icon), MUTED)
        if number:
            draw.text((fx + icon + 10, fy - 2), number, font=number_font, fill=INK)
            draw.text((fx + icon + 10, fy + _u(width, 24)), label or chip, font=label_font, fill=MUTED)
        else:
            draw.text((fx + icon + 10, fy + 6), chip, font=label_font, fill=INK)


def _draw_price_benefit(draw, price, benefit, *, band, width):
    pad = _u(width, 40)
    y = band["price_top"] + _u(width, 8)
    h = max(_u(width, 92), int(band["price_contact"] * 0.42))
    used = font(_u(width, 48), bold=True)
    price = price or ""
    tw = _text_width(draw, price, used) if price else 0
    pad_x = _u(width, 32)
    card_w = tw + pad_x * 2 if price else 0
    if price:
        draw.rounded_rectangle((pad, y, pad + card_w, y + h), h // 2, fill=NAVY)
        draw.text((pad + pad_x, y + (h - _u(width, 48)) // 2 - 2), price, font=used, fill=WHITE)
    if benefit:
        bx = pad + card_w + _u(width, 24) if card_w else pad
        by = y + _u(width, 16)
        bw = width - pad - bx
        bh = h - _u(width, 32)
        draw.rounded_rectangle((bx, by, bx + bw, by + bh), _u(width, 16), fill=CARD, outline=CARD_LINE, width=1)
        body = font(_u(width, 22), bold=True)
        text_y = by + _u(width, 18)
        for line in _wrap(draw, benefit, body, bw - _u(width, 36))[:2]:
            draw.text((bx + _u(width, 18), text_y), line, font=body, fill=INK)
            text_y += _u(width, 28)
    return y + h


def _draw_cta(draw, *, xy, width, label, fill, icon="wa"):
    if not label:
        return 0
    used = font(_u(width, 22), bold=True)
    tw = _text_width(draw, label, used)
    icon_s = _u(width, 32)
    pad_x = _u(width, 26)
    h = _u(width, 72)
    x, y = xy
    pill_w = tw + pad_x * 2 + icon_s + 14
    if fill:
        draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, fill=fill)
        text_fill = WHITE
    else:
        draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, fill=CARD, outline=(210, 214, 220), width=2)
        text_fill = INK
    if icon == "wa":
        _icon_wa(draw, (x + pad_x, y + (h - icon_s) // 2), icon_s)
    else:
        _icon_ig(draw, (x + pad_x, y + (h - icon_s) // 2), icon_s)
    draw.text((x + pad_x + icon_s + 12, y + _u(width, 20)), label, font=used, fill=text_fill)
    return pill_w


def _draw_contact(canvas, agent, *, band, after_price):
    if not agent:
        return False
    width, height = canvas.size
    pad = _u(width, 40)
    block_top = after_price + _u(width, 10)
    block_h = band["cta_top"] - block_top - _u(width, 8)
    if block_h < _u(width, 90):
        block_h = _u(width, 110)
    x1 = width - pad
    x0 = pad
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x0, block_top, x1, block_top + block_h), _u(width, 16), fill=CARD, outline=CARD_LINE, width=1)
    cutout_h = min(_u(width, 228), max(_u(width, 160), block_h - _u(width, 12)))
    photo_x = x1 - _u(width, 168)
    photo_y = block_top + max(4, block_h - cutout_h)
    pasted = False
    photo = load_agent_photo(agent.get("photo_path"))
    if photo is not None:
        pasted = paste_agent_cutout(canvas, photo, (photo_x, photo_y), height=cutout_h)
    text_x = x0 + _u(width, 24)
    text_y = block_top + _u(width, 18)
    name = agent.get("name") or ""
    title = agent.get("title") or ""
    if name:
        draw.text((text_x, text_y), name, font=font(_u(width, 24), bold=True), fill=INK)
        text_y += _u(width, 32)
    if title:
        draw.text((text_x, text_y), title, font=font(_u(width, 16)), fill=MUTED)
        text_y += _u(width, 28)
    icon = _u(width, 22)
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    if whatsapp:
        _icon_wa(draw, (text_x, text_y), icon)
        draw.text((text_x + icon + 10, text_y), whatsapp, font=font(_u(width, 16)), fill=INK)
        text_y += _u(width, 28)
    if instagram:
        _icon_ig(draw, (text_x, text_y), icon)
        draw.text((text_x + icon + 10, text_y), instagram, font=font(_u(width, 16)), fill=INK)
    return pasted


def render_modern_premium(size, photos, facts, copy, agent, options, language="es", *, fallback_hero=None):
    """Deprecated name. The export renderer is render_modern_premium_v1."""
    return render_modern_premium_v1(
        size,
        photos,
        facts,
        copy,
        agent,
        options,
        language=language,
        fallback_hero=fallback_hero,
    )


def render_modern_premium_v1(size, photos, facts, copy, agent, options, language="es", *, fallback_hero=None):
    """Compact editorial flyer: hero, gallery, feature card, price, contact, CTAs."""
    width, height = size
    facts = facts or {}
    copy = copy or {}
    options = options or {}
    photos = [item for item in (photos or []) if item is not None]
    band = _layout_bands(height)
    canvas = Image.new("RGBA", size, (*IVORY, 255))
    draw = ImageDraw.Draw(canvas)
    pad = _u(width, 40)
    _draw_header(canvas, facts, language=language, band=band)
    _draw_hero(
        canvas,
        photos,
        facts,
        copy,
        language=language,
        band=band,
        fallback=fallback_hero,
    )
    _draw_thumbs(canvas, photos, band=band)
    chips = facts.get("chips") or copy.get("attributes") or []
    if options.get("show_features", True) and chips:
        _draw_features(draw, chips, band=band, width=width)
    price = facts.get("price_label") if options.get("show_price", True) else ""
    benefit = _clip(copy.get("subheadline") or facts.get("benefit_line") or "", 46)
    after_price = _draw_price_benefit(draw, price, benefit, band=band, width=width)
    if options.get("include_agent") and agent:
        _draw_contact(canvas, agent, band=band, after_price=after_price)
    cta_y = band["cta_top"] + _u(width, 8)
    cta = _clip(copy.get("cta") or "", 32)
    cta_x = pad
    if cta:
        cta_x += _draw_cta(draw, xy=(pad, cta_y), width=width, label=cta, fill=WA_GREEN, icon="wa") + _u(width, 14)
    instagram = " ".join(str((agent or {}).get("instagram") or "").split())
    if instagram:
        ig_label = marketing_label("cta_instagram", language)
        _draw_cta(
            draw,
            xy=(cta_x, cta_y),
            width=width,
            label=ig_label,
            fill=None,
            icon="ig",
        )
    footer_y = height - _u(width, 36)
    draw.line((pad, footer_y - 10, width - pad, footer_y - 10), fill=CARD_LINE, width=1)
    legal = facts.get("legal_footer_line") or facts.get("broker_footer_text") or ""
    brand = facts.get("wordmark_text") or _office_brand(facts)
    website = " ".join(
        str(
            facts.get("website")
            or facts.get("organization_website")
            or facts.get("office_website")
            or ""
        ).split()
    )
    if website and "://" not in website and "." not in website:
        website = ""
    parts = [part for part in (brand, legal, website) if part]
    if parts:
        draw.text((pad, footer_y), "  |  ".join(parts), font=font(_u(width, 12)), fill=MUTED)
    return canvas.convert("RGB")
