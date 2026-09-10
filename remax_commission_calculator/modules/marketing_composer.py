"""Compose real property/agent/logo/facts onto an AI graphic background."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from modules.marketing_renderer import (
    ELECTRIC,
    FORMAT_SIZES,
    NAVY,
    WHITE,
    MUTED,
    font,
    fit_cover,
    load_agent_photo,
    load_property_photos,
    paste_circle,
    paste_rounded,
    png_to_pdf_bytes,
    _paste_logo,
    _text_width,
    _u,
)


def _box(slot, width, height):
    slot = slot or {}
    return (
        int(float(slot.get("x") or 0) * width),
        int(float(slot.get("y") or 0) * height),
        max(_u(width, 8), int(float(slot.get("w") or 0.2) * width)),
        max(_u(width, 8), int(float(slot.get("h") or 0.08) * height)),
    )


def compose_marketing_image(context, art, *, fmt, options):
    size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    width, height = size
    background = (art or {}).get("background_png")
    if background:
        canvas = Image.open(io.BytesIO(background)).convert("RGBA")
        if canvas.size != size:
            canvas = canvas.resize(size, Image.Resampling.LANCZOS)
    else:
        canvas = Image.new("RGBA", size, WHITE)
    layout = (art or {}).get("layout") or {}
    facts = (context or {}).get("facts") or {}
    photos = load_property_photos((context or {}).get("photos") or [])
    theme = layout.get("text_theme") or art.get("text_theme") or "dark"
    ink = WHITE if theme == "light" else NAVY
    mute = (230, 236, 245) if theme == "light" else MUTED
    for slot in layout.get("photo_slots") or []:
        index = int(slot.get("photo_index") or 0)
        if index >= len(photos):
            continue
        x, y, w, h = _box(slot, width, height)
        radius = int(float(slot.get("radius") or 0.04) * min(w, h))
        if radius <= 2:
            fitted = fit_cover(photos[index], w, h).convert("RGBA")
            canvas.paste(fitted, (x, y))
        else:
            paste_rounded(canvas, photos[index], (x, y), (w, h), radius=radius)
    logo = layout.get("logo_slot")
    if logo:
        x, y, w, h = _box(logo, width, height)
        _paste_logo(canvas, facts.get("organization_logo"), box=(w, h), xy=(x, y))
    agent = (context or {}).get("agent") if options.get("include_agent") else None
    agent_slot = layout.get("agent_slot")
    if agent and options.get("show_agent_photo", True) and agent_slot:
        x, y, w, h = _box(agent_slot, width, height)
        photo = load_agent_photo(agent.get("photo_path"))
        if photo is not None:
            if agent_slot.get("shape") == "circle":
                paste_circle(canvas, photo, (x, y), min(w, h))
            else:
                paste_rounded(canvas, photo, (x, y), (w, h), radius=_u(width, 22))
    draw = ImageDraw.Draw(canvas)
    title = facts.get("title") or ""
    hook = art.get("headline") or ""
    title_box = layout.get("title_anchor")
    if title_box:
        x, y, w, _h = _box(title_box, width, height)
        if hook:
            draw.text((x, y), hook, font=font(_u(width, 22), italic=True), fill=ELECTRIC)
            y += _u(width, 32)
        draw.text((x, y), title, font=font(_u(width, 48), bold=True), fill=ink)
        loc = facts.get("location_line") or ""
        if loc:
            draw.text((x, y + _u(width, 56)), loc, font=font(_u(width, 22)), fill=mute)
    if options.get("show_features", True) and facts.get("chips") and layout.get("features_anchor"):
        x, y, _w, _h = _box(layout["features_anchor"], width, height)
        draw.text((x, y), "   ·   ".join(facts["chips"][:4]), font=font(_u(width, 20)), fill=mute)
    if options.get("show_price") and facts.get("price_label") and layout.get("price_anchor"):
        x, y, _w, _h = _box(layout["price_anchor"], width, height)
        draw.text((x, y), facts["price_label"], font=font(_u(width, 58), bold=True), fill=ink)
    cta = art.get("cta")
    cta_box = layout.get("cta_anchor")
    if cta and cta_box:
        x, y, w, h = _box(cta_box, width, height)
        label = f"{cta}  →"
        used = font(_u(width, 22), bold=True)
        tw = _text_width(draw, label, used)
        pill_w = max(w, tw + _u(width, 40))
        draw.rounded_rectangle((x, y, x + pill_w, y + max(h, _u(width, 52))), max(h, 52) // 2, fill=ELECTRIC)
        draw.text((x + (pill_w - tw) // 2, y + _u(width, 12)), label, font=used, fill=WHITE)
    if agent and not options.get("show_agent_photo", True) and agent_slot:
        x, y, _w, _h = _box(agent_slot, width, height)
        draw.text((x, y), agent.get("name") or "", font=font(_u(width, 22), bold=True), fill=ink)
    if agent and options.get("show_agent_photo", True) and agent_slot:
        x, y, w, h = _box(agent_slot, width, height)
        draw.text(
            (x, y + h + _u(width, 6)),
            agent.get("name") or "",
            font=font(_u(width, 20), bold=True),
            fill=ink,
        )
    rgb = canvas.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), size
