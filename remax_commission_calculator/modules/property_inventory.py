"""Property inventory helpers: identity, commercial status, display."""

from __future__ import annotations

from modules.formatting import format_number
from modules.i18n import translate
from modules.listings_normalize import normalize_neighborhood
from modules.property_features import (
    FEATURE_KEYS,
    active_feature_keys,
    normalize_property_features,
)
from modules.property_types import (
    COMMERCIAL_STATUSES,
    LISTING_CURRENCIES,
    normalize_listing_purpose,
    normalize_property_type,
)


IDENTITY_FIELDS = (
    "address",
    "jurisdiction",
    "agent_id",
    "property_type",
    "listing_price",
    "listing_purpose",
    "listing_currency",
)

UNAVAILABLE_COMMERCIAL_STATUSES = frozenset(
    ("sold", "rented", "withdrawn")
)

WORKFLOW_APPROVED = "approved"


def _clean_text(value):
    text = str(value or "").strip()
    return text or None


def _same_price(left, right):
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        return abs(float(left) - float(right)) < 0.0001
    except (TypeError, ValueError):
        return False


def _same_int(left, right):
    if left in (None, "") and right in (None, ""):
        return True
    if left in (None, "") or right in (None, ""):
        return False
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def identity_fields_changed(current, proposed):
    current = current or {}
    proposed = proposed or {}

    if _clean_text(current.get("address")) != _clean_text(proposed.get("address")):
        return True
    if _clean_text(current.get("jurisdiction")) != _clean_text(
        proposed.get("jurisdiction")
    ):
        return True
    if not _same_int(current.get("agent_id"), proposed.get("agent_id")):
        return True
    if normalize_property_type(current.get("property_type")) != normalize_property_type(
        proposed.get("property_type")
    ):
        return True
    if not _same_price(current.get("listing_price"), proposed.get("listing_price")):
        return True
    if normalize_listing_purpose(current.get("listing_purpose")) != (
        normalize_listing_purpose(proposed.get("listing_purpose"))
    ):
        return True
    current_currency = _clean_text(current.get("listing_currency"))
    proposed_currency = _clean_text(proposed.get("listing_currency"))
    if current_currency:
        current_currency = current_currency.upper()
    if proposed_currency:
        proposed_currency = proposed_currency.upper()
    return current_currency != proposed_currency


def is_commercially_available(property_row):
    """
    Future search rule. Does not change operation creation.

    Legacy: commercial_status NULL keeps the approved-only rule.
    """
    if (property_row or {}).get("status") != WORKFLOW_APPROVED:
        return False

    commercial_status = (property_row or {}).get("commercial_status")
    if commercial_status in (None, ""):
        return True
    return commercial_status not in UNAVAILABLE_COMMERCIAL_STATUSES


def listing_currency_label(currency, language="es"):
    cleaned = str(currency or "").strip().upper()
    if cleaned in LISTING_CURRENCIES:
        return cleaned
    return translate("listing_currency_unknown", language=language)


def format_listing_money(amount, currency=None, language="es"):
    if amount is None or amount == "":
        return None
    number = format_number(amount, language=language)
    cleaned = str(currency or "").strip().upper()
    if cleaned in LISTING_CURRENCIES:
        return f"{cleaned} {number}"
    return number


def property_feature_labels(features, language="es"):
    labels = []
    for key in active_feature_keys(features):
        labels.append(translate(f"property_feature_{key}", language=language))
    extras = normalize_property_features(features)
    for key, enabled in extras.items():
        if key in FEATURE_KEYS or not enabled:
            continue
        labels.append(str(key))
    return labels


def property_fact_parts(property_row, language="es"):
    row = property_row or {}
    parts = []
    if row.get("rooms") is not None:
        parts.append(
            translate("property_rooms_n", language=language, n=row["rooms"])
        )
    if row.get("bedrooms") is not None:
        parts.append(
            translate("property_bedrooms_n", language=language, n=row["bedrooms"])
        )
    if row.get("bathrooms") is not None:
        parts.append(
            translate("property_bathrooms_n", language=language, n=row["bathrooms"])
        )
    return parts


def property_area_label(property_row, language="es"):
    row = property_row or {}
    area = row.get("total_m2")
    if area is None:
        area = row.get("covered_m2")
    if area is None:
        return None
    number = format_number(area, language=language, decimals=0)
    return translate("property_area_m2", language=language, n=number)


def property_parking_label(property_row, language="es"):
    parking = (property_row or {}).get("parking_spaces")
    if parking is None or int(parking) <= 0:
        return None
    return translate("property_parking_n", language=language, n=int(parking))


def decorate_property_for_display(property_row, language="es"):
    row = dict(property_row or {})
    features = normalize_property_features(
        row.get("features") if row.get("features") is not None else row.get("features_json")
    )
    row["features"] = features
    row["neighborhood"] = normalize_neighborhood(row.get("neighborhood"))
    row["feature_labels"] = property_feature_labels(features, language)
    row["fact_parts"] = property_fact_parts(row, language)
    row["area_label"] = property_area_label(row, language)
    row["parking_label"] = property_parking_label(row, language)
    row["price_display"] = format_listing_money(
        row.get("listing_price"),
        row.get("listing_currency"),
        language=language,
    )
    row["currency_known"] = (
        str(row.get("listing_currency") or "").strip().upper() in LISTING_CURRENCIES
    )
    row["currency_label"] = listing_currency_label(
        row.get("listing_currency"),
        language=language,
    )
    purpose = normalize_listing_purpose(row.get("listing_purpose"))
    row["purpose_label"] = (
        translate(f"listing_purpose_{purpose}", language=language)
        if purpose
        else None
    )
    commercial = row.get("commercial_status")
    row["commercial_status_label"] = (
        translate(f"commercial_status_{commercial}", language=language)
        if commercial in COMMERCIAL_STATUSES
        else None
    )
    return row
