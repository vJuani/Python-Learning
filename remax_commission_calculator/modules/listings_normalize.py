"""Stable listing shape for internal properties and future portals."""

from __future__ import annotations

import hashlib
import json

from modules.listing_sources import SOURCE_INTERNAL, normalize_listing_source
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

    source = normalize_listing_source(
        raw.get("source"),
        default=SOURCE_INTERNAL,
    )
    images = raw.get("images") or raw.get("images_json") or []
    if isinstance(images, str):
        try:
            images = json.loads(images)
        except (TypeError, ValueError):
            images = []
    if not isinstance(images, list):
        images = []

    return {
        "source": source or SOURCE_INTERNAL,
        "external_id": _clean_text(raw.get("external_id")),
        "external_url": _clean_text(
            raw.get("external_url") or raw.get("url") or raw.get("permalink")
        ),
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
        "features": normalize_property_features(
            raw.get("features") or raw.get("features_json")
        ),
        "description": _clean_text(raw.get("description")),
        "images": list(images),
        "commercial_status": _clean_text(raw.get("commercial_status")),
        "published_at": _clean_text(raw.get("published_at")),
        "updated_at": _clean_text(
            raw.get("updated_at") or raw.get("source_updated_at")
        ),
    }


def normalize_neighborhood(value):
    text = " ".join(str(value or "").split())
    return text or None


def listing_from_property(property_row):
    return normalize_listing(
        source=SOURCE_INTERNAL,
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
        images=property_row.get("images"),
    )


def listing_from_external_listing(external_listing):
    row = external_listing or {}
    return normalize_listing(
        source=row.get("source"),
        external_id=row.get("external_id"),
        external_url=row.get("external_url") or row.get("url"),
        address=row.get("address"),
        neighborhood=row.get("neighborhood"),
        jurisdiction=row.get("jurisdiction"),
        property_type=row.get("property_type"),
        purpose=row.get("purpose") or row.get("listing_purpose"),
        price=row.get("price") or row.get("listing_price"),
        currency=row.get("currency") or row.get("listing_currency"),
        rooms=row.get("rooms"),
        bedrooms=row.get("bedrooms"),
        bathrooms=row.get("bathrooms"),
        covered_m2=row.get("covered_m2"),
        total_m2=row.get("total_m2"),
        parking_spaces=row.get("parking_spaces"),
        features=row.get("features") or row.get("features_json"),
        description=row.get("description"),
        images=row.get("images") or row.get("images_json"),
        commercial_status=row.get("commercial_status"),
        published_at=row.get("published_at") or row.get("first_seen_at"),
        updated_at=row.get("source_updated_at") or row.get("updated_at"),
    )


def listing_content_hash(listing):
    payload = {
        key: listing.get(key)
        for key in (
            "source",
            "external_id",
            "external_url",
            "address",
            "neighborhood",
            "jurisdiction",
            "property_type",
            "purpose",
            "price",
            "currency",
            "rooms",
            "bedrooms",
            "bathrooms",
            "covered_m2",
            "total_m2",
            "parking_spaces",
            "features",
            "description",
            "images",
            "commercial_status",
        )
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def attach_listing_identity(listing, *, property_id=None, external_listing_id=None):
    attached = dict(listing or {})
    attached["internal_property_id"] = property_id
    attached["external_listing_id"] = external_listing_id
    attached["external_url"] = attached.get("external_url")
    return attached
