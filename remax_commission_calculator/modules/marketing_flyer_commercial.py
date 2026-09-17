"""Commercial v2 flyers. Editorial composition inspired by marketing_modern_reference."""

from __future__ import annotations

import re

from PIL import Image, ImageDraw, ImageFilter

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
    paste_agent_cutout,
    paste_cover_rounded,
)

MODERN_COMMERCIAL_V2 = "modern_commercial_v2"
PREMIUM_EDITORIAL_V2 = "premium_editorial_v2"
SOCIAL_PUNCH_V2 = "social_punch_v2"
V2_TEMPLATES = frozenset(
    {MODERN_COMMERCIAL_V2, PREMIUM_EDITORIAL_V2, SOCIAL_PUNCH_V2}
)
RENDERER_USED = "pillow_commercial_v2"

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

# Target vertical proportions (reference art direction)
VARIANT = {
    MODERN_COMMERCIAL_V2: {
        "hero": 0.44,
        "gallery": 0.17,
        "location": 0.10,
        "agent": 0.195,
        "overlay": 0.92,
        "thumbs": 3,
        "captions": True,
        "title": 110,
        "price_amount": 64,
        "cta": NAVY_CTA,
        "hero_full_bleed": False,
    },
    PREMIUM_EDITORIAL_V2: {
        "hero": 0.48,
        "gallery": 0.145,
        "location": 0.095,
        "agent": 0.175,
        "overlay": 0.72,
        "thumbs": 2,
        "captions": False,
        "title": 100,
        "price_amount": 56,
        "cta": NAVY_DEEP,
        "hero_full_bleed": False,
    },
    SOCIAL_PUNCH_V2: {
        "hero": 0.46,
        "gallery": 0.155,
        "location": 0.10,
        "agent": 0.19,
        "overlay": 1.05,
        "thumbs": 3,
        "captions": True,
        "title": 116,
        "price_amount": 66,
        "cta": (8, 16, 38),
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
    name = layout if layout in VARIANT else MODERN_COMMERCIAL_V2
    return render_commercial_v2(
        size,
        photos,
        facts,
        copy,
        agent,
        options,
        language=language,
        fallback_hero=fallback_hero,
        variant=name,
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
    return render_commercial_v2(
        size,
        photos,
        facts,
        copy,
        agent,
        options,
        language=language,
        fallback_hero=fallback_hero,
        variant=PREMIUM_EDITORIAL_V2,
    )


def render_social_punch_v2(
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
        variant=SOCIAL_PUNCH_V2,
    )


def _bands(height, spec):
    header = max(56, int(height * 0.048))
    hero = int(height * spec["hero"])
    gallery = int(height * spec["gallery"])
    location = int(height * spec["location"])
    agent = int(height * spec["agent"])
    used = header + hero + gallery + location + agent
    footer = max(48, height - used)
    return {
        "header": header,
        "hero": hero,
        "gallery": gallery,
        "location": location,
        "agent": agent,
        "footer": footer,
        "hero_top": header,
        "gallery_top": header + hero,
        "location_top": header + hero + gallery,
        "agent_top": header + hero + gallery + location,
        "footer_top": header + hero + gallery + location + agent,
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
    width, height = size
    facts = facts or {}
    copy = copy or {}
    options = options or {}
    spec = VARIANT.get(variant) or VARIANT[MODERN_COMMERCIAL_V2]
    photos = [item for item in (photos or []) if item is not None]
    band = _bands(height, spec)
    canvas = Image.new("RGBA", size, (*PAGE_BG, 255))
    pad = _u(width, 36)
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
        show_features=options.get("show_features", True),
        show_price=options.get("show_price", True),
    )
    _draw_thumbs(canvas, photos, band=band, pad=pad, spec=spec, photo_rows=options.get("photo_rows"))
    _draw_location(canvas, facts, copy, band=band, pad=pad)
    if options.get("include_agent") and agent:
        _draw_agent_cta(canvas, agent, copy, band=band, pad=pad, spec=spec)
    else:
        _draw_cta_only(canvas, copy, band=band, pad=pad, spec=spec)
    _draw_footer(canvas, facts, band=band, pad=pad)
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
    show_features,
    show_price,
):
    width, _height = canvas.size
    top = band["hero_top"]
    gap = _u(width, 6)
    hero_h = band["hero"] - gap
    box = (width - pad * 2, hero_h)
    radius = _u(width, 26)
    hero = photos[0] if photos else fallback
    if hero is not None:
        paste_cover_rounded(canvas, hero, (pad, top), box, radius=radius, focus=(0.48, 0.32))
    shade = _left_hero_shade(box[0], hero_h, strength=float(spec.get("overlay") or 1.0))
    mask = Image.new("L", box, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box[0] - 1, hero_h - 1), radius=radius, fill=255)
    shade.putalpha(Image.composite(shade.split()[-1], Image.new("L", box, 0), mask))
    canvas.paste(shade, (pad, top), shade)

    draw = ImageDraw.Draw(canvas)
    x = pad + _u(width, 40)
    y = top + _u(width, 34)
    # Protagonist column — short stacked lines at maximum type size.
    max_w = int(box[0] * 0.46)
    max_w = max(max_w, _u(width, 360))

    badge = default_kicker(language, facts)
    if badge:
        used = font(_u(width, 15), bold=True)
        tw = _text_width(draw, badge, used)
        bh = _u(width, 34)
        # Dark capsule, white type (reference)
        draw.rounded_rectangle(
            (x, y, x + tw + _u(width, 28), y + bh),
            bh // 2,
            fill=(18, 24, 36),
        )
        draw.text((x + _u(width, 14), y + _u(width, 7)), badge, font=used, fill=WHITE)
        y += bh + _u(width, 22)

    title_px = _u(width, spec["title"])
    lines = _headline_lines(copy, facts, language)[:3]
    # Fit type to the column so stacks stay 2–3 editorial lines (not one word each).
    while title_px > _u(width, 64):
        probe = font(title_px, bold=True)
        if all(_text_width(draw, line, probe) <= max_w for line in lines):
            break
        title_px -= 2
    title_font = font(title_px, bold=True)
    for line in lines:
        draw.text((x, y), line, font=title_font, fill=WHITE)
        y += int(title_px * 0.95)

    bajada = _clip(
        copy.get("subheadline") or facts.get("benefit_line") or "",
        96 if spec["captions"] else 72,
    )
    if bajada:
        body = font(_u(width, 23))
        y += _u(width, 12)
        for line in _wrap(draw, bajada, body, int(box[0] * 0.50))[:2]:
            draw.text((x, y), line, font=body, fill=SOFT_INK)
            y += _u(width, 30)

    features_y = top + hero_h - _u(width, 108)
    if show_features:
        _draw_hero_stats(draw, facts, copy, xy=(x, features_y), width=width)

    price = facts.get("price_label") if show_price else ""
    if price:
        _draw_price_card(
            draw,
            price,
            box=(pad + box[0] - _u(width, 18), top + hero_h - _u(width, 18), width),
            amount_px=_u(width, spec.get("price_amount") or 56),
        )


def _draw_hero_stats(draw, facts, copy, *, xy, width):
    items = _feature_items(facts, copy)
    if not items:
        return
    x, y = xy
    icon = _u(width, 30)
    number_font = font(_u(width, 26), bold=True)
    label_font = font(_u(width, 13))
    for index, (number, label) in enumerate(items):
        painter = FEATURE_ICONS[min(index, 3)]
        painter(draw, (x, y, x + icon, y + icon), WHITE)
        tx = x + icon + 8
        if number:
            draw.text((tx, y - 2), number, font=number_font, fill=WHITE)
            draw.text((tx, y + _u(width, 26)), label, font=label_font, fill=SOFT_INK)
            span = max(_text_width(draw, number, number_font), _text_width(draw, label, label_font))
        else:
            draw.text((tx, y + 4), label, font=label_font, fill=WHITE)
            span = _text_width(draw, label, label_font)
        x += icon + span + _u(width, 34)


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
    rows = list(photo_rows or [])[1 : 1 + spec["thumbs"]]
    width, _height = canvas.size
    slots = spec["thumbs"]
    gap = _u(width, 12)
    thumb_h = band["gallery"] - _u(width, 14)
    cell = (width - pad * 2 - gap * (slots - 1)) // max(1, slots)
    radius = _u(width, 16)
    top = band["gallery_top"] + _u(width, 6)
    draw = ImageDraw.Draw(canvas)
    default_scenes = ("living", "cocina", "jardin")
    for index in range(slots):
        xy = (pad + index * (cell + gap), top)
        if index < len(extras) and extras[index] is not None:
            paste_cover_rounded(
                canvas,
                extras[index],
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
        else:
            draw.rounded_rectangle(
                (xy[0], xy[1], xy[0] + cell, xy[1] + thumb_h),
                radius,
                fill=(210, 214, 220),
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


def _draw_location(canvas, facts, copy, *, band, pad):
    """Solid info band — address + commercial benefit, not floating scraps."""
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    top = band["location_top"]
    h = band["location"]
    draw.rectangle((0, top, width, top + h), fill=BAND_BG)

    street = _clip(copy.get("street") or facts.get("title") or "", 44)
    zone = _clip(copy.get("zone") or facts.get("zone_line") or facts.get("location_line") or "", 52)
    pin = _u(width, 30)
    street_font = font(_u(width, 30), bold=True)
    zone_font = font(_u(width, 18))
    block_h = _u(width, 30) + ( _u(width, 28) if zone else 0 )
    inner_y = top + max(_u(width, 16), (h - block_h) // 2)
    _icon_pin(draw, (pad, inner_y + 2), pin)
    tx = pad + pin + 14
    if street:
        draw.text((tx, inner_y), street, font=street_font, fill=INK)
        inner_y += _u(width, 34)
    if zone:
        draw.text((tx, inner_y), zone, font=zone_font, fill=MUTED)

    mid = int(width * 0.48)
    rule_top = top + _u(width, 16)
    rule_bot = top + h - _u(width, 16)
    draw.line((mid, rule_top, mid, rule_bot), fill=DIVIDER, width=2)
    benefit = _clip(facts.get("benefit_line") or copy.get("subheadline") or "", 90)
    if benefit:
        bx = mid + _u(width, 22)
        tree = _u(width, 22)
        body = font(_u(width, 18))
        lines = _wrap(draw, benefit, body, width - bx - tree - pad - 16)[:3]
        block = tree + max(0, len(lines) * _u(width, 24) - _u(width, 4))
        cy = top + max(_u(width, 16), (h - max(tree, block)) // 2)
        draw.ellipse((bx, cy, bx + tree, cy + tree), outline=NAVY_CTA, width=2)
        text_x = bx + tree + 12
        text_y = cy
        for line in lines:
            draw.text((text_x, text_y), line, font=body, fill=INK)
            text_y += _u(width, 24)


def _draw_agent_cta(canvas, agent, copy, *, band, pad, spec):
    """Editorial agent block + integrated CTA — person overlaps, never floats alone."""
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    top = band["agent_top"]
    h = band["agent"]
    # Fill the band — no dead PAGE_BG strip around a tiny card
    card_top = top + _u(width, 4)
    card_h = h - _u(width, 6)
    draw.rounded_rectangle(
        (pad // 2, card_top, width - pad // 2, card_top + card_h),
        _u(width, 18),
        fill=WHITE,
    )

    cutout_h = min(_u(width, 250), max(_u(width, 170), int(card_h * 1.12)))
    photo_x = pad
    photo_y = card_top + card_h - cutout_h + _u(width, 10)
    photo = load_agent_photo(agent.get("photo_path"))
    text_x = pad + _u(width, 18)
    if photo is not None:
        pasted = paste_agent_cutout(canvas, photo, (photo_x, photo_y), height=cutout_h)
        if pasted:
            text_x = photo_x + int(cutout_h * 0.56) + _u(width, 4)

    script = font(_u(width, 30), italic=True)
    whisper = "Hablemos de tu próximo hogar"
    draw.text((text_x, card_top + _u(width, 8)), whisper, font=script, fill=(200, 208, 218))

    name = agent.get("name") or ""
    title = agent.get("title") or "Agente inmobiliario"
    y = card_top + _u(width, 42)
    if name:
        draw.text((text_x, y), name, font=font(_u(width, 30), bold=True), fill=INK)
        y += _u(width, 36)
    if title:
        draw.text((text_x, y), title, font=font(_u(width, 17)), fill=MUTED)
        y += _u(width, 30)

    icon = _u(width, 26)
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    contact_font = font(_u(width, 17))
    if whatsapp:
        _icon_wa(draw, (text_x, y), icon)
        draw.text((text_x + icon + 10, y + 2), whatsapp, font=contact_font, fill=INK)
        y += _u(width, 32)
    if instagram:
        _icon_ig(draw, (text_x, y), icon)
        draw.text((text_x + icon + 10, y + 2), instagram, font=contact_font, fill=INK)

    cta = _clip(copy.get("cta") or "Consultame para visitarla", 34)
    btn_w = min(_u(width, 420), int(width * 0.42))
    btn_h = _u(width, 82)
    btn_x = width - pad - btn_w
    btn_y = card_top + max(_u(width, 48), (card_h - btn_h) // 2 - _u(width, 18))
    _draw_cta_block(
        draw,
        xy=(btn_x, btn_y),
        size=(btn_w, btn_h),
        label=cta,
        fill=spec["cta"],
        width=width,
    )
    trust = (
        "Asesoramiento personalizado",
        "Acompañamiento en todo el proceso",
        "Tu proyecto en manos expertas",
    )
    tiny = font(_u(width, 12))
    ty = btn_y + btn_h + _u(width, 14)
    for line in trust:
        tw = _text_width(draw, line, tiny)
        draw.text((btn_x + max(0, (btn_w - tw) // 2), ty), line, font=tiny, fill=MUTED)
        ty += _u(width, 17)


def _draw_cta_only(canvas, copy, *, band, pad, spec):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    cta = _clip(copy.get("cta") or "Consultame para visitarla", 36)
    top = band["agent_top"] + _u(width, 22)
    _draw_cta_block(
        draw,
        xy=(pad, top),
        size=(width - pad * 2, _u(width, 84)),
        label=cta,
        fill=spec["cta"],
        width=width,
    )


def _draw_cta_block(draw, *, xy, size, label, fill, width):
    x, y = xy
    w, h = size
    draw.rounded_rectangle((x, y, x + w, y + h), min(h // 2, 36), fill=fill)
    used = font(_u(width, 22), bold=True)
    tw = _text_width(draw, label, used)
    icon = _u(width, 30)
    gap = 12
    arrow_w = _u(width, 28)
    content_w = icon + gap + tw + gap + arrow_w
    inner = x + max(_u(width, 18), (w - content_w) // 2)
    _icon_wa(draw, (inner, y + (h - icon) // 2), icon)
    draw.text(
        (inner + icon + gap, y + (h - _u(width, 22)) // 2 - 1),
        label,
        font=used,
        fill=WHITE,
    )
    arrow = font(_u(width, 26), bold=True)
    draw.text(
        (x + w - _u(width, 40), y + (h - _u(width, 26)) // 2 - 2),
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
