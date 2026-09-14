"""Deterministic branding overlay. The model never redraws logo, agent, or facts."""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageDraw

from modules.marketing_copy import summarize_listing_copy
from modules.marketing_language import default_cta, default_headline
from modules.marketing_renderer import (
    FORMAT_SIZES,
    _address_block,
    _agent_block,
    _cta_pill,
    _feature_icons,
    _header,
    _legal_footer,
    _office_logo,
    _price,
    _u,
)
from modules.marketing_visual_spec import normalize_style, theme_palette

logger = logging.getLogger(__name__)

OVERLAY_POST_PROCESS = "branding_overlay"
OVERLAY_FN = "modules.marketing_overlay.stamp_branding_overlay"
PROVIDER_SKIP_ROLES = frozenset({"logo", "agent"})


def overlay_enabled(options=None):
    options = options or {}
    if options.get("deterministic_overlay") is False:
        return False
    return True


def provider_references(references):
    return [
        item
        for item in (references or [])
        if item.get("role") not in PROVIDER_SKIP_ROLES
    ]


def _paint_band(canvas, *, xy, size, color):
    band = Image.new("RGBA", size, (*color, 255))
    canvas.paste(band, xy)


def stamp_branding_overlay(
    png_bytes,
    *,
    context,
    copy=None,
    art=None,
    fmt="story",
    options=None,
    style="light",
    language="es",
):
    """Stamp real logo, facts, agent and legal onto an AI photo composition."""
    options = options or {}
    art = art or {}
    facts = (context or {}).get("facts") or {}
    agent = (context or {}).get("agent") if options.get("include_agent") else None
    chosen = normalize_style(style or options.get("style") or "light")
    colors = theme_palette(chosen)
    size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    canvas = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    if canvas.size != size:
        canvas = canvas.resize(size, Image.Resampling.LANCZOS)
    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)
    pad = _u(width, 40)
    header_h = _u(width, 108)
    lower_top = int(height * 0.58)
    _paint_band(canvas, xy=(0, 0), size=(width, header_h), color=colors["field"])
    _paint_band(
        canvas,
        xy=(0, lower_top),
        size=(width, height - lower_top),
        color=colors["field"],
    )
    planned = copy or summarize_listing_copy(
        facts,
        agent,
        headline=(art or {}).get("headline") or default_headline(language, facts, chosen),
        cta=options.get("cta") or (art or {}).get("cta") or default_cta(language),
        language=language,
    )
    _header(
        canvas,
        facts,
        accent=colors["accent"],
        kicker=planned.get("kicker") or facts.get("kicker") or "",
        fill=colors["ink"],
        mute=colors["mute"],
    )
    y = lower_top + _u(width, 18)
    y = _address_block(
        draw,
        facts,
        planned,
        xy=(pad, y),
        width=width,
        max_width=width - pad * 2,
        light=colors["theme"] == "blue",
    )
    if options.get("show_features", True):
        _feature_icons(
            draw,
            facts.get("chips") or planned.get("attributes") or [],
            (pad, y + 6),
            width=width,
            color=colors["accent"],
            text_fill=colors["ink"],
        )
        y += _u(width, 52)
    if options.get("show_price", True):
        _price(draw, facts, options, xy=(pad, y + 4), width=width, size=70, fill=colors["ink"])
        y += _u(width, 92)
    _cta_pill(
        draw,
        (pad, min(y, height - _u(width, 240))),
        planned.get("cta") or default_cta(language),
        width=width,
        accent=colors["accent"],
        min_w=_u(width, 220),
    )
    stamped_agent = False
    if options.get("include_agent") and agent:
        show_photo = bool(options.get("show_agent_photo", True) and agent.get("photo_path"))
        overlay_agent = {
            "name": planned.get("agent_name") or agent.get("name") or "",
            "title": planned.get("agent_title") or agent.get("title") or "",
            "whatsapp": planned.get("agent_whatsapp") or agent.get("whatsapp") or "",
            "instagram": planned.get("agent_instagram") or agent.get("instagram") or "",
            "photo_path": agent.get("photo_path") if show_photo else None,
        }
        _agent_block(
            canvas,
            overlay_agent,
            xy=(width - pad - _u(width, 128), height - _u(width, 248)),
            width=width,
            show_photo=show_photo,
            size=_u(width, 128),
            text_beside=True,
            ink=colors["ink"],
            mute=colors["mute"],
        )
        stamped_agent = bool(show_photo)
    _legal_footer(canvas, facts, line=colors["line"], mute=colors["mute"])
    rgb = canvas.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG", optimize=True)
    logo = _office_logo(facts)
    logger.info(
        "marketing_overlay stamped logo=%s agent_photo=%s style=%s",
        bool(logo),
        stamped_agent,
        chosen,
    )
    return {
        "png_bytes": buffer.getvalue(),
        "agent_photo_composited": stamped_agent,
        "logo_stamped": bool(logo),
        "post_process": OVERLAY_POST_PROCESS,
        "post_process_fn": OVERLAY_FN,
    }
