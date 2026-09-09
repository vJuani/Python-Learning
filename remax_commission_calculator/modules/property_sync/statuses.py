"""Map provider listing statuses onto JRH commercial_status. No new states."""

from __future__ import annotations

from modules.property_types import COMMERCIAL_STATUSES

STATUS_ALIASES = {
    "available": "available",
    "active": "available",
    "published": "available",
    "online": "available",
    "reserved": "reserved",
    "reservada": "reserved",
    "sold": "sold",
    "vendida": "sold",
    "rented": "rented",
    "alquilada": "rented",
    "paused": "withdrawn",
    "paused_listing": "withdrawn",
    "archived": "withdrawn",
    "unpublished": "withdrawn",
    "inactive": "withdrawn",
    "unavailable": "withdrawn",
    "deleted": "withdrawn",
    "withdrawn": "withdrawn",
}


def map_external_status(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return "available", None
    mapped = STATUS_ALIASES.get(raw)
    if mapped in COMMERCIAL_STATUSES:
        return mapped, raw
    if raw in COMMERCIAL_STATUSES:
        return raw, raw
    return None, raw


def is_deleted_status(value):
    raw = str(value or "").strip().lower()
    return raw in {"deleted", "unpublished", "archived", "inactive"}
