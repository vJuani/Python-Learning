"""Deterministic geographic helpers. One source for Properties, Needs, ACM, JRH.

Distance is Haversine on the WGS84 sphere (mean Earth radius 6_371_000 m).
Never calls Google Distance Matrix, Directions, or any network API.

Performance: callers MUST pre-filter with bounding_box() in SQL
(latitude/longitude BETWEEN) plus organization/permission filters, then
apply filter_by_radius() on the reduced set. Do not Haversine 100k rows
in Python.

SQLite and PostgreSQL both support the BETWEEN bbox. Exact distance stays
in this module so Postgres can later swap in ST_DWithin without changing
scoring.
"""

from __future__ import annotations

import math

from modules.maps.location import parse_coordinate


EARTH_RADIUS_M = 6_371_000.0

# Need matching: distance as a fraction of the requested radius.
# Uses the existing zone weight (25). Outside the radius is a hard fail.
NEED_RADIUS_BANDS = (
    (0.25, 1.00),
    (0.50, 0.85),
    (0.75, 0.60),
    (1.00, 0.35),
)

# ACM default urban bands for the existing zone weight (30).
# Centralized so property type/location can override later.
ACM_DISTANCE_BANDS_M = (
    (500, 1.00),
    (1000, 0.85),
    (2000, 0.70),
    (5000, 0.40),
)
ACM_DISTANCE_BEYOND_RATIO = 0.15

DEFAULT_NEARBY_RADIUS_KM = 1.0
NEARBY_RADIUS_OPTIONS_KM = (1.0, 2.0, 5.0)
NEED_RADIUS_OPTIONS_KM = (0.5, 1.0, 2.0, 5.0)
MAX_RADIUS_KM = 50.0
MIN_RADIUS_KM = 0.05

# Candidate pool when ACM has coords but the agent chose "no distance limit".
ACM_UNLIMITED_SEARCH_KM = 15.0

DEG_LAT_M = 111_320.0


def _lat(value):
    return parse_coordinate(value, kind="lat")


def _lng(value):
    return parse_coordinate(value, kind="lng")


def radius_km_to_meters(radius_km):
    km = parse_radius_km(radius_km)
    if km is None:
        return None
    return km * 1000.0


def parse_radius_km(value, *, default=None):
    if value in (None, ""):
        return default
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default
    if number != number or number <= 0:
        return default
    return max(MIN_RADIUS_KM, min(MAX_RADIUS_KM, number))


def parse_radius_to_meters(value, unit="km"):
    """Parse a numeric radius plus unit (km or m) into meters."""
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    folded = str(unit or "km").strip().lower()
    if folded in {"m", "metro", "metros"}:
        meters = number
    else:
        meters = number * 1000.0
    km = meters / 1000.0
    if km < MIN_RADIUS_KM or km > MAX_RADIUS_KM:
        if meters < MIN_RADIUS_KM * 1000:
            return None
        meters = min(MAX_RADIUS_KM * 1000.0, meters)
    return meters


def distance_between_coordinates(lat1, lng1, lat2, lng2):
    """Haversine distance in meters, or None if any coordinate is missing."""
    a_lat = _lat(lat1)
    a_lng = _lng(lng1)
    b_lat = _lat(lat2)
    b_lng = _lng(lng2)
    if None in (a_lat, a_lng, b_lat, b_lng):
        return None
    if a_lat == b_lat and a_lng == b_lng:
        return 0.0
    phi1 = math.radians(a_lat)
    phi2 = math.radians(b_lat)
    d_phi = math.radians(b_lat - a_lat)
    d_lambda = math.radians(b_lng - a_lng)
    hav = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(hav)))


def bounding_box(lat, lng, radius_m):
    """Axis-aligned bbox that contains the radius circle.

    Used as a cheap SQL pre-filter. A point inside the bbox may still
    fall outside the circle; filter_by_radius() is the exact check.
    A valid in-radius point is never excluded by this box.
    """
    center_lat = _lat(lat)
    center_lng = _lng(lng)
    try:
        meters = float(radius_m)
    except (TypeError, ValueError):
        return None
    if None in (center_lat, center_lng) or meters <= 0:
        return None
    delta_lat = meters / DEG_LAT_M
    cos_lat = math.cos(math.radians(center_lat))
    meters_per_deg_lng = DEG_LAT_M * max(0.01, abs(cos_lat))
    delta_lng = meters / meters_per_deg_lng
    return {
        "south": max(-90.0, center_lat - delta_lat),
        "north": min(90.0, center_lat + delta_lat),
        "west": max(-180.0, center_lng - delta_lng),
        "east": min(180.0, center_lng + delta_lng),
    }


