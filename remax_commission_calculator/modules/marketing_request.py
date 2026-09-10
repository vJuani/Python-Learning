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

MAX_PER_FORMAT = 6


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
        "show_price": True,
        "copy_density": "low",
        "visual_direction": "premium varied",
        "variation_strength": "high",
        "prompt": "",
    }


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
    return items


def _fallback_parse(prompt, *, variation=False):
    text = (prompt or "").strip() or ("" if variation else DEFAULT_PROMPT)
    folded = text.lower()
    parsed = empty_request()
    parsed["prompt"] = text
    parsed["with_agent"] = not re.search(r"sin (mi )?foto|sin agente|sin jose|sin josé", folded)
    parsed["show_price"] = not re.search(r"sin precio|ocult(a|á) el precio", folded)
    if re.search(r"sin (mucho )?texto|sin descripci|poco texto|copy corto", folded):
        parsed["copy_density"] = "low"
    if re.search(r"m[aá]s (jugado|oscuro|elegante)|regener", folded):
        parsed["variation_strength"] = "high"
    pack = bool(re.search(r"pack|contenido para esta|publicidad", folded))
    stories = re.search(r"(\d+)\s*(?:opciones(?:\s+de)?\s+)?(historias?|stories|story)", folded)
    posts = re.search(r"(\d+)\s*(?:opciones(?:\s+de)?\s+)?(posts?|publicaciones)", folded)
    flyers = re.search(r"(\d+)\s*(?:opciones(?:\s+de)?\s+)?(flyers?|folletos?)", folded)
    statuses = re.search(r"(\d+)\s*(?:opciones(?:\s+de)?\s+)?(estados?|whatsapp)", folded)
    if stories:
        parsed["story_count"] = _clamp(stories.group(1))
    if posts:
        parsed["post_count"] = _clamp(posts.group(1))
    if flyers:
        parsed["flyer_count"] = _clamp(flyers.group(1))
    if statuses:
        parsed["status_count"] = _clamp(statuses.group(1))
    if not variation and (pack or (not stories and not posts and not flyers and not statuses)):
        if "historia" in folded and not stories:
            parsed["story_count"] = 3
        if "post" in folded and not posts:
            parsed["post_count"] = 3
        if "flyer" in folded and not flyers:
            parsed["flyer_count"] = 3
        if not any((parsed["story_count"], parsed["post_count"], parsed["flyer_count"], parsed["status_count"])):
            parsed["story_count"] = 3
            parsed["post_count"] = 3
            parsed["flyer_count"] = 3
    if parsed["story_count"] and re.search(r"whatsapp|estado", folded) and not statuses:
        parsed["status_count"] = 0
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
