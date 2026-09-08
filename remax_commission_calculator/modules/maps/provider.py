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
)
from modules.maps.location import apply_place_to_location


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


class GoogleMapsProvider(MapProvider):
    name = PROVIDER_GOOGLE

    def browser_key(self):
        return maps_browser_key()


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