def bbox_sql_clauses(box, *, table="properties"):
    """Return (sql_fragment, params) for latitude/longitude BETWEEN."""
    if not box:
        return "", []
    prefix = f"{table}." if table else ""
    sql = (
        f"{prefix}latitude IS NOT NULL AND {prefix}longitude IS NOT NULL "
        f"AND {prefix}latitude BETWEEN ? AND ? "
        f"AND {prefix}longitude BETWEEN ? AND ?"
    )
    params = [box["south"], box["north"], box["west"], box["east"]]
    return sql, params


def attach_distance(row, lat, lng, *, lat_key="latitude", lng_key="longitude"):
    payload = dict(row or {})
    meters = distance_between_coordinates(
        lat,
        lng,
        payload.get(lat_key),
        payload.get(lng_key),
    )
    payload["distance_meters"] = meters
    payload["distance_label"] = format_distance(meters) if meters is not None else None
    return payload


def filter_by_radius(
    rows,
    lat,
    lng,
    radius_m,
    *,
    lat_key="latitude",
    lng_key="longitude",
    exclude_id=None,
    id_key="id",
):
    """Keep rows with coords inside radius_m. Ungeocoded rows are excluded."""
    try:
        limit = float(radius_m)
    except (TypeError, ValueError):
        return []
    if limit <= 0:
        return []
    kept = []
    for row in rows or []:
        if exclude_id is not None and row.get(id_key) == exclude_id:
            continue
        item = attach_distance(row, lat, lng, lat_key=lat_key, lng_key=lng_key)
        meters = item.get("distance_meters")
        if meters is None or meters > limit:
            continue
        kept.append(item)
    return rank_by_distance(kept)


def rank_by_distance(rows):
    return sorted(
        rows or [],
        key=lambda item: (
            item.get("distance_meters") is None,
            item.get("distance_meters") if item.get("distance_meters") is not None else 0,
            item.get("id") or 0,
        ),
    )


def format_distance(meters, language="es"):
    if meters is None:
        return ""
    try:
        value = float(meters)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value < 1000:
        rounded = int(round(value))
        return f"{rounded} m"
    km = value / 1000.0
    if km < 10:
        text = f"{km:.1f}".rstrip("0").rstrip(".")
    else:
        text = f"{km:.0f}"
    if language == "es":
        text = text.replace(".", ",")
    return f"{text} km"


def need_geo_ratio(distance_m, radius_m):
    """Zone-dimension ratio for a Need with a required radius.

    0–25% of radius → 1.00
    25–50% → 0.85
    50–75% → 0.60
    75–100% → 0.35
    outside or unknown → None (hard fail)
    """
    if distance_m is None or radius_m in (None, 0):
        return None
    try:
        distance = float(distance_m)
        radius = float(radius_m)
    except (TypeError, ValueError):
        return None
    if radius <= 0 or distance < 0 or distance > radius:
        return None
    fraction = distance / radius
    for ceiling, ratio in NEED_RADIUS_BANDS:
        if fraction <= ceiling:
            return ratio
    return None


def acm_geo_ratio(distance_m, bands=None, beyond=None):
    """Zone-dimension ratio for ACM when both points have coordinates."""
    if distance_m is None:
        return None
    try:
        distance = float(distance_m)
    except (TypeError, ValueError):
        return None
    if distance < 0:
        return None
    table = bands or ACM_DISTANCE_BANDS_M
    for ceiling, ratio in table:
        if distance <= ceiling:
            return ratio
    return ACM_DISTANCE_BEYOND_RATIO if beyond is None else beyond


def need_geo_from_preferences(prefs):
    prefs = prefs or {}
    lat = _lat(prefs.get("center_latitude"))
    lng = _lng(prefs.get("center_longitude"))
    radius_km = parse_radius_km(prefs.get("radius_km"))
    if lat is None or lng is None or radius_km is None:
        return None
    return {
        "center_latitude": lat,
        "center_longitude": lng,
        "radius_km": radius_km,
        "radius_m": radius_km * 1000.0,
        "location_reference_text": (prefs.get("location_reference_text") or "").strip() or None,
        "location_place_id": (prefs.get("location_place_id") or "").strip() or None,
        "location_property_id": prefs.get("location_property_id"),
    }
