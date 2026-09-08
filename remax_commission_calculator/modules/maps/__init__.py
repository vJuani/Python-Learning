"""Maps V1: property location, Google Places autocomplete, and Maps links."""

from modules.maps.config import (
    GEOCODE_MANUAL,
    GEOCODE_RESOLVED,
    GEOCODE_STALE,
    GEOCODE_UNRESOLVED,
    get_maps_provider_name,
    maps_browser_key,
    maps_is_configured,
    maps_region,
)
from modules.maps.links import (
    build_directions_url,
    build_open_maps_url,
)
from modules.maps.location import (
    addresses_differ_substantially,
    apply_place_to_location,
    attach_property_maps,
    has_coordinates,
    location_from_form,
    parse_address_components,
    parse_coordinate,
)
from modules.maps.provider import get_maps_provider

__all__ = (
    "GEOCODE_MANUAL",
    "GEOCODE_RESOLVED",
    "GEOCODE_STALE",
    "GEOCODE_UNRESOLVED",
    "addresses_differ_substantially",
    "apply_place_to_location",
    "attach_property_maps",
    "build_directions_url",
    "build_open_maps_url",
    "get_maps_provider",
    "get_maps_provider_name",
    "has_coordinates",
    "location_from_form",
    "maps_browser_key",
    "maps_is_configured",
    "maps_region",
    "parse_address_components",
    "parse_coordinate",
)
