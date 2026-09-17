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


def _hero_shade(width, height):
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = shade.load()
    band = max(1, int(width * 0.56))
    for x in range(band):
        fade = 1.0 - (x / float(band)) ** 1.05
        alpha = int(188 * fade)
        for y in range(height):
            pixels[x, y] = (8, 12, 22, alpha)
    return shade.filter(ImageFilter.GaussianBlur(radius=0.6))


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


def _draw_header(canvas, facts, *, language):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    pad = _u(width, 44)
    placed = _paste_logo(
        canvas,
        _office_logo(facts),
        box=(_u(width, 52), _u(width, 52)),
        xy=(pad, _u(width, 18)),
    )
    brand = facts.get("wordmark_text") or _office_brand(facts)
    text_x = pad + (placed[0] + _u(width, 12) if placed else 0)
    if brand:
        draw.text((text_x, _u(width, 28)), brand, font=font(_u(width, 22), bold=True), fill=INK)
    tagline = marketing_label("tagline", language)
    if tagline:
        used = font(_u(width, 13), bold=True)
        tw = _text_width(draw, tagline, used)
        draw.text((width - pad - tw, _u(width, 34)), tagline, font=used, fill=MUTED)


def _draw_hero(canvas, photos, facts, copy, *, language, fallback=None):
    width, height = canvas.size
    pad = _u(width, 44)
    top = _u(width, 88)
    hero_h = int(height * 0.42)
    box = (width - pad * 2, hero_h)
    radius = _u(width, 22)
    hero = photos[0] if photos else fallback
    if hero is not None:
        paste_cover_rounded(canvas, hero, (pad, top), box, radius=radius, focus=(0.5, 0.36))
    shade = _hero_shade(box[0], hero_h)
    mask = Image.new("L", box, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box[0] - 1, hero_h - 1), radius=radius, fill=255)
    shade.putalpha(Image.composite(shade.split()[-1], Image.new("L", box, 0), mask))
    canvas.paste(shade, (pad, top), shade)
    draw = ImageDraw.Draw(canvas)
    x = pad + _u(width, 36)
    y = top + _u(width, 36)
    max_w = int(box[0] * 0.52)
    badge = default_kicker(language, facts)
    if badge:
        used = font(_u(width, 16), bold=True)
        tw = _text_width(draw, badge, used)
        bh = _u(width, 36)
        draw.rounded_rectangle((x, y, x + tw + _u(width, 28), y + bh), bh // 2, fill=WHITE)
        draw.text((x + _u(width, 14), y + _u(width, 8)), badge, font=used, fill=INK)
        y += bh + _u(width, 22)
    title_font = font(_u(width, 44), bold=True)
    for line in hero_stack_lines(language, facts):
        for wrapped in _wrap(draw, line, title_font, max_w)[:1]:
            draw.text((x, y), wrapped, font=title_font, fill=WHITE)
            y += _u(width, 50)
    bajada = _clip(copy.get("subheadline") or facts.get("benefit_line") or "", 70)
    if bajada:
        body = font(_u(width, 18))
        y += _u(width, 8)
        for line in _wrap(draw, bajada, body, max_w)[:2]:
            draw.text((x, y), line, font=body, fill=(230, 234, 240))
            y += _u(width, 26)
    street = _clip(copy.get("street") or facts.get("title") or "", 32)
    zone = _clip(copy.get("zone") or facts.get("zone_line") or "", 36)
    y += _u(width, 16)
    pin_y = y + 2
    draw.ellipse((x, pin_y, x + 14, pin_y + 14), outline=WHITE, width=2)
    draw.polygon([(x + 7, pin_y + 20), (x + 2, pin_y + 12), (x + 12, pin_y + 12)], outline=WHITE)
    loc_font = font(_u(width, 18), bold=True)
    if street:
        draw.text((x + 24, y), street, font=loc_font, fill=WHITE)
        y += _u(width, 26)
    if zone:
        draw.text((x + 24, y), zone, font=font(_u(width, 16)), fill=(210, 216, 224))
    return top + hero_h


def _draw_thumbs(canvas, photos, *, top):
    width, _height = canvas.size
    extras = photos[1:4]
    if not extras:
        return top
    pad = _u(width, 44)
    gap = _u(width, 14)
    thumb_h = int(canvas.size[1] * 0.145)
    cell = (width - pad * 2 - gap * (len(extras) - 1)) // len(extras)
    radius = _u(width, 16)
    for index, photo in enumerate(extras):
        paste_cover_rounded(
            canvas,
            photo,
            (pad + index * (cell + gap), top + _u(width, 16)),
            (cell, thumb_h),
            radius=radius,
            focus=(0.5, 0.40),
        )
    return top + _u(width, 16) + thumb_h


def _draw_features(draw, chips, *, xy, width):
    if not chips:
        return
    x, y = xy
    icon = _u(width, 30)
    number_font = font(_u(width, 22), bold=True)
    label_font = font(_u(width, 13))
    slot = _u(width, 168)
    for index, chip in enumerate(chips[:4]):
        number, label = _parse_chip(chip)
        painter = FEATURE_ICONS[min(index, 3)]
        fx = x + index * slot
        painter(draw, (fx, y, fx + icon, y + icon), MUTED)
        if number:
            draw.text((fx + icon + 8, y - 2), number, font=number_font, fill=INK)
            draw.text((fx + icon + 8, y + _u(width, 22)), label or chip, font=label_font, fill=MUTED)
        else:
            draw.text((fx + icon + 8, y + 6), chip, font=label_font, fill=INK)


def _draw_price_card(draw, price, *, xy, width):
    if not price:
        return 0
    used = font(_u(width, 36), bold=True)
    tw = _text_width(draw, price, used)
    pad_x = _u(width, 28)
    h = _u(width, 72)
    x, y = xy
    draw.rounded_rectangle((x, y, x + tw + pad_x * 2, y + h), h // 2, fill=NAVY)
    draw.text((x + pad_x, y + _u(width, 16)), price, font=used, fill=WHITE)
    return tw + pad_x * 2


def _draw_cta(draw, *, xy, width, label, fill, icon="wa"):
    if not label:
        return 0
    used = font(_u(width, 18), bold=True)
    tw = _text_width(draw, label, used)
    icon_s = _u(width, 26)
    pad_x = _u(width, 22)
    h = _u(width, 56)
    x, y = xy
    pill_w = tw + pad_x * 2 + icon_s + 10
    if fill:
        draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, fill=fill)
        text_fill = WHITE
    else:
        draw.rounded_rectangle((x, y, x + pill_w, y + h), h // 2, outline=(210, 214, 220), width=2)
        text_fill = INK
    if icon == "wa":
        _icon_wa(draw, (x + pad_x, y + (h - icon_s) // 2), icon_s)
    else:
        _icon_ig(draw, (x + pad_x, y + (h - icon_s) // 2), icon_s)
    draw.text((x + pad_x + icon_s + 10, y + _u(width, 16)), label, font=used, fill=text_fill)
    return pill_w


def _draw_agent(canvas, agent, *, width, height):
    if not agent:
        return False
    pad = _u(width, 44)
    photo = load_agent_photo(agent.get("photo_path"))
    cutout_h = _u(width, 340)
    x = width - pad - _u(width, 190)
    y = height - _u(width, 470)
    pasted = False
    if photo is not None:
        pasted = paste_agent_cutout(canvas, photo, (x, y), height=cutout_h)
    draw = ImageDraw.Draw(canvas)
    text_x = x - _u(width, 250)
    text_y = y + _u(width, 70)
    name = agent.get("name") or ""
    title = agent.get("title") or ""
    if name:
        draw.text((text_x, text_y), name, font=font(_u(width, 20), bold=True), fill=INK)
        text_y += _u(width, 26)
    if title:
        draw.text((text_x, text_y), title, font=font(_u(width, 14)), fill=MUTED)
        text_y += _u(width, 24)
    icon = _u(width, 20)
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    if whatsapp:
        _icon_wa(draw, (text_x, text_y), icon)
        draw.text((text_x + icon + 8, text_y), whatsapp, font=font(_u(width, 14)), fill=INK)
        text_y += _u(width, 24)
    if instagram:
        _icon_ig(draw, (text_x, text_y), icon)
        draw.text((text_x + icon + 8, text_y), instagram, font=font(_u(width, 14)), fill=INK)
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
    """Final export layout: hero overlay, 3 thumbs, price pill, cutout agent. Not the old listing card."""
    width, height = size
    facts = facts or {}
    copy = copy or {}
    options = options or {}
    photos = [item for item in (photos or []) if item is not None]
    canvas = Image.new("RGBA", size, (*IVORY, 255))
    draw = ImageDraw.Draw(canvas)
    _draw_header(canvas, facts, language=language)
    hero_bottom = _draw_hero(
        canvas,
        photos,
        facts,
        copy,
        language=language,
        fallback=fallback_hero,
    )
    thumbs_bottom = _draw_thumbs(canvas, photos, top=hero_bottom)
    y = thumbs_bottom + _u(width, 28)
    pad = _u(width, 44)
    chips = facts.get("chips") or copy.get("attributes") or []
    if options.get("show_features", True) and chips:
        _draw_features(draw, chips, xy=(pad, y), width=width)
        y += _u(width, 70)
    price = facts.get("price_label") if options.get("show_price", True) else ""
    price_w = _draw_price_card(draw, price, xy=(pad, y), width=width) if price else 0
    benefit = _clip(copy.get("subheadline") or facts.get("benefit_line") or "", 42)
    if benefit and price_w:
        draw.text(
            (pad + price_w + _u(width, 24), y + _u(width, 22)),
            benefit,
            font=font(_u(width, 18)),
            fill=MUTED,
        )
    if options.get("include_agent") and agent:
        _draw_agent(canvas, agent, width=width, height=height)
    cta_y = height - _u(width, 140)
    cta = _clip(copy.get("cta") or "", 32)
    cta_x = pad
    if cta:
        cta_x += _draw_cta(draw, xy=(pad, cta_y), width=width, label=cta, fill=WA_GREEN, icon="wa") + _u(width, 16)
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
    footer_y = height - _u(width, 48)
    draw.line((pad, footer_y - 12, width - pad, footer_y - 12), fill=(226, 228, 222), width=1)
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
