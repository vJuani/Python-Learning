"""MarketingFinalCompositor. Real photos, real Agent, exact facts, one wordmark."""

from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from modules.marketing_branding import usable_brand_name
from modules.marketing_references import agent_overlay_image
from modules.marketing_renderer import (
    ELECTRIC,
    FORMAT_SIZES,
    IVORY,
    NAVY,
    WHITE,
    font,
    load_property_photos,
    paste_circle,
    paste_rounded,
    _office_logo,
    _paste_logo,
    _u,
)
from modules.marketing_visual_spec import composition_spec, safe_inset, theme_palette


MUTED_ON_LIGHT = (91, 107, 124)


def _serif(size, *, bold=False):
    candidates = (
        (r"C:\Windows\Fonts\timesbd.ttf" if bold else r"C:\Windows\Fonts\times.ttf"),
        (r"C:\Windows\Fonts\georgiab.ttf" if bold else r"C:\Windows\Fonts\georgia.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return font(size, bold=bold)


def _brand_backdrop(size, *, theme):
    fill = NAVY if theme == "blue" else IVORY
    return Image.new("RGBA", size, (*fill, 255))


def _draw_wordmark(draw, xy, *, width, fill=WHITE, brand=""):
    x, y = xy
    label = usable_brand_name(brand) or "RE/MAX Data House"
    used = font(_u(width, 26), bold=True)
    draw.text((x, y), label, font=used, fill=fill)


def _draw_office_lockup(canvas, draw, facts, *, pad, top, width, fill, office):
    placed = _paste_logo(
        canvas,
        _office_logo(facts),
        box=(_u(width, 64), _u(width, 64)),
        xy=(pad, top),
    )
    text_x = pad + (placed[0] + _u(width, 14) if placed else 0)
    label = facts.get("wordmark_text") or usable_brand_name(office) or "RE/MAX Data House"
    draw.text(
        (text_x, top + _u(width, 16)),
        label,
        font=font(_u(width, 26), bold=True),
        fill=fill,
    )


def _icon_pin(draw, box, fill):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    r = (x1 - x0) * 0.28
    draw.ellipse((cx - r, y0 + 2, cx + r, y0 + 2 + r * 2), outline=fill, width=2)
    draw.polygon([(cx, y1 - 1), (cx - r, y0 + r * 1.6), (cx + r, y0 + r * 1.6)], outline=fill)


def _icon_rooms(draw, box, fill):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 3, y0 + 8, x1 - 3, y1 - 4), 4, outline=fill, width=2)


def _icon_bed(draw, box, fill):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 2, y0 + 10, x1 - 2, y1 - 3), 4, outline=fill, width=2)


def _icon_bath(draw, box, fill):
    x0, y0, x1, y1 = box
    draw.ellipse((x0 + 4, y0 + 2, x1 - 4, y1 - 8), outline=fill, width=2)


def _icon_area(draw, box, fill):
    x0, y0, x1, y1 = box
    draw.rectangle((x0 + 4, y0 + 4, x1 - 4, y1 - 4), outline=fill, width=2)


def _parse_chip(chip):
    text = str(chip or "").strip()
    match = re.match(r"^(\d+(?:[.,]\d+)?)\s*(.*)$", text)
    if not match:
        return text, ""
    return match.group(1), match.group(2).strip()


def _fact_icons(chips):
    painters = [_icon_rooms, _icon_bed, _icon_bath, _icon_area]
    rows = []
    for index, chip in enumerate((chips or [])[:4]):
        number, label = _parse_chip(chip)
        rows.append((painters[index] if index < len(painters) else _icon_area, number, label or chip))
    return rows


def _has_alpha(photo):
    return photo is not None and photo.mode in {"RGBA", "LA"} and "A" in photo.getbands()


def _paste_agent_integrated(canvas, photo, *, slot, width, height, inset):
    if photo is None:
        return False
    if slot == "small_footer":
        diameter = _u(width, 148)
        x = width - inset - diameter - _u(width, 4)
        y = height - inset - diameter - _u(width, 92)
        paste_circle(canvas, photo, (x, y), diameter)
        return True
    if slot == "bottom_integrated":
        target_h = int(height * 0.20)
        image = photo.convert("RGBA")
        scale = target_h / float(image.height or 1)
        new = image.resize((max(1, int(image.width * scale)), target_h), Image.Resampling.LANCZOS)
        x = width - new.width - inset
        y = height - new.height
        if _has_alpha(image):
            canvas.paste(new, (x, y), new)
        else:
            paste_circle(canvas, photo, (width - inset - _u(width, 300), height - inset - _u(width, 360)), _u(width, 300))
        return True
    # large_lateral — enter from lower right, overlapping photos
    target_h = int(height * 0.22)
    image = photo.convert("RGBA")
    scale = target_h / float(image.height or 1)
    new = image.resize((max(1, int(image.width * scale)), target_h), Image.Resampling.LANCZOS)
    x = width - new.width + _u(width, 12)
    y = height - new.height - _u(width, 168)
    if _has_alpha(image):
        canvas.paste(new, (x, y), new)
    else:
        diameter = _u(width, 400)
        paste_circle(
            canvas,
            photo,
            (width - diameter - inset + _u(width, 10), height - diameter - _u(width, 200)),
            diameter,
        )
    return True


