"""Isolated Marketing IA text layer for quality evaluation.

Production composer (/marketing/generate) is unchanged. This module
only generates structured copy for /marketing/eval.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

from modules.jrh_ai_provider import get_jrh_ai_model, get_jrh_ai_provider_name
from modules.marketing_context import ai_prompt_facts

logger = logging.getLogger(__name__)

PROMPT_VERSION = "creative-text-v1"

FORMATS = ("post", "story", "carousel", "whatsapp", "copy")
STYLES = ("premium", "modern", "minimal", "dynamic")
TONES = ("formal", "close", "commercial", "aspirational", "exclusive")
ORIGINS = ("listing", "free", "personal_brand")

VARIANT_PRESETS = (
    {"label": "Directa", "style": "modern", "tone": "close"},
    {"label": "Aspiracional", "style": "dynamic", "tone": "aspirational"},
    {"label": "Premium", "style": "premium", "tone": "exclusive"},
)

STYLE_BRIEFS = {
    "premium": "Sobrio, elegante, sin hype. Casi sin emojis. Ritmo pausado.",
    "modern": "Directo y limpio. Frases cortas. Cero adornos innecesarios.",
    "minimal": "Muy corto. Solo lo imprescindible. Sin relleno.",
    "dynamic": "Hook fuerte, ritmo y energía. Puede usar 1 emoji si suma.",
}

TONE_BRIEFS = {
    "formal": "Institucional, preciso, respetuoso.",
    "close": "Cercano, conversacional, como un asesor que conoce al cliente.",
    "commercial": "Orientado a acción y beneficio, sin gritar oferta.",
    "aspirational": "Estilo de vida y deseo, sin inventar lujo.",
    "exclusive": "Selecto y contenido. Menos volumen, más criterio.",
}

FORMAT_BRIEFS = {
    "post": (
        "Caption social real de feed. Headline útil y CTA claro. "
        "Hashtags solo de zona/tipo, máximo 6."
    ),
    "story": (
        "3 a 5 frames con progresión (gancho → dato → cierre). "
        "Texto corto por frame. CTA solo en el último."
    ),
    "carousel": (
        "Hook inicial, desarrollo en slides distintos y cierre. "
        "Cada slide aporta un dato nuevo. No repetir el post."
    ),
    "whatsapp": (
        "Mensaje natural y conversacional. Sin hashtags. "
        "No debe parecer caption de Instagram."
    ),
    "copy": (
        "Pieza genérica reutilizable (web, ficha, brochure). "
        "No duplicar el caption del post."
    ),
}

FORMAT_SCHEMAS = {
    "post": {
        "headline": "string",
        "caption": "string",
        "cta": "string",
        "hashtags": ["string"],
    },
    "story": {
        "frames": [{"text": "string"}],
        "cta": "string",
    },
    "carousel": {
        "hook": "string",
        "slides": [{"title": "string", "body": "string"}],
        "close": "string",
        "cta": "string",
    },
    "whatsapp": {
        "message": "string",
        "cta": "string",
    },
    "copy": {
        "headline": "string",
        "body": "string",
        "cta": "string",
    },
}


def normalize_format(value):
    key = str(value or "").strip().lower()
    return key if key in FORMATS else "post"


def normalize_style(value):
    key = str(value or "").strip().lower()
    return key if key in STYLES else "modern"


def normalize_tone(value):
    key = str(value or "").strip().lower()
    return key if key in TONES else "close"


def normalize_origin(value):
    key = str(value or "").strip().lower()
    return key if key in ORIGINS else "listing"


def build_creative_brief(*, fmt, style, tone, origin="listing", notes=""):
    fmt = normalize_format(fmt)
    style = normalize_style(style)
    tone = normalize_tone(tone)
    origin = normalize_origin(origin)
    parts = [
        FORMAT_BRIEFS[fmt],
        f"Style {style}: {STYLE_BRIEFS[style]}",
        f"Tone {tone}: {TONE_BRIEFS[tone]}",
    ]
    if origin == "personal_brand":
        parts.append("Origen marca personal: voz del agente, no de una oficina genérica.")
    elif origin == "free":
        parts.append("Origen libre: usar notas del evaluador; no inventar ficha.")
    if notes:
        parts.append(f"Notas: {notes.strip()[:240]}")
    return " ".join(parts)


def eval_prompt_facts(context, *, notes="", origin="listing"):
    facts = dict(ai_prompt_facts(context) or {})
    raw = (context or {}).get("facts") or {}
    extras = {
        "rooms": raw.get("rooms"),
        "bedrooms": raw.get("bedrooms"),
        "bathrooms": raw.get("bathrooms"),
        "covered_m2": raw.get("covered_m2"),
        "price_label": raw.get("price_label"),
        "location_line": raw.get("location_line"),
    }
    facts.update({key: value for key, value in extras.items() if value not in (None, "")})
    if notes:
        facts["evaluator_notes"] = str(notes).strip()[:400]
    facts["origin"] = normalize_origin(origin)
    return facts


def _system_instructions(fmt, style, tone):
    schema = json.dumps(FORMAT_SCHEMAS[fmt], ensure_ascii=False)
    return (
        "Sos un copywriter inmobiliario argentino para evaluación interna de JRH One. "
        "Usá SOLO los hechos provistos. Nunca inventes amenities, vistas, precios, "
        "ambientes ni metros. Sin hype vacío. Español rioplatense.\n"
        f"Formato: {fmt}. {FORMAT_BRIEFS[fmt]}\n"
        f"Style: {style}. {STYLE_BRIEFS[style]}\n"
        f"Tone: {tone}. {TONE_BRIEFS[tone]}\n"
        f"Devolvé JSON exacto con esta forma: {schema}. "
        "Agregá también 'brief' (1 oración que explique la decisión creativa)."
    )


def _uses_live_openai():
    provider = get_jrh_ai_provider_name()
    if provider in {"mock", "test", "rules"}:
        return False
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _clip(text, limit):
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    clipped = value[: limit - 1].rsplit(" ", 1)[0].strip()
    return clipped or value[:limit]


def _place(facts):
    return (
        facts.get("title")
        or facts.get("locality")
        or facts.get("location_line")
        or "esta propiedad"
    )


def _type_line(facts):
    return facts.get("type_label") or "propiedad"


def _mock_output(fmt, style, tone, facts):
    place = _place(facts)
    kind = _type_line(facts)
    chips = " · ".join(facts.get("chips") or [])
    notes = facts.get("evaluator_notes") or ""
    purpose = facts.get("purpose_label") or ""
    if style == "minimal":
        hook = place
        body = chips or purpose or kind
        cta = "Escribime"
    elif style == "premium":
        hook = f"{kind} en {place}"
        body = f"{purpose} {chips}".strip() or f"{kind} disponible en {place}."
        cta = "Coordinemos una visita"
    elif style == "dynamic":
        hook = f"Pará: {place}"
        body = f"{kind} {purpose} {chips}".strip()
        cta = "Lo vemos hoy?"
    else:
        hook = f"{kind} en {place}"
        body = chips or f"{kind} {purpose}".strip()
        cta = "Pedí info"

    if tone == "formal":
        cta = "Solicitá más información"
    elif tone == "close":
        cta = "Si te copa, te cuento más"
    elif tone == "commercial":
        cta = "Reservá tu visita"
    elif tone == "aspirational":
        cta = "Imaginá vivir acá"
    elif tone == "exclusive":
        cta = "Consultá disponibilidad"

    extra = f" {notes}" if notes else ""
    if fmt == "post":
        caption = f"{hook}. {body}.{extra}".strip()
        if style == "minimal":
            caption = f"{hook}. {cta}."
        return {
            "headline": _clip(hook, 56),
            "caption": _clip(caption, 420),
            "cta": _clip(cta, 36),
            "hashtags": ["#" + "".join((facts.get("locality") or "Inmuebles").split()[:2])],
        }
    if fmt == "story":
        count = 3 if style == "minimal" else (5 if style == "dynamic" else 4)
        frames = [
            {"text": _clip(hook, 48)},
            {"text": _clip(body or kind, 48)},
        ]
        if count >= 4:
            frames.append({"text": _clip(chips or purpose or place, 48)})
        if count >= 5:
            frames.append({"text": _clip(f"{kind} en {place}", 48)})
        frames.append({"text": _clip(cta, 48)})
        return {"frames": frames[:count], "cta": _clip(cta, 36)}
    if fmt == "carousel":
        slides = [
            {"title": "Ubicación", "body": _clip(place, 80)},
            {"title": "La propiedad", "body": _clip(body or kind, 80)},
        ]
        if style != "minimal":
            slides.append({"title": "Detalles", "body": _clip(chips or purpose or kind, 80)})
        return {
            "hook": _clip(hook, 72),
            "slides": slides,
            "close": _clip(f"{kind} en {place}.", 80),
            "cta": _clip(cta, 36),
        }
    if fmt == "whatsapp":
        message = (
            f"Hola, te paso esta {kind} en {place}."
            if style != "dynamic"
            else f"Hola! Vi esto y pensé en vos: {kind} en {place}."
        )
        if chips:
            message += f" {chips}."
        if extra.strip():
            message += extra
        return {"message": _clip(message, 360), "cta": _clip(cta, 36)}
    copy_body = (
        f"{kind} en {place}. {body}.".strip()
        if style != "minimal"
        else f"{kind} en {place}."
    )
    return {
        "headline": _clip(hook, 72),
        "body": _clip(copy_body, 420),
        "cta": _clip(cta, 36),
    }


def _sanitize_output(fmt, parsed, facts, style, tone):
    fallback = _mock_output(fmt, style, tone, facts)
    if not isinstance(parsed, dict):
        return fallback
    if fmt == "post":
        tags = parsed.get("hashtags") if isinstance(parsed.get("hashtags"), list) else []
        clean_tags = []
        for tag in tags:
            token = str(tag or "").strip()
            if not token:
                continue
            if not token.startswith("#"):
                token = "#" + "".join(token.split())
            clean_tags.append(token)
            if len(clean_tags) >= 6:
                break
        return {
            "headline": _clip(parsed.get("headline") or fallback["headline"], 72),
            "caption": _clip(parsed.get("caption") or fallback["caption"], 480),
            "cta": _clip(parsed.get("cta") or fallback["cta"], 48),
            "hashtags": clean_tags or fallback["hashtags"],
        }
    if fmt == "story":
        frames = parsed.get("frames") if isinstance(parsed.get("frames"), list) else []
        clean = []
        for item in frames:
            text = item.get("text") if isinstance(item, dict) else item
            text = _clip(text, 80)
            if text:
                clean.append({"text": text})
            if len(clean) >= 5:
                break
        if len(clean) < 3:
            clean = fallback["frames"]
        return {"frames": clean, "cta": _clip(parsed.get("cta") or fallback["cta"], 48)}
    if fmt == "carousel":
        slides = parsed.get("slides") if isinstance(parsed.get("slides"), list) else []
        clean = []
        for item in slides:
            if not isinstance(item, dict):
                continue
            title = _clip(item.get("title"), 48)
            body = _clip(item.get("body"), 140)
            if title or body:
                clean.append({"title": title, "body": body})
            if len(clean) >= 6:
                break
        if len(clean) < 2:
            clean = fallback["slides"]
        return {
            "hook": _clip(parsed.get("hook") or fallback["hook"], 90),
            "slides": clean,
            "close": _clip(parsed.get("close") or fallback["close"], 90),
            "cta": _clip(parsed.get("cta") or fallback["cta"], 48),
        }
    if fmt == "whatsapp":
        message = _clip(parsed.get("message") or fallback["message"], 480)
        message = " ".join(part for part in message.split() if not part.startswith("#"))
        return {
            "message": message,
            "cta": _clip(parsed.get("cta") or fallback["cta"], 48),
        }
    return {
        "headline": _clip(parsed.get("headline") or fallback["headline"], 80),
        "body": _clip(parsed.get("body") or fallback["body"], 520),
        "cta": _clip(parsed.get("cta") or fallback["cta"], 48),
    }


def render_creative_text(fmt, output):
    fmt = normalize_format(fmt)
    output = output or {}
    if fmt == "post":
        tags = " ".join(output.get("hashtags") or [])
        return "\n".join(
            part
            for part in (
                output.get("headline"),
                output.get("caption"),
                output.get("cta"),
                tags,
            )
            if part
        )
    if fmt == "story":
        frames = output.get("frames") or []
        lines = [f"{index + 1}. {item.get('text')}" for index, item in enumerate(frames)]
        if output.get("cta"):
            lines.append(f"CTA: {output['cta']}")
        return "\n".join(lines)
    if fmt == "carousel":
        lines = [f"Hook: {output.get('hook') or ''}"]
        for index, slide in enumerate(output.get("slides") or [], start=1):
            lines.append(f"{index}. {slide.get('title')}: {slide.get('body')}")
        if output.get("close"):
            lines.append(f"Cierre: {output['close']}")
        if output.get("cta"):
            lines.append(f"CTA: {output['cta']}")
        return "\n".join(lines)
    if fmt == "whatsapp":
        return "\n".join(part for part in (output.get("message"), output.get("cta")) if part)
    return "\n".join(
        part
        for part in (output.get("headline"), output.get("body"), output.get("cta"))
        if part
    )


def generate(context, *, fmt="post", style="modern", tone="close", notes="", origin="listing"):
    """Generate one structured creative. Does not persist anything."""
    fmt = normalize_format(fmt)
    style = normalize_style(style)
    tone = normalize_tone(tone)
    origin = normalize_origin(origin)
    notes = str(notes or "").strip()
    facts = eval_prompt_facts(context, notes=notes, origin=origin)
    brief = build_creative_brief(fmt=fmt, style=style, tone=tone, origin=origin, notes=notes)
    started = time.perf_counter()
    model = get_jrh_ai_model()
    provider = get_jrh_ai_provider_name()
    tokens_input = None
    tokens_output = None
    error = None
    parsed = None
    llm_brief = None

    if _uses_live_openai():
        try:
            from modules.cash_ai_provider import request_structured_json

            meta = request_structured_json(
                instructions=_system_instructions(fmt, style, tone),
                user_content=[
                    {
                        "type": "text",
                        "text": (
                            f"Brief: {brief}\n"
                            f"Hechos: {json.dumps(facts, ensure_ascii=False)}"
                        ),
                    }
                ],
                model=model,
                log_prefix="marketing_creative",
                return_meta=True,
            )
            parsed = meta.get("data") if isinstance(meta, dict) else None
            model = (meta or {}).get("model") or model
            tokens_input = (meta or {}).get("tokens_input")
            tokens_output = (meta or {}).get("tokens_output")
            if isinstance(parsed, dict):
                llm_brief = str(parsed.get("brief") or "").strip() or None
        except Exception as exc:
            error = str(exc)[:240]
            if "sk-" in error:
                error = error.replace("sk-", "sk-***")
            logger.info("marketing_creative fallback fmt=%s error=%s", fmt, error)
            parsed = None
    else:
        provider = "mock"

    output = _sanitize_output(fmt, parsed, facts, style, tone)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "format": fmt,
        "style": style,
        "tone": tone,
        "origin": origin,
        "notes": notes,
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "provider": provider,
        "creative_brief": llm_brief or brief,
        "input_summary": facts,
        "output": output,
        "rendered_text": render_creative_text(fmt, output),
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "duration_ms": duration_ms,
        "error": error,
    }


def generate_variants(context, *, fmt="post", notes="", origin="listing"):
    """Generate Directa / Aspiracional / Premium. Not fired by default."""
    group_id = uuid.uuid4().hex
    results = []
    for preset in VARIANT_PRESETS:
        item = generate(
            context,
            fmt=fmt,
            style=preset["style"],
            tone=preset["tone"],
            notes=notes,
            origin=origin,
        )
        item["variant_label"] = preset["label"]
        item["group_id"] = group_id
        results.append(item)
    return {"group_id": group_id, "items": results}
