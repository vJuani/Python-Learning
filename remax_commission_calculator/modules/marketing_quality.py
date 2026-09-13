"""MarketingQualityValidator. Approved reference is the minimum bar."""

from __future__ import annotations

import io
import logging

from PIL import Image

from modules.marketing_visual_spec import (
    HERO_ATTENTION,
    QUALITY_TARGET,
    format_from_size,
    safe_area,
)

logger = logging.getLogger(__name__)

QUALITY_THRESHOLD = 78
HARD_FAIL = {
    "unreadable",
    "unsafe_edges",
    "text_clipped",
    "agent_clipped",
    "logo_clipped",
    "agent_missing",
    "agent_not_composited",
}


def _inner_box(image, fmt):
    width, height = image.size
    area = safe_area(fmt)
    return (
        area["left"],
        area["top"],
        max(area["left"] + 1, width - area["right"]),
        max(area["top"] + 1, height - area["bottom"]),
    )


def _band(image, side, depth):
    width, height = image.size
    depth = max(1, int(depth))
    if side == "top":
        return image.crop((0, 0, width, min(depth, height)))
    if side == "bottom":
        return image.crop((0, max(0, height - depth), width, height))
    if side == "left":
        return image.crop((0, 0, min(depth, width), height))
    return image.crop((max(0, width - depth), 0, width, height))


def _variance(image):
    sample = image.convert("L").resize((24, 16))
    pixels = list(sample.getdata())
    if not pixels:
        return 0
    mean = sum(pixels) / len(pixels)
    return sum((value - mean) ** 2 for value in pixels) / len(pixels)


def _margin_is_busy(band):
    wide = band.size[0] >= band.size[1]
    sample = band.convert("RGB").resize((48, 12) if wide else (12, 48))
    pixels = list(sample.getdata())
    if not pixels:
        return False
    buckets = {}
    for red, green, blue in pixels:
        key = (red // 18, green // 18, blue // 18)
        buckets[key] = buckets.get(key, 0) + 1
    dominant = max(buckets.values()) / len(pixels)
    gray = [0.3 * red + 0.6 * green + 0.1 * blue for red, green, blue in pixels]
    mean = sum(gray) / len(gray)
    var = sum((value - mean) ** 2 for value in gray) / len(gray)
    return dominant < 0.86 or var > 1400


def _whitespace_ratio(image, fmt=None):
    box = _inner_box(image, fmt) if fmt else (0, 0, image.size[0], image.size[1])
    sample = image.crop(box).convert("RGB").resize((48, 48))
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


def _inner_variance(image, fmt):
    return _variance(image.crop(_inner_box(image, fmt)))


def _safe_area_violations(image, fmt):
    area = safe_area(fmt)
    reasons = []
    checks = (
        ("left", area["left"], "text_clipped"),
        ("right", area["right"], "agent_clipped"),
        ("top", area["top"], "logo_clipped"),
        ("bottom", area["bottom"], "agent_clipped"),
    )
    busy_sides = 0
    for side, depth, reason in checks:
        band = _band(image, side, depth)
        if _margin_is_busy(band):
            busy_sides += 1
            if reason not in reasons:
                reasons.append(reason)
    if busy_sides:
        reasons.append("unsafe_edges")
    return reasons


def validate_creative(
    png_bytes,
    *,
    size,
    options=None,
    references=None,
    agent_photo_sent=False,
    agent_photo_composited=None,
    fmt=None,
):
    options = options or {}
    reasons = []
    fmt = fmt or options.get("format") or format_from_size(size)
    try:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return {"ok": False, "score": 0, "reasons": ["unreadable"], "status": "failed_quality"}
    if image.size != tuple(size):
        reasons.append("wrong_size")
    white_ratio = _whitespace_ratio(image, fmt)
    if white_ratio > 0.72:
        reasons.append("excessive_whitespace")
    if _inner_variance(image, fmt) < 180:
        reasons.append("flat_composition")
    if options.get("layout_engine") not in {"jrh_listing", "openai_images"} and _inner_variance(image, fmt) < 160:
        reasons.append("property_not_prominent")
    reasons.extend(_safe_area_violations(image, fmt))
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
    reasons = list(dict.fromkeys(reasons))
    score = 90
    penalties = {
        "excessive_whitespace": 26,
        "flat_composition": 20,
        "property_not_prominent": 22,
        "unsafe_edges": 24,
        "text_clipped": 20,
        "agent_clipped": 20,
        "logo_clipped": 16,
        "agent_missing": 28,
        "agent_not_composited": 28,
        "property_missing": 30,
        "wrong_size": 10,
    }
    for reason in reasons:
        score -= penalties.get(reason, 8)
    ok = score >= QUALITY_THRESHOLD and "unreadable" not in reasons
    if any(reason in HARD_FAIL for reason in reasons):
        ok = False
    if "property_not_prominent" in reasons and "property_missing" in reasons:
        ok = False
    if not ok:
        logger.info("marketing quality rejected score=%s reasons=%s target=%s", score, reasons[:6], QUALITY_TARGET)
    return {
        "ok": ok,
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "status": "completed" if ok else "failed_quality",
        "agent_expected": agent_expected,
        "agent_present": bool(agent_photo_composited or agent_photo_sent),
        "quality_target": QUALITY_TARGET,
        "hero_attention": HERO_ATTENTION,
        "format": fmt,
    }


class MarketingQualityValidator:
    def validate(self, *args, **kwargs):
        return validate_creative(*args, **kwargs)
