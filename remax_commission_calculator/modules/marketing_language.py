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
        "cta": "Consultame",
        "cta_sale": "Consultame",
        "cta_rent": "Consultame disponibilidad",
        "cta_short": "Consultame",
        "for_sale": "en venta",
        "for_rent": "en alquiler",
        "kicker_sale": "EN VENTA",
        "kicker_rent": "EN ALQUILER",
        "agent_role": "Agente inmobiliario",
        "available": "Disponible",
        "kicker": "EN VENTA",
        "tagline": "CONFIANZA  ·  EXPERIENCIA  ·  RESULTADOS",
        "cta_instagram": "Seguime en Instagram",
        "headline_editorial": "Tu próximo hogar está acá",
        "headline_commercial": "Disponible ahora",
        "headline_luxury": "Exclusiva",
        "whisper": "Hablemos de tu próximo hogar",
        "location_blurb": (
            "Un entorno residencial, tranquilo y con excelente conectividad. "
            "Cerca de colegios, comercios y de todo lo que necesitás."
        ),
        "trust_advice": "Asesoramiento personalizado",
        "trust_support": "Acompañamiento en todo el proceso",
        "trust_project": "Tu proyecto en manos expertas",
        "closing_line": "Tu próximo capítulo te espera",
    },
    "en": {
        "default_headline_sale": "For Sale",
        "default_headline_investment": "Investment Opportunity",
        "cta": "Message me",
        "cta_sale": "Message me",
        "cta_rent": "Ask availability",
        "cta_short": "Message me",
        "for_sale": "for sale",
        "for_rent": "for rent",
        "kicker_sale": "FOR SALE",
        "kicker_rent": "FOR RENT",
        "agent_role": "Real Estate Agent",
        "available": "Available",
        "kicker": "FOR SALE",
        "tagline": "TRUST  ·  EXPERIENCE  ·  RESULTS",
        "cta_instagram": "Follow on Instagram",
        "headline_editorial": "Your next home is here",
        "headline_commercial": "Available now",
        "headline_luxury": "Exclusive",
        "whisper": "Let's talk about your next home",
        "location_blurb": (
            "A quiet residential setting with excellent connectivity. "
            "Close to schools, shops and everything you need."
        ),
        "trust_advice": "Personalized advice",
        "trust_support": "Support throughout the process",
        "trust_project": "Your project in expert hands",
        "closing_line": "Your next chapter awaits",
    },
}

TYPE_ALIASES = {
    "departamento": "apartment",
    "apartment": "apartment",
    "casa": "house",
    "house": "house",
    "ph": "ph",
    "terreno": "land",
    "land": "land",
    "local": "commercial",
    "commercial": "commercial",
    "oficina": "office",
    "office": "office",
}

HYPE_COPY_RE = re.compile(
    r"oportunidad [uú]nica|no te lo pod[eé]s perder|hogar de tus sue[nñ]os|"
    r"inversi[oó]n (imperdible|[uú]nica)|lujo extremo|imperdible|"
    r"la mejor (propiedad|casa|depto)|precio irrepetible",
    re.I,
)

COPY_STYLE_ALIASES = {
    "premium": "premium",
    "light": "premium",
    "light_premium": "premium",
    "elegant": "premium",
    "elegante": "premium",
    "editorial": "premium",
    "editorial_premium": "premium",
    "luxury": "premium",
    "minimal": "premium",
    "modern": "modern",
    "moderno": "modern",
    "blue": "modern",
    "blue_premium": "modern",
    "commercial": "commercial",
    "moderno_comercial": "commercial",
    "modern_commercial": "commercial",
    "price_led": "commercial",
    "aspirational": "aspirational",
    "aspiracional": "aspirational",
    "dynamic": "aspirational",
    "exclusive": "aspirational",
}

SALE_CTA = {
    "es": {
        "premium": "Coordinemos una visita.",
        "modern": "Escribime para más información.",
        "commercial": "Consultame para visitarla.",
        "aspirational": "No dudes en contactarme.",
    },
    "en": {
        "premium": "Let's book a visit.",
        "modern": "Message me for details.",
        "commercial": "Message me to visit.",
        "aspirational": "Happy to help you visit.",
    },
}

