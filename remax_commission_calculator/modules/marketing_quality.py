"""MarketingQualityValidator. Approved reference is the minimum bar."""

from __future__ import annotations

import io
import logging

from PIL import Image

from modules.marketing_visual_spec import HERO_ATTENTION, QUALITY_TARGET

logger = logging.getLogger(__name__)

QUALITY_THRESHOLD = 78


def _whitespace_ratio(image):
    sample = image.convert("RGB").resize((48, 48))
    pixels = list(sample.getdata())
    if not pixels:
        return 1.0
    empty = 0
    for red, green, blue in pixels:
        if red > 236 and green > 236 and blue > 236:
            empty += 1
        elif red < 18 and green < 28 and blue < 48:
            empty += 0.45
    return empty / len(pixels)


def _edge_variance(image):
    sample = image.convert("L").resize((32, 32))
    pixels = list(sample.getdata())
    if not pixels:
        return 0
    mean = sum(pixels) / len(pixels)
    return sum((value - mean) ** 2 for value in pixels) / len(pixels)


def _upper_variance(image):
    width, height = image.size
    hero = image.crop((0, 0, width, int(height * 0.55))).convert("L").resize((24, 24))
    pixels = list(hero.getdata())
    if not pixels:
        return 0
    mean = sum(pixels) / len(pixels)
    return sum((value - mean) ** 2 for value in pixels) / len(pixels)


def _edge_clip_risk(image):
    width, height = image.size
    band = 8
    edges = [
        image.crop((0, 0, width, band)),
        image.crop((0, height - band, width, height)),
        image.crop((0, 0, band, height)),
        image.crop((width - band, 0, width, height)),
    ]
    busy = 0
    for edge in edges:
        sample = edge.convert("L").resize((16, 4))
        pixels = list(sample.getdata())
        if not pixels:
            continue
        mean = sum(pixels) / len(pixels)
        var = sum((value - mean) ** 2 for value in pixels) / len(pixels)
        if var > 2200:
            busy += 1
    return busy >= 3


def validate_creative(
    png_bytes,
    *,
    size,
    options=None,
    references=None,
    agent_photo_sent=False,
    agent_photo_composited=None,
):
    options = options or {}
    reasons = []
    try:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return {"ok": False, "score": 0, "reasons": ["unreadable"], "status": "failed_quality"}
    if image.size != tuple(size):
        reasons.append("wrong_size")
    white_ratio = _whitespace_ratio(image)
    if white_ratio > 0.58:
        reasons.append("excessive_whitespace")
    if _edge_variance(image) < 220:
        reasons.append("flat_composition")
    if options.get("layout_engine") not in {"jrh_listing", "openai_images"} and _upper_variance(image) < 160:
        reasons.append("property_not_prominent")
    if _edge_clip_risk(image):
        reasons.append("unsafe_edges")
    refs = list(references or [])
    agent_expected = bool(options.get("include_agent") and options.get("show_agent_photo"))
    if agent_expected:
        if not agent_photo_sent and not any(item.get("role") == "agent" for item in refs):
            if agent_photo_composited is not True:
                reasons.append("agent_missing")
        if agent_photo_composited is False and options.get("agent_photo_loaded"):
            reasons.append("agent_not_composited")
    if not any(str(item.get("role") or "").startswith("property") for item in refs):
        if not options.get("photo_ids"):
            reasons.append("property_missing")
    score = 90
    penalties = {
        "excessive_whitespace": 26,
        "flat_composition": 20,
        "property_not_prominent": 22,
        "unsafe_edges": 12,
        "agent_missing": 28,
        "agent_not_composited": 28,
        "property_missing": 30,
        "wrong_size": 10,
    }
    for reason in reasons:
        score -= penalties.get(reason, 8)
    ok = score >= QUALITY_THRESHOLD and "unreadable" not in reasons
    if "agent_not_composited" in reasons or "agent_missing" in reasons:
        ok = False
    if "property_not_prominent" in reasons and "property_missing" in reasons:
        ok = False
    if not ok:
        logger.info("marketing quality rejected score=%s reasons=%s target=%s", score, reasons[:5], QUALITY_TARGET)
    return {
        "ok": ok,
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "status": "completed" if ok else "failed_quality",
        "agent_expected": agent_expected,
        "agent_present": bool(agent_photo_composited or agent_photo_sent),
        "quality_target": QUALITY_TARGET,
        "hero_attention": HERO_ATTENTION,
    }


class MarketingQualityValidator:
    def validate(self, *args, **kwargs):
        return validate_creative(*args, **kwargs)
