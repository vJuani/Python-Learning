"""Commercial v2 flyers. Visual language of modern_commercial_v2_reference.

Does not copy the reference pixel-for-pixel. Replicates hierarchy, spacing,
and commercial polish for 4:5 posts. V1 variants are not rendered.
"""

from __future__ import annotations

import logging
import re

from PIL import Image, ImageDraw, ImageFilter

from modules.marketing_context import MarketingError
from modules.marketing_flyer_modern import (
    FEATURE_ICONS,
    _clip,
    _icon_ig,
    _icon_wa,
)
from modules.marketing_language import (
    default_kicker,
    is_placeholder_copy,
    marketing_label,
    sellable_headline_lines,
)
from modules.marketing_photo_selector import photo_scene_label
from modules.marketing_renderer import (
    INK,
    MUTED,
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
    paste_circle,
    paste_cover_rounded,
)

MODERN_COMMERCIAL_V2 = "modern_commercial_v2"
PREMIUM_EDITORIAL_V2 = "premium_editorial_v2"
SOCIAL_PUNCH_V2 = "social_punch_v2"
V2_TEMPLATES = frozenset({MODERN_COMMERCIAL_V2})
RENDERER_USED = "pillow_commercial_v2"
logger = logging.getLogger(__name__)

# Palette aligned to the modern reference
NAVY_DEEP = (12, 24, 46)
NAVY_CTA = (14, 28, 56)
PRICE_FILL = (14, 26, 48)
SOFT_INK = (232, 236, 242)
PAGE_BG = (236, 239, 243)
BAND_BG = (224, 229, 236)
DIVIDER = (198, 204, 214)
CAPTION_SCRIM = (8, 12, 22)

SCENE_CAPTIONS = {
    "living": ("LIVING", "Luz y amplitud"),
    "cocina": ("COCINA", "Diseño y funcionalidad"),
    "jardin": ("JARDÍN", "Ideal para disfrutar en familia"),
    "fachada": ("FACHADA", "Primera impresión"),
    "dormitorio": ("DORMITORIO", "Descanso y confort"),
}

# Target vertical proportions (reference art direction — 4:5 commercial)
VARIANT = {
    MODERN_COMMERCIAL_V2: {
        "hero": 0.36,
        "facts": 0.075,
        "gallery": 0.165,
        "location": 0.095,
        "agent": 0.175,
        "overlay": 0.88,
        "thumbs": 3,
        "captions": True,
        "title": 92,
        "price_amount": 58,
        "cta": NAVY_CTA,
        "hero_full_bleed": False,
    },
}


def render_layout_v2(
    layout,
    size,
    photos,
    facts,
    copy,
    agent,
    options,
    language="es",
    *,
    fallback_hero=None,
):
    if layout != MODERN_COMMERCIAL_V2:
        raise MarketingError("marketing_err_no_v2_template", 400)
    return render_modern_commercial_v2(
        size,
        photos,
        facts,
        copy,
        agent,
        options,
        language=language,
        fallback_hero=fallback_hero,
    )


def render_modern_commercial_v2(
    size, photos, facts, copy, agent, options, language="es", *, fallback_hero=None
):
    return render_commercial_v2(
        size,
        photos,
        facts,
        copy,
        agent,
        options,
        language=language,
        fallback_hero=fallback_hero,
        variant=MODERN_COMMERCIAL_V2,
    )


def render_premium_editorial_v2(
    size, photos, facts, copy, agent, options, language="es", *, fallback_hero=None
):
    raise MarketingError("marketing_err_v1_blocked", 400)


def render_social_punch_v2(
    size, photos, facts, copy, agent, options, language="es", *, fallback_hero=None
):
    raise MarketingError("marketing_err_v1_blocked", 400)


def _bands(height, spec, *, has_facts=True, has_gallery=True, has_location=True, has_agent=True):
    header = max(56, int(height * 0.048))
    footer = max(48, int(height * 0.042))
    facts = int(height * spec.get("facts", 0.075)) if has_facts else 0
    gallery = int(height * spec["gallery"]) if has_gallery else 0
    location = int(height * spec["location"]) if has_location else 0
    agent = int(height * spec["agent"]) if has_agent else int(height * 0.09)
    used = header + facts + gallery + location + agent + footer
    hero = max(int(height * 0.30), height - used)
    footer = max(40, height - header - hero - facts - gallery - location - agent)
    return {
        "header": header,
        "hero": hero,
        "facts": facts,
        "gallery": gallery,
        "location": location,
        "agent": agent,
        "footer": footer,
        "hero_top": header,
        "facts_top": header + hero,
        "gallery_top": header + hero + facts,
        "location_top": header + hero + facts + gallery,
        "agent_top": header + hero + facts + gallery + location,
        "footer_top": header + hero + facts + gallery + location + agent,
    }


