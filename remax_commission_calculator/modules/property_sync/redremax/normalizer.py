"""RedREMAX payload → hub-ready dict. Never writes DB. Drops sensitive objects."""

from __future__ import annotations

from modules.property_sync.redremax.mapping import (
    LOCATION_SOURCE,
    PROVIDER_REDREMAX,
    map_operation_type,
    map_property_type,
)
from modules.property_sync.redremax.photos import (
    has_blueprints,
    inspect_photos,
    public_photo_audit,
)
from modules.property_sync.redremax.privacy import strip_sensitive_fields


class RedRemaxPropertyNormalizer:
    source = PROVIDER_REDREMAX

    def normalize(self, payload, *, expected_office_id=None):
        raw = strip_sensitive_fields(payload or {})
        warnings = []
        office = _clean(raw.get("office"))
        if expected_office_id and office and office != expected_office_id:
            return None, ["redremax_warn_office_mismatch"]

        address_block = raw.get("address") if isinstance(raw.get("address"), dict) else {}
        address_text = _address_text(address_block)
        if not address_text:
            warnings.append("redremax_warn_missing_address")

        latitude, longitude = _coords(raw.get("location"))
        operation, raw_operation, operation_warn = map_operation_type(raw.get("type"))
        if operation_warn:
            warnings.append(operation_warn)
        property_type, raw_property_type, type_warn = map_property_type(raw.get("propertyType"))
        if type_warn:
            warnings.append(type_warn)

        price_block = raw.get("price") if isinstance(raw.get("price"), dict) else {}
        dimensions = raw.get("dimensions") if isinstance(raw.get("dimensions"), dict) else {}
        rooms_count = _rooms_count(raw)

        photo_audit = inspect_photos(raw.get("photos"))
        media = photo_audit["selected"]
        if photo_audit["payload_count"] == 0:
            warnings.append("redremax_warn_no_photos")
        elif photo_audit["valid_count"] == 0:
            warnings.append("redremax_warn_photos_rejected")
        if has_blueprints(raw):
            warnings.append("redremax_warn_blueprints_skipped")

        hub = {
            "external_id": _clean(raw.get("id")),
            "id": _clean(raw.get("id")),
            "address": address_text,
            "formatted_address": _clean(address_block.get("displayAddress")) or address_text,
            "locality": _clean(address_block.get("city")),
            "neighborhood": _clean(address_block.get("neighborhood")),
            "jurisdiction": _jurisdiction(address_block),
            "administrative_area": _clean(address_block.get("state") or address_block.get("region")),
            "country": _clean(address_block.get("country")),
            "postal_code": _clean(address_block.get("postalCode")),
            "latitude": latitude,
            "longitude": longitude,
            "location_source": LOCATION_SOURCE if latitude is not None else None,
            "price": price_block.get("value"),
            "listing_price": price_block.get("value"),
            "currency": price_block.get("currency"),
            "listing_currency": price_block.get("currency"),
            "operation_type": operation,
            "listing_purpose": operation,
            "property_type": property_type,
            "status": raw.get("status"),
            "description": _clean(raw.get("description")),
            "title": _clean(raw.get("title")),
            "rooms": rooms_count,
            "bedrooms": _as_int(raw.get("bedrooms")),
            "bathrooms": _as_int(raw.get("bathrooms")),
            "parking_spaces": _as_int(raw.get("parkingSpaces")),
            "covered_m2": _surface(dimensions.get("covered")),
            "total_m2": _total_surface(property_type, dimensions),
            "updated_at": _clean(raw.get("updatedAt") or raw.get("updated_at")),
            "external_updated_at": _clean(raw.get("updatedAt") or raw.get("updated_at")),
            "external_agent_id": _clean(raw.get("associate")),
            "agent": {"external_agent_id": _clean(raw.get("associate"))},
            "media": media,
            "photos": media,
            "photo_audit": public_photo_audit(photo_audit),
            "warnings": warnings,
            "external_metadata": sanitized_metadata(raw, price_block, dimensions, address_block),
            "price_history": normalize_price_history(raw.get("priceHistory")),
            "external_price_exposure": _clean(price_block.get("exposure")),
            "external_operation": raw_operation,
            "external_property_type": raw_property_type,
        }
        return hub, warnings


