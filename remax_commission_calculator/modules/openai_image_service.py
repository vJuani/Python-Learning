"""OpenAI Images service for finished JRH marketing creatives."""

from __future__ import annotations

import logging
import os

from modules.marketing_context import (
    MarketingError,
    build_property_marketing_context,
)
from modules.marketing_image_provider import (
    get_marketing_image_model,
    get_marketing_image_provider,
    get_marketing_image_provider_name,
)
from modules.marketing_references import collect_reference_images
from modules.marketing_renderer import FORMAT_SIZES
from modules.marketing_request import MAX_BATCH_ITEMS

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1"
FORMAT_ALIASES = {
    "historia": "story",
    "story": "story",
    "stories": "story",
    "historias": "story",
    "post": "post",
    "posts": "post",
    "flyer": "flyer",
    "flyers": "flyer",
    "status": "status",
    "pack": "pack",
}
STYLE_ALIASES = {
    "premium": "premium",
    "elegant": "premium",
    "elegante": "premium",
    "minimal": "minimal",
    "minimalista": "minimal",
    "modern": "modern",
    "moderno": "modern",
}
STYLE_BRIEFS = {
    "premium": (
        "premium editorial real-estate, navy/white/deep blue palette, "
        "polished luxury listing, restrained gold-free elegance"
    ),
    "minimal": (
        "minimalist real-estate, generous negative space, few words, "
        "one hero photo, quiet navy accents"
    ),
    "modern": (
        "modern architectural listing, crisp geometry, high contrast, "
        "electric blue accents on navy and white"
    ),
}
FORMAT_BRIEFS = {
    "story": (
        "Instagram / WhatsApp story, vertical 9:16, 1080x1920. "
        "Mobile-first. Hero photo fills most of the frame."
    ),
    "status": (
        "WhatsApp status, vertical 9:16, 1080x1920. "
        "Same story treatment, even shorter copy."
    ),
    "post": (
        "Instagram feed post, 4:5 portrait, 1080x1350. "
        "Square-leaning composition that stays readable in-feed."
    ),
    "flyer": (
        "Vertical flyer / one-pager, 1240x1754. "
        "Print-aware hierarchy, still photographic, never a Word template."
    ),
}


def get_openai_api_key():
    return os.environ.get("OPENAI_API_KEY", "").strip()


def get_openai_image_model():
    return get_marketing_image_model() or DEFAULT_OPENAI_IMAGE_MODEL


def openai_images_configured():
    if get_marketing_image_provider_name() == "mock":
        return True
    return bool(get_openai_api_key())


def assert_openai_configured():
    if openai_images_configured():
        return
    raise MarketingError("marketing_err_no_api_key", 400)


def normalize_style(style):
    key = str(style or "premium").strip().lower()
    return STYLE_ALIASES.get(key, "premium")


def normalize_format(fmt):
    key = str(fmt or "story").strip().lower()
    return FORMAT_ALIASES.get(key, "story")


def resolve_generation_plan(formats=None, count=1, quantity=None):
    raw_quantity = quantity if quantity not in (None, "") else count
    qty = str(raw_quantity if raw_quantity not in (None, "") else 1).strip().lower()
    if isinstance(formats, str):
        formats = [formats]
    normalized = [normalize_format(item) for item in (formats or ["story"])]
    if qty == "pack" or "pack" in normalized:
        return [
            {"format": fmt, "index": index}
            for fmt in ("story", "post", "flyer")
            for index in (1, 2, 3)
        ]
    try:
        total = int(qty)
    except (TypeError, ValueError):
        total = 1
    total = max(1, min(int(total), MAX_BATCH_ITEMS))
    chosen = [item for item in normalized if item in FORMAT_SIZES] or ["story"]
    items = []
    for fmt in chosen:
        for index in range(1, total + 1):
            items.append({"format": fmt, "index": index})
            if len(items) >= MAX_BATCH_ITEMS:
                return items
    return items