def _headline_lines(copy, facts, language):
    """Punchy stacked hero lines — protagonist typography, not a thin caption."""
    raw = " ".join(str((copy or {}).get("headline") or "").split())
    if raw and not is_placeholder_copy(raw):
        lines = _stack_headline(raw, language)
        if lines:
            return lines
    return _stack_headline(
        " ".join(sellable_headline_lines(language, facts)),
        language,
    ) or sellable_headline_lines(language, facts)


def _stack_headline(text, language):
    """Punchy hero stack. Prefer short lines so type can stay massive."""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    connector = " en " if language != "en" else " in "
    lower = text.lower()
    token = connector.strip()
    idx = lower.rfind(token)
    if idx > 0:
        left = text[:idx].strip()
        right = text[idx:].strip()
        left_words = left.split()
        # Keep "4 ambientes" as one unit; split adjective phrases word-wise.
        if left_words and left_words[0][:1].isdigit():
            return [left, right]
        if 1 <= len(left_words) <= 3:
            return [*left_words, right]
        mid = (len(left_words) + 1) // 2
        return [" ".join(left_words[:mid]), " ".join(left_words[mid:]), right]
    words = text.split()
    if len(words) <= 3:
        return words
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _feature_items(facts, copy):
    chips = (facts or {}).get("chips") or (copy or {}).get("attributes") or []
    items = []
    for chip in chips[:4]:
        number, label = _parse_chip(chip)
        items.append((number or "", label or chip))
    return items


def _split_price(price_label):
    text = " ".join(str(price_label or "").split())
    if not text:
        return "", ""
    match = re.match(
        r"^(USD|U\$S|US\$|ARS|\$)?\s*([0-9][0-9\.\,\s]*)$",
        text,
        flags=re.I,
    )
    if match:
        currency = (match.group(1) or "USD").upper().replace("U$S", "USD").replace("US$", "USD")
        if currency == "$":
            currency = "USD"
        amount = match.group(2).strip()
        return currency, amount
    parts = text.split(" ", 1)
    if len(parts) == 2 and parts[0].upper() in {"USD", "ARS", "U$S", "$"}:
        return parts[0].upper().replace("U$S", "USD"), parts[1]
    return "", text


def _left_hero_shade(width, height, *, strength=1.0):
    """Dark elegant left overlay so hero type stays readable."""
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = shade.load()
    band = max(1, int(width * 0.58))
    for x in range(band):
        t = x / float(band)
        if t < 0.22:
            fade = 0.94
        elif t < 0.58:
            fade = 0.94 - (t - 0.22) * 1.05
        else:
            fade = max(0.0, (1.0 - t) * 0.85)
        alpha = int(188 * max(0.0, min(1.0, fade)) * strength)
        for y in range(height):
            v = 1.0 + 0.06 * (y / float(max(1, height - 1)))
            pixels[x, y] = (6, 10, 20, int(min(210, alpha * v)))
    return shade.filter(ImageFilter.GaussianBlur(radius=9))


def _ellipsis(draw, text, used_font, max_width):
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    if _text_width(draw, value, used_font) <= max_width:
        return value
    ellipsis = "…"
    while value and _text_width(draw, value + ellipsis, used_font) > max_width:
        value = value[:-1].rstrip()
    return (value + ellipsis) if value else ellipsis


