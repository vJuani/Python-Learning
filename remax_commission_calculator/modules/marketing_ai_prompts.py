"""Per-format creative prompts and strict JSON schemas for Marketing IA."""

from __future__ import annotations


PROMPT_VERSIONS = {
    "brief": "marketing_brief_v2",
    "post": "marketing_post_v2",
    "story": "marketing_story_v2",
    "carousel": "marketing_carousel_v2",
    "copy": "marketing_copy_v2",
    "whatsapp": "marketing_whatsapp_v2",
}

MAX_TOKENS = {
    "brief": 450,
    "post": 700,
    "story": 650,
    "carousel": 900,
    "copy": 520,
    "whatsapp": 320,
}

VARIANT_ANGLES = ("direct", "aspirational", "premium")

FEATURE_LABELS = {
    "es": {
        "balcony": "balcón",
        "terrace": "terraza",
        "garden": "jardín",
        "pool": "pileta",
        "grill": "parrilla",
        "laundry": "lavadero",
        "storage": "baulera",
        "elevator": "ascensor",
        "security": "seguridad",
        "furnished": "amoblado",
    },
    "en": {
        "balcony": "balcony",
        "terrace": "terrace",
        "garden": "garden",
        "pool": "pool",
        "grill": "grill",
        "laundry": "laundry",
        "storage": "storage",
        "elevator": "elevator",
        "security": "security",
        "furnished": "furnished",
    },
}

STYLE_DIRECTIONS = {
    "premium": (
        "STYLE premium: sobrio, elegante, pocas o ninguna emoji. "
        "Priorizá atributos reales y la experiencia de vivir ahí. Frases contenidas."
    ),
    "modern": (
        "STYLE modern: directo, limpio, frases cortas, ritmo ágil. "
        "Sin adornos innecesarios. Una idea por oración."
    ),
    "minimal": (
        "STYLE minimal: muy breve. Pocas palabras. Dejá que el dato hable. "
        "Sin relleno. Máxima densidad, mínimo texto."
    ),
    "elegant": (
        "STYLE elegant: sofisticado y sereno. Ritmo pausado. "
        "Elegancia concreta, no lujo inventado."
    ),
    "dynamic": (
        "STYLE dynamic: hooks fuertes, frases breves, ritmo alto. "
        "Energía comercial sin gritar ni usar clichés."
    ),
    "corporate": (
        "STYLE corporate: profesional, informativo, tono institucional. "
        "Claridad de oficina. Sin jerga de influencer."
    ),
}

TONE_DIRECTIONS = {
    "formal": "TONE formal: trato respetuoso, usted si el idioma es ES, precisión.",
    "close": "TONE close: cercano, conversado, de agente a persona. Natural, no infantil.",
    "commercial": "TONE commercial: vendedor claro. Beneficio + siguiente paso. Sin presión falsa.",
    "aspirational": "TONE aspirational: proyectá estilo de vida con hechos reales. Nada de 'hogar de tus sueños'.",
    "exclusive": "TONE exclusive: selecto y contenido. Menos volumen, más criterio.",
    "professional": "TONE formal: trato profesional y preciso.",
}

HYPE_BANNED = (
    "oportunidad única",
    "el hogar de tus sueños",
    "no te lo podés perder",
    "inversión imperdible",
    "la mejor propiedad de la zona",
    "precio irrepetible",
    "increíble oportunidad",
)


def _object(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties.keys()),
    }


BRIEF_SCHEMA = _object(
    {
        "audience": {"type": "string"},
        "primary_goal": {"type": "string"},
        "creative_angle": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "main_hook": {"type": "string"},
        "supporting_points": {"type": "array", "items": {"type": "string"}},
        "cta_strategy": {"type": "string"},
        "avoid": {"type": "array", "items": {"type": "string"}},
    }
)

POST_SCHEMA = _object(
    {
        "headline": {"type": "string"},
        "caption": {"type": "string"},
        "cta": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    }
)

STORY_FRAME_SCHEMA = _object(
    {
        "headline": {"type": "string"},
        "body": {"type": "string"},
        "cta": {"type": "string"},
    }
)

STORY_SCHEMA = _object(
    {
        "frames": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": STORY_FRAME_SCHEMA,
        }
    }
)

CAROUSEL_SLIDE_SCHEMA = _object(
    {
        "slide": {"type": "integer"},
        "title": {"type": "string"},
        "body": {"type": "string"},
    }
)

CAROUSEL_SCHEMA = _object(
    {
        "hook": {"type": "string"},
        "slides": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": CAROUSEL_SLIDE_SCHEMA,
        },
        "final_cta": {"type": "string"},
    }
)

WHATSAPP_SCHEMA = _object(
    {
        "message": {"type": "string"},
        "cta": {"type": "string"},
    }
)