def _fact_line(facts, include_price=True):
    facts = facts or {}
    chips = list(facts.get("chips") or [])
    parts = [
        facts.get("title"),
        facts.get("type_label"),
        facts.get("purpose_label"),
        facts.get("location_line") or facts.get("locality"),
    ]
    if include_price and facts.get("price_label"):
        parts.append(f"Price: {facts['price_label']}")
    if chips:
        parts.append("Facts: " + " · ".join(chips[:4]))
    return " | ".join(part for part in parts if part)


def build_marketing_image_prompt(
    context,
    fmt,
    *,
    options=None,
    art=None,
    request_text="",
    style=None,
    cta="",
    include_price=True,
    include_agent=False,
    variation_index=1,
):
    """Internal art-direction prompt. Never shown to the end user."""
    options = options or {}
    art = art or {}
    facts = (context or {}).get("facts") or {}
    agent = (context or {}).get("agent") or {}
    photos = list((context or {}).get("photos") or [])
    chosen_style = normalize_style(style or options.get("style") or "premium")
    fmt = normalize_format(fmt)
    cta_text = (cta or options.get("cta") or (art or {}).get("cta") or "").strip()
    note = (request_text or options.get("request_text") or options.get("prompt") or "").strip()
    headline = ((art or {}).get("headline") or facts.get("title") or "").strip()
    show_price = include_price if include_price is not None else options.get("show_price", True)
    if (facts.get("price_policy") or {}).get("private"):
        show_price = False
    want_agent = bool(include_agent and options.get("show_agent_photo", include_agent) and agent)
    photo_count = len(photos)
    secondary = "one supporting photo" if photo_count > 1 else "no invented secondary photo"
    agent_block = (
        "Integrate the REAL agent portrait as a small-to-medium professional cutout, "
        "clean, premium, never duplicated, never huge, never a second hero. "
        "Keep the exact same person. Do not invent another face."
        if want_agent
        else "Do not include any agent portrait, invented person, or stock headshot."
    )
    price_block = (
        f"Show the exact price {facts.get('price_label')} as a short, high-contrast fact."
        if show_price and facts.get("price_label")
        else "Do not show a price."
    )
    photo_block = (
        "Use the real listing hero photo as the large dominant image. "
        f"Use {secondary}. Do not invent another property or rooms that are not in the references."
        if photo_count
        else (
            "No listing photo is available. Create a premium branded JRH card with facts only. "
            "Do not invent a specific interior or facade for this home."
        )
    )
    logo_block = (
        "Include the JRH One wordmark or logo small and elegant if a logo reference is provided. "
        "Brand: JRH One. Palette: navy #0A1633, electric blue #0D47FF, white, deep charcoal."
    )
    return (
        "Create one finished premium real-estate marketing piece. "
        "This is the final ad, not a background and not a PowerPoint collage. "
        f"Format: {FORMAT_BRIEFS.get(fmt, FORMAT_BRIEFS['story'])} "
        f"Style: {STYLE_BRIEFS[chosen_style]}. "
        "Composition: clean editorial hierarchy, modern readable type, short copy only, "
        "large hero photograph, a compact facts strip (location, rooms, bedrooms, bathrooms, m²). "
        f"{photo_block} {price_block} {agent_block} {logo_block} "
        f"Listing: {_fact_line(facts, include_price=show_price)}. "
        f"Suggested headline (keep short, rewrite if needed): {headline or 'Disponible'}. "
        f"CTA: {cta_text or 'Consultame'}. "
        f"User note: {note or 'none'}. "
        f"Variant {variation_index}: change crop, overlay geometry and type placement, "
        "but keep the same listing and the same real photos. "
        "No long paragraphs. No fake testimonials. No watermarks except JRH One branding. "
        "No comic, no 3D mascot, no cluttered collage."
    )


def collect_generation_references(context, options):
    packed = collect_reference_images(context, options)
    return packed


