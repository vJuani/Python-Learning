"""Natural-language marketing request. Explicit user counts beat chips and defaults."""

from __future__ import annotations

import logging
import os
import re

from modules.jrh_ai_provider import get_jrh_ai_provider_name

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "Haceme una historia premium con mi foto y mis datos, "
    "sin descripción larga."
)

MAX_PER_FORMAT = 12
MAX_BATCH_ITEMS = 12

_WORD_NUMBERS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}
_WORD_ALT = "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
_COUNT_TOKEN = rf"(?:\d+|{_WORD_ALT})"

_FORMAT_SPECS = (
    (
        "story_count",
        rf"({_COUNT_TOKEN})\s*(?:opciones(?:\s+de)?\s+)?(?:historias|stories|estados)",
        rf"({_COUNT_TOKEN})\s*(?:opciones(?:\s+de)?\s+)?(?:historia|story|estado)",
        r"\b(?:historias|stories|estados)\b",
        r"\b(?:historia|story|estado)\b",
    ),
    (
        "post_count",
        rf"({_COUNT_TOKEN})\s*(?:opciones(?:\s+de)?\s+)?(?:posts|publicaciones|feeds)",
        rf"({_COUNT_TOKEN})\s*(?:opciones(?:\s+de)?\s+)?(?:post|publicaci[oó]n|feed)",
        r"\b(?:posts|publicaciones|feeds)\b",
        r"\b(?:post|feed)\b",
    ),
    (
        "flyer_count",
        rf"({_COUNT_TOKEN})\s*(?:opciones(?:\s+de)?\s+)?(?:flyers|folletos|volantes|fichas)",
        rf"({_COUNT_TOKEN})\s*(?:opciones(?:\s+de)?\s+)?(?:flyer|folleto|volante|ficha)",
        r"\b(?:flyers|folletos|volantes|fichas)\b",
        r"\b(?:flyer|folleto|volante|ficha)\b",
    ),
)

_AGENT_OFF = re.compile(
    r"sin agente|sin (?:mis )?datos|ni mis datos|sin m[ií](?!\s+foto)|sin jose|sin josé"
)
_PHOTO_OFF = re.compile(r"sin mi foto|sin foto|sin retrato|sin (?:mi )?cara")
_PHOTO_ON = re.compile(
    r"con mi foto|us[aá] mi foto|usando mi foto|poneme en(?: la)?(?: la)? publicaci|"
    r"con mis datos|subilo con mis|usando mis datos"
)
_DATA_ON = re.compile(r"con mis datos|subilo con mis|usando mis datos")


def _clamp(value, default=0):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, min(MAX_PER_FORMAT, number))


def _count_token(token):
    raw = str(token or "").strip().lower()
    if raw in _WORD_NUMBERS:
        return _WORD_NUMBERS[raw]
    return _clamp(raw)


def empty_request():
    return {
        "story_count": 0,
        "post_count": 0,
        "flyer_count": 0,
        "status_count": 0,
        "with_agent": True,
        "with_agent_photo": True,
        "show_price": True,
        "copy_density": "very_low",
        "visual_direction": "premium varied",
        "variation_strength": "high",
        "prompt": "",
        "explicit_formats": False,
        "agent_presentation": default_agent_presentation(),
    }


def default_agent_presentation():
    return {
        "show_agent": True,
        "show_photo": True,
        "show_name": True,
        "show_phone": True,
        "show_email": False,
        "preferred_position": "auto",
    }


