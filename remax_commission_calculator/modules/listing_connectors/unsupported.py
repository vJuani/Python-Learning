"""Portals without an authorized search integration."""

from __future__ import annotations

from modules.listing_connectors.base import BaseListingConnector, ListingSearchResult
from modules.listing_sources import (
    SOURCE_ARGENPROP,
    SOURCE_ZONAPROP,
    STATUS_UNSUPPORTED_SEARCH,
)


class UnsupportedSearchConnector(BaseListingConnector):
    def __init__(self, source):
        self.source = source

    def search(self, criteria, *, organization_id, agent_id=None):
        return ListingSearchResult(status=STATUS_UNSUPPORTED_SEARCH, listings=[])

    def fetch(self, external_id, *, organization_id):
        return None

    def normalize(self, record):
        return None


def zonaprop_connector():
    return UnsupportedSearchConnector(SOURCE_ZONAPROP)


def argenprop_connector():
    return UnsupportedSearchConnector(SOURCE_ARGENPROP)