def sanitized_metadata(raw, price_block, dimensions, address_block):
    return {
        "mlsid": _clean(raw.get("mlsid")),
        "qrid": _clean(raw.get("qrid")),
        "office": _clean(raw.get("office")),
        "network": _clean(raw.get("network")),
        "node": _clean(raw.get("node")),
        "associate": _clean(raw.get("associate")),
        "associate_qrid": _clean(raw.get("associateQrid")),
        "title": _clean(raw.get("title")),
        "display_address": _clean(address_block.get("displayAddress")),
        "private_community": _clean(address_block.get("privatecommunity")),
        "subregion": _clean(address_block.get("subregion")),
        "associate_status": _clean(raw.get("associateStatus")),
        "property_type": _clean(raw.get("propertyType")),
        "operation": _clean(raw.get("type")),
        "status": _clean(raw.get("status")),
        "status_changes": _clean(raw.get("statusChanges")),
        "publication_quality": raw.get("publicationQuality"),
        "external_price_exposure": _clean(price_block.get("exposure")),
        "price_type": _clean(price_block.get("type")),
        "expires_on": _clean(raw.get("expiresOn")),
        "available_date": _clean(raw.get("availableDate")),
        "created_at": _clean(raw.get("createdAt")),
        "approved_at": _clean(raw.get("approvedAt")),
        "street": _clean(address_block.get("streetName")),
        "street_number": _clean(address_block.get("streetNumber")),
        "floor": _clean(address_block.get("floor")),
        "apartment": _clean(address_block.get("apartment")),
        "county": _clean(address_block.get("county")),
        "dimensions": {
            "land": dimensions.get("land"),
            "total_built": dimensions.get("totalBuilt"),
            "covered": dimensions.get("covered"),
            "uncovered": dimensions.get("uncovered"),
            "semicovered": dimensions.get("semicovered"),
        },
        "toilet_rooms": raw.get("toiletrooms"),
        "total_floors": raw.get("totalFloors"),
        "building_total_units": raw.get("buildingTotalUnits"),
        "studio": raw.get("studio"),
        "external_feature_ids": list(raw.get("features") or [])
        if isinstance(raw.get("features"), list)
        else [],
        "has_blueprints": has_blueprints(raw),
        "commission": _commission_snapshot(raw.get("commission")),
        "apt_credit": raw.get("aptCredit"),
        "commercial_use": raw.get("commercialUse"),
        "professional_use": raw.get("professionalUse"),
        "furnished": raw.get("furnished"),
        "financing": raw.get("financing"),
        "in_private_community": raw.get("inPrivateCommunity"),
        "property_condition": raw.get("propertyCondition"),
        "orientation": raw.get("orientation"),
        "disposition": raw.get("disposition"),
        "year_build": raw.get("yearBuild"),
        "maintenance": raw.get("maintenance")
        if isinstance(raw.get("maintenance"), dict)
        else None,
    }


def normalize_price_history(rows):
    items = []
    if not isinstance(rows, list):
        return items
    for row in rows:
        row = row or {}
        items.append(
            {
                "price": row.get("price"),
                "date": _clean(row.get("date")),
                "currency": _clean(row.get("currency")),
                "usd_value": row.get("usd"),
                "local_value": row.get("local"),
                "local_currency": _clean(row.get("localCurrency")),
                "exchange_rate_snapshot": row.get("tc"),
            }
        )
    return items


def _commission_snapshot(value):
    if not isinstance(value, dict):
        return None
    return {
        "seller": value.get("seller"),
        "buyer": value.get("buyer"),
    }


def _address_text(block):
    display = _clean(block.get("displayAddress"))
    if display:
        return display
    street = " ".join(
        part
        for part in (
            _clean(block.get("streetName")),
            _clean(block.get("streetNumber")),
        )
        if part
    )
    return street or None


def _jurisdiction(block):
    return (
        _clean(block.get("county"))
        or _clean(block.get("subregion"))
        or _clean(block.get("region"))
        or _clean(block.get("state"))
        or _clean(block.get("city"))
        or "CABA"
    )


def _coords(location):
    if not isinstance(location, (list, tuple)) or len(location) < 2:
        return None, None
    try:
        longitude = float(location[0])
        latitude = float(location[1])
    except (TypeError, ValueError):
        return None, None
    if longitude != longitude or latitude != latitude:
        return None, None
    return latitude, longitude


def _rooms_count(raw):
    total = _as_int(raw.get("totalRooms"))
    if total is not None:
        return total
    rooms = raw.get("rooms")
    if isinstance(rooms, list):
        return None
    return _as_int(rooms)


def _surface(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _total_surface(property_type, dimensions):
    if property_type == "land":
        return _surface(dimensions.get("land"))
    return None


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