RENT_CTA = {
    "es": {
        "premium": "Escribime para coordinar una visita.",
        "modern": "Consultame disponibilidad.",
        "commercial": "Consultame disponibilidad.",
        "aspirational": "Pedime más información por WhatsApp.",
    },
    "en": {
        "premium": "Message me to visit.",
        "modern": "Ask availability.",
        "commercial": "Ask availability.",
        "aspirational": "Message me on WhatsApp.",
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


def _is_rental(facts):
    return str((facts or {}).get("purpose") or "").lower() in {"rental", "temporary_rental"}


def _rooms_count(facts):
    raw = (facts or {}).get("rooms")
    if raw in (None, ""):
        return None
    try:
        number = int(float(raw))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _area_value(facts):
    facts = facts or {}
    raw = facts.get("covered_m2")
    if raw in (None, ""):
        raw = facts.get("total_m2")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def listing_type_key(facts):
    facts = facts or {}
    raw = str(facts.get("property_type") or "").strip().lower()
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    label = str(facts.get("type_label") or "").strip().casefold()
    return TYPE_ALIASES.get(label)


def normalize_copy_style(style=None):
    key = str(style or "").strip().lower()
    return COPY_STYLE_ALIASES.get(key, "premium")


def _headline_zone(facts):
    return " ".join(str((facts or {}).get("locality") or "").split())


def _headline_subject(language, facts):
    kind = listing_type_key(facts)
    rooms = _rooms_count(facts)
    type_label = " ".join(str((facts or {}).get("type_label") or "").split())
    if language == "es":
        if kind == "house":
            return "Casa"
        if kind == "ph":
            return "PH"
        if kind == "land":
            return "Terreno"
        if kind == "commercial":
            return "Local"
        if kind == "office":
            return "Oficina"
        if rooms == 1:
            return "Monoambiente"
        if rooms:
            return f"{rooms} ambientes"
        return type_label or ("Departamento" if kind == "apartment" else "")
    if kind == "house":
        return "House"
    if kind == "ph":
        return "PH"
    if kind == "land":
        return "Land"
    if kind == "commercial":
        return "Storefront"
    if kind == "office":
        return "Office"
    if rooms == 1:
        return "Studio"
    if rooms:
        return f"{rooms}-room apartment"
    return type_label or ("Apartment" if kind == "apartment" else "")


def operation_headline(language="es", facts=None, style=None):
    """Commercial title: 2 AMBIENTES EN VENTA EN VICTORIA."""
    del style
    pack = copy_pack(language)
    facts = facts or {}
    rental = _is_rental(facts)
    suffix = pack["for_rent"] if rental else pack["for_sale"]
    subject = _headline_subject(language, facts)
    zone = _headline_zone(facts)
    if subject and zone:
        connector = "en" if language == "es" else "in"
        return f"{subject} {suffix} {connector} {zone}".upper()
    if subject:
        return f"{subject} {suffix}".upper()
    return (pack["kicker_rent"] if rental else pack["kicker_sale"]).upper()


def default_headline(language="es", facts=None, style=None):
    return sellable_headline(language, facts, style)


def _amenity_blob(facts):
    values = [str(item).casefold() for item in (facts or {}).get("amenities") or []]
    if (facts or {}).get("patio"):
        values.append("patio")
    return " ".join(values)


def _has_garden(facts):
    blob = _amenity_blob(facts)
    return any(token in blob for token in ("jard", "garden", "patio", "parque"))


def sellable_headline(language="es", facts=None, style=None):
    """Short commercial headline from listing facts. Title case, not all-caps."""
    facts = facts or {}
    kind = listing_type_key(facts)
    rooms = _rooms_count(facts)
    zone = _headline_zone(facts)
    subject = _headline_subject(language, facts)
    garden = _has_garden(facts)
    if language == "es":
        if kind == "house" and garden:
            title = "Casa con jardín"
        elif kind == "house":
            title = "Casa moderna"
        elif kind == "ph":
            title = "PH con diseño"
        elif rooms and rooms >= 4:
            title = f"{rooms} ambientes"
        elif rooms == 1:
            title = "Monoambiente"
        elif subject:
            title = subject
        else:
            title = "Tu próximo hogar"
        if zone:
            return f"{title} en {zone}"
        return title
    if kind == "house" and garden:
        title = "House with garden"
    elif kind == "house":
        title = "Modern house"
    elif rooms and rooms >= 4:
        title = f"{rooms}-room home"
    elif subject:
        title = subject
    else:
        title = "Your next home"
    if zone:
        return f"{title} in {zone}"
    return title


def sellable_headline_lines(language="es", facts=None, style=None):
    headline = sellable_headline(language, facts, style)
    connector = " en " if language == "es" else " in "
    if connector in headline:
        left, right = headline.rsplit(connector, 1)
        if left and right:
            return [left, f"{connector.strip()} {right}"]
    words = headline.split()
    if len(words) <= 3:
        return [headline]
    return [" ".join(words[:-2]), " ".join(words[-2:])]


def is_placeholder_copy(text):
    folded = " ".join(str(text or "").casefold().split())
    if not folded:
        return True
    if "copy de prueba" in folded or folded in {"prueba", "test copy", "lorem ipsum"}:
        return True
    if folded in {"disponible ahora", "available now", "en venta", "for sale"}:
        return True
    return False


def hero_stack_lines(language="es", facts=None):
    """Hero overlay lines: sellable title case, two rows max."""
    return sellable_headline_lines(language, facts)[:3]


def operation_kicker(language="es", facts=None):
    pack = copy_pack(language)
    return pack["kicker_rent"] if _is_rental(facts) else pack["kicker_sale"]


def default_kicker(language="es", facts=None):
    if facts:
        return operation_kicker(language, facts)
    return copy_pack(language)["kicker"]


def commercial_cta(language="es", facts=None, style=None):
    language = normalize_language(language)
    flavor = normalize_copy_style(style)
    pool = RENT_CTA if _is_rental(facts) else SALE_CTA
    pack = pool.get(language) or pool["es"]
    return pack.get(flavor) or pack["premium"]


def listing_benefit_line(language="es", facts=None, style=None):
    """One grounded bajada with commercial intent."""
    facts = facts or {}
    rental = _is_rental(facts)
    rooms = _rooms_count(facts)
    garden = _has_garden(facts)
    zone = _headline_zone(facts)
    if language == "es":
        if garden and rooms:
            line = "Espacio, jardín y una distribución pensada para vivir."
        elif garden:
            line = "Ideal para disfrutar al aire libre, en familia."
        elif rooms and rooms >= 4:
            line = "Ambientes amplios, luz y comodidad para el día a día."
        elif rental:
            line = "Lista para tu próxima etapa. Consultá disponibilidad."
        elif zone:
            line = f"Diseño, confort y una ubicación privilegiada en {zone}."
        else:
            line = "Espacios amplios, diseño y confort en una ubicación privilegiada."
    else:
        if garden:
            line = "Outdoor space and a layout made for everyday living."
        elif rooms and rooms >= 4:
            line = "Generous rooms, light and comfort day to day."
        elif rental:
            line = "Ready for your next chapter. Ask availability."
        elif zone:
            line = f"Design, comfort and a strong location in {zone}."
        else:
            line = "Space, light and comfort in a privileged location."
    line = " ".join(str(line).split())
    if HYPE_COPY_RE.search(line):
        return ""
    return line


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
    commercial = (
        "venta" in folded
        or "alquiler" in folded
        or "sale" in folded
        or "rent" in folded
    )
    typed = any(
        token in folded
        for token in (
            "ambiente",
            "monoambiente",
            "departamento",
            "casa",
            "ph",
            "oficina",
            "local",
            "terreno",
            "apartment",
            "studio",
            "house",
            "office",
        )
    )
    if commercial and typed:
        return False
    if folded.startswith("en ") or folded.startswith("in "):
        rest = folded.split(" ", 1)[1]
        if rest in {"venta", "alquiler", "sale", "rent"}:
            return False
        return True
    locality = " ".join(str((facts or {}).get("locality") or "").lower().split())
    return bool(locality and locality in folded and len(folded.split()) <= 3)


def default_cta(language="es", facts=None, style=None):
    return commercial_cta(language, facts, style=style)


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