def _facts_block(draw, facts, options, *, x, y, width, max_right, ink, mute, show_cta=True):
    headline = facts.get("operation_title") or ""
    street = facts.get("title") or ""
    locality = facts.get("locality") or ""
    jurisdiction = facts.get("jurisdiction") or ""
    place = " · ".join(part for part in (locality, jurisdiction) if part) or (facts.get("location_line") or "")
    price = facts.get("price_label") if options.get("show_price") else ""
    chips = (facts.get("chips") or [])[:4]
    if headline:
        draw.text((x, y), headline, font=font(_u(width, 38), bold=True), fill=ink)
        y += _u(width, 48)
    if street:
        _icon_pin(draw, (x, y, x + _u(width, 28), y + _u(width, 28)), ink)
        draw.text((x + _u(width, 36), y - 2), street, font=font(_u(width, 28), bold=True), fill=ink)
        y += _u(width, 40)
    if place:
        draw.text((x + _u(width, 36), y), place, font=font(_u(width, 20)), fill=mute)
        y += _u(width, 36)
    if price:
        draw.text((x, y), price, font=font(_u(width, 58), bold=True), fill=ink)
        y += _u(width, 70)
    if options.get("show_features", True) and chips:
        icon_size = _u(width, 32)
        slot = max(_u(width, 140), (max_right - x) // max(1, len(chips)))
        for index, (painter, number, label) in enumerate(_fact_icons(chips)):
            fx = x + index * slot
            painter(draw, (fx, y, fx + icon_size, y + icon_size), ink)
            draw.text((fx + icon_size + 6, y - 2), str(number), font=font(_u(width, 26), bold=True), fill=ink)
            draw.text((fx + icon_size + 6, y + _u(width, 26)), label, font=font(_u(width, 15)), fill=mute)
        y += _u(width, 70)
    if show_cta:
        label = options.get("cta") or "Contáctanos"
        used = font(_u(width, 22), bold=True)
        tw = draw.textbbox((0, 0), label, font=used)[2]
        pill_h = _u(width, 52)
        pill_w = tw + _u(width, 48)
        draw.rounded_rectangle((x, y, x + pill_w, y + pill_h), pill_h // 2, outline=ELECTRIC, width=2)
        draw.text((x + (pill_w - tw) // 2, y + _u(width, 12)), label, font=used, fill=ELECTRIC)
        y += pill_h + _u(width, 28)
    return y


def compose_marketing_image(context, art, *, fmt, options):
    """Final compositor. Never invents property, agent, logo or facts."""
    size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    width, height = size
    options = options or {}
    facts = (context or {}).get("facts") or {}
    photos = load_property_photos((context or {}).get("photos") or [])
    direction = (art or {}).get("visual_direction") or "light"
    spec = composition_spec(direction)
    colors = theme_palette(direction)
    theme = colors["theme"]
    inset = safe_inset(fmt)
    pad = max(_u(width, inset["x"]), inset["x"])
    top = max(_u(width, 28), inset["y"] // 2)
    canvas = _brand_backdrop(size, theme=theme)
    draw = ImageDraw.Draw(canvas)
    ink = colors["ink"]
    mute = colors["mute"]
    line = colors["line"]
    office = usable_brand_name(
        facts.get("brand_name"), facts.get("office_name"), facts.get("organization_name")
    )
    _draw_office_lockup(canvas, draw, facts, pad=pad, top=top, width=width, fill=ink, office=office)
    kicker = ((art or {}).get("kicker") or facts.get("kicker") or "").strip()
    if kicker:
        used = font(_u(width, 16))
        tw = draw.textbbox((0, 0), kicker, font=used)[2]
        draw.text((width - pad - tw, top + _u(width, 20)), kicker, font=used, fill=mute)

    show_agent = bool(options.get("include_agent"))
    show_photo = bool(show_agent and options.get("show_agent_photo"))
    portrait = agent_overlay_image(context, options) if show_photo else None
    has_portrait = portrait is not None
    slot = {
        "lower_right_small": "small_footer",
        "footer_compact": "small_footer",
        "tiny_footer": "small_footer",
        "small_footer": "small_footer",
        "bottom_integrated": "bottom_integrated",
        "large_lateral": "large_lateral",
    }.get(spec["agent"] if has_portrait else "none", "small_footer" if has_portrait else "none")

    hero = photos[0] if photos else None
    extras = photos[1:3]
    radius = _u(width, 20)
    hero_top = top + _u(width, 88)
    hero_h = int(height * (0.50 if extras else 0.58))
    if hero is not None:
        paste_rounded(canvas, hero, (pad, hero_top), (width - pad * 2, hero_h), radius=radius)
    thumbs_top = hero_top + hero_h + _u(width, 22)
    thumb_h = int(height * 0.18) if extras else 0
    if thumb_h and extras:
        gap = _u(width, 14)
        thumbs_w = int(width * 0.92) - pad
        if len(extras) == 1:
            paste_rounded(canvas, extras[0], (pad, thumbs_top), (thumbs_w, thumb_h), radius=_u(width, 16))
        else:
            cell = (thumbs_w - gap) // 2
            paste_rounded(canvas, extras[0], (pad, thumbs_top), (cell, thumb_h), radius=_u(width, 16))
            paste_rounded(canvas, extras[1], (pad + cell + gap, thumbs_top), (cell, thumb_h), radius=_u(width, 16))
    facts_y = thumbs_top + (thumb_h or 0) + _u(width, 36)
    if (art or {}).get("cta"):
        options = dict(options)
        options["cta"] = art.get("cta")

    composited = False
    if has_portrait:
        composited = _paste_agent_integrated(
            canvas, portrait, slot=slot, width=width, height=height, inset=pad
        )

    max_right = width - pad - (_u(width, 380) if has_portrait and slot == "large_lateral" else 0)
    _facts_block(
        draw,
        facts,
        options,
        x=pad,
        y=facts_y,
        width=width,
        max_right=max_right,
        ink=ink,
        mute=mute,
    )
    agent = (context or {}).get("agent") if show_agent else None
    name = (agent or {}).get("name") or "" if options.get("show_name", True) else ""
    role = (agent or {}).get("title") or ""
    whatsapp = (agent or {}).get("whatsapp") or ""
    instagram = (agent or {}).get("instagram") or ""
    legal = facts.get("legal_footer_line") or facts.get("broker_footer_text") or ""
    footer_y = height - pad - _u(width, 22)
    draw.line((pad, footer_y - _u(width, 16), width - pad, footer_y - _u(width, 16)), fill=line, width=1)
    if legal:
        draw.text((pad, footer_y), legal, font=font(_u(width, 12)), fill=mute)
    if name:
        name_font = font(_u(width, 22), bold=True)
        name_w = draw.textbbox((0, 0), name, font=name_font)[2]
        photo_reserve = _u(width, 164) if has_portrait else 0
        nx = width - pad - photo_reserve - name_w - _u(width, 16) if has_portrait else pad
        ny = footer_y - _u(width, 96)
        draw.text((nx, ny), name, font=name_font, fill=ink)
        line_y = ny + _u(width, 28)
        if role:
            role_font = font(_u(width, 14))
            role_w = draw.textbbox((0, 0), role, font=role_font)[2]
            rx = width - pad - photo_reserve - role_w - _u(width, 16) if has_portrait else pad
            draw.text((rx, line_y), role, font=role_font, fill=mute)
            line_y += _u(width, 22)
        contact_font = font(_u(width, 13))
        for contact in (whatsapp, instagram):
            if not contact:
                continue
            cw = draw.textbbox((0, 0), contact, font=contact_font)[2]
            cx = width - pad - photo_reserve - cw - _u(width, 16) if has_portrait else pad
            draw.text((cx, line_y), contact, font=contact_font, fill=mute)
            line_y += _u(width, 20)

    rgb = ImageEnhance.Contrast(canvas.convert("RGB")).enhance(1.03)
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), size, {
        "agent_photo_composited": composited,
        "layout": direction,
        "theme": theme,
        "spec": "JRH_MARKETING_VISUAL_SPEC",
    }


class MarketingFinalCompositor:
    def compose(self, context, art, *, fmt, options):
        return compose_marketing_image(context, art, fmt=fmt, options=options)
