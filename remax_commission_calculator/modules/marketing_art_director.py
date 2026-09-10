"""AI art direction. Outputs slot layouts, never property facts."""

from __future__ import annotations

import logging
import os

from modules.jrh_ai_provider import get_jrh_ai_provider_name
from modules.marketing_context import ai_prompt_facts

logger = logging.getLogger(__name__)

STORY_DIRECTIONS = ("editorial_dark", "bright_geometric", "photo_lifestyle")
POST_DIRECTIONS = ("luxury_minimal", "bold_grid", "clean_agent_led")
FLYER_DIRECTIONS = ("editorial_property", "modern_sales", "premium_brochure")
STATUS_DIRECTIONS = STORY_DIRECTIONS

DIRECTION_POOL = {
    "story": STORY_DIRECTIONS,
    "status": STATUS_DIRECTIONS,
    "post": POST_DIRECTIONS,
    "flyer": FLYER_DIRECTIONS,
}

HOOKS = {
    "editorial_dark": "Tu próximo comienzo está acá.",
    "bright_geometric": "Espacios que inspiran.",
    "photo_lifestyle": "Una oportunidad en Victoria.",
    "luxury_minimal": "Viví lo que te gusta.",
    "bold_grid": "Más que una propiedad.",
    "clean_agent_led": "Te acompaño en cada paso.",
    "editorial_property": "Disponible en una ubicación estratégica.",
    "modern_sales": "Consultá esta oportunidad.",
    "premium_brochure": "Un hogar con identidad.",
}


def _slot(x, y, w, h, **extra):
    data = {"x": x, "y": y, "w": w, "h": h}
    data.update(extra)
    return data


