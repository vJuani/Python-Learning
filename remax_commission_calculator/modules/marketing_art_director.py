"""MarketingArtDirector. Unique briefs at the approved JRH quality level."""

from __future__ import annotations

import logging
import os
import re

from modules.jrh_ai_provider import get_jrh_ai_provider_name
from modules.marketing_context import ai_prompt_facts
from modules.marketing_visual_spec import (
    AVOID,
    COMPOSITIONS,
    COPY_DENSITY,
    MAX_CREATIVE_WORDS,
    QUALITY_TARGET,
    build_visual_brief,
)

logger = logging.getLogger(__name__)

STORY_DIRECTIONS = tuple(COMPOSITIONS)
POST_DIRECTIONS = ("white_architectural", "editorial_navy", "photo_led_luxury")
FLYER_DIRECTIONS = ("editorial_navy", "white_architectural", "photo_led_luxury")
STATUS_DIRECTIONS = STORY_DIRECTIONS

DIRECTION_POOL = {
    "story": STORY_DIRECTIONS,
    "status": STATUS_DIRECTIONS,
    "post": POST_DIRECTIONS,
    "flyer": FLYER_DIRECTIONS,
}

HOOKS = {
    "editorial_navy": "Tu próximo hogar te espera",
    "white_architectural": "Espacios que inspiran",
    "photo_led_luxury": "Disponible ahora",
}

INVENTED_CLAIM_RE = re.compile(
    r"excelente ubicaci|apto cr[eé]dito|conectado con todo|vista al r[ií]o|"
    r"pileta|la mejor|palacio|inversi[oó]n [uú]nica|oportunidad [uú]nica|amenities",
    re.I,
)


def pick_direction(fmt, index, used):
    pool = list(DIRECTION_POOL.get(fmt) or STORY_DIRECTIONS)
    unused = [item for item in pool if item not in used]
    choice = unused[0] if unused else pool[index % len(pool)]
    used.add(choice)
    return choice


def _safe_hook(text, facts):
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    if INVENTED_CLAIM_RE.search(raw):
        locality = facts.get("locality") or facts.get("title") or ""
        return f"En {locality}" if locality else None
    words = raw.split()
    if len(words) > MAX_CREATIVE_WORDS:
        raw = " ".join(words[:MAX_CREATIVE_WORDS])
    return raw[:48]


def _fallback_direction(fmt, index, used, facts, request):
    direction = pick_direction(fmt, index, used)
    show_photo = request.get("with_agent_photo") is not False and request.get("with_agent") is not False
    brief = build_visual_brief(fmt, direction, show_agent_photo=show_photo)
    hook = HOOKS.get(direction) or "Tu próximo hogar te espera"
    return {
        "visual_direction": direction,
        "creative_brief": brief["composition"],
        "background_style": brief["composition"],
        "layout": brief,
        "visual_brief": brief,
        "headline": hook,
        "short_hook": "",
        "cta": "Consultame",
        "copy_density": request.get("copy_density") or COPY_DENSITY,
        "text_theme": "light" if brief["theme"] == "dark" else "dark",
        "quality_target": QUALITY_TARGET,
        "avoid": list(AVOID),
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
                "Art-direct one finished commercial real-estate advertisement at the "
                "approved JRH quality target. Very little copy: optional headline "
                "<=8 words, CTA <=2 words. Do not invent amenities or claims. "
                "Return JSON with visual_direction, creative_brief, headline, cta, "
                "text_theme (light|dark)."
            ),
            user_content=[
                {
                    "type": "text",
                    "text": (
                        f"Format={fmt} index={index} used={sorted(used_directions or [])} "
                        f"suggested={fallback['visual_direction']} brief={fallback['visual_brief']}"
                    ),
                }
            ],
            log_prefix="marketing_art",
        )
        if not isinstance(parsed, dict):
            return fallback
        fallback["headline"] = _safe_hook(parsed.get("headline"), (context or {}).get("facts") or {}) or fallback["headline"]
        if parsed.get("cta"):
            fallback["cta"] = str(parsed.get("cta"))[:20]
        if parsed.get("creative_brief"):
            fallback["creative_brief"] = str(parsed["creative_brief"])[:280]
        return fallback
    except Exception:
        logger.info("marketing_art_director fallback")
        return fallback
