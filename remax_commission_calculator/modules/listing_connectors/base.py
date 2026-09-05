"""Common contract for listing sources. No HTTP here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from modules.listing_sources import (
    SEARCH_ENABLED,
    SEARCH_INDEXED,
    source_search_status,
)
from modules.listings_normalize import normalize_listing


@dataclass(frozen=True)
class ListingSearchResult:
    status: str
    listings: list = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self):
        return self.status in (SEARCH_ENABLED, SEARCH_INDEXED)


class ListingConnectorError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


class ListingConnector(Protocol):
    source: str

    def search(self, criteria, *, organization_id, agent_id=None):
        ...

    def fetch(self, external_id, *, organization_id):
        ...

    def sync(self, payload, *, organization_id):
        ...

    def normalize(self, record):
        ...


class BaseListingConnector:
    source = ""

    def search_status(self):
        return source_search_status(self.source)

    def search(self, criteria, *, organization_id, agent_id=None):
        return ListingSearchResult(status=self.search_status(), listings=[])

    def fetch(self, external_id, *, organization_id):
        return None

    def sync(self, payload, *, organization_id):
        raise ListingConnectorError("listing_connector_sync_not_implemented")

    def normalize(self, record):
        return normalize_listing(record, source=self.source)
