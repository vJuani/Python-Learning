"""ACM source catalog and human-readable comparable diffs.

Does not scrape, fetch portals, or change valuation numbers.
"""

from __future__ import annotations

from decimal import Decimal

from modules.acm_engine import SOURCE_CLOSED, SOURCE_INTERNAL, SOURCE_MANUAL, to_decimal
from modules.i18n import translate


SOURCE_ZONAPROP = "zonaprop"
SOURCE_ARGENPROP = "argenprop"
SOURCE_MERCADOLIBRE = "mercadolibre"
SOURCE_REMAX_WEB = "remax_web"
SOURCE_OTHER = "other_external"

ACM_SOURCES = (
    SOURCE_INTERNAL,
    SOURCE_CLOSED,
    SOURCE_MANUAL,
    SOURCE_ZONAPROP,
    SOURCE_ARGENPROP,
    SOURCE_MERCADOLIBRE,
    SOURCE_REMAX_WEB,
    SOURCE_OTHER,
)

CONNECTED_SOURCES = (
    SOURCE_INTERNAL,
    SOURCE_CLOSED,
    SOURCE_MANUAL,
)

SOURCE_LABEL_KEYS = {
    SOURCE_INTERNAL: "acm_source_jrh",
    SOURCE_CLOSED: "acm_source_closing",
    SOURCE_MANUAL: "acm_source_manual",
    SOURCE_ZONAPROP: "acm_source_zonaprop",
    SOURCE_ARGENPROP: "acm_source_argenprop",
    SOURCE_MERCADOLIBRE: "acm_source_mercadolibre",
    SOURCE_REMAX_WEB: "acm_source_remax",
    SOURCE_OTHER: "acm_source_other",
}


def source_label(source_type, language="es"):
    key = SOURCE_LABEL_KEYS.get(source_type or "", "acm_source_other")
    return translate(key, language=language)


def source_catalog(language="es"):
    items = []
    for source in ACM_SOURCES:
        connected = source in CONNECTED_SOURCES
        items.append(
            {
                "id": source,
                "label": source_label(source, language),
                "connected": connected,
                "status": "connected" if connected else "coming_soon",
            }
        )
    return items


def _split_diff(diff):
    if isinstance(diff, (list, tuple)) and diff:
        key = str(diff[0])
        value = diff[1] if len(diff) > 1 else ""
        return key, value
    return str(diff or ""), ""


def _format_pct(value, language="es"):
    number = to_decimal(value)
    if number is None:
        return str(value or "")
    text = f"{abs(number):.1f}"
    if language == "es":
        return text.replace(".", ",")
    return text


def format_diff_label(diff, language="es"):
    """Map stored score diffs to human copy. Never echo raw tuples/lists."""
    key, value = _split_diff(diff)
    if key.startswith("acm_diff_"):
        key = key[len("acm_diff_") :]
    if key == "area_delta":
        number = to_decimal(value) or Decimal("0")
        label_key = (
            "acm_diff_area_less" if number < 0 else "acm_diff_area_more"
        )
        return translate(
            label_key,
            language=language,
            value=_format_pct(number, language),
        )
    if key == "rooms_delta":
        number = to_decimal(value) or Decimal("0")
        label_key = (
            "acm_diff_rooms_less" if number < 0 else "acm_diff_rooms_more"
        )
        return translate(
            label_key,
            language=language,
            value=str(abs(int(number))),
        )
    if key == "bedrooms_delta":
        number = to_decimal(value) or Decimal("0")
        label_key = (
            "acm_diff_bedrooms_less"
            if number < 0
            else "acm_diff_bedrooms_more"
        )
        return translate(
            label_key,
            language=language,
            value=str(abs(int(number))),
        )
    mapped = translate(f"acm_diff_{key}", language=language, value=value)
    raw = str(diff)
    if mapped.startswith("acm_diff_") or "[" in mapped or "(" in mapped:
        return translate("acm_diff_generic", language=language)
    return mapped


def format_match_label(key, language="es"):
    if not isinstance(key, str):
        return ""
    return translate(f"acm_match_{key}", language=language)
