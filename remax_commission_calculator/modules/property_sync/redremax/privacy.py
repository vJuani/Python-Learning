"""RedREMAX privacy helpers. Do not invent public-publish rights from exposure."""

from __future__ import annotations

SENSITIVE_KEYS = frozenset(
    {
        "clients",
        "clientsdata",
        "documents",
        "privatenotes",
        "authorization",
        "cookie",
        "bearer",
        "jsessionid",
    }
)


def is_external_price_publicly_usable(property_row):
    """V1: never claim brochure/social/portal rights from price.exposure."""
    return False


def is_sensitive_key(name):
    return str(name or "").strip().lower() in SENSITIVE_KEYS


def strip_sensitive_fields(payload):
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if not is_sensitive_key(key)
    }
