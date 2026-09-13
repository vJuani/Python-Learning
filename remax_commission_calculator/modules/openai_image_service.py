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
from modules.marketing_branding import (
    branding_from_facts,
    branding_prompt_block,
    validate_creative_branding,
)
from modules.marketing_copy import summarize_listing_copy
from modules.marketing_language import (
    default_agent_role,
    default_cta,
    default_headline,
    forbidden_language_hits,
    language_prompt_block,
    resolve_creative_language,
    validate_creative_language,
)
from modules.marketing_quality import validate_creative
from modules.marketing_references import collect_reference_images
from modules.marketing_renderer import FORMAT_SIZES
from modules.marketing_request import MAX_BATCH_ITEMS
from modules.marketing_visual_spec import (
    AVOID,
    HIERARCHY,
    MAX_STORY_ATTRIBUTES,
    STYLE_BRIEFS,
    normalize_style,
    safe_area_prompt,
)

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1"
MAX_LAYOUT_ATTEMPTS = 2
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
    chips = list(facts.get("chips") or [])[:MAX_STORY_ATTRIBUTES]
    copy = summarize_listing_copy(facts)
    parts = [
        copy.get("street") or facts.get("title"),
        facts.get("type_label"),
        facts.get("purpose_label"),
        copy.get("zone") or facts.get("locality"),
    ]
    if include_price and (copy.get("price") or facts.get("price_label")):
        parts.append(f"Price: {copy.get('price') or facts['price_label']}")
    if chips:
        parts.append("Facts: " + " · ".join(chips))
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
    repair_reasons=None,
    language=None,
):
    """Internal art-direction prompt. Never shown to the end user."""
    options = options or {}
    art = art or {}
    facts = (context or {}).get("facts") or {}
    agent = (context or {}).get("agent") or {}
    photos = list((context or {}).get("photos") or [])
    language = resolve_creative_language(
        locale=language or options.get("language") or (context or {}).get("language"),
        request_text=request_text or options.get("request_text") or options.get("prompt") or "",
    )
    chosen_style = normalize_style(
        art.get("visual_direction") or style or options.get("style") or "premium"
    )
    fmt = normalize_format(fmt)
    cta_text = (cta or options.get("cta") or (art or {}).get("cta") or "").strip()
    note = (request_text or options.get("request_text") or options.get("prompt") or "").strip()
    headline = ((art or {}).get("headline") or "").strip() or default_headline(
        language, facts, chosen_style
    )
    if forbidden_language_hits(headline, language):
        headline = default_headline(language, facts, chosen_style)
    if forbidden_language_hits(cta_text, language):
        cta_text = default_cta(language)
    show_price = include_price if include_price is not None else options.get("show_price", True)
    if (facts.get("price_policy") or {}).get("private"):
        show_price = False
    want_agent = bool(include_agent and options.get("show_agent_photo", include_agent) and agent)
    copy = summarize_listing_copy(
        facts,
        agent if want_agent or include_agent else None,
        headline=headline,
        cta=cta_text or default_cta(language),
        language=language,
    )
    photo_count = len(photos)
    secondary = (
        "at most two small supporting photos of the SAME listing"
        if photo_count > 2
        else ("one supporting photo of the SAME listing" if photo_count > 1 else "no invented secondary photo")
    )
    if fmt in {"story", "status"}:
        secondary = (
            "one or two small supporting photos only"
            if photo_count > 1
            else "no invented secondary photo"
        )
    agent_block = (
        "Integrate the REAL agent portrait as a small-to-medium professional cutout, "
        "the official ficha/ACM photo, clean crop, never duplicated, never a second hero. "
        f"Agent block: name '{copy['agent_name']}', short title '{copy['agent_title'] or default_agent_role(language)}'"
        + (f", phone '{copy['agent_phone']}'" if copy.get("agent_phone") else "")
        + ". Never crop the head, shoulders or the name. If it does not fit, shrink the "
        "portrait automatically. Keep the exact same person. Do not invent another face."
        if want_agent
        else "Do not include any agent portrait, invented person, or stock headshot."
    )
    price_block = (
        f"Show the exact price {copy.get('price') or facts.get('price_label')} once, high contrast, inside the safe area."
        if show_price and (copy.get("price") or facts.get("price_label"))
        else "Do not show a price."
    )
    photo_block = (
        "Use the real listing hero photo as the large dominant image. "
        f"Use {secondary}. Do not invent another property or rooms that are not in the references."
        if photo_count
        else (
            "No listing photo is available. Create a premium branded office card with facts only. "
            "Do not invent a specific interior or facade for this home."
        )
    )
    branding = branding_from_facts(facts)
    logo_block = branding_prompt_block(
        branding,
        include_agent=want_agent,
        language=language,
    )
    copy_block = (
        "ALLOWED COPY ONLY: office logo or wordmark, one short headline, one short street OR zone, "
        f"at most {MAX_STORY_ATTRIBUTES} attributes, price if requested, one CTA, "
        "mandatory legal broker footer"
        + (", agent name + short title" if want_agent else "")
        + ". Forbidden: JRH One, Inmobiliaria Principal, long paragraphs, decorative slogans, leftover phrases in corners, "
        "vertical captions, stacked competing headlines, icon rows, amateur flyer clutter."
    )
    hierarchy = " → ".join(HIERARCHY)
    avoid = ", ".join(AVOID)
    repair = ""
    if repair_reasons:
        repair = (
            " REPAIR PASS: the previous layout failed safe-area validation "
            f"({', '.join(repair_reasons)}). Pull every word, logo, price, CTA and agent "
            "name inward. Shrink type and the agent automatically. Leave empty air in the margins."
        )
    if repair_reasons and "wrong_language" in repair_reasons:
        repair += (
            " The previous creative used the wrong language. "
            "Rewrite every visible word in the locked language. Do not mix languages."
        )
    return (
        "Create one finished premium real-estate marketing piece. "
        "This is the final ad, ready to publish: editorial, clean, modern, elegant, minimal. "
        "Not a background, not a PowerPoint collage, not an amateur flyer. "
        f"{language_prompt_block(language)} "
        f"Format: {FORMAT_BRIEFS.get(fmt, FORMAT_BRIEFS['story'])} "
        f"{safe_area_prompt(fmt)} "
        f"Style: {STYLE_BRIEFS[chosen_style]}. "
        f"Hierarchy: {hierarchy}. Property first, then price, facts, contact, then legal broker. "
        "Do not let four large texts compete. Never confuse the agent with the legal broker. "
        f"{copy_block} {photo_block} {price_block} {agent_block} {logo_block} "
        f"Legal footer: {copy.get('legal_footer') or branding.get('legal_footer_line')}. "
        "Typography: every title and name must fit. Shrink the font, tighten tracking, "
        "or wrap to two lines. If it still overflows, summarize "
        f"('{copy['street']}' / '{copy['zone']}'). "
        f"Listing: {_fact_line(facts, include_price=show_price)}. "
        f"Headline: {copy['headline']}. "
        f"Street: {copy['street']}. Zone: {copy['zone']}. "
        f"CTA: {copy['cta']}. "
        f"Visible language: {language}. "
        f"User note: {note or 'none'}. "
        f"Variant {variation_index}: change crop and type placement, "
        "but keep the same listing, the same real photos and this style family. "
        f"Avoid: {avoid}. "
        "No comic, no 3D mascot, no cluttered collage."
        f"{repair}"
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


def generate_validated_marketing_image(
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
    size=None,
    references=None,
    language=None,
):
    """Generate, validate safe areas, and regenerate layout once if needed."""
    options = dict(options or {})
    art = art or {}
    fmt = normalize_format(fmt)
    size = size or FORMAT_SIZES.get(fmt) or FORMAT_SIZES["story"]
    options["format"] = fmt
    options["layout_engine"] = "openai_images"
    language = resolve_creative_language(
        locale=language or options.get("language") or (context or {}).get("language"),
        request_text=request_text or options.get("request_text") or options.get("prompt") or "",
    )
    options["language"] = language
    chosen_style = normalize_style(
        art.get("visual_direction") or style or options.get("style") or "premium"
    )
    last_png = None
    last_verdict = None
    last_prompt = None
    repair_reasons = None
    for attempt in range(1, MAX_LAYOUT_ATTEMPTS + 1):
        if repair_reasons and "wrong_language" in repair_reasons:
            art = dict(art)
            art["headline"] = default_headline(language, (context or {}).get("facts") or {}, chosen_style)
            cta = default_cta(language)
        last_prompt = build_marketing_image_prompt(
            context,
            fmt,
            options=options,
            art=art,
            request_text=request_text,
            style=chosen_style,
            cta=cta,
            include_price=include_price,
            include_agent=include_agent,
            variation_index=variation_index,
            repair_reasons=repair_reasons,
            language=language,
        )
        last_png = generate_one_marketing_image(
            prompt=last_prompt,
            size=size,
            references=references,
            visual_direction=chosen_style,
        )
        last_verdict = validate_creative(
            last_png,
            size=size,
            options=options,
            references=references,
            agent_photo_sent=any(item.get("role") == "agent" for item in (references or [])),
            agent_photo_composited=any(item.get("role") == "agent" for item in (references or [])),
            fmt=fmt,
        )
        last_verdict = dict(last_verdict)
        last_verdict["attempt"] = attempt
        planned = summarize_listing_copy(
            (context or {}).get("facts") or {},
            (context or {}).get("agent") if include_agent else None,
            headline=(art or {}).get("headline") or "",
            cta=cta or options.get("cta") or "",
            language=language,
        )
        language_verdict = validate_creative_language(
            last_png,
            language=language,
            planned_copy=planned,
            extra_text=f"{art.get('headline') or ''} {cta or options.get('cta') or ''}",
        )
        last_verdict["language"] = language
        last_verdict["language_hits"] = language_verdict.get("hits") or []
        if not language_verdict.get("ok"):
            last_verdict["ok"] = False
            reasons = list(last_verdict.get("reasons") or [])
            if "wrong_language" not in reasons:
                reasons.append("wrong_language")
            last_verdict["reasons"] = reasons
            last_verdict["status"] = "failed_quality"
            options["language_retry"] = True
        branding = branding_from_facts((context or {}).get("facts") or {})
        brand_verdict = validate_creative_branding(
            last_png,
            planned_copy=planned,
            extra_text=f"{planned.get('brand_name') or ''} {planned.get('legal_footer') or ''}",
            branding=branding,
        )
        last_verdict["requires_legal_review"] = bool(
            branding.get("requires_legal_review") or not branding.get("publishable")
        )
        if not brand_verdict.get("ok"):
            last_verdict["ok"] = False
            reasons = list(last_verdict.get("reasons") or [])
            for reason in brand_verdict.get("reasons") or []:
                if reason not in reasons:
                    reasons.append(reason)
            last_verdict["reasons"] = reasons
            last_verdict["status"] = "failed_quality"
        if last_verdict.get("ok"):
            break
        repair_reasons = last_verdict.get("reasons") or []
        logger.info(
            "marketing layout retry fmt=%s attempt=%s reasons=%s",
            fmt,
            attempt,
            repair_reasons[:6],
        )
    return {
        "png_bytes": last_png,
        "quality": last_verdict or {"ok": False, "reasons": ["unreadable"]},
        "prompt": last_prompt,
        "style": chosen_style,
        "size": size,
        "language": language,
        "language_retry": bool(options.get("language_retry")),
        "requires_legal_review": bool(
            (last_verdict or {}).get("requires_legal_review")
        ),
        "publishable": not bool((last_verdict or {}).get("requires_legal_review")),
    }


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
        "language": resolve_creative_language(
            locale=language,
            request_text=request_text,
        ),
    }
    if options:
        item_options.update(options)
    results = []
    for item in plan:
        fmt = item["format"]
        packed = collect_generation_references(context, item_options)
        generated = generate_validated_marketing_image(
            context,
            fmt,
            options=item_options,
            request_text=request_text,
            style=chosen_style,
            cta=cta,
            include_price=include_price,
            include_agent=item_options["include_agent"],
            variation_index=item["index"],
            references=packed["references"],
            language=item_options.get("language") or language,
        )
        results.append(
            {
                "format": fmt,
                "index": item["index"],
                "png_bytes": generated["png_bytes"],
                "size": generated["size"],
                "style": generated["style"],
                "quality": generated.get("quality"),
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