COPY_SCHEMA = _object(
    {
        "headline": {"type": "string"},
        "body": {"type": "string"},
        "cta": {"type": "string"},
    }
)

FORMAT_SCHEMAS = {
    "post": POST_SCHEMA,
    "story": STORY_SCHEMA,
    "carousel": CAROUSEL_SCHEMA,
    "whatsapp": WHATSAPP_SCHEMA,
    "copy": COPY_SCHEMA,
}


def _language_line(language):
    if str(language or "es").lower().startswith("en"):
        return "Write in natural English for Argentine real-estate marketing."
    return "Escribí en español rioplatense, natural, de inmobiliaria argentina. No uses spanglish."


def _identity(channel):
    return (
        "Sos el motor creativo de JRH One, especializado en marketing inmobiliario. "
        "Transformás información real de propiedades, agentes y marcas en contenido "
        "comercial atractivo, claro y creíble. Escribís como un profesional del marketing "
        f"inmobiliario en Argentina, no como un chatbot. Canal: {channel}. "
        "Usá SOLO hechos del payload y del brief. Si un dato no está, no lo menciones. "
        "Evitá clichés de IA. Prohibido: "
        + "; ".join(HYPE_BANNED)
        + ". Priorizá claridad, ritmo, un beneficio concreto y un CTA accionable."
    )


def build_brief_instructions(language="es"):
    return (
        f"{_identity('creative brief')}\n"
        f"{_language_line(language)}\n"
        "Esta etapa NO escribe el copy final. Armá un brief interno.\n"
        "audience: para quién es el contenido, inferido de hechos reales.\n"
        "primary_goal: el objetivo pedido, en una frase.\n"
        "creative_angle: un ángulo vendible basado en hechos, no un slogan vacío.\n"
        "key_facts: 3 a 6 hechos reales del payload. Nada inventado. Nada de comisiones.\n"
        "main_hook: la idea que abre el contenido.\n"
        "supporting_points: 2 a 4 apoyos reales.\n"
        "cta_strategy: qué acción pedir (visita, WhatsApp, consulta).\n"
        "avoid: clichés y datos que NO hay que afirmar porque no están en el payload.\n"
        "No incluyas IDs, comisiones, clientes ni notas privadas."
    )


def _style_tone(style, tone):
    parts = []
    if style and style in STYLE_DIRECTIONS:
        parts.append(STYLE_DIRECTIONS[style])
    if tone and tone in TONE_DIRECTIONS:
        parts.append(TONE_DIRECTIONS[tone])
    return "\n".join(parts)


def build_copy_instructions(content_type, *, style=None, tone=None, language="es"):
    extra = _style_tone(style, tone)
    lang = _language_line(language)
    if content_type == "post":
        body = (
            f"{_identity('Instagram/Facebook post')}\n{lang}\n{extra}\n"
            "Escribí UN post. No escribas historia, WhatsApp ni carrusel.\n"
            "headline: 6 a 12 palabras. Gancho. Distinto del caption.\n"
            "caption: 2 a 6 líneas. Hook, 1-2 hechos, cierre. Sin copiar el headline. Sin hashtags en el caption.\n"
            "cta: una acción corta. hashtags: 3 a 6, zona/tipo/marca reales.\n"
            "Si STYLE es minimal, caption más corto. Si premium, menos emoji."
        )
    elif content_type == "story":
        body = (
            f"{_identity('Instagram/WhatsApp story')}\n{lang}\n{extra}\n"
            "Escribí SOLO una historia vertical de 3 a 4 frames. No es un post ni un WhatsApp.\n"
            "Frame 1 hook. Frames del medio un hecho nuevo. Último frame CTA en cta.\n"
            "En frames previos cta puede ir vacío. No repitas texto entre frames. Frases cortas."
        )
    elif content_type == "carousel":
        body = (
            f"{_identity('carrusel de Instagram')}\n{lang}\n{extra}\n"
            "Escribí SOLO un carrusel. hook de portada. slides 3 a 5 con progresión "
            "gancho → valor/zona → características reales → CTA. final_cta claro. Slides distintos."
        )
    elif content_type == "whatsapp":
        body = (
            f"{_identity('mensaje de WhatsApp')}\n{lang}\n{extra}\n"
            "Escribí SOLO un mensaje de chat. No es caption de Instagram.\n"
            "message: 2 a 5 oraciones. Sin hashtags. Máximo un emoji. cta conversacional."
        )
    else:
        body = (
            f"{_identity('copy inmobiliario')}\n{lang}\n{extra}\n"
            "Escribí SOLO un copy corto. headline, body de 2 a 4 oraciones, cta. "
            "headline distinto de body. No es post ni story ni WhatsApp."
        )
    return "\n".join(line for line in body.splitlines() if line).strip()


def format_schema(content_type):
    return FORMAT_SCHEMAS[content_type]


def format_schema_name(content_type):
    return PROMPT_VERSIONS[content_type]
