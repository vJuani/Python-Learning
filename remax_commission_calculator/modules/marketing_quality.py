"""Reject obviously broken creatives before they reach the Agent."""

from __future__ import annotations

import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

QUALITY_THRESHOLD = 62


def _whitespace_ratio(image):
    sample = image.convert("RGB").resize((48, 48))
    pixels = list(sample.getdata())
    if not pixels:
        return 1.0
    empty = 0
    for red, green, blue in pixels:
        if red > 236 and green > 236 and blue > 236:
            empty += 1
    return empty / len(pixels)


def _edge_variance(image):
    sample = image.convert("L").resize((32, 32))
    pixels = list(sample.getdata())
    if not pixels:
        return 0
    mean = sum(pixels) / len(pixels)
    return sum((value - mean) ** 2 for value in pixels) / len(pixels)


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
    if white_ratio > 0.72:
        reasons.append("excessive_whitespace")
    if _edge_variance(image) < 180:
        reasons.append("flat_composition")
    refs = list(references or [])
    agent_expected = bool(options.get("include_agent") and options.get("show_agent_photo"))
    if agent_expected:
        if not agent_photo_sent and not any(item.get("role") == "agent" for item in refs):
            reasons.append("agent_missing")
        if agent_photo_composited is False and options.get("agent_photo_loaded"):
            reasons.append("agent_not_composited")
    if not any(str(item.get("role") or "").startswith("property") for item in refs):
        reasons.append("property_missing")
    score = 88
    if "excessive_whitespace" in reasons:
        score -= 28
    if "flat_composition" in reasons:
        score -= 18
    if "agent_missing" in reasons:
        score -= 24
    if "agent_not_composited" in reasons:
        score -= 24
    if "property_missing" in reasons:
        score -= 30
    if "wrong_size" in reasons:
        score -= 10
    ok = score >= QUALITY_THRESHOLD and "unreadable" not in reasons
    if "agent_not_composited" in reasons or "agent_missing" in reasons:
        ok = False
    if not ok:
        logger.info("marketing quality rejected score=%s reasons=%s", score, reasons[:4])
    return {
        "ok": ok,
        "score": max(0, min(100, score)),
        "reasons": reasons,
        "status": "completed" if ok else "failed_quality",
        "agent_expected": agent_expected,
        "agent_present": bool(agent_photo_sent or agent_photo_composited),
    }
