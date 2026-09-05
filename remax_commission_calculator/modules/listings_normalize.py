"""Stable listing shape for internal properties and future portals."""

from __future__ import annotations

from modules.property_features import normalize_property_features
from modules.property_types import (
    LISTING_PURPOSES,
    normalize_listing_purpose,
    normalize_property_type,
)


def _clean_text(value):
    text = str(value or "").strip()
    return text or None


def _optional_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _optional_int(value):
    number = _optional_number(value)
    if number is None:
        return None
    return int(number)


def normalize_listing(source_record=None, **fields):
    """
    Return a portal-agnostic listing dict.

    Missing fields stay None. Never invents rooms, currency or features.
    """
    raw = dict(source_record or {})
    raw.update(fields)

    currency = (raw.get("currency") or raw.get("listing_currency") or "")
    currency = str(currency).strip().upper() or None
    if currency not in ("USD", "ARS"):
        currency = None

    purpose = normalize_listing_purpose(
        raw.get("purpose") or raw.get("listing_purpose")
    )
    if purpose not in LISTING_PURPOSES:
        purpose = None

    return {
        "source": _clean_text(raw.get("source")) or "internal",
        "external_id": _clean_text(raw.get("external_id")),
        "address": _clean_text(raw.get("address")),
        "neighborhood": normalize_neighborhood(raw.get("neighborhood")),
        "jurisdiction": _clean_text(raw.get("jurisdiction")),
        "property_type": normalize_property_type(raw.get("property_type")),
        "purpose": purpose,
        "price": _optional_number(raw.get("price") or raw.get("listing_price")),
        "currency": currency,
        "rooms": _optional_int(raw.get("rooms")),
        "bedrooms": _optional_int(raw.get("bedrooms")),
        "bathrooms": _optional_int(raw.get("bathrooms")),
        "covered_m2": _optional_number(raw.get("covered_m2")),
        "total_m2": _optional_number(raw.get("total_m2")),
        "parking_spaces": _optional_int(raw.get("parking_spaces")),
        "features": normalize_property_features(raw.get("features")),
        "description": _clean_text(raw.get("description")),
        "images": list(raw.get("images") or []),
        "commercial_status": _clean_text(raw.get("commercial_status")),
    }


def normalize_neighborhood(value):
    text = " ".join(str(value or "").split())
    return text or None


def listing_from_property(property_row):
    return normalize_listing(
        source="internal",
        external_id=property_row.get("external_id"),
        address=property_row.get("address"),
        neighborhood=property_row.get("neighborhood"),
        jurisdiction=property_row.get("jurisdiction"),
        property_type=property_row.get("property_type"),
        purpose=property_row.get("listing_purpose"),
        price=property_row.get("listing_price"),
        currency=property_row.get("listing_currency"),
        rooms=property_row.get("rooms"),
        bedrooms=property_row.get("bedrooms"),
        bathrooms=property_row.get("bathrooms"),
        covered_m2=property_row.get("covered_m2"),
        total_m2=property_row.get("total_m2"),
        parking_spaces=property_row.get("parking_spaces"),
        features=property_row.get("features")
        or property_row.get("features_json"),
        description=property_row.get("description"),
        commercial_status=property_row.get("commercial_status"),
    )
