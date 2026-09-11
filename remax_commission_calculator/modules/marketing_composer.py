"""MarketingFinalCompositor. Real photos, real Agent, exact facts, one wordmark."""

from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from modules.marketing_references import agent_overlay_image
from modules.marketing_renderer import (
    ELECTRIC,
    FORMAT_SIZES,
    WHITE,
    font,
    load_property_photos,
    paste_circle,
    paste_rounded,
    _u,
)
from modules.marketing_visual_spec import composition_spec, safe_inset


NAVY_DEEP = (6, 14, 32)
INK = (17, 28, 51)
MUTED_ON_DARK = (176, 190, 214)
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
    width, height = size
    if theme == "light":
        canvas = Image.new("RGBA", size, (247, 249, 252, 255))
        glow = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(glow)
        draw.ellipse(
            (-int(width * 0.2), int(height * 0.7), int(width * 1.2), int(height * 1.3)),
            fill=(13, 71, 255, 28),
        )
        return Image.alpha_composite(canvas, glow)
    canvas = Image.new("RGBA", size, (*NAVY_DEEP, 255))
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    draw.ellipse(
        (-int(width * 0.25), int(height * 0.52), int(width * 1.25), int(height * 1.22)),
        fill=(13, 71, 255, 110),
    )
    return Image.alpha_composite(canvas, glow)


def _draw_wordmark(draw, xy, *, width, fill=WHITE):
    x, y = xy
    brand = font(_u(width, 40), bold=True)
    small = font(_u(width, 15), bold=True)
    draw.text((x, y), "JRH", font=brand, fill=fill)
    jrh_w = draw.textbbox((0, 0), "JRH", font=brand)[2]
    bar_x = x + jrh_w + _u(width, 12)
    draw.rectangle((bar_x, y + _u(width, 8), bar_x + 3, y + _u(width, 34)), fill=fill)
    draw.text((bar_x + _u(width, 14), y), "ONE", font=brand, fill=fill)
    subtitle = (180, 196, 220) if fill == WHITE else (91, 107, 124)
    draw.text((x, y + _u(width, 44)), "BIENES RAÍCES", font=small, fill=subtitle)


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
        diameter = _u(width, 220)
        x = width - inset - diameter - _u(width, 8)
        y = height - inset - diameter - _u(width, 70)
        paste_circle(canvas, photo, (x, y), diameter)
        return True
    if slot == "bottom_integrated":
        target_h = int(height * 0.28)
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
    target_h = int(height * 0.32)
    image = photo.convert("RGBA")
    scale = target_h / float(image.height or 1)
    new = image.resize((max(1, int(image.width * scale)), target_h), Image.Resampling.LANCZOS)
    x = width - new.width + _u(width, 12)
    y = height - new.height - _u(width, 120)
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
    title = facts.get("title") or ""
    locality = facts.get("locality") or ""
    jurisdiction = facts.get("jurisdiction") or ""
    place = " · ".join(part for part in (locality, jurisdiction) if part) or (facts.get("location_line") or "")
    price = facts.get("price_label") if options.get("show_price") else ""
    chips = (facts.get("chips") or [])[:4]
    if title:
        _icon_pin(draw, (x, y, x + _u(width, 32), y + _u(width, 32)), ink)
        draw.text((x + _u(width, 40), y - 2), title, font=font(_u(width, 40), bold=True), fill=ink)
        y += _u(width, 46)
    if place:
        draw.text((x + _u(width, 40), y), place, font=font(_u(width, 20)), fill=mute)
        y += _u(width, 40)
    if price:
        draw.text((x, y), price, font=font(_u(width, 62), bold=True), fill=ink)
        y += _u(width, 74)
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
        draw.text((x, y), "CONSULTAME", font=font(_u(width, 26), bold=True), fill=ink)
        cta_w = draw.textbbox((0, 0), "CONSULTAME", font=font(_u(width, 26), bold=True))[2]
        draw.text((x + cta_w + _u(width, 10), y), "→", font=font(_u(width, 26), bold=True), fill=ELECTRIC)
        y += _u(width, 40)
    return y


