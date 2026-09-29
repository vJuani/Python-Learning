"""Read-only market comparables for ACM. Never creates properties."""

from modules.database.external_listings_repository import list_active_external_listings
from modules.database.tenant import require_organization_id
from modules.listing_sources import MARKET_COMPARABLE_SOURCES


def search_market_comparables(organization_id, *, sources=None, limit=50):
    """Return portal listings for analysis. They are not office inventory."""
    organization_id = require_organization_id(organization_id)
    chosen = [
        source
        for source in (sources or MARKET_COMPARABLE_SOURCES)
        if source in MARKET_COMPARABLE_SOURCES
    ]
    rows = []
    for source in chosen:
        for listing in list_active_external_listings(
            organization_id,
            source=source,
            limit=limit,
        ):
            rows.append(
                {
                    "external_source": listing.get("source"),
                    "external_url": listing.get("external_url"),
                    "external_listing_id": listing.get("external_id"),
                    "address": listing.get("address"),
                    "neighborhood": listing.get("neighborhood"),
                    "property_type": listing.get("property_type"),
                    "price": listing.get("price"),
                    "currency": listing.get("currency"),
                    "rooms": listing.get("rooms"),
                    "covered_m2": listing.get("covered_m2"),
                }
            )
            if len(rows) >= limit:
                return rows
    return rows