def render_commercial_v2(
    size,
    photos,
    facts,
    copy,
    agent,
    options,
    language="es",
    *,
    fallback_hero=None,
    variant=MODERN_COMMERCIAL_V2,
):
    if variant != MODERN_COMMERCIAL_V2:
        raise MarketingError("marketing_err_no_v2_template", 400)
    width, height = size
    facts = facts or {}
    copy = copy or {}
    options = options or {}
    spec = VARIANT[MODERN_COMMERCIAL_V2]
    photos = [item for item in (photos or []) if item is not None]
    feature_items = _feature_items(facts, copy) if options.get("show_features", True) else []
    street = _clip(copy.get("street") or facts.get("title") or "", 44)
    zone = _clip(copy.get("zone") or facts.get("zone_line") or facts.get("location_line") or "", 52)
    amenities = [str(item).strip() for item in (facts.get("amenities") or []) if str(item).strip()][:3]
    has_location = bool(street or zone or amenities)
    has_gallery = len(photos) > 1
    has_agent = bool(options.get("include_agent") and agent)
    band = _bands(
        height,
        spec,
        has_facts=bool(feature_items),
        has_gallery=has_gallery,
        has_location=has_location,
        has_agent=has_agent,
    )
    canvas = Image.new("RGBA", size, (*PAGE_BG, 255))
    pad = _u(width, 40)
    _draw_header(canvas, facts, language=language, height=band["header"], pad=pad)
    _draw_hero(
        canvas,
        photos,
        facts,
        copy,
        language=language,
        band=band,
        pad=pad,
        spec=spec,
        fallback=fallback_hero,
        show_price=options.get("show_price", True),
    )
    if feature_items:
        _draw_facts_row(canvas, feature_items, band=band, pad=pad)
    if has_gallery:
        _draw_thumbs(canvas, photos, band=band, pad=pad, spec=spec, photo_rows=options.get("photo_rows"))
    if has_location:
        _draw_location(
            canvas,
            facts,
            copy,
            band=band,
            pad=pad,
            street=street,
            zone=zone,
            amenities=amenities,
            language=language,
        )
    if has_agent:
        _draw_agent_cta(canvas, agent, copy, band=band, pad=pad, spec=spec)
    else:
        _draw_cta_only(canvas, copy, band=band, pad=pad, spec=spec)
    _draw_footer(canvas, facts, band=band, pad=pad)
    logger.info("template_used=%s", MODERN_COMMERCIAL_V2)
    return canvas.convert("RGB")


