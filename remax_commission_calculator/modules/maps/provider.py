"""Map provider abstraction. V1 only needs capability + place normalization."""

from __future__ import annotations

from modules.maps.config import (
    PROVIDER_GOOGLE,
    PROVIDER_MOCK,
    get_maps_provider_name,
    maps_browser_key,
    maps_is_configured,
    maps_public_config,
    maps_region,
    maps_server_key,
)
from modules.maps.location import apply_place_to_location, parse_coordinate


class MapProvider:
    name = PROVIDER_GOOGLE

    def is_configured(self):
        return maps_is_configured()

    def public_config(self):
        return maps_public_config()

    def region(self):
        return maps_region()

    def normalize_place(self, place, *, geocoded_at=None):
        return apply_place_to_location(place, geocoded_at=geocoded_at)

    def find_place(self, query, *, region=None):
        """Transient geocode for an explicit search. Never used for distance."""
        return None


class GoogleMapsProvider(MapProvider):
    name = PROVIDER_GOOGLE

    def browser_key(self):
        return maps_browser_key()

    def find_place(self, query, *, region=None):
        text = " ".join(str(query or "").split())
        if not text:
            return None
        key = maps_server_key()
        if not key:
            return None
        import json
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode(
            {
                "address": text,
                "key": key,
                "region": (region or self.region() or "ar").lower(),
            }
        )
        url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, TimeoutError):
            return None
        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            return None
        first = results[0] or {}
        geometry = (first.get("geometry") or {}).get("location") or {}
        lat = parse_coordinate(geometry.get("lat"), kind="lat")
        lng = parse_coordinate(geometry.get("lng"), kind="lng")
        if lat is None or lng is None:
            return None
        return {
            "place_id": first.get("place_id"),
            "formatted_address": first.get("formatted_address"),
            "latitude": lat,
            "longitude": lng,
            "address_components": first.get("address_components") or [],
        }


class MockMapsProvider(MapProvider):
    name = PROVIDER_MOCK

    def is_configured(self):
        return True

    def public_config(self):
        config = maps_public_config()
        config["configured"] = True
        config["autocomplete_enabled"] = False
        config["embed_enabled"] = False
        config["browser_key"] = ""
        return config

    def browser_key(self):
        return ""


class DisabledMapsProvider(MapProvider):
    name = "none"

    def is_configured(self):
        return False

    def public_config(self):
        return {
            "provider": "none",
            "configured": False,
            "autocomplete_enabled": False,
            "embed_enabled": False,
            "browser_key": "",
            "region": maps_region().lower(),
        }

    def browser_key(self):
        return ""


def get_maps_provider():
    name = get_maps_provider_name()
    if name == PROVIDER_MOCK:
        return MockMapsProvider()
    if name == PROVIDER_GOOGLE and maps_browser_key():
        return GoogleMapsProvider()
    return DisabledMapsProvider()


def find_place(query, *, region=None):
    """Explicit search lookup. Distance scoring never goes through here."""
    return get_maps_provider().find_place(query, region=region)
