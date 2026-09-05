"""Canonical listing sources and search/sync capabilities."""

from __future__ import annotations


SOURCE_INTERNAL = "internal"
SOURCE_REMAX = "remax"
SOURCE_ZONAPROP = "zonaprop"
SOURCE_ARGENPROP = "argenprop"
SOURCE_MERCADOLIBRE = "mercadolibre"

LISTING_SOURCES = (
    SOURCE_INTERNAL,
    SOURCE_REMAX,
    SOURCE_ZONAPROP,
    SOURCE_ARGENPROP,
    SOURCE_MERCADOLIBRE,
)

LISTING_SOURCE_SET = set(LISTING_SOURCES)

SEARCH_ENABLED = "enabled"
SEARCH_INDEXED = "indexed"
SEARCH_NOT_AUTHORIZED = "not_authorized"
SEARCH_UNSUPPORTED_SEARCH = "unsupported_search"
SEARCH_NOT_CONFIGURED = "not_configured"
SEARCH_UNAVAILABLE = "unavailable"

SYNC_INTERNAL = "internal"
SYNC_MANUAL_IMPORT = "manual_import"
SYNC_NONE = "none"

STATUS_ENABLED = SEARCH_ENABLED
STATUS_INDEXED = SEARCH_INDEXED
STATUS_NOT_AUTHORIZED = SEARCH_NOT_AUTHORIZED
STATUS_UNSUPPORTED_SEARCH = SEARCH_UNSUPPORTED_SEARCH
STATUS_NOT_CONFIGURED = SEARCH_NOT_CONFIGURED
STATUS_UNAVAILABLE = SEARCH_UNAVAILABLE
STATUS_IMPORT_ONLY = SEARCH_INDEXED
STATUS_FIXTURE = SEARCH_NOT_AUTHORIZED

SOURCE_CAPABILITIES = {
    SOURCE_INTERNAL: {
        "search": SEARCH_ENABLED,
        "sync": SYNC_INTERNAL,
        "visible_in_match": True,
    },
    SOURCE_REMAX: {
        "search": SEARCH_INDEXED,
        "sync": SYNC_MANUAL_IMPORT,
        "visible_in_match": True,
    },
    SOURCE_ZONAPROP: {
        "search": SEARCH_UNSUPPORTED_SEARCH,
        "sync": SYNC_NONE,
        "visible_in_match": False,
    },
    SOURCE_ARGENPROP: {
        "search": SEARCH_UNSUPPORTED_SEARCH,
        "sync": SYNC_NONE,
        "visible_in_match": False,
    },
    SOURCE_MERCADOLIBRE: {
        "search": SEARCH_NOT_AUTHORIZED,
        "sync": SYNC_NONE,
        "visible_in_match": False,
    },
}

SOURCE_SEARCH_STATUS = {
    source: spec["search"] for source, spec in SOURCE_CAPABILITIES.items()
}

SOURCE_LABEL_KEYS = {
    SOURCE_INTERNAL: "listing_source_internal",
    SOURCE_REMAX: "listing_source_remax",
    SOURCE_ZONAPROP: "listing_source_zonaprop",
    SOURCE_ARGENPROP: "listing_source_argenprop",
    SOURCE_MERCADOLIBRE: "listing_source_mercadolibre",
}

SEARCH_LABEL_KEYS = {
    SEARCH_ENABLED: "listing_search_enabled",
    SEARCH_INDEXED: "listing_search_indexed",
    SEARCH_NOT_AUTHORIZED: "listing_search_not_authorized",
    SEARCH_UNSUPPORTED_SEARCH: "listing_search_unsupported",
    SEARCH_NOT_CONFIGURED: "listing_search_not_configured",
    SEARCH_UNAVAILABLE: "listing_search_unavailable",
}

SYNC_LABEL_KEYS = {
    SYNC_INTERNAL: "listing_sync_internal",
    SYNC_MANUAL_IMPORT: "listing_sync_manual_import",
    SYNC_NONE: "listing_sync_none",
}


class UnknownListingSource(ValueError):
    pass


def normalize_listing_source(value, *, default=None):
    source = str(value or "").strip().lower()
    if not source:
        return default
    if source == "remax_web":
        return SOURCE_REMAX
    if source not in LISTING_SOURCE_SET:
        raise UnknownListingSource(source)
    return source


def require_listing_source(value):
    source = normalize_listing_source(value)
    if source is None:
        raise UnknownListingSource(value)
    return source


def source_capability(source):
    normalized = normalize_listing_source(source, default=source)
    spec = SOURCE_CAPABILITIES.get(normalized)
    if spec is None:
        spec = {
            "search": SEARCH_UNSUPPORTED_SEARCH,
            "sync": SYNC_NONE,
            "visible_in_match": False,
        }
    search = spec["search"]
    sync = spec["sync"]
    return {
        "source": normalized,
        "search": search,
        "sync": sync,
        "visible_in_match": bool(spec["visible_in_match"]),
        "label_key": SOURCE_LABEL_KEYS.get(
            normalized,
            "listing_source_internal",
        ),
        "search_label_key": SEARCH_LABEL_KEYS.get(
            search,
            "listing_search_unsupported",
        ),
        "sync_label_key": SYNC_LABEL_KEYS.get(sync, "listing_sync_none"),
    }


def listing_source_capabilities():
    return {
        source: source_capability(source)
        for source in LISTING_SOURCES
    }


def match_visible_sources():
    return [
        source
        for source in LISTING_SOURCES
        if source_capability(source)["visible_in_match"]
    ]


def source_search_status(source):
    return source_capability(source)["search"]


def source_supports_search(source):
    return source_search_status(source) in (
        SEARCH_ENABLED,
        SEARCH_INDEXED,
    )