def agent_presentation_config(prompt, parsed=None):
    folded = (prompt or "").lower()
    config = default_agent_presentation()
    if parsed:
        config["show_agent"] = parsed.get("with_agent") is not False
        config["show_photo"] = parsed.get("with_agent_photo") is not False
    if _AGENT_OFF.search(folded):
        config.update(
            {
                "show_agent": False,
                "show_photo": False,
                "show_name": False,
                "show_phone": False,
                "show_email": False,
            }
        )
    if _DATA_ON.search(folded):
        config["show_agent"] = True
        config["show_name"] = True
        config["show_phone"] = True
        config["show_email"] = True
        if not _PHOTO_OFF.search(folded):
            config["show_photo"] = True
    if _PHOTO_ON.search(folded) and not _PHOTO_OFF.search(folded):
        config["show_agent"] = True
        config["show_photo"] = True
    if _PHOTO_OFF.search(folded):
        config["show_photo"] = False
        if _DATA_ON.search(folded):
            config["show_agent"] = True
            config["show_name"] = True
            config["show_phone"] = True
            config["show_email"] = True
    if re.search(r"solo mi (whatsapp|tel[eé]fono|celular)", folded):
        config["show_agent"] = True
        config["show_phone"] = True
        config["show_email"] = False
        config["show_photo"] = False
    if re.search(r"poneme abajo|abajo", folded):
        config["preferred_position"] = "bottom"
    return config


def _parse_format_counts(folded):
    counts = {"story_count": 0, "post_count": 0, "flyer_count": 0}
    explicit = False
    for key, numbered_plural, numbered_singular, bare_plural, bare_singular in _FORMAT_SPECS:
        match = re.search(numbered_plural, folded) or re.search(numbered_singular, folded)
        if match:
            counts[key] = _count_token(match.group(1))
            explicit = True
            continue
        if re.search(bare_plural, folded):
            counts[key] = 3
            explicit = True
            continue
        if re.search(bare_singular, folded):
            counts[key] = 1
            explicit = True
    return counts, explicit


def expand_items(parsed):
    items = []
    for fmt, key in (
        ("story", "story_count"),
        ("post", "post_count"),
        ("flyer", "flyer_count"),
        ("status", "status_count"),
    ):
        for index in range(_clamp(parsed.get(key), 0)):
            items.append({"format": fmt, "index": index + 1})
            if len(items) >= MAX_BATCH_ITEMS:
                parsed["capped"] = True
                return items
    return items


def requested_counts(parsed):
    return {
        "story": _clamp(parsed.get("story_count"), 0),
        "post": _clamp(parsed.get("post_count"), 0),
        "flyer": _clamp(parsed.get("flyer_count"), 0),
    }


def counts_match_request(parsed, items):
    expected = requested_counts(parsed)
    actual = {"story": 0, "post": 0, "flyer": 0}
    for item in items or []:
        fmt = item.get("format")
        if fmt in actual:
            actual[fmt] += 1
    return expected == actual


def _fallback_parse(prompt, *, variation=False):
    text = (prompt or "").strip() or ("" if variation else DEFAULT_PROMPT)
    folded = text.lower()
    parsed = empty_request()
    parsed["prompt"] = text
    parsed["with_agent"] = not _AGENT_OFF.search(folded)
    parsed["with_agent_photo"] = parsed["with_agent"] and not _PHOTO_OFF.search(folded)
    parsed["show_price"] = not re.search(r"sin precio|ocult(a|á) el precio", folded)
    parsed["copy_density"] = "very_low"
    if re.search(r"m[aá]s texto|descripci[oó]n larga", folded) and not re.search(
        r"sin descripci", folded
    ):
        parsed["copy_density"] = "low"
    if re.search(r"m[aá]s (jugado|oscuro|elegante)|regener|minimal", folded):
        parsed["variation_strength"] = "high"
    counts, explicit = _parse_format_counts(folded)
    parsed.update(counts)
    parsed["explicit_formats"] = explicit
    pack = bool(re.search(r"\bpack\b|pack completo|contenido para esta", folded))
    if not variation and not explicit:
        if pack:
            parsed["story_count"] = 3
            parsed["post_count"] = 3
            parsed["flyer_count"] = 3
        elif re.search(r"algo lindo|algo para|contenido", folded):
            parsed["story_count"] = 1
            parsed["post_count"] = 1
            parsed["flyer_count"] = 1
        elif not variation:
            parsed["story_count"] = 1
    parsed["status_count"] = 0
    parsed["agent_presentation"] = agent_presentation_config(text, parsed)
    parsed["with_agent"] = parsed["agent_presentation"]["show_agent"]
    parsed["with_agent_photo"] = parsed["agent_presentation"]["show_photo"]
    return parsed


