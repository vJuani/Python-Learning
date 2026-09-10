"""Explicit RedREMAX → JRH maps. Unknown values become warnings, not silent guesses."""

from __future__ import annotations

from modules.property_types import LISTING_PURPOSES, PROPERTY_TYPES


PROVIDER_REDREMAX = "redremax"
DEFAULT_API_BASE_URL = "https://api-ar.redremax.com"
REDREMAX_ALLOWED_IMAGE_HOSTS = frozenset(
    {
        "redremax-images.s3.amazonaws.com",
        "redremax-images.s3-us-west-1.amazonaws.com",
    }
)
PHOTO_HOST_ALLOWLIST = REDREMAX_ALLOWED_IMAGE_HOSTS
LOCATION_SOURCE = "external_redremax"

# Observed: "sale". JRH listing_purpose stays "sale" (UI label: Venta).
# Prepared possibles only — unknown stays original + warning.
REDREMAX_OPERATION_MAP = {
    "sale": "sale",
    "venta": "sale",
    "rental": "rental",
    "rent": "rental",
    "alquiler": "rental",
    "temporary_rental": "temporary_rental",
}

# Observed: "Terrenos y Lotes". Additional labels are explicit, not inferred per-row.
REDREMAX_PROPERTY_TYPE_MAP = {
    "terrenos y lotes": "land",
    "terreno": "land",
    "lote": "land",
    "departamento": "apartment",
    "departamentos": "apartment",
    "casa": "house",
    "casas": "house",
    "ph": "ph",
    "local": "commercial",
    "local comercial": "commercial",
    "oficina": "office",
    "oficinas": "office",
}


def map_operation_type(value):
    raw = str(value or "").strip()
    if not raw:
        return None, raw, None
    mapped = REDREMAX_OPERATION_MAP.get(raw.lower())
    if mapped in LISTING_PURPOSES:
        return mapped, raw, None
    return None, raw, "redremax_warn_unknown_operation"


def map_property_type(value):
    raw = str(value or "").strip()
    if not raw:
        return None, raw, None
    mapped = REDREMAX_PROPERTY_TYPE_MAP.get(raw.lower())
    if mapped in PROPERTY_TYPES:
        return mapped, raw, None
    return "other", raw, "redremax_warn_unknown_property_type"
