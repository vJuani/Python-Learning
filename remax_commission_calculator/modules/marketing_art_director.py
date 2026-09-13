"""MarketingArtDirector. Unique briefs at the approved JRH quality level."""

from __future__ import annotations

import logging
import os
import re

from modules.jrh_ai_provider import get_jrh_ai_provider_name
from modules.marketing_context import ai_prompt_facts
from modules.marketing_language import (
    default_cta,
    default_headline,
    default_kicker,
    forbidden_language_hits,
    is_location_headline,
    resolve_creative_language,
)
from modules.marketing_visual_spec import (
    AVOID,
    COPY_DENSITY,
    EDITORIAL_PREMIUM,
    LUXURY_MINIMAL,
    MAX_CREATIVE_WORDS,
    MODERN_COMMERCIAL,
    QUALITY_TARGET,
    build_visual_brief,
    normalize_style,
)

logger = logging.getLogger(__name__)

STORY_DIRECTIONS = (EDITORIAL_PREMIUM, MODERN_COMMERCIAL, LUXURY_MINIMAL)
POST_DIRECTIONS = (MODERN_COMMERCIAL, EDITORIAL_PREMIUM, LUXURY_MINIMAL)
FLYER_DIRECTIONS = (EDITORIAL_PREMIUM, MODERN_COMMERCIAL, LUXURY_MINIMAL)
STATUS_DIRECTIONS = STORY_DIRECTIONS

DIRECTION_POOL = {
    "story": STORY_DIRECTIONS,
    "status": STATUS_DIRECTIONS,
    "post": POST_DIRECTIONS,
    "flyer": FLYER_DIRECTIONS,
}

HOOKS = {
    EDITORIAL_PREMIUM: "Viví distinto",
    MODERN_COMMERCIAL: "Disponible ahora",
    LUXURY_MINIMAL: "Exclusiva",
    "editorial_navy": "Viví distinto",
    "white_architectural": "Disponible ahora",
    "photo_led_luxury": "Exclusiva",
}

INVENTED_CLAIM_RE = re.compile(
    r"excelente ubicaci|apto cr[eé]dito|conectado con todo|vista al r[ií]o|"
    r"pileta|la mejor|palacio|inversi[oó]n [uú]nica|oportunidad [uú]nica|amenities",
    re.I,
)


def pick_direction(fmt, index, used, preferred=None):
    pool = list(DIRECTION_POOL.get(fmt) or STORY_DIRECTIONS)
    preferred = normalize_style(preferred) if preferred else None
    if preferred and preferred not in pool:
        preferred = None
    if preferred and preferred not in used:
        used.add(preferred)
        return preferred
    unused = [item for item in pool if item not in used]
    choice = unused[0] if unused else pool[index % len(pool)]
    used.add(choice)
    return choice


def _safe_kicker(text, facts, language="es"):
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    if forbidden_language_hits(raw, language):
        return None
    if INVENTED_CLAIM_RE.search(raw) or is_location_headline(raw, facts):
        return None
    words = raw.split()
    if len(words) > MAX_CREATIVE_WORDS:
        raw = " ".join(words[:MAX_CREATIVE_WORDS])
    return raw[:48]


def _fallback_direction(fmt, index, used, facts, request):
    language = resolve_creative_language(
        locale=request.get("language"),
        request_text=request.get("request_text") or request.get("prompt") or "",
    )
    direction = pick_direction(
        fmt,
        index,
        used,
        preferred=request.get("style") or request.get("visual_direction"),
    )
    show_photo = request.get("with_agent_photo") is not False and request.get("with_agent") is not False
    brief = build_visual_brief(fmt, direction, show_agent_photo=show_photo)
    headline = default_headline(language, facts)
    kicker = default_kicker(language)
    return {
        "visual_direction": direction,
        "creative_brief": brief["composition"],
        "background_style": brief["composition"],
        "layout": brief,
        "visual_brief": brief,
        "headline": headline,
        "kicker": kicker,
        "short_hook": kicker,
        "cta": default_cta(language),
        "language": language,
        "copy_density": request.get("copy_density") or COPY_DENSITY,
        "text_theme": "light" if brief["theme"] == "dark" else "dark",
        "quality_target": QUALITY_TARGET,
        "avoid": list(AVOID),
    }


def plan_item(context, request, *, fmt, index, used_directions):
    facts = ai_prompt_facts(context)
    fallback = _fallback_direction(fmt, index, used_directions, (context or {}).get("facts") or {}, request)
    language = fallback.get("language") or resolve_creative_language(
        locale=request.get("language") or (context or {}).get("language"),
        request_text=request.get("request_text") or request.get("prompt") or "",
    )
    if get_jrh_ai_provider_name() in {"mock", "test", "rules"}:
        return fallback
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return fallback
    try:
        from modules.cash_ai_provider import request_structured_json

        lang_line = (
            "Write headline and CTA in Spanish only. No English taglines."
            if language == "es"
            else "Write headline and CTA in English only. No Spanish taglines."
        )
        parsed = request_structured_json(
            instructions=(
                "Art-direct one finished commercial real-estate advertisement. "
                "The large title MUST be the operation line (property type + sale/rent), "
                "never the locality and never a poetic slogan. "
                f"CTA <=3 words. {lang_line} Do not invent amenities or claims. "
                "Return JSON with visual_direction, creative_brief, kicker, cta, "
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
        fallback["headline"] = default_headline(
            language, (context or {}).get("facts") or {}
        )
        kicker = _safe_kicker(
            parsed.get("kicker") or parsed.get("headline"),
            (context or {}).get("facts") or {},
            language,
        )
        if kicker:
            fallback["kicker"] = kicker
            fallback["short_hook"] = kicker
        if parsed.get("cta") and not forbidden_language_hits(parsed.get("cta"), language):
            fallback["cta"] = str(parsed.get("cta"))[:20]
        if parsed.get("creative_brief"):
            fallback["creative_brief"] = str(parsed["creative_brief"])[:280]
        return fallback
    except Exception:
        logger.info("marketing_art_director fallback")
        return fallback
