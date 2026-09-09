"""ExternalPropertyPayload → NormalizedProperty. Mapping stays out of routes."""

from __future__ import annotations

import hashlib
import json

from modules.listings_normalize import normalize_neighborhood
from modules.property_features import normalize_property_features
from modules.property_types import (
    LISTING_CURRENCIES,
    normalize_listing_purpose,
    normalize_property_type,
)
from modules.property_sync.statuses import is_deleted_status, map_external_status


class NormalizeError(ValueError):
    def __init__(self, message_key, **details):
        super().__init__(message_key)
        self.message_key = message_key
        self.details = details


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise NormalizeError("sync_err_invalid_number", value=value) from error


def _int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise NormalizeError("sync_err_invalid_number", value=value) from error


def _coords(payload):
    lat = payload.get("latitude")
    lng = payload.get("longitude")
    if lat in (None, "") or lng in (None, ""):
        return None, None
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None, None
    if lat_f != lat_f or lng_f != lng_f:
        return None, None
    return lat_f, lng_f


def normalize_external_property(payload, *, source):
    raw = dict(payload or {})
    external_id = _clean(raw.get("external_id") or raw.get("id"))
    if not external_id:
        raise NormalizeError("sync_err_missing_external_id")
    address = _clean(raw.get("address"))
    if not address:
        raise NormalizeError("sync_err_missing_address")

    currency = _clean(raw.get("currency") or raw.get("listing_currency"))
    if currency:
        currency = currency.upper()
        if currency not in LISTING_CURRENCIES:
            raise NormalizeError("sync_err_invalid_currency", currency=currency)

    commercial_status, external_status = map_external_status(
        raw.get("status") or raw.get("commercial_status") or raw.get("external_status")
    )
    if commercial_status is None:
        raise NormalizeError("sync_err_invalid_status", status=external_status)

    latitude, longitude = _coords(raw)
    location_source = "external" if latitude is not None else None

    features = raw.get("features")
    if isinstance(features, str):
        features = [part.strip() for part in features.split(",") if part.strip()]

    normalized = {
        "external_source": source,
        "external_id": external_id,
        "external_url": _clean(raw.get("external_url") or raw.get("url")),
        "external_updated_at": _clean(raw.get("updated_at") or raw.get("external_updated_at")),
        "external_status": external_status,
        "address": address,
        "jurisdiction": _clean(raw.get("jurisdiction")) or "CABA",
        "property_type": normalize_property_type(raw.get("property_type") or raw.get("type")),
        "listing_purpose": normalize_listing_purpose(
            raw.get("operation_type") or raw.get("listing_purpose")
        ),
        "listing_price": _number(raw.get("price") or raw.get("listing_price")),
        "listing_currency": currency,
        "neighborhood": normalize_neighborhood(
            raw.get("neighborhood") or raw.get("locality")
        ),
        "locality": _clean(raw.get("locality")),
        "formatted_address": _clean(raw.get("formatted_address")),
        "rooms": _int(raw.get("rooms")),
        "bedrooms": _int(raw.get("bedrooms")),
        "bathrooms": _int(raw.get("bathrooms")),
        "covered_m2": _number(raw.get("covered_surface") or raw.get("covered_m2")),
        "total_m2": _number(raw.get("total_surface") or raw.get("total_m2")),
        "parking_spaces": _int(raw.get("parking_spaces")),
        "description": _clean(raw.get("description")),
        "features": normalize_property_features(features),
        "commercial_status": commercial_status,
        "deleted": bool(raw.get("deleted")) or is_deleted_status(external_status),
        "latitude": latitude,
        "longitude": longitude,
        "location_source": location_source,
        "agent": {
            "external_agent_id": _clean(
                raw.get("external_agent_id")
                or (raw.get("agent") or {}).get("external_id")
            ),
            "agent_name": _clean(
                raw.get("agent_name") or (raw.get("agent") or {}).get("name")
            ),
            "email": _clean(
                raw.get("agent_email")
                or raw.get("email")
                or (raw.get("agent") or {}).get("email")
            ),
        },
        "media": list(raw.get("media") or raw.get("photos") or []),
    }
    normalized["sync_hash"] = compute_sync_hash(normalized)
    return normalized


def compute_sync_hash(normalized):
    payload = {
        key: normalized.get(key)
        for key in (
            "external_id",
            "address",
            "listing_price",
            "listing_currency",
            "description",
            "rooms",
            "bedrooms",
            "bathrooms",
            "covered_m2",
            "total_m2",
            "commercial_status",
            "external_status",
            "property_type",
            "listing_purpose",
            "neighborhood",
            "features",
            "latitude",
            "longitude",
            "deleted",
        )
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
