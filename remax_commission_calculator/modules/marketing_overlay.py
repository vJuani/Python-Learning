"""Deterministic branding overlay. The model never redraws logo, agent, or facts."""

from __future__ import annotations

import io
import logging

from PIL import Image

from modules.marketing_copy import summarize_listing_copy
from modules.marketing_language import default_cta, default_headline
from modules.marketing_renderer import (
    FORMAT_SIZES,
    _office_logo,
    load_property_photos,
    render_modern_listing,
)
from modules.marketing_visual_spec import normalize_style

logger = logging.getLogger(__name__)

OVERLAY_POST_PROCESS = "branding_overlay"
OVERLAY_FN = "modules.marketing_overlay.stamp_branding_overlay"
OVERLAY_LAYOUT = "modern-editorial-v1"
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
    """Compose the final listing piece. Listing photos win over the AI collage."""
    options = options or {}
    art = art or {}
    facts = (context or {}).get("facts") or {}
    agent = (context or {}).get("agent") if options.get("include_agent") else None
    chosen = normalize_style(style or options.get("style") or "light")
    size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    fallback = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if fallback.size != size:
        fallback = fallback.resize(size, Image.Resampling.LANCZOS)
    photos = load_property_photos((context or {}).get("photos") or [])
    planned = copy or summarize_listing_copy(
        facts,
        agent,
        headline=(art or {}).get("headline") or default_headline(language, facts, chosen),
        cta=options.get("cta") or (art or {}).get("cta") or default_cta(language),
        language=language,
    )
    overlay_agent = None
    show_photo = False
    if options.get("include_agent") and agent:
        show_photo = bool(options.get("show_agent_photo", True) and agent.get("photo_path"))
        overlay_agent = {
            "name": planned.get("agent_name") or agent.get("name") or "",
            "title": planned.get("agent_title") or agent.get("title") or "",
            "whatsapp": planned.get("agent_whatsapp") or agent.get("whatsapp") or "",
            "instagram": planned.get("agent_instagram") or agent.get("instagram") or "",
            "photo_path": agent.get("photo_path") if show_photo else None,
        }
    canvas = render_modern_listing(
        size,
        photos,
        facts,
        planned,
        overlay_agent,
        options,
        chosen,
        fallback_hero=fallback if not photos else None,
    )
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    logo = _office_logo(facts)
    logger.info(
        "marketing_overlay stamped layout=%s logo=%s agent_photo=%s photos=%s style=%s",
        OVERLAY_LAYOUT,
        bool(logo),
        show_photo,
        len(photos),
        chosen,
    )
    return {
        "png_bytes": buffer.getvalue(),
        "agent_photo_composited": show_photo,
        "logo_stamped": bool(logo),
        "post_process": OVERLAY_POST_PROCESS,
        "post_process_fn": OVERLAY_FN,
        "layout": OVERLAY_LAYOUT,
        "listing_photos": len(photos),
    }
