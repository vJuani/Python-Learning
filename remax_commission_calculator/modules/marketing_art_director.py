"""AI art direction. Unique commercial briefs per item, never property facts."""

from __future__ import annotations

import logging
import os
import re

from modules.jrh_ai_provider import get_jrh_ai_provider_name
from modules.marketing_context import ai_prompt_facts

logger = logging.getLogger(__name__)

STORY_DIRECTIONS = ("property_hero", "clean_collage", "luxury_minimal")
POST_DIRECTIONS = ("property_hero", "price_led", "clean_collage")
FLYER_DIRECTIONS = ("clean_collage", "property_hero", "luxury_minimal")
STATUS_DIRECTIONS = STORY_DIRECTIONS

DIRECTION_POOL = {
    "story": STORY_DIRECTIONS,
    "status": STATUS_DIRECTIONS,
    "post": POST_DIRECTIONS,
    "flyer": FLYER_DIRECTIONS,
}

HOOKS = {
    "property_hero": "Tu próximo hogar",
    "clean_collage": "En esta zona",
    "luxury_minimal": "Disponible ahora",
    "price_led": "Consultá esta propiedad",
    "luxury_editorial": "Tu próximo hogar",
    "bright_architectural": "En esta zona",
    "contemporary_lifestyle": "Disponible ahora",
    "property_led": "Consultá esta propiedad",
    "agent_lifestyle": "Consultame",
    "editorial_premium": "Disponible ahora",
    "sales_focused": "Consultá esta propiedad",
    "photo_brochure": "Tu próximo hogar",
}

BRIEFS = {
    "property_hero": (
        "PROPERTY HERO: one real listing photo full-bleed, huge price, "
        "real agent cutout at the bottom. Commercial real-estate ad, not editorial poetry."
    ),
    "clean_collage": (
        "CLEAN COLLAGE: three REAL listing photos, price + 3 facts, "
        "agent cutout on the side. Strong advertising layout, little text."
    ),
    "luxury_minimal": (
        "LUXURY MINIMAL: one hero photo, intentional air, strong typography, "
        "small real agent cutout. No extra claims."
    ),
    "price_led": "Price-led commercial post: price and address as the visual hero.",
}

INVENTED_CLAIMS = (
    "excelente ubicaci",
    "apto cr[eé]dito",
    "conectado con todo",
    "vista al r[ií]o",
    "pileta",
    "la mejor",
    "palacio",
    "inversi[oó]n [uú]nica",
    "oportunidad [uú]nica",
    "amenities",
)

INVENTED_CLAIM_RE = re.compile("|".join(INVENTED_CLAIMS), re.I)


def _layout(direction, fmt):
    return {
        "creative_brief": BRIEFS.get(direction) or BRIEFS["property_hero"],
        "text_theme": "light" if direction in {"property_hero", "luxury_minimal", "price_led"} else "dark",
        "background_style": BRIEFS.get(direction) or "",
        "format": fmt,
        "agent_slot": "bottom-right" if direction != "clean_collage" else "bottom-left",
    }


def pick_direction(fmt, index, used):
    pool = list(DIRECTION_POOL.get(fmt) or STORY_DIRECTIONS)
    unused = [item for item in pool if item not in used]
    choice = (unused or pool)[index % len(unused or pool)]
    used.add(choice)
    return choice


def _safe_hook(text, facts):
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    if INVENTED_CLAIM_RE.search(raw):
        locality = facts.get("locality") or facts.get("title") or ""
        return f"En {locality}" if locality else "Consultame"
    return raw[:36]


def _fallback_direction(fmt, index, used, facts, request):
    direction = pick_direction(fmt, index, used)
    layout = _layout(direction, fmt)
    locality = facts.get("locality") or ""
    hook = HOOKS.get(direction) or "Tu próximo hogar"
    if locality and hook == "En esta zona":
        hook = f"En {locality}"
    return {
        "visual_direction": direction,
        "creative_brief": layout.get("creative_brief"),
        "background_style": layout.get("background_style"),
        "layout": layout,
        "headline": hook,
        "short_hook": "",
        "cta": "Consultame",
        "copy_density": request.get("copy_density") or "very_low",
        "text_theme": layout.get("text_theme") or "dark",
    }


def plan_item(context, request, *, fmt, index, used_directions):
    facts = ai_prompt_facts(context)
    fallback = _fallback_direction(fmt, index, used_directions, (context or {}).get("facts") or {}, request)
    if get_jrh_ai_provider_name() in {"mock", "test", "rules"}:
        return fallback
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return fallback
    try:
        from modules.cash_ai_provider import request_structured_json

        parsed = request_structured_json(
            instructions=(
                "Art-direct one finished commercial real-estate advertisement. "
                "Very little copy: optional headline <=4 words, optional hook <=3 words, "
                "CTA <=2 words. Do not invent amenities, views, prices, rooms or claims "
                "like apto crédito or excelente ubicación. Return JSON with "
                "visual_direction, creative_brief, headline, short_hook, cta, "
                "text_theme (light|dark)."
            ),
            user_content=[
                {
                    "type": "text",
                    "text": (
                        f"Format={fmt} index={index} used={sorted(used_directions or [])} "
                        f"request={request} public_facts={facts} "
                        f"suggested={fallback['visual_direction']}"
                    ),
                }
            ],
            log_prefix="marketing_art",
        )
        if not isinstance(parsed, dict):
            return fallback
        fallback["headline"] = _safe_hook(parsed.get("headline"), (context or {}).get("facts") or {}) or fallback["headline"]
        fallback["short_hook"] = _safe_hook(parsed.get("short_hook"), (context or {}).get("facts") or {}) or ""
        if parsed.get("cta"):
            fallback["cta"] = str(parsed.get("cta"))[:20]
        if parsed.get("creative_brief"):
            fallback["creative_brief"] = str(parsed["creative_brief"])[:220]
        if parsed.get("text_theme") in {"light", "dark"}:
            fallback["text_theme"] = parsed["text_theme"]
            fallback["layout"]["text_theme"] = parsed["text_theme"]
        return fallback
    except Exception:
        logger.info("marketing_art_director fallback")
        return fallback
