"""Locale for Marketing creatives. Session language wins over request text."""

from __future__ import annotations

import io
import logging
import re

from modules.i18n import DEFAULT_LANGUAGE, normalize_language

logger = logging.getLogger(__name__)

MARKETING_COPY = {
    "es": {
        "default_headline_sale": "En venta",
        "default_headline_investment": "Inversión disponible",
        "cta": "Contáctanos",
        "cta_short": "Consultame",
        "for_sale": "en venta",
        "for_rent": "en alquiler",
        "agent_role": "Agente inmobiliario",
        "available": "Disponible",
        "kicker": "Tu próximo hogar está acá",
        "headline_editorial": "Tu próximo hogar está acá",
        "headline_commercial": "Disponible ahora",
        "headline_luxury": "Exclusiva",
    },
    "en": {
        "default_headline_sale": "For Sale",
        "default_headline_investment": "Investment Opportunity",
        "cta": "Inquire Now",
        "cta_short": "Inquire",
        "for_sale": "for sale",
        "for_rent": "for rent",
        "agent_role": "Real Estate Agent",
        "available": "Available",
        "kicker": "Your next home is here",
        "headline_editorial": "Your next home is here",
        "headline_commercial": "Available now",
        "headline_luxury": "Exclusive",
    },
}

JURISDICTION_SHORT = {
    "buenos aires": "PBA",
    "provincia de buenos aires": "PBA",
    "bs as": "PBA",
    "bs. as.": "PBA",
    "bs.as.": "PBA",
    "caba": "CABA",
    "capital federal": "CABA",
    "ciudad autonoma de buenos aires": "CABA",
    "ciudad autónoma de buenos aires": "CABA",
}

ENGLISH_CREATIVE_WORDS = (
    "discover",
    "investment",
    "inquire",
    "luxury",
    "available",
    "now",
    "awaits",
    "office",
    "space",
)
SPANISH_CREATIVE_WORDS = (
    "consultame",
    "descubrí",
    "descubri",
    "alquiler",
    "próximo",
    "proximo",
    "inversión",
    "inversion",
    "hogar",
)
SPANISH_REQUEST_HINTS = (
    "haceme",
    "hace me",
    "historia",
    "con mi foto",
    "mis datos",
    "consultame",
    "alquiler",
    "venta",
    "flyer",
    "folleto",
)
ENGLISH_REQUEST_HINTS = (
    "make me",
    "create a",
    "story",
    "with my photo",
    "my details",
    "inquire",
    "for sale",
    "for rent",
)
BRAND_ALLOWLIST = ("jrh", "one", "remax", "whatsapp", "instagram")


def copy_pack(language="es"):
    language = normalize_language(language)
    return dict(MARKETING_COPY.get(language) or MARKETING_COPY[DEFAULT_LANGUAGE])


def marketing_label(key, language="es"):
    pack = copy_pack(language)
    return pack.get(key) or MARKETING_COPY[DEFAULT_LANGUAGE].get(key) or ""


def detect_request_language(text):
    folded = " ".join(str(text or "").lower().split())
    if not folded:
        return None
    spanish = sum(1 for hint in SPANISH_REQUEST_HINTS if hint in folded)
    english = sum(1 for hint in ENGLISH_REQUEST_HINTS if hint in folded)
    if spanish > english:
        return "es"
    if english > spanish:
        return "en"
    return None


def resolve_creative_language(*, locale=None, request_text="", organization_language=None):
    """1) app locale  2) request language  3) organization default."""
    if locale not in (None, ""):
        return normalize_language(locale)
    detected = detect_request_language(request_text)
    if detected:
        return detected
    return normalize_language(organization_language or DEFAULT_LANGUAGE)


def operation_headline(language="es", facts=None, style=None):
    """Commercial title: DEPARTAMENTO EN VENTA. Never the locality."""
    pack = copy_pack(language)
    facts = facts or {}
    type_label = " ".join(str(facts.get("type_label") or "").split())
    purpose = str(facts.get("purpose") or "").lower()
    rental = purpose in {"rental", "temporary_rental"}
    suffix = pack["for_rent"] if rental else pack["for_sale"]
    if type_label:
        return f"{type_label} {suffix}".upper()
    return suffix.upper()


