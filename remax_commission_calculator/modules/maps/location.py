"""Parse and apply property location without inventing coordinates."""

from __future__ import annotations

import re
import unicodedata

from modules.maps.config import (
    GEOCODE_MANUAL,
    GEOCODE_RESOLVED,
    GEOCODE_STALE,
    GEOCODE_UNRESOLVED,
)
from modules.maps.links import build_directions_url, build_open_maps_url


LOCATION_FIELDS = (
    "formatted_address",
    "locality",
    "administrative_area",
    "country",
    "postal_code",
    "google_place_id",
    "latitude",
    "longitude",
    "geocoded_at",
    "geocode_status",
)

_EMPTY_LOCATION = {
    "formatted_address": None,
    "locality": None,
    "administrative_area": None,
    "country": None,
    "postal_code": None,
    "google_place_id": None,
    "latitude": None,
    "longitude": None,
    "geocoded_at": None,
    "geocode_status": GEOCODE_MANUAL,
}


def _fold(text):
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).lower()


def normalize_address_key(value):
    return re.sub(r"[^a-z0-9]+", "", _fold(value))


def addresses_differ_substantially(left, right):
    return normalize_address_key(left) != normalize_address_key(right)


def parse_coordinate(value, *, kind):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if kind == "lat" and -90 <= number <= 90:
        return number
    if kind == "lng" and -180 <= number <= 180:
        return number
    return None


def has_coordinates(row):
    row = row or {}
    lat = parse_coordinate(row.get("latitude"), kind="lat")
    lng = parse_coordinate(row.get("longitude"), kind="lng")
    return lat is not None and lng is not None


def _component_map(components):
    mapped = {}
    for item in components or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("long_name") or item.get("short_name") or "").strip()
        if not name:
            continue
        for type_name in item.get("types") or []:
            mapped[type_name] = name
            short = (item.get("short_name") or "").strip()
            if short and type_name == "country":
                mapped["country_code"] = short
            if short and type_name == "administrative_area_level_1":
                mapped["administrative_area_short"] = short
    return mapped


def parse_address_components(components):
    mapped = _component_map(components)
    route = mapped.get("route") or ""
    number = mapped.get("street_number") or ""
    street = " ".join(part for part in (route, number) if part).strip()
    neighborhood = (
        mapped.get("neighborhood")
        or mapped.get("sublocality_level_1")
        or mapped.get("sublocality")
        or None
    )
    locality = (
        mapped.get("locality")
        or mapped.get("administrative_area_level_2")
        or None
    )
    return {
        "street_address": street or None,
        "neighborhood": neighborhood,
        "locality": locality,
        "administrative_area": mapped.get("administrative_area_level_1"),
        "country": mapped.get("country"),
        "postal_code": mapped.get("postal_code"),
    }


def apply_place_to_location(place, *, geocoded_at=None):
    """Normalize a selected place. Does not call Google."""
    place = place or {}
    parsed = parse_address_components(place.get("address_components"))
    lat = parse_coordinate(
        (place.get("geometry") or {}).get("location", {}).get("lat")
        if isinstance(place.get("geometry"), dict)
        else place.get("latitude"),
        kind="lat",
    )
    lng = parse_coordinate(
        (place.get("geometry") or {}).get("location", {}).get("lng")
        if isinstance(place.get("geometry"), dict)
        else place.get("longitude"),
        kind="lng",
    )
    place_id = (place.get("place_id") or place.get("google_place_id") or "").strip()
    formatted = (
        place.get("formatted_address")
        or place.get("formattedAddress")
        or ""
    ).strip() or None
    if not place_id or lat is None or lng is None:
        return dict(_EMPTY_LOCATION)
    display = (place.get("display_address") or parsed.get("street_address") or "").strip()
    return {
        "display_address": display or None,
        "formatted_address": formatted,
        "neighborhood": parsed.get("neighborhood"),
        "locality": parsed.get("locality"),
        "administrative_area": parsed.get("administrative_area"),
        "country": parsed.get("country"),
        "postal_code": parsed.get("postal_code"),
        "google_place_id": place_id,
        "latitude": lat,
        "longitude": lng,
        "geocoded_at": geocoded_at,
        "geocode_status": GEOCODE_RESOLVED,
    }


def cleared_location(*, status=GEOCODE_STALE):
    payload = dict(_EMPTY_LOCATION)
    payload["geocode_status"] = status
    return payload


def location_from_form(form, existing=None, *, geocoded_at=None):
    """
    Build location kwargs from a property form.

    A newly selected place (place_id + lat + lng) wins.
    If the commercial address changed and no new place was selected,
    previous coordinates are dropped. They are never reused silently.
    """
    existing = existing or {}
    form = form or {}
    address = (form.get("address") or "").strip()
    place_id = (form.get("google_place_id") or "").strip() or None
    lat = parse_coordinate(form.get("latitude"), kind="lat")
    lng = parse_coordinate(form.get("longitude"), kind="lng")
    selected = bool(place_id and lat is not None and lng is not None)
    existing_place = (existing.get("google_place_id") or "").strip()
    if (
        selected
        and existing_place
        and existing_place == place_id
        and addresses_differ_substantially(existing.get("address"), address)
    ):
        return cleared_location(status=GEOCODE_STALE)

    if selected:
        return {
            "formatted_address": (form.get("formatted_address") or "").strip() or None,
            "locality": (form.get("locality") or "").strip() or None,
            "administrative_area": (
                form.get("administrative_area") or ""
            ).strip() or None,
            "country": (form.get("country") or "").strip() or None,
            "postal_code": (form.get("postal_code") or "").strip() or None,
            "google_place_id": place_id,
            "latitude": lat,
            "longitude": lng,
            "geocoded_at": geocoded_at,
            "geocode_status": GEOCODE_RESOLVED,
            "neighborhood": (form.get("neighborhood") or "").strip() or None,
        }

    if has_coordinates(existing) or existing.get("google_place_id"):
        if addresses_differ_substantially(existing.get("address"), address):
            return cleared_location(status=GEOCODE_STALE)
        return {
            field: existing.get(field) for field in LOCATION_FIELDS
        }

    return cleared_location(status=GEOCODE_MANUAL)


def location_line(row):
    row = row or {}
    parts = [
        part
        for part in (
            row.get("neighborhood"),
            row.get("locality"),
            row.get("administrative_area"),
        )
        if part
    ]
    seen = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return ", ".join(seen)


def attach_property_maps(row, *, maps_configured=None):
    from modules.maps.config import maps_is_configured

    item = dict(row or {})
    configured = (
        maps_is_configured() if maps_configured is None else bool(maps_configured)
    )
    coords = has_coordinates(item)
    status = item.get("geocode_status") or (
        GEOCODE_RESOLVED if coords else GEOCODE_UNRESOLVED
    )
    item["has_coordinates"] = coords
    item["geocode_status"] = status
    item["location_line"] = location_line(item)
    item["maps_open_url"] = build_open_maps_url(item)
    item["maps_directions_url"] = build_directions_url(item)
    item["maps_configured"] = configured
    item["show_map"] = bool(coords and configured)
    return item