def generate_one_marketing_image(
    *,
    prompt,
    size,
    references=None,
    visual_direction=None,
):
    assert_openai_configured()
    provider = get_marketing_image_provider()
    logger.info(
        "openai_image generate model=%s provider=%s refs=%s size=%s",
        get_openai_image_model(),
        get_marketing_image_provider_name(),
        len(references or []),
        size,
    )
    return provider.generate_creative(
        prompt=prompt,
        size=size,
        visual_direction=visual_direction,
        references=references,
    )


def _context_from_inputs(
    property_obj,
    agent_obj=None,
    *,
    include_agent=False,
    language="es",
):
    if isinstance(property_obj, dict) and property_obj.get("facts"):
        context = dict(property_obj)
        if include_agent and agent_obj and not context.get("agent"):
            context["agent"] = agent_obj
        context["include_agent"] = bool(include_agent and context.get("agent"))
        return context
    context = build_property_marketing_context(
        property_obj,
        language=language,
        include_agent=include_agent,
    )
    if include_agent and agent_obj and not context.get("agent"):
        context["agent"] = agent_obj
    return context


def generate_marketing_images(
    property_obj,
    agent_obj=None,
    agent_photo_url=None,
    property_photo_urls=None,
    request_text="",
    formats=None,
    include_agent=False,
    include_price=True,
    count=1,
    *,
    style="premium",
    cta="",
    language="es",
    quantity=None,
    options=None,
):
    """Generate finished marketing images with the real OpenAI Images API."""
    assert_openai_configured()
    context = _context_from_inputs(
        property_obj,
        agent_obj,
        include_agent=include_agent,
        language=language,
    )
    if property_photo_urls:
        extras = []
        for index, url in enumerate(property_photo_urls or []):
            if not url:
                continue
            extras.append(
                {
                    "id": f"url-{index}",
                    "original_url": url,
                    "is_cover": index == 0,
                    "position": index,
                }
            )
        if extras:
            context = dict(context)
            context["photos"] = extras
            context["photo_count"] = len(extras)
    if include_agent and agent_obj:
        agent = dict(context.get("agent") or {})
        agent.update(agent_obj)
        if agent_photo_url and not agent.get("photo_path"):
            agent["photo_url"] = agent_photo_url
        context["agent"] = agent
    elif include_agent and agent_photo_url:
        agent = dict(context.get("agent") or {})
        agent["photo_url"] = agent_photo_url
        context["agent"] = agent
    plan = resolve_generation_plan(formats, count=count, quantity=quantity)
    chosen_style = normalize_style(style)
    item_options = {
        "include_agent": bool(include_agent and context.get("agent")),
        "show_agent_photo": bool(include_agent and context.get("agent")),
        "show_price": bool(include_price),
        "style": chosen_style,
        "cta": (cta or "").strip(),
        "request_text": (request_text or "").strip(),
        "prompt": (request_text or "").strip(),
    }
    if options:
        item_options.update(options)
    results = []
    for item in plan:
        fmt = item["format"]
        packed = collect_generation_references(context, item_options)
        prompt = build_marketing_image_prompt(
            context,
            fmt,
            options=item_options,
            request_text=request_text,
            style=chosen_style,
            cta=cta,
            include_price=include_price,
            include_agent=item_options["include_agent"],
            variation_index=item["index"],
        )
        size = FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
        png_bytes = generate_one_marketing_image(
            prompt=prompt,
            size=size,
            references=packed["references"],
            visual_direction=chosen_style,
        )
        results.append(
            {
                "format": fmt,
                "index": item["index"],
                "png_bytes": png_bytes,
                "size": size,
                "style": chosen_style,
                "model": get_openai_image_model(),
                "provider": get_marketing_image_provider_name(),
                "agent_photo_sent": any(
                    ref.get("role") == "agent" for ref in packed["references"]
                ),
                "property_photo_count": packed.get("property_photo_count") or 0,
            }
        )
    return results


def map_image_error(error):
    message = str(getattr(error, "args", [error])[0] if error else "")
    if "missing_openai_api_key" in message or "image_provider_unavailable" in message:
        return "marketing_err_no_api_key"
    if message.startswith("openai_") or "openai" in message:
        return "marketing_err_openai_failed"
    return "marketing_err_item_failed"
