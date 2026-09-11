"""Exact facts, one official logo, and the real ACM/ficha agent cutout over AI art."""

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
    _paste_logo,
    _u,
)


def _scrim(canvas, box, color=(10, 22, 51, 168)):
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle(box, fill=color)
    return Image.alpha_composite(canvas, overlay)


def _paste_cutout(canvas, photo, box, *, position="bottom-right"):
    """Paste the professional cutout as-is. No circular avatar crop."""
    if photo is None:
        return False
    width, height = canvas.size
    max_w, max_h = box
    image = photo.convert("RGBA") if photo.mode != "RGBA" else photo
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return False
    scale = min(max_w / src_w, max_h / src_h)
    new_size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
    image = image.resize(new_size, Image.Resampling.LANCZOS)
    if position == "bottom-left":
        xy = (_u(width, 36), height - new_size[1] - _u(width, 36))
    elif position == "footer":
        xy = (width - new_size[0] - _u(width, 40), height - new_size[1] - _u(width, 24))
    else:
        xy = (width - new_size[0] - _u(width, 28), height - new_size[1] - _u(width, 28))
    canvas.paste(image, xy, image)
    return True


def _agent_position(direction, preferred):
    if preferred in {"bottom", "bottom-right"}:
        return "bottom-right"
    if preferred == "bottom-left":
        return "bottom-left"
    if direction in {"clean_collage", "bright_architectural"}:
        return "bottom-left"
    if direction in {"luxury_minimal"}:
        return "footer"
    return "bottom-right"


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
        "property_hero",
        "luxury_minimal",
        "luxury_editorial",
        "editorial_dark",
        "contemporary_lifestyle",
        "photo_lifestyle",
        "agent_lifestyle",
    }
    band_top = int(height * 0.78)
    canvas = _scrim(
        canvas,
        (0, band_top, width, height),
        (10, 22, 51, 188) if dark_band else (255, 255, 255, 200),
    )
    ink = WHITE if dark_band else NAVY
    mute = (214, 222, 234) if dark_band else MUTED
    draw = ImageDraw.Draw(canvas)
    logo_box = (_u(width, 150), _u(width, 40))
    _paste_logo(canvas, facts.get("organization_logo"), box=logo_box, xy=(_u(width, 40), _u(width, 32)))
    title = facts.get("title") or ""
    locality = facts.get("locality") or ""
    price = facts.get("price_label") if options.get("show_price") else ""
    chips = (facts.get("chips") or [])[:4]
    cta = (art or {}).get("cta") or "Consultame"
    x = _u(width, 40)
    y = band_top + _u(width, 28)
    if title:
        draw.text((x, y), title, font=font(_u(width, 56), bold=True), fill=ink)
        y += _u(width, 64)
    if locality:
        draw.text((x, y), locality, font=font(_u(width, 28)), fill=mute)
        y += _u(width, 38)
    if price:
        draw.text((x, y), price, font=font(_u(width, 68), bold=True), fill=ink)
        y += _u(width, 76)
    if options.get("show_features", True) and chips:
        compact = "  ·  ".join(chips[:3])
        draw.text((x, y), compact, font=font(_u(width, 26), bold=True), fill=mute)
        y += _u(width, 38)
    agent = (context or {}).get("agent") if options.get("include_agent") else None
    composited = False
    if agent:
        presentation = options.get("agent_presentation") or {}
        photo = agent_overlay_image(context, options)
        position = _agent_position(direction, presentation.get("preferred_position") or "auto")
        box = (_u(width, 360), _u(width, 480)) if direction != "luxury_minimal" else (_u(width, 220), _u(width, 300))
        if photo is not None:
            composited = _paste_cutout(canvas, photo, box, position=position)
        name = agent.get("name") or "" if options.get("show_name", True) else ""
        phone = agent.get("phone") or "" if options.get("show_phone", True) else ""
        email = agent.get("email") or "" if options.get("show_email") else ""
        contact_y = height - _u(width, 86)
        if name:
            draw.text((x, contact_y), name, font=font(_u(width, 26), bold=True), fill=ink)
            contact_y += _u(width, 32)
        extras = [item for item in (phone, email) if item]
        if extras:
            draw.text((x, contact_y), "  ·  ".join(extras[:2]), font=font(_u(width, 22)), fill=mute)
        if cta:
            draw.text((x, height - _u(width, 42)), cta, font=font(_u(width, 24), bold=True), fill=ELECTRIC)
    elif cta:
        draw.text((x, height - _u(width, 42)), cta, font=font(_u(width, 24), bold=True), fill=ELECTRIC)
    rgb = ImageEnhance.Contrast(canvas.convert("RGB")).enhance(1.02)
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), size, {"agent_photo_composited": composited}