def parse_marketing_request(prompt, *, language="es", variation=False):
    text = (prompt or "").strip() or ("" if variation else DEFAULT_PROMPT)
    if variation and not (prompt or "").strip():
        return empty_request()
    fallback = _fallback_parse(text, variation=variation)
    if variation:
        counted = any(
            fallback.get(key)
            for key in ("story_count", "post_count", "flyer_count", "status_count")
        )
        if not counted:
            fallback["story_count"] = 0
            fallback["post_count"] = 0
            fallback["flyer_count"] = 0
            fallback["status_count"] = 0
            fallback["explicit_formats"] = False
    if get_jrh_ai_provider_name() in {"mock", "test", "rules"}:
        return fallback
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return fallback
    if fallback.get("explicit_formats"):
        return fallback
    try:
        from modules.cash_ai_provider import request_structured_json

        parsed = request_structured_json(
            instructions=(
                "Parse a Spanish real-estate marketing request into JSON. "
                "If the user names a format or count, return ONLY those counts. "
                "una/un = 1. Never expand a single story into a 3/3/3 pack. "
                "Counts default to 0 if that format was not requested. "
                "Pack/pack completo/contenido para esta = 3 stories, 3 posts, 3 flyers. "
                "Never invent property facts. Return: "
                '{"story_count","post_count","flyer_count","status_count",'
                '"with_agent","show_price","copy_density","visual_direction",'
                '"variation_strength"}.'
            ),
            user_content=[{"type": "text", "text": text}],
            log_prefix="marketing_request",
        )
        if not isinstance(parsed, dict):
            return fallback
        result = empty_request()
        result.update(
            {
                "story_count": _clamp(parsed.get("story_count"), fallback["story_count"]),
                "post_count": _clamp(parsed.get("post_count"), fallback["post_count"]),
                "flyer_count": _clamp(parsed.get("flyer_count"), fallback["flyer_count"]),
                "status_count": _clamp(parsed.get("status_count"), fallback["status_count"]),
                "with_agent": parsed.get("with_agent", fallback["with_agent"]) is not False,
                "with_agent_photo": parsed.get("with_agent_photo", fallback.get("with_agent_photo", True))
                is not False,
                "show_price": parsed.get("show_price", fallback["show_price"]) is not False,
                "copy_density": parsed.get("copy_density") or "very_low",
                "visual_direction": parsed.get("visual_direction") or "premium varied",
                "variation_strength": parsed.get("variation_strength") or "high",
                "prompt": text,
                "explicit_formats": False,
            }
        )
        result["agent_presentation"] = agent_presentation_config(text, result)
        result["with_agent"] = result["agent_presentation"]["show_agent"]
        result["with_agent_photo"] = result["agent_presentation"]["show_photo"]
        if variation and not expand_items(result):
            return result
        if not expand_items(result):
            return fallback
        return result
    except Exception:
        logger.info("marketing_request fallback parse")
        return fallback


def interpret_marketing_request(prompt, *, language="es", variation=False, context=None):
    """Public interpreter used by Creative Composer and JRH IA."""
    parsed = parse_marketing_request(prompt, language=language, variation=variation)
    parsed["formats"] = requested_counts(parsed)
    parsed["freeform_direction"] = parsed.get("prompt") or ""
    parsed["agent_presentation"] = parsed.get("agent_presentation") or agent_presentation_config(
        parsed.get("prompt") or prompt, parsed
    )
    if context and ((context.get("facts") or {}).get("price_policy") or {}).get("private"):
        parsed["show_price"] = False
    return parsed
