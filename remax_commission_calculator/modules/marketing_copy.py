"""Marketing copy: structured LLM output with deterministic fallback. Never mutates facts."""

from __future__ import annotations

import logging
import os
import re

from modules.i18n import translate
from modules.jrh_ai_provider import get_jrh_ai_provider_name
from modules.marketing_context import ai_prompt_facts

logger = logging.getLogger(__name__)

LIMITS = {
    "headline": 56,
    "subheadline": 96,
    "description": 180,
    "cta": 36,
    "caption": 420,
}

MAX_HASHTAGS = 6
HYPE_RE = re.compile(
    r"vista al r[ií]o|la mejor (propiedad|casa|depto)|ol[ií]mpica|"
    r" palacio |lujo extremo|incre[ií]ble oportunidad unica",
    re.I,
)


def _clip(text, limit):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    clipped = value[: limit - 1].rsplit(" ", 1)[0].strip()
    return clipped or value[:limit]


def _hashtags_from_facts(facts, language="es"):
    tags = []
    locality = str(facts.get("locality") or "").strip()
    if locality:
        token = re.sub(r"[^A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9]+", "", locality)
        if token:
            tags.append("#" + token.replace(" ", ""))
    type_label = str(facts.get("type_label") or "").strip()
    if type_label:
        token = re.sub(r"[^A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9]+", "", type_label)
        if token:
            tags.append("#" + token)
    purpose = str(facts.get("purpose") or "")
    if purpose == "rental":
        tags.append("#Alquiler")
    elif purpose == "sale":
        tags.append("#Venta")
    tags.append("#JRHOne")
    unique = []
    seen = set()
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)
    return unique[:MAX_HASHTAGS]


def _fallback_copy(context, *, tone="professional", language="es"):
    facts = (context or {}).get("facts") or {}
    locality = facts.get("locality") or facts.get("jurisdiction") or ""
    type_label = (facts.get("type_label") or translate("property_type_apartment", language=language)).lower()
    title = facts.get("title") or locality
    if tone == "warm":
        headline = translate("marketing_copy_warm_headline", language=language, place=locality or title)
        cta = translate("marketing_copy_warm_cta", language=language)
    elif tone == "commercial":
        headline = translate("marketing_copy_commercial_headline", language=language, place=locality or title)
        cta = translate("marketing_copy_commercial_cta", language=language)
    else:
        headline = translate("marketing_copy_pro_headline", language=language, place=locality or title)
        cta = translate("marketing_copy_pro_cta", language=language)
    chips = " · ".join(facts.get("chips") or [])
    subheadline = chips or facts.get("location_line") or title
    description = facts.get("description") or translate(
        "marketing_copy_fallback_desc",
        language=language,
        type=type_label,
        place=locality or title,
    )
    caption_lines = [
        headline,
        description,
        chips,
        cta,
        " ".join(_hashtags_from_facts(facts, language)),
    ]
    return {
        "headline": _clip(headline, LIMITS["headline"]),
        "subheadline": _clip(subheadline, LIMITS["subheadline"]),
        "description": _clip(description, LIMITS["description"]),
        "cta": _clip(cta, LIMITS["cta"]),
        "caption": _clip("\n".join(part for part in caption_lines if part), LIMITS["caption"]),
        "hashtags": _hashtags_from_facts(facts, language),
        "source": "fallback",
    }


def _sanitize_ai_copy(parsed, context, *, language="es"):
    fallback = _fallback_copy(context, language=language)
    if not isinstance(parsed, dict):
        return fallback
    facts = (context or {}).get("facts") or {}
    allowed_numbers = {
        str(facts.get("rooms") or ""),
        str(facts.get("bedrooms") or ""),
        str(facts.get("bathrooms") or ""),
        str(facts.get("covered_m2") or "").replace(".0", ""),
        str(facts.get("total_m2") or "").replace(".0", ""),
    }
    allowed_numbers.discard("")
    title = str(facts.get("title") or "")
    street_num = re.search(r"\d+", title)
    if street_num:
        allowed_numbers.add(street_num.group(0))

    def _clean_field(key):
        raw = parsed.get(key)
        text = _clip(raw, LIMITS.get(key, 200))
        if not text:
            return fallback[key]
        if HYPE_RE.search(text):
            return fallback[key]
        for number in re.findall(r"\d+(?:[.,]\d+)?", text):
            compact = number.replace(".", "").replace(",", "")
            if compact not in allowed_numbers and number not in allowed_numbers:
                if key in {"headline", "subheadline", "description"}:
                    return fallback[key]
        return text

    hashtags = parsed.get("hashtags") if isinstance(parsed.get("hashtags"), list) else []
    clean_tags = []
    for tag in hashtags:
        token = str(tag or "").strip()
        if not token:
            continue
        if not token.startswith("#"):
            token = "#" + re.sub(r"\s+", "", token)
        if HYPE_RE.search(token):
            continue
        clean_tags.append(token)
        if len(clean_tags) >= MAX_HASHTAGS:
            break
    if not clean_tags:
        clean_tags = fallback["hashtags"]
    copy = {
        "headline": _clean_field("headline"),
        "subheadline": _clean_field("subheadline"),
        "description": _clean_field("description"),
        "cta": _clean_field("cta"),
        "caption": _clean_field("caption"),
        "hashtags": clean_tags[:MAX_HASHTAGS],
        "source": "ai",
    }
    if not copy["caption"]:
        copy["caption"] = fallback["caption"]
    return copy


def generate_marketing_copy(context, *, tone="professional", language="es"):
    fallback = _fallback_copy(context, tone=tone, language=language)
    if get_jrh_ai_provider_name() in {"mock", "test", "rules"}:
        return fallback
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return fallback
    facts = ai_prompt_facts(context)
    try:
        from modules.cash_ai_provider import request_structured_json

        parsed = request_structured_json(
            instructions=(
                "Write short Argentine-Spanish real-estate marketing copy. "
                "Use ONLY the provided facts. Never invent views, amenities, "
                "prices, areas or rooms. No hype. Return JSON: "
                '{"headline","subheadline","description","cta","caption","hashtags":[]}.'
            ),
            user_content=[
                {
                    "type": "text",
                    "text": (
                        f"Tone: {tone}. Facts: {facts}. "
                        "Hashtags: max 6, location/type only."
                    ),
                }
            ],
            log_prefix="marketing_copy",
        )
        return _sanitize_ai_copy(parsed, context, language=language)
    except Exception:
        logger.info("marketing_copy fallback to deterministic")
        return fallback
