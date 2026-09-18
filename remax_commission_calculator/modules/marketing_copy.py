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


def _wrap_words(text, max_chars, max_lines):
    words = [part for part in str(text or "").split() if part]
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if len(lines) < max_lines and current:
        lines.append(current)
    return lines[:max_lines]


def autofit_text(text, *, max_chars=28, max_lines=2, min_chars=10):
    """Shrink, wrap, then summarize so type never overflows a creative."""
    value = " ".join(str(text or "").split())
    if not value:
        return {"text": "", "lines": [], "font_scale": 1.0, "summarized": False}
    for scale, width in ((1.0, max_chars), (0.92, int(max_chars * 1.08)), (0.84, int(max_chars * 1.16))):
        lines = _wrap_words(value, max(min_chars, width), max_lines)
        if lines and " ".join(lines) == value:
            return {
                "text": "\n".join(lines),
                "lines": lines,
                "font_scale": scale,
                "summarized": False,
            }
    lines = _wrap_words(value, max_chars, max_lines) or [_clip(value, max_chars)]
    return {
        "text": "\n".join(lines),
        "lines": lines,
        "font_scale": 0.8,
        "summarized": " ".join(lines) != value,
    }


def _zone_line(facts):
    from modules.marketing_language import marketing_zone_line

    return marketing_zone_line(facts)


def summarize_listing_copy(facts, agent=None, *, headline="", cta="", language="es", style=None):
    from modules.marketing_language import (
        HYPE_COPY_RE,
        default_agent_role,
        default_cta,
        default_headline,
        default_kicker,
        is_placeholder_copy,
        listing_benefit_line,
        marketing_label,
    )

    facts = facts or {}
    language = language or "es"
    street = autofit_text(facts.get("title") or "", max_chars=28, max_lines=1)
    zone = autofit_text(_zone_line(facts), max_chars=36, max_lines=1)
    chosen_headline = " ".join(str(headline or "").split()) or default_headline(
        language, facts, style
    )
    if HYPE_COPY_RE.search(chosen_headline) or is_placeholder_copy(chosen_headline):
        chosen_headline = default_headline(language, facts, style)
    hook = autofit_text(
        chosen_headline or marketing_label("available", language),
        max_chars=46,
        max_lines=2,
    )
    kicker = autofit_text(
        facts.get("kicker") or default_kicker(language, facts),
        max_chars=18,
        max_lines=1,
    )
    benefit = " ".join(
        str(listing_benefit_line(language, facts, style=style) or facts.get("benefit_line") or "").split()
    )
    if HYPE_COPY_RE.search(benefit):
        benefit = ""
    bajada = autofit_text(benefit, max_chars=44, max_lines=2)
    chips = [str(item).strip() for item in (facts.get("chips") or []) if str(item).strip()][:4]
    agent = agent or {}
    name = autofit_text(agent.get("name") or "", max_chars=24, max_lines=2)
    title = autofit_text(agent.get("title") or default_agent_role(language), max_chars=22, max_lines=1)
    whatsapp = {"text": " ".join(str(agent.get("whatsapp") or "").split())}
    instagram = {"text": " ".join(str(agent.get("instagram") or "").split())}
    email = {"text": " ".join(str(agent.get("email") or "").split())}
    chosen_cta = " ".join(str(cta or "").split()) or default_cta(language, facts, style)
    if HYPE_COPY_RE.search(chosen_cta):
        chosen_cta = default_cta(language, facts, style)
    cta_fit = autofit_text(chosen_cta, max_chars=36, max_lines=1)
    broker = " ".join(
        part
        for part in (
            "Corredor Público" if language == "es" else "Licensed Broker",
            facts.get("legal_broker_name") or "",
        )
        if part
    ).strip()
    license_no = " ".join(str(facts.get("legal_broker_license") or "").split())
    legal = autofit_text(
        facts.get("legal_footer_line")
        or " ".join(part for part in (broker, license_no) if part),
        max_chars=72,
        max_lines=2,
    )
    legal_name = autofit_text(broker, max_chars=40, max_lines=1)
    legal_license = autofit_text(license_no, max_chars=40, max_lines=1)
    return {
        "headline": hook["text"].replace("\n", " "),
        "headline_lines": hook["lines"],
        "kicker": kicker["text"],
        "subheadline": bajada["text"].replace("\n", " "),
        "street": street["text"],
        "zone": zone["text"],
        "attributes": chips,
        "price": facts.get("price_label") or "",
        "cta": cta_fit["text"] or default_cta(language, facts, style),
        "agent_name": name["text"].replace("\n", " "),
        "agent_title": title["text"],
        "agent_whatsapp": whatsapp["text"],
        "agent_instagram": instagram["text"],
        "agent_email": email["text"],
        "legal_broker_line": legal_name["text"],
        "legal_license_line": legal_license["text"],
        "legal_footer": legal["text"].replace("\n", " "),
        "brand_name": facts.get("brand_name") or facts.get("organization_name") or "",
        "summarized": any(
            item.get("summarized") for item in (street, zone, hook, name, title, legal)
        ),
    }


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
    brand = str(facts.get("brand_name") or facts.get("organization_name") or "").strip()
    if brand:
        token = re.sub(r"[^A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9]+", "", brand)
        if token:
            tags.append("#" + token)
    elif language == "en":
        tags.append("#RealEstate")
    else:
        tags.append("#Inmuebles")
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
                "Write short Argentine-Spanish real-estate marketing copy that sells. "
                "Strong headline (benefit + place, title case), one-line bajada, clear CTA. "
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
