"""Internal inventory connector. Uses properties, never HTTP."""

from __future__ import annotations

from modules.database.properties_repository import (
    get_property_record,
    list_match_candidates,
)
from modules.listing_connectors.base import BaseListingConnector, ListingSearchResult
from modules.listing_sources import SOURCE_INTERNAL, STATUS_ENABLED
from modules.listings_normalize import attach_listing_identity, listing_from_property


class InternalListingConnector(BaseListingConnector):
    source = SOURCE_INTERNAL

    def search(self, criteria, *, organization_id, agent_id=None):
        from modules.property_match import (
            CANDIDATE_LIMIT,
            query_filters_from_criteria,
        )

        filters = query_filters_from_criteria(criteria)
        rows = list_match_candidates(
            organization_id,
            agent_id=agent_id,
            property_types=filters["property_types"] or None,
            listing_purpose=filters["listing_purpose"],
            listing_currency=filters["listing_currency"],
            max_listing_price=filters["max_listing_price"],
            limit=CANDIDATE_LIMIT,
        )
        listings = []
        for row in rows:
            listing = attach_listing_identity(
                listing_from_property(row),
                property_id=row.get("id"),
            )
            listing["status"] = row.get("status")
            listings.append(listing)
        return ListingSearchResult(status=STATUS_ENABLED, listings=listings)

    def fetch(self, external_id, *, organization_id):
        try:
            property_id = int(external_id)
        except (TypeError, ValueError):
            return None
        row = get_property_record(property_id, organization_id)
        if row is None:
            return None
        listing = attach_listing_identity(
            listing_from_property(row),
            property_id=row["id"],
        )
        listing["status"] = row.get("status")
        return listing

    def normalize(self, record):
        listing = listing_from_property(record)
        return attach_listing_identity(listing, property_id=record.get("id"))
