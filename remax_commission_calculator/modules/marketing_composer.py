"""Exact facts, one official logo, and the real agent portrait over AI art."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageEnhance

from modules.marketing_references import agent_overlay_image
from modules.marketing_renderer import (
    ELECTRIC,
    FORMAT_SIZES,
    NAVY,
    WHITE,
    MUTED,
    font,
    paste_rounded,
    _paste_logo,
    _u,
)


def _scrim(canvas, box, color=(10, 22, 51, 168)):
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle(box, fill=color)
    return Image.alpha_composite(canvas, overlay)


def compose_marketing_image(context, art, *, fmt, options):
    size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    width, height = size
    background = (art or {}).get("background_png") or (art or {}).get("creative_png")
    if background:
        canvas = Image.open(io.BytesIO(background)).convert("RGBA")
        if canvas.size != size:
            canvas = canvas.resize(size, Image.Resampling.LANCZOS)
    else:
        canvas = Image.new("RGBA", size, (*WHITE, 255))
    facts = (context or {}).get("facts") or {}
    options = options or {}
    direction = (art or {}).get("visual_direction") or ""
    dark_band = direction in {
        "luxury_editorial",
        "editorial_dark",
        "contemporary_lifestyle",
        "photo_lifestyle",
        "agent_lifestyle",
    }
    band_top = int(height * 0.70)
    canvas = _scrim(
        canvas,
        (0, band_top, width, height),
        (10, 22, 51, 200) if dark_band else (255, 255, 255, 214),
    )
    ink = WHITE if dark_band else NAVY
    mute = (214, 222, 234) if dark_band else MUTED
    draw = ImageDraw.Draw(canvas)
    logo_box = (_u(width, 170), _u(width, 46))
    _paste_logo(canvas, facts.get("organization_logo"), box=logo_box, xy=(_u(width, 48), _u(width, 40)))
    title = facts.get("title") or ""
    location = facts.get("location_line") or ""
    price = facts.get("price_label") if options.get("show_price") else ""
    chips = (facts.get("chips") or [])[:4]
    hook = (art or {}).get("headline") or ""
    x = _u(width, 48)
    y = band_top + _u(width, 36)
    if hook:
        draw.text((x, y), hook[:48], font=font(_u(width, 28), italic=True), fill=ELECTRIC)
        y += _u(width, 40)
    if title:
        draw.text((x, y), title, font=font(_u(width, 62), bold=True), fill=ink)
        y += _u(width, 74)
    if location:
        draw.text((x, y), location, font=font(_u(width, 28)), fill=mute)
        y += _u(width, 40)
    if price:
        draw.text((x, y), price, font=font(_u(width, 72), bold=True), fill=ink)
        y += _u(width, 82)
    if options.get("show_features", True) and chips:
        draw.text((x, y), "  ·  ".join(chips), font=font(_u(width, 26), bold=True), fill=mute)
        y += _u(width, 40)
    agent = (context or {}).get("agent") if options.get("include_agent") else None
    if agent:
        name = agent.get("name") or ""
        phone = agent.get("phone") or "" if options.get("show_phone", True) else ""
        photo = agent_overlay_image(context, options)
        ax = width - _u(width, 280)
        ay = height - _u(width, 320)
        if photo is not None:
            paste_rounded(canvas, photo, (ax, ay), (_u(width, 210), _u(width, 250)), radius=_u(width, 28))
        if name:
            draw.text((x, height - _u(width, 92)), name, font=font(_u(width, 26), bold=True), fill=ink)
        if phone:
            draw.text((x, height - _u(width, 56)), phone, font=font(_u(width, 22)), fill=mute)
    rgb = ImageEnhance.Contrast(canvas.convert("RGB")).enhance(1.02)
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), size
