"""AI art direction. Unique briefs per item, never property facts."""

from __future__ import annotations

import logging
import os

from modules.jrh_ai_provider import get_jrh_ai_provider_name
from modules.marketing_context import ai_prompt_facts

logger = logging.getLogger(__name__)

STORY_DIRECTIONS = ("luxury_editorial", "bright_architectural", "contemporary_lifestyle")
POST_DIRECTIONS = ("property_led", "price_led", "agent_lifestyle")
FLYER_DIRECTIONS = ("editorial_premium", "sales_focused", "photo_brochure")
STATUS_DIRECTIONS = STORY_DIRECTIONS

DIRECTION_POOL = {
    "story": STORY_DIRECTIONS,
    "status": STATUS_DIRECTIONS,
    "post": POST_DIRECTIONS,
    "flyer": FLYER_DIRECTIONS,
}

HOOKS = {
    "luxury_editorial": "Tu próximo comienzo está acá.",
    "bright_architectural": "Espacios que inspiran.",
    "contemporary_lifestyle": "Viví lo que te gusta.",
    "property_led": "Una oportunidad con identidad.",
    "price_led": "Consultá esta propiedad.",
    "agent_lifestyle": "Te acompaño en cada paso.",
    "editorial_premium": "Disponible en una ubicación estratégica.",
    "sales_focused": "Consultá esta oportunidad.",
    "photo_brochure": "Un hogar con identidad.",
}

BRIEFS = {
    "luxury_editorial": "Luxury editorial: full-bleed hero photo, dark elegant type, small agent integration.",
    "bright_architectural": "Bright architectural: multi-photo composition, white space + blue accents, agent at bottom.",
    "contemporary_lifestyle": "Contemporary lifestyle: hero photo, large expressive typography, agent portrait integrated asymmetrically.",
    "property_led": "Property-led feed post: the listing photo dominates.",
    "price_led": "Price-led commercial post: price and address as the visual hero.",
    "agent_lifestyle": "Agent + lifestyle: the agent is a co-hero beside the property.",
    "editorial_premium": "Editorial premium flyer: magazine cover energy, photography first.",
    "sales_focused": "Sales-focused flyer: clear offer hierarchy, not a dashboard.",
    "photo_brochure": "Photo brochure: three real listing photos, agency-grade layout.",
}


def _layout(direction, fmt):
    return {
        "creative_brief": BRIEFS.get(direction) or BRIEFS["bright_architectural"],
        "text_theme": "light" if direction in {"luxury_editorial", "price_led"} else "dark",
        "background_style": BRIEFS.get(direction) or "",
        "format": fmt,
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
    lowered = raw.lower()
    if any(token in lowered for token in ("vista al río", "pileta", "la mejor", "palacio")):
        locality = facts.get("locality") or facts.get("title") or ""
        return f"Una oportunidad en {locality}." if locality else "Espacios que inspiran."
    return raw[:72]


def _fallback_direction(fmt, index, used, facts, request):
    direction = pick_direction(fmt, index, used)
    layout = _layout(direction, fmt)
    locality = facts.get("locality") or ""
    hook = HOOKS.get(direction) or "Espacios que inspiran."
    if "Victoria" in hook and locality and locality != "Victoria":
        hook = hook.replace("Victoria", locality)
    return {
        "visual_direction": direction,
        "creative_brief": layout.get("creative_brief"),
        "background_style": layout.get("background_style"),
        "layout": layout,
        "headline": hook,
        "short_hook": facts.get("type_label") or "",
        "cta": "Consultá" if request.get("copy_density") == "low" else "Consultá por esta propiedad",
        "copy_density": request.get("copy_density") or "low",
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
                "Art-direct one finished real-estate advertisement. Do not invent "
                "amenities, views, prices or rooms. Return JSON with visual_direction, "
                "creative_brief, headline (<=8 words), short_hook (<=6 words), "
                "cta (<=4 words), text_theme (light|dark)."
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
        fallback["short_hook"] = _safe_hook(parsed.get("short_hook"), (context or {}).get("facts") or {}) or fallback["short_hook"]
        if parsed.get("cta"):
            fallback["cta"] = str(parsed.get("cta"))[:36]
        if parsed.get("creative_brief"):
            fallback["creative_brief"] = str(parsed["creative_brief"])[:220]
        if parsed.get("text_theme") in {"light", "dark"}:
            fallback["text_theme"] = parsed["text_theme"]
            fallback["layout"]["text_theme"] = parsed["text_theme"]
        return fallback
    except Exception:
        logger.info("marketing_art_director fallback")
        return fallback
