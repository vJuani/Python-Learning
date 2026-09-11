"""Natural-language marketing request. LLM first, structured fallback for mock."""

from __future__ import annotations

import logging
import os
import re

from modules.jrh_ai_provider import get_jrh_ai_provider_name

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "Haceme 3 historias, 3 posts y 3 flyers, todos diferentes, "
    "premium, sin descripción larga y usando mi foto."
)

MAX_PER_FORMAT = 12
MAX_BATCH_ITEMS = 12


def _clamp(value, default=0):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, min(MAX_PER_FORMAT, number))


def empty_request():
    return {
        "story_count": 0,
        "post_count": 0,
        "flyer_count": 0,
        "status_count": 0,
        "with_agent": True,
        "with_agent_photo": True,
        "show_price": True,
        "copy_density": "low",
        "visual_direction": "premium varied",
        "variation_strength": "high",
        "prompt": "",
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
    if re.search(r"sin agente|sin (mis )?datos|ni mis datos|sin m[ií](?!\s+foto)", folded):
        config.update({"show_agent": False, "show_photo": False, "show_name": False, "show_phone": False, "show_email": False})
    if re.search(r"con mis datos|subilo con mis|usando mis datos|con mi foto|usando mi foto", folded):
        config["show_agent"] = True
        config["show_name"] = True
        if not re.search(r"sin mi foto|sin foto|sin retrato|sin (mi )?cara", folded):
            config["show_photo"] = True
    if re.search(r"sin mi foto|sin foto|sin retrato|sin (mi )?cara", folded):
        config["show_photo"] = False
    if re.search(r"solo mi (whatsapp|tel[eé]fono|celular)", folded):
        config["show_agent"] = True
        config["show_phone"] = True
        config["show_email"] = False
    if re.search(r"poneme abajo|abajo", folded):
        config["preferred_position"] = "bottom"
    return config


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


def _fallback_parse(prompt, *, variation=False):
    text = (prompt or "").strip() or ("" if variation else DEFAULT_PROMPT)
    folded = text.lower()
    parsed = empty_request()
    parsed["prompt"] = text
    parsed["with_agent"] = not re.search(
        r"sin agente|sin (mis )?datos|ni mis datos|sin m[ií](?!\s+foto)|sin jose|sin josé",
        folded,
    )
    parsed["with_agent_photo"] = parsed["with_agent"] and not re.search(
        r"sin mi foto|sin foto|sin retrato",
        folded,
    )
    parsed["show_price"] = not re.search(r"sin precio|ocult(a|á) el precio", folded)
    if re.search(r"sin (mucho )?texto|sin descripci|poco texto|copy corto", folded):
        parsed["copy_density"] = "low"
    if re.search(r"m[aá]s (jugado|oscuro|elegante)|regener|minimal", folded):
        parsed["variation_strength"] = "high"
    pack = bool(re.search(r"pack|contenido para esta|publicidad", folded))
    stories = re.search(
        r"(\d+)\s*(?:opciones(?:\s+de)?\s+)?(historias?|stories|story|estados?)",
        folded,
    )
    posts = re.search(
        r"(\d+)\s*(?:opciones(?:\s+de)?\s+)?(posts?|publicaciones|feeds?)",
        folded,
    )
    flyers = re.search(
        r"(\d+)\s*(?:opciones(?:\s+de)?\s+)?(flyers?|folletos?|volantes?|fichas?)",
        folded,
    )
    if stories:
        parsed["story_count"] = _clamp(stories.group(1))
    if posts:
        parsed["post_count"] = _clamp(posts.group(1))
    if flyers:
        parsed["flyer_count"] = _clamp(flyers.group(1))
    if re.search(r"whatsapp", folded) and not stories and not parsed["story_count"]:
        parsed["story_count"] = 3 if pack or "historia" in folded else parsed["story_count"]
    if not variation and (pack or (not stories and not posts and not flyers)):
        if "historia" in folded and not stories:
            parsed["story_count"] = 3
        if "post" in folded and not posts:
            parsed["post_count"] = 3
        if "flyer" in folded or "volante" in folded:
            if not flyers:
                parsed["flyer_count"] = 3
        if not any((parsed["story_count"], parsed["post_count"], parsed["flyer_count"], parsed["status_count"])):
            if re.search(r"algo lindo|algo para|contenido", folded) and not pack:
                parsed["story_count"] = 1
                parsed["post_count"] = 1
                parsed["flyer_count"] = 1
            else:
                parsed["story_count"] = 3
                parsed["post_count"] = 3
                parsed["flyer_count"] = 3
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
    if get_jrh_ai_provider_name() in {"mock", "test", "rules"}:
        return fallback
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return fallback
    try:
        from modules.cash_ai_provider import request_structured_json

        parsed = request_structured_json(
            instructions=(
                "Parse a Spanish real-estate marketing request into JSON. "
                "Counts default to 0 if omitted. Pack/contenido = 3 stories, "
                "3 posts, 3 flyers. Never invent property facts. Return: "
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
                "with_agent_photo": parsed.get("with_agent_photo", fallback.get("with_agent_photo", True)) is not False,
                "show_price": parsed.get("show_price", fallback["show_price"]) is not False,
                "copy_density": parsed.get("copy_density") or "low",
                "visual_direction": parsed.get("visual_direction") or "premium varied",
                "variation_strength": parsed.get("variation_strength") or "high",
                "prompt": text,
            }
        )
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
    parsed["formats"] = {
        "story": parsed.get("story_count") or 0,
        "post": parsed.get("post_count") or 0,
        "flyer": parsed.get("flyer_count") or 0,
    }
    parsed["freeform_direction"] = parsed.get("prompt") or ""
    parsed["agent_presentation"] = parsed.get("agent_presentation") or agent_presentation_config(
        parsed.get("prompt") or prompt, parsed
    )
    if context and ((context.get("facts") or {}).get("price_policy") or {}).get("private"):
        parsed["show_price"] = False
    return parsed

