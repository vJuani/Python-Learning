"""Google Maps destination links. No API key and no user-origin tracking."""

from __future__ import annotations

from urllib.parse import quote


def _query_text(row):
    row = row or {}
    return (
        (row.get("formatted_address") or "").strip()
        or (row.get("address") or "").strip()
        or (row.get("property_address") or "").strip()
    )


def _has_coords(row):
    try:
        lat = float(row.get("latitude"))
        lng = float(row.get("longitude"))
    except (TypeError, ValueError):
        return False
    return lat == lat and lng == lng


def build_open_maps_url(row):
    row = row or {}
    place_id = (row.get("google_place_id") or "").strip()
    if place_id:
        return (
            "https://www.google.com/maps/search/?api=1"
            f"&query={quote(_query_text(row) or place_id)}"
            f"&query_place_id={quote(place_id)}"
        )
    if _has_coords(row):
        lat = float(row["latitude"])
        lng = float(row["longitude"])
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    query = _query_text(row)
    if not query:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"


def build_directions_url(row):
    """Directions with destination only. Google Maps resolves the origin."""
    row = row or {}
    place_id = (row.get("google_place_id") or "").strip()
    if place_id:
        destination = _query_text(row) or place_id
        return (
            "https://www.google.com/maps/dir/?api=1"
            f"&destination={quote(destination)}"
            f"&destination_place_id={quote(place_id)}"
        )
    if _has_coords(row):
        lat = float(row["latitude"])
        lng = float(row["longitude"])
        return (
            "https://www.google.com/maps/dir/?api=1"
            f"&destination={lat},{lng}"
        )
    query = _query_text(row)
    if not query:
        return ""
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(query)}"