def _layout(direction, fmt):
    tall = fmt in {"story", "status", "flyer"}
    layouts = {
        "editorial_dark": {
            "photo_slots": [_slot(0.0, 0.0, 1.0, 0.62, photo_index=0, radius=0)],
            "agent_slot": _slot(0.08, 0.84, 0.22, 0.12, shape="rounded"),
            "logo_slot": _slot(0.08, 0.04, 0.22, 0.05),
            "title_anchor": _slot(0.08, 0.66, 0.84, 0.08),
            "price_anchor": _slot(0.08, 0.76, 0.5, 0.06),
            "features_anchor": _slot(0.08, 0.82, 0.7, 0.04),
            "cta_anchor": _slot(0.55, 0.88, 0.37, 0.05),
            "text_theme": "light",
            "background_style": "full-bleed navy fade, electric accent line",
        },
        "bright_geometric": {
            "photo_slots": [
                _slot(0.06, 0.14, 0.58, 0.42, photo_index=0, radius=0.04),
                _slot(0.66, 0.14, 0.28, 0.20, photo_index=1, radius=0.04),
                _slot(0.66, 0.36, 0.28, 0.20, photo_index=2, radius=0.04),
            ],
            "agent_slot": _slot(0.68, 0.78, 0.24, 0.16, shape="circle"),
            "logo_slot": _slot(0.06, 0.04, 0.24, 0.06),
            "title_anchor": _slot(0.06, 0.60, 0.7, 0.08),
            "price_anchor": _slot(0.06, 0.72, 0.5, 0.07),
            "features_anchor": _slot(0.06, 0.80, 0.6, 0.04),
            "cta_anchor": _slot(0.06, 0.88, 0.4, 0.05),
            "text_theme": "dark",
            "background_style": "white field, electric curves, navy header pill",
        },
        "photo_lifestyle": {
            "photo_slots": [
                _slot(0.08, 0.10, 0.84, 0.38, photo_index=0, radius=0.045),
                _slot(0.08, 0.78, 0.28, 0.14, photo_index=1, radius=0.03),
                _slot(0.38, 0.78, 0.28, 0.14, photo_index=2, radius=0.03),
            ],
            "agent_slot": _slot(0.70, 0.76, 0.22, 0.16, shape="circle"),
            "logo_slot": _slot(0.08, 0.03, 0.22, 0.05),
            "title_anchor": _slot(0.08, 0.50, 0.84, 0.10),
            "price_anchor": _slot(0.08, 0.66, 0.45, 0.07),
            "features_anchor": _slot(0.08, 0.62, 0.8, 0.04),
            "cta_anchor": _slot(0.55, 0.67, 0.37, 0.05),
            "text_theme": "dark",
            "background_style": "airy white, script accent, soft navy footer",
        },
        "luxury_minimal": {
            "photo_slots": [_slot(0.08, 0.12, 0.84, 0.48, photo_index=0, radius=0.05)],
            "agent_slot": _slot(0.72, 0.78, 0.20, 0.16, shape="circle"),
            "logo_slot": _slot(0.08, 0.04, 0.22, 0.05),
            "title_anchor": _slot(0.08, 0.63, 0.6, 0.08),
            "price_anchor": _slot(0.08, 0.78, 0.4, 0.07),
            "features_anchor": _slot(0.08, 0.73, 0.6, 0.04),
            "cta_anchor": _slot(0.08, 0.88, 0.36, 0.05),
            "text_theme": "dark",
            "background_style": "white luxury, thin navy rules, lots of air",
        },
        "bold_grid": {
            "photo_slots": [
                _slot(0.05, 0.12, 0.58, 0.46, photo_index=0, radius=0.02),
                _slot(0.65, 0.12, 0.30, 0.22, photo_index=1, radius=0.02),
                _slot(0.65, 0.36, 0.30, 0.22, photo_index=2, radius=0.02),
            ],
            "agent_slot": _slot(0.08, 0.80, 0.18, 0.14, shape="rounded"),
            "logo_slot": _slot(0.05, 0.03, 0.22, 0.06),
            "title_anchor": _slot(0.05, 0.62, 0.9, 0.08),
            "price_anchor": _slot(0.30, 0.82, 0.36, 0.07),
            "features_anchor": _slot(0.05, 0.72, 0.9, 0.04),
            "cta_anchor": _slot(0.68, 0.84, 0.26, 0.05),
            "text_theme": "dark",
            "background_style": "hard geometric blocks, electric panels",
        },
        "clean_agent_led": {
            "photo_slots": [
                _slot(0.06, 0.10, 0.55, 0.50, photo_index=0, radius=0.04),
            ],
            "agent_slot": _slot(0.64, 0.14, 0.30, 0.36, shape="rounded"),
            "logo_slot": _slot(0.06, 0.03, 0.22, 0.05),
            "title_anchor": _slot(0.06, 0.64, 0.88, 0.08),
            "price_anchor": _slot(0.06, 0.76, 0.5, 0.07),
            "features_anchor": _slot(0.06, 0.72, 0.7, 0.04),
            "cta_anchor": _slot(0.06, 0.86, 0.4, 0.05),
            "text_theme": "dark",
            "background_style": "clean white, agent as co-hero, navy type",
        },
        "editorial_property": {
            "photo_slots": [
                _slot(0.07, 0.10, 0.86, 0.36, photo_index=0, radius=0.03),
                _slot(0.07, 0.48, 0.41, 0.18, photo_index=1, radius=0.03),
                _slot(0.50, 0.48, 0.20, 0.18, photo_index=2, radius=0.03),
            ],
            "agent_slot": _slot(0.72, 0.48, 0.21, 0.18, shape="rounded"),
            "logo_slot": _slot(0.07, 0.03, 0.24, 0.05),
            "title_anchor": _slot(0.07, 0.69, 0.86, 0.07),
            "price_anchor": _slot(0.07, 0.78, 0.45, 0.06),
            "features_anchor": _slot(0.07, 0.84, 0.7, 0.03),
            "cta_anchor": _slot(0.07, 0.90, 0.4, 0.04),
            "text_theme": "dark",
            "background_style": "brochure white, navy footer bar",
        },
        "modern_sales": {
            "photo_slots": [
                _slot(0.42, 0.08, 0.52, 0.36, photo_index=0, radius=0.04),
                _slot(0.06, 0.50, 0.42, 0.22, photo_index=1, radius=0.03),
                _slot(0.50, 0.50, 0.44, 0.22, photo_index=2, radius=0.03),
            ],
            "agent_slot": _slot(0.70, 0.76, 0.24, 0.16, shape="circle"),
            "logo_slot": _slot(0.06, 0.06, 0.28, 0.06),
            "title_anchor": _slot(0.06, 0.16, 0.34, 0.20),
            "price_anchor": _slot(0.06, 0.76, 0.4, 0.07),
            "features_anchor": _slot(0.06, 0.40, 0.32, 0.08),
            "cta_anchor": _slot(0.06, 0.88, 0.4, 0.05),
            "text_theme": "dark",
            "background_style": "split sales sheet, electric CTA, navy type",
        },
        "premium_brochure": {
            "photo_slots": [
                _slot(0.08, 0.08, 0.54, 0.40, photo_index=0, radius=0.04),
                _slot(0.64, 0.08, 0.28, 0.19, photo_index=1, radius=0.03),
                _slot(0.64, 0.29, 0.28, 0.19, photo_index=2, radius=0.03),
            ],
            "agent_slot": _slot(0.08, 0.78, 0.20, 0.14, shape="circle"),
            "logo_slot": _slot(0.64, 0.78, 0.28, 0.06),
            "title_anchor": _slot(0.08, 0.52, 0.84, 0.08),
            "price_anchor": _slot(0.08, 0.64, 0.5, 0.07),
            "features_anchor": _slot(0.08, 0.72, 0.7, 0.04),
            "cta_anchor": _slot(0.32, 0.84, 0.36, 0.05),
            "text_theme": "dark",
            "background_style": "premium brochure, navy wordmark, electric marks",
        },
    }
    layout = dict(layouts.get(direction) or layouts["bright_geometric"])
    if not tall and direction == "editorial_dark":
        layout["photo_slots"] = [_slot(0.0, 0.0, 1.0, 0.55, photo_index=0, radius=0)]
    return layout


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
                "Art-direct one real-estate creative. Do not invent amenities, "
                "views, prices or rooms. Return JSON with visual_direction, "
                "background_style, headline (<=8 words), short_hook (<=6 words), "
                "cta (<=4 words), text_theme (light|dark). No property photos."
            ),
            user_content=[
                {
                    "type": "text",
                    "text": (
                        f"Format={fmt} index={index} request={request} "
                        f"public_facts={facts} suggested={fallback['visual_direction']}"
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
        if parsed.get("background_style"):
            fallback["background_style"] = str(parsed["background_style"])[:160]
        if parsed.get("text_theme") in {"light", "dark"}:
            fallback["text_theme"] = parsed["text_theme"]
            fallback["layout"]["text_theme"] = parsed["text_theme"]
        return fallback
    except Exception:
        logger.info("marketing_art_director fallback")
        return fallback