def default_headline(language="es", facts=None, style=None):
    return operation_headline(language, facts, style)


def default_kicker(language="es"):
    return copy_pack(language)["kicker"]


def marketing_zone_line(facts):
    facts = facts or {}
    locality = " ".join(str(facts.get("locality") or "").split())
    jurisdiction = " ".join(str(facts.get("jurisdiction") or "").split())
    short = JURISDICTION_SHORT.get(jurisdiction.casefold()) if jurisdiction else None
    if locality and short:
        return f"{locality}, {short}"
    if locality and jurisdiction and locality.casefold() != jurisdiction.casefold():
        return f"{locality}, {jurisdiction}"
    return locality or (short or jurisdiction) or " ".join(
        str(facts.get("location_line") or "").split()
    )


def is_location_headline(text, facts=None):
    folded = " ".join(str(text or "").lower().split())
    if not folded:
        return False
    if folded.startswith("en ") or folded.startswith("in "):
        rest = folded.split(" ", 1)[1]
        if rest in {"venta", "alquiler", "sale", "rent"}:
            return False
        return True
    locality = " ".join(str((facts or {}).get("locality") or "").lower().split())
    return bool(locality and locality in folded and len(folded.split()) <= 3)


def default_cta(language="es"):
    return copy_pack(language)["cta"]


def default_agent_role(language="es"):
    return copy_pack(language)["agent_role"]


def style_headline(language, direction):
    pack = copy_pack(language)
    mapping = {
        "light_premium": pack["headline_editorial"],
        "blue_premium": pack["headline_commercial"],
        "editorial_premium": pack["headline_editorial"],
        "editorial_navy": pack["headline_editorial"],
        "modern_commercial": pack["headline_commercial"],
        "white_architectural": pack["headline_commercial"],
        "luxury_minimal": pack["headline_luxury"],
        "photo_led_luxury": pack["headline_luxury"],
    }
    return mapping.get(direction) or pack["default_headline_sale"]


def _word_hits(text, words):
    folded = " ".join(str(text or "").lower().split())
    if not folded:
        return []
    hits = []
    for word in words:
        if word in BRAND_ALLOWLIST:
            continue
        if re.search(rf"\b{re.escape(word)}\b", folded):
            hits.append(word)
    return hits


def forbidden_language_hits(text, language="es"):
    language = normalize_language(language)
    if language == "es":
        return _word_hits(text, ENGLISH_CREATIVE_WORDS)
    return _word_hits(text, SPANISH_CREATIVE_WORDS)


def extract_image_text(png_bytes):
    if not png_bytes:
        return ""
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return ""
    try:
        image = Image.open(io.BytesIO(png_bytes))
        return " ".join(str(pytesseract.image_to_string(image) or "").split())
    except Exception:
        logger.info("marketing language ocr skipped")
        return ""


def validate_creative_language(
    png_bytes=None,
    *,
    language="es",
    planned_copy=None,
    extra_text="",
):
    language = normalize_language(language)
    parts = [extra_text or ""]
    if isinstance(planned_copy, dict):
        parts.extend(
            str(planned_copy.get(key) or "")
            for key in ("headline", "cta", "street", "zone", "agent_title", "agent_name")
        )
    elif planned_copy:
        parts.append(str(planned_copy))
    parts.append(extract_image_text(png_bytes))
    combined = " ".join(part for part in parts if part)
    hits = forbidden_language_hits(combined, language)
    return {
        "ok": not hits,
        "hits": hits,
        "language": language,
        "status": "completed" if not hits else "wrong_language",
    }


def language_prompt_block(language="es"):
    language = normalize_language(language)
    if language == "es":
        return (
            "LANGUAGE LOCK: All visible text in the final creative must be written in Spanish only. "
            "Do not use English words, English taglines, or English CTA buttons. "
            "Headline, subheadline, labels, price captions and CTA must all be in Spanish. "
            "Use a premium Rioplatense real-estate tone, neutral and professional. "
            "Never write Discover, Inquire Now, Luxury, Available, or Investment on the canvas."
        )
    return (
        "LANGUAGE LOCK: All visible text in the final creative must be written in English only. "
        "Do not mix Spanish words, Spanish taglines, or Spanish CTA buttons. "
        "Headline, subheadline, labels and CTA must all be in English."
    )
