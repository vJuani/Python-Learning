"""Deterministic branding overlay. The model never redraws logo, agent, or facts."""

from __future__ import annotations

import io
import logging

from PIL import Image

from modules.marketing_context import MarketingError
from modules.marketing_copy import summarize_listing_copy
from modules.marketing_language import default_cta, default_headline
from modules.marketing_flyer_modern import (
    V2_TEMPLATES,
    resolve_layout_template,
)
from modules.marketing_flyer_commercial import (
    RENDERER_USED as COMMERCIAL_V2_RENDERER,
    render_layout_v2,
)
from modules.marketing_renderer import (
    FORMAT_SIZES,
    _office_logo,
    load_property_photos,
)
from modules.marketing_visual_spec import normalize_style

logger = logging.getLogger(__name__)

OVERLAY_POST_PROCESS = "branding_overlay"
OVERLAY_FN = "modules.marketing_overlay.stamp_branding_overlay"
# Display/debug default — always a V2 id.
OVERLAY_LAYOUT = "modern_commercial_v2"
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
    """Compose the final listing piece with a V2 template only."""
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
        cta=options.get("cta") or (art or {}).get("cta") or default_cta(language, facts, chosen),
        language=language,
        style=chosen,
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
    layout = resolve_layout_template(
        fmt,
        {
            **options,
            # Prefer the caller's creative style before normalize_style buckets.
            "style": style or options.get("style") or options.get("creative_style") or chosen,
        },
    )
    if layout not in V2_TEMPLATES:
        raise MarketingError("marketing_err_no_v2_template", 400)
    canvas = render_layout_v2(
        layout,
        size,
        photos,
        facts,
        planned,
        overlay_agent,
        {**options, "photo_rows": (context or {}).get("photos") or []},
        language=language,
        fallback_hero=fallback if not photos else None,
    )
    renderer_used = COMMERCIAL_V2_RENDERER
    layout_version = layout
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    logo = _office_logo(facts)
    logger.info(
        "marketing_overlay stamped layout=%s logo=%s agent_photo=%s photos=%s style=%s",
        layout,
        bool(logo),
        show_photo,
        len(photos),
        chosen,
    )
    logger.info("template_used=%s", layout)
    return {
        "png_bytes": buffer.getvalue(),
        "agent_photo_composited": show_photo,
        "logo_stamped": bool(logo),
        "post_process": OVERLAY_POST_PROCESS,
        "post_process_fn": OVERLAY_FN,
        "layout": layout,
        "template_used": layout,
        "renderer_used": renderer_used,
        "layout_version": layout_version,
        "listing_photos": len(photos),
    }