def _draw_header(canvas, facts, *, language, height, pad):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    logo = min(_u(width, 38), max(26, height - 16))
    top = max(10, (height - logo) // 2)
    placed = _paste_logo(canvas, _office_logo(facts), box=(logo, logo), xy=(pad, top))
    brand = facts.get("wordmark_text") or _office_brand(facts)
    text_x = pad + (placed[0] + _u(width, 12) if placed else 0)
    if brand:
        draw.text(
            (text_x, top + max(0, (logo - _u(width, 20)) // 2)),
            brand,
            font=font(_u(width, 20), bold=True),
            fill=INK,
        )
    # Reference: three value words stacked on the right
    tagline = marketing_label("tagline", language) or ""
    words = [part.strip() for part in re.split(r"[·|]", tagline) if part.strip()]
    if not words and tagline:
        words = [tagline]
    if words:
        used = font(_u(width, 11), bold=True)
        line_h = _u(width, 14)
        block_h = line_h * len(words[:3])
        y = max(8, (height - block_h) // 2)
        max_tw = max(_text_width(draw, word.upper(), used) for word in words[:3])
        x = width - pad - max_tw
        # Small rule above the stack
        draw.line((x, y - 6, x + max_tw, y - 6), fill=DIVIDER, width=1)
        for word in words[:3]:
            label = word.upper()
            tw = _text_width(draw, label, used)
            draw.text((width - pad - tw, y), label, font=used, fill=MUTED)
            y += line_h


def _draw_hero(
    canvas,
    photos,
    facts,
    copy,
    *,
    language,
    band,
    pad,
    spec,
    fallback,
    show_price,
):
    width, _height = canvas.size
    top = band["hero_top"]
    gap = _u(width, 8)
    hero_h = band["hero"] - gap
    box = (width - pad * 2, hero_h)
    radius = _u(width, 28)
    hero = photos[0] if photos else fallback
    if hero is not None:
        paste_cover_rounded(canvas, hero, (pad, top), box, radius=radius, focus=(0.48, 0.32))
    shade = _left_hero_shade(box[0], hero_h, strength=float(spec.get("overlay") or 1.0))
    mask = Image.new("L", box, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box[0] - 1, hero_h - 1), radius=radius, fill=255)
    shade.putalpha(Image.composite(shade.split()[-1], Image.new("L", box, 0), mask))
    canvas.paste(shade, (pad, top), shade)

    draw = ImageDraw.Draw(canvas)
    x = pad + _u(width, 36)
    y = top + _u(width, 32)
    max_w = int(box[0] * 0.58)
    max_w = max(max_w, _u(width, 340))

    badge = default_kicker(language, facts)
    if badge:
        used = font(_u(width, 14), bold=True)
        label = _ellipsis(draw, badge, used, max_w)
        tw = _text_width(draw, label, used)
        bh = _u(width, 32)
        draw.rounded_rectangle(
            (x, y, x + tw + _u(width, 28), y + bh),
            bh // 2,
            fill=(18, 24, 36),
        )
        draw.text((x + _u(width, 14), y + _u(width, 6)), label, font=used, fill=WHITE)
        y += bh + _u(width, 18)

    title_px = _u(width, spec["title"])
    lines = _headline_lines(copy, facts, language)[:3]
    while title_px > _u(width, 42):
        probe = font(title_px, bold=True)
        if all(_text_width(draw, line, probe) <= max_w for line in lines):
            break
        title_px -= 2
    for index, line in enumerate(lines):
        italic = index == len(lines) - 1 and line.lower().startswith(("en ", "in "))
        used = font(title_px, bold=not italic, italic=italic)
        label = _ellipsis(draw, line, used, max_w)
        draw.text((x, y), label, font=used, fill=WHITE)
        y += int(title_px * 0.96)

    bajada = copy.get("subheadline") or facts.get("benefit_line") or ""
    if bajada:
        body_px = _u(width, 22)
        body = font(body_px)
        y += _u(width, 10)
        wrapped = _wrap(draw, bajada, body, max_w)[:2]
        for line in wrapped:
            draw.text((x, y), _ellipsis(draw, line, body, max_w), font=body, fill=SOFT_INK)
            y += _u(width, 28)

    price = facts.get("price_label") if show_price else ""
    if price:
        _draw_price_card(
            draw,
            price,
            box=(pad + box[0] - _u(width, 16), top + hero_h - _u(width, 16), width),
            amount_px=_u(width, spec.get("price_amount") or 56),
        )


def _draw_facts_row(canvas, items, *, band, pad):
    if not items or band.get("facts", 0) <= 0:
        return
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    top = band["facts_top"]
    h = band["facts"]
    slots = min(4, len(items))
    inner = width - pad * 2
    cell = inner // slots
    icon = _u(width, 34)
    number_font = font(_u(width, 28), bold=True)
    label_font = font(_u(width, 13), bold=True)
    icon_color = (90, 104, 122)
    y = top + max(_u(width, 10), (h - icon - _u(width, 8)) // 2)
    for index, (number, label) in enumerate(items[:slots]):
        x = pad + index * cell
        painter = FEATURE_ICONS[min(index, 3)]
        painter(draw, (x, y, x + icon, y + icon), icon_color)
        tx = x + icon + _u(width, 10)
        max_tw = cell - icon - _u(width, 18)
        if number:
            draw.text(
                (tx, y - 2),
                _ellipsis(draw, number, number_font, max_tw),
                font=number_font,
                fill=INK,
            )
            caption = _ellipsis(draw, (label or "").upper(), label_font, max_tw)
            draw.text((tx, y + _u(width, 28)), caption, font=label_font, fill=MUTED)
        else:
            draw.text(
                (tx, y + 6),
                _ellipsis(draw, label, label_font, max_tw),
                font=label_font,
                fill=INK,
            )


def _draw_price_card(draw, price, *, box, amount_px):
    """Primary commercial anchor: USD over a large amount."""
    right, bottom, width = box
    currency, amount = _split_price(price)
    amount = amount or price
    currency = currency or "USD"
    cur_font = font(_u(width, 20), bold=True)
    amt_font = font(amount_px, bold=True)
    pad_x = _u(width, 32)
    pad_y = _u(width, 18)
    tw = max(_text_width(draw, currency, cur_font), _text_width(draw, amount, amt_font))
    h = pad_y * 2 + _u(width, 22) + amount_px + 6
    w = tw + pad_x * 2
    x0 = right - w
    y0 = bottom - h
    radius = min(_u(width, 24), h // 3)
    # Soft rim so the card pops over dark hero photography
    draw.rounded_rectangle(
        (x0 - 2, y0 - 2, x0 + w + 2, y0 + h + 2),
        radius + 2,
        fill=(255, 255, 255, 36),
    )
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius, fill=PRICE_FILL)
    draw.text((x0 + pad_x, y0 + pad_y), currency, font=cur_font, fill=SOFT_INK)
    draw.text(
        (x0 + pad_x, y0 + pad_y + _u(width, 24)),
        amount,
        font=amt_font,
        fill=WHITE,
    )


def _draw_thumbs(canvas, photos, *, band, pad, spec, photo_rows=None):
    extras = list(photos[1 : 1 + spec["thumbs"]])
    extras = [item for item in extras if item is not None]
    if not extras:
        return
    rows = list(photo_rows or [])[1 : 1 + len(extras)]
    width, _height = canvas.size
    slots = len(extras)
    gap = _u(width, 14)
    thumb_h = band["gallery"] - _u(width, 16)
    cell = (width - pad * 2 - gap * (slots - 1)) // max(1, slots)
    radius = _u(width, 18)
    top = band["gallery_top"] + _u(width, 8)
    default_scenes = ("living", "cocina", "jardin")
    for index, extra in enumerate(extras):
        xy = (pad + index * (cell + gap), top)
        paste_cover_rounded(
            canvas,
            extra,
            xy,
            (cell, thumb_h),
            radius=radius,
            focus=(0.5, 0.42),
        )
        if spec["captions"]:
            meta = rows[index] if index < len(rows) else {}
            scene = photo_scene_label(meta or {}) or default_scenes[min(index, 2)]
            title, subtitle = SCENE_CAPTIONS.get(scene, ("", ""))
            if not title:
                title = default_scenes[min(index, 2)].upper()
                subtitle = SCENE_CAPTIONS.get(default_scenes[min(index, 2)], ("", ""))[1]
            _draw_thumb_caption(
                canvas,
                xy=xy,
                size=(cell, thumb_h),
                radius=radius,
                title=title,
                subtitle=subtitle,
                width=width,
            )


def _draw_thumb_caption(canvas, *, xy, size, radius, title, subtitle, width):
    """Editorial labels sitting on the photo, not floating below."""
    cell_w, thumb_h = size
    scrim_h = _u(width, 64)
    scrim = Image.new("RGBA", (cell_w, scrim_h), (0, 0, 0, 0))
    pixels = scrim.load()
    for y in range(scrim_h):
        t = y / float(max(1, scrim_h - 1))
        alpha = int(185 * (t ** 0.85))
        for x in range(cell_w):
            pixels[x, y] = (*CAPTION_SCRIM, alpha)
    mask = Image.new("L", (cell_w, scrim_h), 255)
    # Keep bottom corners rounded with the thumb
    corner = Image.new("L", (cell_w, thumb_h), 0)
    ImageDraw.Draw(corner).rounded_rectangle(
        (0, 0, cell_w - 1, thumb_h - 1), radius=radius, fill=255
    )
    bottom_mask = corner.crop((0, thumb_h - scrim_h, cell_w, thumb_h))
    scrim.putalpha(Image.composite(scrim.split()[-1], Image.new("L", (cell_w, scrim_h), 0), bottom_mask))
    canvas.paste(scrim, (xy[0], xy[1] + thumb_h - scrim_h), scrim)
    draw = ImageDraw.Draw(canvas)
    tx = xy[0] + _u(width, 12)
    ty = xy[1] + thumb_h - scrim_h + _u(width, 10)
    if title:
        draw.text((tx, ty), title, font=font(_u(width, 15), bold=True), fill=WHITE)
        ty += _u(width, 20)
    if subtitle:
        draw.text((tx, ty), subtitle, font=font(_u(width, 12)), fill=SOFT_INK)


def _icon_pin(draw, xy, size, fill=NAVY_CTA):
    x, y = xy
    draw.ellipse((x, y, x + size * 0.72, y + size * 0.72), outline=fill, width=max(2, size // 10))
    draw.polygon(
        (
            (x + size * 0.12, y + size * 0.48),
            (x + size * 0.36, y + size),
            (x + size * 0.60, y + size * 0.48),
        ),
        fill=fill,
    )


def _icon_mail(draw, xy, size):
    x, y = xy
    draw.rounded_rectangle((x, y + 2, x + size, y + size - 2), max(3, size // 6), outline=MUTED, width=2)
    draw.polygon(
        (
            (x + 2, y + 4),
            (x + size / 2, y + size * 0.48),
            (x + size - 2, y + 4),
        ),
        outline=MUTED,
    )


def _icon_tree(draw, xy, size, fill=NAVY_CTA):
    x, y = xy
    draw.ellipse((x + 2, y, x + size - 2, y + size * 0.72), outline=fill, width=2)
    draw.line((x + size / 2, y + size * 0.55, x + size / 2, y + size), fill=fill, width=2)


def _icon_wifi(draw, xy, size, fill=NAVY_CTA):
    x, y = xy
    for index, scale in enumerate((0.95, 0.65, 0.35)):
        pad = int(size * (1 - scale) / 2)
        draw.arc(
            (x + pad, y + pad, x + size - pad, y + size - pad),
            200,
            340,
            fill=fill,
            width=max(2, size // 10),
        )
    dot = max(2, size // 8)
    draw.ellipse(
        (x + size / 2 - dot, y + size * 0.72, x + size / 2 + dot, y + size * 0.72 + dot * 2),
        fill=fill,
    )


def _icon_shop(draw, xy, size, fill=NAVY_CTA):
    x, y = xy
    draw.rectangle((x + 4, y + size * 0.38, x + size - 4, y + size - 2), outline=fill, width=2)
    draw.polygon(
        (
            (x + 2, y + size * 0.40),
            (x + size / 2, y + 2),
            (x + size - 2, y + size * 0.40),
        ),
        outline=fill,
    )


AMENITY_ICONS = (_icon_tree, _icon_shop, _icon_wifi)


def _draw_location(canvas, facts, copy, *, band, pad, street, zone, amenities, language):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    top = band["location_top"]
    h = band["location"]
    if h <= 0:
        return
    pin = _u(width, 28)
    street_font = font(_u(width, 24), bold=True)
    zone_font = font(_u(width, 16))
    left_w = int(width * 0.34)
    inner_y = top + _u(width, 16)
    if street or zone:
        _icon_pin(draw, (pad, inner_y + 2), pin)
        tx = pad + pin + 12
        max_tw = left_w - pin - 20
        if street:
            draw.text((tx, inner_y), _ellipsis(draw, street, street_font, max_tw), font=street_font, fill=INK)
            inner_y += _u(width, 28)
        if zone:
            draw.text((tx, inner_y), _ellipsis(draw, zone, zone_font, max_tw), font=zone_font, fill=MUTED)

    amenity_font = font(_u(width, 13))
    amenity_x = pad + left_w + _u(width, 8)
    amenity_w = int(width * 0.36)
    if amenities:
        slot = amenity_w // max(1, len(amenities))
        icon = _u(width, 22)
        ay = top + _u(width, 14)
        for index, label in enumerate(amenities):
            ax = amenity_x + index * slot
            painter = AMENITY_ICONS[min(index, len(AMENITY_ICONS) - 1)]
            painter(draw, (ax, ay), icon)
            lines = _wrap(draw, label, amenity_font, slot - _u(width, 8))[:2]
            ty = ay + icon + 4
            for line in lines:
                draw.text((ax, ty), line, font=amenity_font, fill=MUTED)
                ty += _u(width, 16)

    closing = marketing_label("closing_line", language) or marketing_label("whisper", language)
    if closing:
        used = font(_u(width, 18), italic=True)
        max_tw = int(width * 0.22)
        rx = width - pad - max_tw
        wrapped = _wrap(draw, closing, used, max_tw)[:2]
        ty = top + max(_u(width, 22), (h - len(wrapped) * _u(width, 24)) // 2)
        for line in wrapped:
            draw.text((rx, ty), _ellipsis(draw, line, used, max_tw), font=used, fill=(176, 154, 122))
            ty += _u(width, 24)


def _draw_agent_cta(canvas, agent, copy, *, band, pad, spec):
    """Agent block + CTA. Circular portrait, contacts, strong button — not a stuck cutout."""
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    top = band["agent_top"]
    h = band["agent"]
    card_top = top + _u(width, 6)
    card_h = h - _u(width, 10)
    diameter = min(_u(width, 168), max(_u(width, 118), card_h - _u(width, 24)))
    photo = load_agent_photo(agent.get("photo_path"))
    text_x = pad
    if photo is not None:
        photo_y = card_top + max(0, (card_h - diameter) // 2)
        paste_circle(canvas, photo, (pad, photo_y), diameter)
        text_x = pad + diameter + _u(width, 18)

    name = agent.get("name") or ""
    title = agent.get("title") or ""
    y = card_top + _u(width, 10)
    name_font = font(_u(width, 30), bold=True)
    title_font = font(_u(width, 16))
    contact_max = int(width * 0.42)
    if name:
        draw.text((text_x, y), _ellipsis(draw, name, name_font, contact_max), font=name_font, fill=INK)
        y += _u(width, 36)
    if title:
        draw.text((text_x, y), _ellipsis(draw, title, title_font, contact_max), font=title_font, fill=MUTED)
        y += _u(width, 26)

    icon = _u(width, 24)
    contact_font = font(_u(width, 16))
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    email = " ".join(str(agent.get("email") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    contacts = []
    if whatsapp:
        contacts.append(("wa", whatsapp))
    if instagram:
        contacts.append(("ig", instagram))
    if email:
        contacts.append(("mail", email))
    for kind, value in contacts[:3]:
        if kind == "wa":
            _icon_wa(draw, (text_x, y), icon)
        elif kind == "ig":
            _icon_ig(draw, (text_x, y), icon)
        else:
            _icon_mail(draw, (text_x, y), icon)
        draw.text(
            (text_x + icon + 10, y + 2),
            _ellipsis(draw, value, contact_font, contact_max - icon - 12),
            font=contact_font,
            fill=INK,
        )
        y += _u(width, 28)

    cta = copy.get("cta") or "Consultame para visitarla"
    btn_w = min(_u(width, 400), int(width * 0.40))
    btn_h = _u(width, 72)
    btn_x = width - pad - btn_w
    btn_y = card_top + max(_u(width, 18), (card_h - btn_h) // 2 - _u(width, 16))
    _draw_cta_block(
        draw,
        xy=(btn_x, btn_y),
        size=(btn_w, btn_h),
        label=cta,
        fill=spec["cta"],
        width=width,
    )
    trust = marketing_label("trust_project", "es") or "Tu proyecto en manos expertas"
    tiny = font(_u(width, 12), bold=True)
    label = _ellipsis(draw, trust.upper(), tiny, btn_w)
    tw = _text_width(draw, label, tiny)
    ty = btn_y + btn_h + _u(width, 12)
    if ty + _u(width, 16) <= card_top + card_h:
        draw.text((btn_x + max(0, (btn_w - tw) // 2), ty), label, font=tiny, fill=MUTED)


def _draw_cta_only(canvas, copy, *, band, pad, spec):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    cta = copy.get("cta") or "Consultame para visitarla"
    top = band["agent_top"] + _u(width, 18)
    btn_h = min(_u(width, 78), max(48, band["agent"] - _u(width, 28)))
    _draw_cta_block(
        draw,
        xy=(pad, top),
        size=(width - pad * 2, btn_h),
        label=cta,
        fill=spec["cta"],
        width=width,
    )


def _draw_cta_block(draw, *, xy, size, label, fill, width):
    x, y = xy
    w, h = size
    draw.rounded_rectangle((x, y, x + w, y + h), min(h // 2, 36), fill=fill)
    used = font(_u(width, 20), bold=True)
    icon = _u(width, 28)
    gap = 10
    arrow_w = _u(width, 24)
    max_tw = max(40, w - icon - arrow_w - gap * 3 - _u(width, 24))
    text = _ellipsis(draw, " ".join(str(label or "").split()), used, max_tw)
    tw = _text_width(draw, text, used)
    content_w = icon + gap + tw + gap + arrow_w
    inner = x + max(_u(width, 16), (w - content_w) // 2)
    _icon_wa(draw, (inner, y + (h - icon) // 2), icon)
    draw.text(
        (inner + icon + gap, y + (h - _u(width, 20)) // 2 - 1),
        text,
        font=used,
        fill=WHITE,
    )
    arrow = font(_u(width, 24), bold=True)
    draw.text(
        (x + w - _u(width, 36), y + (h - _u(width, 24)) // 2 - 2),
        "→",
        font=arrow,
        fill=WHITE,
    )


def _draw_footer(canvas, facts, *, band, pad):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    y = band["footer_top"] + _u(width, 6)
    draw.line((pad, y, width - pad, y), fill=DIVIDER, width=1)
    legal = facts.get("legal_footer_line") or facts.get("broker_footer_text") or ""
    brand = facts.get("wordmark_text") or _office_brand(facts)
    parts = [part for part in (brand, legal) if part]
    if parts:
        draw.text((pad, y + 8), "  ·  ".join(parts), font=font(_u(width, 12)), fill=MUTED)
    mark = "JRH One"
    used = font(_u(width, 13), bold=True)
    tw = _text_width(draw, mark, used)
    draw.text((width - pad - tw, y + 6), mark, font=used, fill=NAVY_DEEP)
