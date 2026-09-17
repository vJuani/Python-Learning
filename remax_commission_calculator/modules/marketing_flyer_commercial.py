"""Commercial v2 flyers. Editorial, premium, and social-punch layouts."""

from __future__ import annotations

from PIL import Image, ImageDraw

from modules.marketing_flyer_modern import (
    FEATURE_ICONS,
    _clip,
    _hero_shade,
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
    IVORY,
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
NAVY_DEEP = (12, 24, 46)
NAVY_CTA = (14, 28, 56)
PRICE_FILL = (16, 28, 48)
SOFT_INK = (226, 232, 240)
SCENE_CAPTIONS = {
    "living": ("LIVING", "Luz y amplitud"),
    "cocina": ("COCINA", "Diseño y funcionalidad"),
    "jardin": ("JARDÍN", "Ideal para disfrutar en familia"),
    "fachada": ("FACHADA", "Primera impresión"),
    "dormitorio": ("DORMITORIO", "Descanso y confort"),
}
VARIANT = {
    MODERN_COMMERCIAL_V2: {
        "hero": 0.455,
        "gallery": 0.145,
        "location": 0.09,
        "agent": 0.205,
        "overlay": 1.0,
        "thumbs": 3,
        "captions": True,
        "title": 78,
        "cta": NAVY_CTA,
    },
    PREMIUM_EDITORIAL_V2: {
        "hero": 0.52,
        "gallery": 0.125,
        "location": 0.08,
        "agent": 0.175,
        "overlay": 0.72,
        "thumbs": 2,
        "captions": False,
        "title": 74,
        "cta": NAVY_DEEP,
    },
    SOCIAL_PUNCH_V2: {
        "hero": 0.48,
        "gallery": 0.13,
        "location": 0.085,
        "agent": 0.20,
        "overlay": 1.18,
        "thumbs": 3,
        "captions": True,
        "title": 84,
        "cta": (8, 16, 38),
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
    header = max(48, int(height * 0.045))
    hero = int(height * spec["hero"])
    gallery = int(height * spec["gallery"])
    location = int(height * spec["location"])
    agent = int(height * spec["agent"])
    used = header + hero + gallery + location + agent
    return {
        "header": header,
        "hero": hero,
        "gallery": gallery,
        "location": location,
        "agent": agent,
        "footer": max(56, height - used),
        "hero_top": header,
        "gallery_top": header + hero,
        "location_top": header + hero + gallery,
        "agent_top": header + hero + gallery + location,
        "footer_top": header + hero + gallery + location + agent,
    }


def _headline_lines(copy, facts, language):
    raw = " ".join(str((copy or {}).get("headline") or "").split())
    if raw and not is_placeholder_copy(raw) and raw.upper() != raw:
        if " en " in raw:
            left, right = raw.rsplit(" en ", 1)
            if left and right:
                return [left, f"en {right}"]
        words = raw.split()
        if len(words) > 3:
            return [" ".join(words[:2]), " ".join(words[2:])]
        return [raw]
    return sellable_headline_lines(language, facts)


def _feature_items(facts, copy):
    chips = (facts or {}).get("chips") or (copy or {}).get("attributes") or []
    items = []
    for chip in chips[:4]:
        number, label = _parse_chip(chip)
        items.append((number or "", label or chip))
    return items


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
    canvas = Image.new("RGBA", size, (*IVORY, 255))
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
    logo = min(_u(width, 34), max(24, height - 14))
    top = max(8, (height - logo) // 2)
    placed = _paste_logo(canvas, _office_logo(facts), box=(logo, logo), xy=(pad, top))
    brand = facts.get("wordmark_text") or _office_brand(facts)
    text_x = pad + (placed[0] + _u(width, 10) if placed else 0)
    if brand:
        draw.text(
            (text_x, top + max(0, (logo - _u(width, 18)) // 2)),
            brand,
            font=font(_u(width, 18), bold=True),
            fill=INK,
        )
    tagline = marketing_label("tagline", language)
    if tagline:
        used = font(_u(width, 12), bold=True)
        tw = _text_width(draw, tagline, used)
        draw.text(
            (width - pad - tw, top + max(0, (logo - _u(width, 12)) // 2)),
            tagline,
            font=used,
            fill=MUTED,
        )


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
    hero_h = band["hero"] - _u(width, 8)
    box = (width - pad * 2, hero_h)
    radius = _u(width, 22)
    hero = photos[0] if photos else fallback
    if hero is not None:
        paste_cover_rounded(canvas, hero, (pad, top), box, radius=radius, focus=(0.5, 0.34))
    shade = _hero_shade(box[0], hero_h)
    if spec["overlay"] != 1.0:
        alpha = shade.split()[-1].point(lambda value: int(max(0, min(255, value * spec["overlay"]))))
        shade.putalpha(alpha)
    mask = Image.new("L", box, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box[0] - 1, hero_h - 1), radius=radius, fill=255)
    shade.putalpha(Image.composite(shade.split()[-1], Image.new("L", box, 0), mask))
    canvas.paste(shade, (pad, top), shade)
    draw = ImageDraw.Draw(canvas)
    x = pad + _u(width, 36)
    y = top + _u(width, 28)
    max_w = int(box[0] * 0.62)
    badge = default_kicker(language, facts)
    if badge:
        used = font(_u(width, 15), bold=True)
        tw = _text_width(draw, badge, used)
        bh = _u(width, 36)
        draw.rounded_rectangle((x, y, x + tw + _u(width, 28), y + bh), bh // 2, fill=WHITE)
        draw.text((x + _u(width, 14), y + _u(width, 8)), badge, font=used, fill=INK)
        y += bh + _u(width, 18)
    title_px = _u(width, spec["title"])
    title_font = font(title_px, bold=True)
    for line in _headline_lines(copy, facts, language)[:2]:
        for wrapped in _wrap(draw, line, title_font, max_w)[:1]:
            draw.text((x, y), wrapped, font=title_font, fill=WHITE)
            y += int(title_px * 1.02)
    bajada = _clip(
        copy.get("subheadline") or facts.get("benefit_line") or "",
        88 if spec["captions"] else 64,
    )
    if bajada:
        body = font(_u(width, 24))
        y += _u(width, 8)
        for line in _wrap(draw, bajada, body, max_w)[:2]:
            draw.text((x, y), line, font=body, fill=SOFT_INK)
            y += _u(width, 30)
    if show_features:
        _draw_hero_stats(draw, facts, copy, xy=(x, top + hero_h - _u(width, 118)), width=width)
    price = facts.get("price_label") if show_price else ""
    if price:
        _draw_price_badge(
            draw,
            price,
            box=(pad + box[0] - _u(width, 28), top + hero_h - _u(width, 28), width),
        )


def _draw_hero_stats(draw, facts, copy, *, xy, width):
    items = _feature_items(facts, copy)
    if not items:
        return
    x, y = xy
    icon = _u(width, 28)
    number_font = font(_u(width, 28), bold=True)
    label_font = font(_u(width, 14))
    for index, (number, label) in enumerate(items):
        painter = FEATURE_ICONS[min(index, 3)]
        painter(draw, (x, y, x + icon, y + icon), WHITE)
        tx = x + icon + 8
        if number:
            draw.text((tx, y - 4), number, font=number_font, fill=WHITE)
            draw.text((tx, y + _u(width, 26)), label, font=label_font, fill=SOFT_INK)
            span = _text_width(draw, number, number_font)
        else:
            draw.text((tx, y + 4), label, font=label_font, fill=WHITE)
            span = _text_width(draw, label, label_font)
        x += icon + span + _u(width, 36)


def _draw_price_badge(draw, price, *, box):
    right, bottom, width = box
    used_px = _u(width, 34)
    used = font(used_px, bold=True)
    tw = _text_width(draw, price, used)
    pad_x = _u(width, 28)
    pad_y = _u(width, 16)
    h = used_px + pad_y * 2
    w = tw + pad_x * 2
    x0 = right - w
    y0 = bottom - h
    draw.rounded_rectangle((x0, y0, x0 + w, y0 + h), h // 2, fill=PRICE_FILL)
    draw.text((x0 + pad_x, y0 + pad_y - 2), price, font=used, fill=WHITE)


def _draw_thumbs(canvas, photos, *, band, pad, spec, photo_rows=None):
    extras = list(photos[1 : 1 + spec["thumbs"]])
    rows = list(photo_rows or [])[1 : 1 + spec["thumbs"]]
    width, _height = canvas.size
    slots = spec["thumbs"]
    gap = _u(width, 14)
    caption_h = _u(width, 46) if spec["captions"] else 0
    thumb_h = band["gallery"] - caption_h - _u(width, 10)
    cell = (width - pad * 2 - gap * (slots - 1)) // max(1, slots)
    radius = _u(width, 16)
    top = band["gallery_top"] + _u(width, 8)
    draw = ImageDraw.Draw(canvas)
    for index in range(slots):
        xy = (pad + index * (cell + gap), top)
        if index < len(extras) and extras[index] is not None:
            paste_cover_rounded(
                canvas,
                extras[index],
                xy,
                (cell, thumb_h),
                radius=radius,
                focus=(0.5, 0.40),
            )
            if spec["captions"]:
                meta = rows[index] if index < len(rows) else {}
                scene = photo_scene_label(meta or {})
                title, subtitle = SCENE_CAPTIONS.get(scene or "", ("", ""))
                if title:
                    draw.text(
                        (xy[0], xy[1] + thumb_h + 6),
                        title,
                        font=font(_u(width, 16), bold=True),
                        fill=INK,
                    )
                    if subtitle:
                        draw.text(
                            (xy[0], xy[1] + thumb_h + _u(width, 24)),
                            subtitle,
                            font=font(_u(width, 13)),
                            fill=MUTED,
                        )
        else:
            draw.rounded_rectangle(
                (xy[0], xy[1], xy[0] + cell, xy[1] + thumb_h),
                radius,
                fill=(236, 238, 234),
            )


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
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    y = band["location_top"] + _u(width, 10)
    pin = _u(width, 28)
    _icon_pin(draw, (pad, y + 4), pin)
    street = _clip(copy.get("street") or facts.get("title") or "", 42)
    zone = _clip(copy.get("zone") or facts.get("zone_line") or facts.get("location_line") or "", 48)
    tx = pad + pin + 12
    if street:
        draw.text((tx, y), street, font=font(_u(width, 28), bold=True), fill=INK)
        y += _u(width, 34)
    if zone:
        draw.text((tx, y), zone, font=font(_u(width, 20)), fill=MUTED)
    benefit = _clip(facts.get("benefit_line") or copy.get("subheadline") or "", 58)
    if benefit:
        bx = int(width * 0.52)
        tree = _u(width, 22)
        draw.ellipse(
            (bx, band["location_top"] + 14, bx + tree, band["location_top"] + 14 + tree),
            outline=NAVY_CTA,
            width=2,
        )
        body = font(_u(width, 20))
        lines = _wrap(draw, benefit, body, width - bx - pad - tree - 8)[:2]
        text_y = band["location_top"] + 12
        for line in lines:
            draw.text((bx + tree + 10, text_y), line, font=body, fill=INK)
            text_y += _u(width, 26)


def _draw_agent_cta(canvas, agent, copy, *, band, pad, spec):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    top = band["agent_top"] + _u(width, 4)
    h = band["agent"] - _u(width, 8)
    cutout_h = min(_u(width, 210), max(_u(width, 150), int(h * 0.92)))
    photo_x = pad
    photo_y = top + max(0, h - cutout_h)
    photo = load_agent_photo(agent.get("photo_path"))
    text_x = pad + _u(width, 8)
    if photo is not None:
        pasted = paste_agent_cutout(canvas, photo, (photo_x, photo_y), height=cutout_h)
        if pasted:
            text_x = photo_x + int(cutout_h * 0.62) + _u(width, 8)
    name = agent.get("name") or ""
    title = agent.get("title") or ""
    y = top + _u(width, 18)
    if name:
        draw.text((text_x, y), name, font=font(_u(width, 30), bold=True), fill=INK)
        y += _u(width, 36)
    if title:
        draw.text((text_x, y), title, font=font(_u(width, 18)), fill=MUTED)
        y += _u(width, 28)
    icon = _u(width, 26)
    whatsapp = " ".join(str(agent.get("whatsapp") or "").split())
    instagram = " ".join(str(agent.get("instagram") or "").split())
    if instagram and not instagram.startswith("@"):
        instagram = f"@{instagram.lstrip('@')}"
    if whatsapp:
        _icon_wa(draw, (text_x, y), icon)
        draw.text((text_x + icon + 10, y + 2), whatsapp, font=font(_u(width, 18)), fill=INK)
        y += _u(width, 32)
    if instagram:
        _icon_ig(draw, (text_x, y), icon)
        draw.text((text_x + icon + 10, y + 2), instagram, font=font(_u(width, 18)), fill=INK)
    cta = _clip(copy.get("cta") or "Consultame para visitarla", 32)
    btn_w = min(_u(width, 430), int(width * 0.42))
    _draw_cta_block(
        draw,
        xy=(width - pad - btn_w, top + max(_u(width, 36), (h - _u(width, 92)) // 2)),
        size=(btn_w, _u(width, 92)),
        label=cta,
        fill=spec["cta"],
        width=width,
    )


def _draw_cta_only(canvas, copy, *, band, pad, spec):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    cta = _clip(copy.get("cta") or "Consultame para visitarla", 34)
    top = band["agent_top"] + _u(width, 18)
    _draw_cta_block(
        draw,
        xy=(pad, top),
        size=(width - pad * 2, _u(width, 96)),
        label=cta,
        fill=spec["cta"],
        width=width,
    )


def _draw_cta_block(draw, *, xy, size, label, fill, width):
    x, y = xy
    w, h = size
    draw.rounded_rectangle((x, y, x + w, y + h), min(h // 2, 40), fill=fill)
    used = font(_u(width, 26), bold=True)
    tw = _text_width(draw, label, used)
    icon = _u(width, 34)
    inner = x + max(_u(width, 22), (w - (tw + icon + 16)) // 2)
    _icon_wa(draw, (inner, y + (h - icon) // 2), icon)
    draw.text((inner + icon + 14, y + (h - _u(width, 26)) // 2 - 1), label, font=used, fill=WHITE)
    arrow = font(_u(width, 30), bold=True)
    draw.text((x + w - _u(width, 48), y + (h - _u(width, 30)) // 2 - 4), "→", font=arrow, fill=WHITE)


def _draw_footer(canvas, facts, *, band, pad):
    width, _height = canvas.size
    draw = ImageDraw.Draw(canvas)
    y = band["footer_top"] + _u(width, 8)
    draw.line((pad, y, width - pad, y), fill=(220, 222, 216), width=1)
    legal = facts.get("legal_footer_line") or facts.get("broker_footer_text") or ""
    brand = facts.get("wordmark_text") or _office_brand(facts)
    parts = [part for part in (brand, legal) if part]
    if parts:
        draw.text((pad, y + 10), "  ·  ".join(parts), font=font(_u(width, 13)), fill=MUTED)
    mark = "JRH One"
    used = font(_u(width, 14), bold=True)
    tw = _text_width(draw, mark, used)
    draw.text((width - pad - tw, y + 8), mark, font=used, fill=NAVY_DEEP)
