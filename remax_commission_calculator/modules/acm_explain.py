"""Explanatory ACM layer. LLM never computes money; it only narrates facts."""

from __future__ import annotations

import re

from modules.acm_engine import display_area, to_decimal
from modules.formatting import format_money
from modules.i18n import translate


_NUMBER_RE = re.compile(r"\d[\d.\s]*")


def _money(value, currency, language):
    if value in (None, ""):
        return None
    return format_money(value, currency=currency or "USD", language=language)


def build_acm_facts(view, language="es"):
    acm = (view or {}).get("acm") or {}
    subject = (view or {}).get("subject") or {}
    metrics = (view or {}).get("metrics") or {}
    quality = (view or {}).get("quality") or {}
    positioning = metrics.get("positioning") or {}
    currency = acm.get("currency") or subject.get("listing_currency") or "USD"
    used = [
        row
        for row in (view or {}).get("comparables") or []
        if row.get("selected") and row.get("valuation_valid")
    ]
    areas = [display_area(row) for row in used]
    areas = [item for item in areas if item]
    checks = quality.get("checks") or {}
    key_fields = (
        "property_type",
        "price",
        "currency",
        "location",
        "area",
        "rooms",
    )
    complete = sum(1 for key in key_fields if checks.get(key) == "ok")
    return {
        "address": subject.get("address") or "",
        "zone": subject.get("neighborhood") or subject.get("jurisdiction") or "",
        "currency": currency,
        "estimated": acm.get("estimated_value"),
        "estimated_label": _money(acm.get("estimated_value"), currency, language),
        "range_min": acm.get("suggested_min_value"),
        "range_max": acm.get("suggested_max_value"),
        "range_min_label": _money(acm.get("suggested_min_value"), currency, language),
        "range_max_label": _money(acm.get("suggested_max_value"), currency, language),
        "confidence": metrics.get("confidence") or "low",
        "found": int(metrics.get("found_count") or 0),
        "used": int(metrics.get("valuation_count") or metrics.get("used_count") or 0),
        "closings": int(metrics.get("closing_count") or 0),
        "listing_price": subject.get("listing_price"),
        "listing_label": _money(subject.get("listing_price"), currency, language),
        "positioning_band": positioning.get("band") or "",
        "positioning_delta_pct": str(positioning.get("delta_pct") or ""),
        "median_ppm2": acm.get("median_price_per_m2") or metrics.get("median_ppm2"),
        "median_ppm2_label": _money(
            acm.get("median_price_per_m2") or metrics.get("median_ppm2"),
            currency,
            language,
        ),
        "area_min": str(min(areas)) if areas else None,
        "area_max": str(max(areas)) if areas else None,
        "subject_area": str(display_area(subject) or ""),
        "can_finalize": bool((view or {}).get("can_finalize")),
        "geo_available": bool((view or {}).get("map", {}).get("available")),
        "quality_complete": complete,
        "quality_total": len(key_fields),
        "status": acm.get("status") or "draft",
    }


def fallback_market_highlights(facts, language="es"):
    facts = facts or {}
    bullets = []
    delta = facts.get("positioning_delta_pct")
    band = facts.get("positioning_band")
    if delta and band:
        bullets.append(
            translate(
                f"acm_highlight_position_{band}",
                language=language,
                value=str(delta).replace(".", ",") if language == "es" else delta,
            )
        )
    if facts.get("median_ppm2_label"):
        bullets.append(
            translate(
                "acm_highlight_median",
                language=language,
                value=facts["median_ppm2_label"],
            )
        )
    if facts.get("area_min") and facts.get("area_max"):
        bullets.append(
            translate(
                "acm_highlight_area",
                language=language,
                area_min=facts["area_min"],
                area_max=facts["area_max"],
            )
        )
    if int(facts.get("closings") or 0) <= 2:
        bullets.append(
            translate(
                "acm_highlight_few_closings",
                language=language,
                n=facts.get("closings") or 0,
                confidence=translate(
                    f"acm_confidence_{facts.get('confidence') or 'low'}",
                    language=language,
                ),
            )
        )
    if not bullets:
        bullets.append(translate("acm_explanation_empty", language=language))
    return bullets


def fallback_explanation(facts, language="es"):
    return " ".join(fallback_market_highlights(facts, language=language))


def _fact_number_tokens(facts):
    tokens = set()
    for value in (facts or {}).values():
        if value in (None, "", False, True):
            continue
        for match in _NUMBER_RE.findall(str(value)):
            digits = re.sub(r"\D", "", match)
            if digits:
                tokens.add(digits)
    return tokens


def explanation_uses_only_fact_numbers(text, facts):
    allowed = _fact_number_tokens(facts)
    for match in _NUMBER_RE.findall(text or ""):
        digits = re.sub(r"\D", "", match)
        if len(digits) >= 3 and digits not in allowed:
            return False
    return True


def explain_acm(facts, *, question="", language="es", narrative=None):
    """Return explanation text. Optional narrative is discarded if it invents numbers."""
    fallback = fallback_explanation(facts, language=language)
    if narrative and explanation_uses_only_fact_numbers(narrative, facts):
        return {
            "text": narrative.strip(),
            "source": "ai",
            "facts": facts,
        }
    return {"text": fallback, "source": "fallback", "facts": facts}


def most_similar_comparable(comparables):
    ranked = sorted(
        (row for row in (comparables or []) if row.get("score") is not None),
        key=lambda row: float(to_decimal(row.get("score")) or 0),
        reverse=True,
    )
    return ranked[0] if ranked else None
