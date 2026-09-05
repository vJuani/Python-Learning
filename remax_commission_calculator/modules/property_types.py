PROPERTY_TYPES = (
    "apartment",
    "house",
    "ph",
    "land",
    "commercial",
    "office",
    "other",
)

LISTING_PURPOSES = (
    "sale",
    "rental",
    "temporary_rental",
)

LISTING_CURRENCIES = (
    "USD",
    "ARS",
)

COMMERCIAL_STATUSES = (
    "available",
    "reserved",
    "sold",
    "rented",
    "withdrawn",
)

COMMERCIAL_STATUS_AVAILABLE = "available"

PROPERTY_TYPE_ALIASES = {
    "apartment": "apartment",
    "departamento": "apartment",
    "depto": "apartment",
    "dpto": "apartment",
    "house": "house",
    "casa": "house",
    "ph": "ph",
    "land": "land",
    "terreno": "land",
    "lote": "land",
    "commercial": "commercial",
    "local": "commercial",
    "office": "office",
    "oficina": "office",
    "other": "other",
    "otro": "other",
}

LISTING_PURPOSE_ALIASES = {
    "sale": "sale",
    "venta": "sale",
    "rental": "rental",
    "alquiler": "rental",
    "rent": "rental",
    "temporary_rental": "temporary_rental",
    "temporario": "temporary_rental",
}


def normalize_property_type(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    return PROPERTY_TYPE_ALIASES.get(raw, raw if raw in PROPERTY_TYPES else None)


def normalize_listing_purpose(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    return LISTING_PURPOSE_ALIASES.get(
        raw,
        raw if raw in LISTING_PURPOSES else None,
    )


INVOICE_FULL_COMMISSION_VALUES = (
    "yes",
    "no",
)


def is_valid_listing_purpose(value):
    return value in LISTING_PURPOSES


def is_valid_listing_currency(value):
    return value in (None, "") or value in LISTING_CURRENCIES


def is_valid_commercial_status(value):
    return value in (None, "") or value in COMMERCIAL_STATUSES