def compose_marketing_image(context, art, *, fmt, options):
    """Final compositor. Never invents property, agent, logo or facts."""
    size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    width, height = size
    options = options or {}
    facts = (context or {}).get("facts") or {}
    photos = load_property_photos((context or {}).get("photos") or [])
    direction = (art or {}).get("visual_direction") or "editorial_navy"
    spec = composition_spec(direction)
    theme = spec["theme"]
    inset = safe_inset(fmt)
    pad = max(_u(width, inset["x"]), inset["x"])
    top = max(_u(width, 28), inset["y"] // 2)
    canvas = _brand_backdrop(size, theme=theme)
    draw = ImageDraw.Draw(canvas)
    ink = WHITE if theme == "dark" else INK
    mute = MUTED_ON_DARK if theme == "dark" else MUTED_ON_LIGHT
    _draw_wordmark(draw, (pad, top), width=width, fill=ink)

    show_agent = bool(options.get("include_agent"))
    show_photo = bool(show_agent and options.get("show_agent_photo"))
    portrait = agent_overlay_image(context, options) if show_photo else None
    has_portrait = portrait is not None
    slot = spec["agent"] if has_portrait else "none"

    hero = photos[0] if photos else None
    extras = photos[1:3]
    radius = _u(width, 20)
    hero_top = top + _u(width, 88)

    if spec["hero"] == "full_bleed" and hero is not None:
        bleed_h = int(height * (0.58 if extras and spec["thumbs"] != "none_or_one" else 0.62))
        paste_rounded(canvas, hero, (0, 0), (width, bleed_h), radius=0)
        _draw_wordmark(draw, (pad, top), width=width, fill=WHITE)
        hook = ((art or {}).get("headline") or "").strip()
        if hook:
            draw.text((pad, hero_top + _u(width, 40)), hook, font=_serif(_u(width, 48), bold=True), fill=WHITE)
        thumbs_top = bleed_h + _u(width, 18)
        thumb_h = int(height * 0.12) if extras and spec["thumbs"] != "none_or_one" else 0
        if thumb_h and extras:
            paste_rounded(canvas, extras[0], (pad, thumbs_top), (int(width * 0.42), thumb_h), radius=radius)
        facts_y = (thumbs_top + thumb_h + _u(width, 28)) if thumb_h else bleed_h + _u(width, 28)
    elif spec["hero"] == "collage_lead":
        hero_h = int(height * 0.36)
        if hero is not None:
            paste_rounded(canvas, hero, (pad, hero_top), (int(width * 0.62) - pad, hero_h), radius=radius)
        if extras:
            cell_h = (hero_h - _u(width, 12)) // 2
            rx = int(width * 0.64)
            rw = width - rx - pad
            paste_rounded(canvas, extras[0], (rx, hero_top), (rw, cell_h), radius=_u(width, 14))
            if len(extras) > 1:
                paste_rounded(
                    canvas,
                    extras[1],
                    (rx, hero_top + cell_h + _u(width, 12)),
                    (rw, cell_h),
                    radius=_u(width, 14),
                )
        facts_y = hero_top + hero_h + _u(width, 32)
        thumbs_top = facts_y
        thumb_h = 0
    else:
        hero_h = int(height * (0.46 if extras else 0.54))
        if hero is not None:
            paste_rounded(canvas, hero, (pad, hero_top), (width - pad * 2, hero_h), radius=radius)
        hook = ((art or {}).get("headline") or "").strip()
        if hook:
            draw.text((pad + _u(width, 16), hero_top + _u(width, 24)), hook, font=_serif(_u(width, 36), bold=True), fill=WHITE)
        thumbs_top = hero_top + hero_h + _u(width, 16)
        thumb_h = int(height * 0.18) if extras else 0
        if thumb_h and extras:
            gap = _u(width, 14)
            thumbs_w = int(width * (0.56 if has_portrait else 0.92)) - pad
            if len(extras) == 1:
                paste_rounded(canvas, extras[0], (pad, thumbs_top), (thumbs_w, thumb_h), radius=_u(width, 16))
            else:
                cell = (thumbs_w - gap) // 2
                paste_rounded(canvas, extras[0], (pad, thumbs_top), (cell, thumb_h), radius=_u(width, 16))
                paste_rounded(canvas, extras[1], (pad + cell + gap, thumbs_top), (cell, thumb_h), radius=_u(width, 16))
        facts_y = thumbs_top + (thumb_h or 0) + _u(width, 28)

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
    if name:
        name_font = font(_u(width, 26), bold=True)
        name_w = draw.textbbox((0, 0), name, font=name_font)[2]
        nx = width - pad - name_w if has_portrait else pad
        ny = height - pad - _u(width, 52)
        draw.text((nx, ny), name, font=name_font, fill=ink)
        if role:
            role_font = font(_u(width, 15))
            role_w = draw.textbbox((0, 0), role, font=role_font)[2]
            rx = width - pad - role_w if has_portrait else pad
            draw.text((rx, ny + _u(width, 30)), role, font=role_font, fill=mute)

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
