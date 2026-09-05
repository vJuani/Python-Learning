"""RE/MAX Red export: office import stays separate; catalog feeds external_listings."""

from __future__ import annotations

from modules.listing_connectors.base import BaseListingConnector, ListingSearchResult
from modules.listing_sources import (
    SEARCH_INDEXED,
    SOURCE_REMAX,
)
from modules.listings_normalize import attach_listing_identity, normalize_listing


CATALOG_STATUS_TO_COMMERCIAL = {
    "active": "available",
    "reserved": "reserved",
    "negotiation": "available",
}


class RemaxExportConnector(BaseListingConnector):
    source = SOURCE_REMAX

    def search(self, criteria, *, organization_id, agent_id=None):
        from modules.database.external_listings_repository import (
            list_active_external_listings,
        )
        from modules.listings_normalize import listing_from_external_listing
        from modules.property_match import CANDIDATE_LIMIT

        listings = []
        for row in list_active_external_listings(
            organization_id,
            source=SOURCE_REMAX,
            limit=CANDIDATE_LIMIT,
        ):
            listing = attach_listing_identity(
                listing_from_external_listing(row),
                external_listing_id=row["id"],
            )
            listing["is_active"] = row.get("is_active")
            listings.append(listing)
        return ListingSearchResult(status=SEARCH_INDEXED, listings=listings)

    def fetch(self, external_id, *, organization_id):
        from modules.database.external_listings_repository import (
            get_external_listing_by_source_id,
        )
        from modules.listings_normalize import listing_from_external_listing

        row = get_external_listing_by_source_id(
            organization_id,
            SOURCE_REMAX,
            external_id,
        )
        if row is None:
            return None
        listing = attach_listing_identity(
            listing_from_external_listing(row),
            external_listing_id=row["id"],
        )
        listing["is_active"] = row.get("is_active")
        return listing

    def record_from_source_row(self, row):
        return {
            "source": SOURCE_REMAX,
            "external_id": row.mlsid,
            "external_url": row.url or None,
            "address": row.address or None,
            "neighborhood": row.locality or None,
            "jurisdiction": row.jurisdiction,
            "property_type": row.property_type,
            "purpose": row.listing_purpose,
            "price": row.price,
            "currency": row.currency,
            "rooms": row.rooms,
            "bedrooms": row.bedrooms,
            "bathrooms": row.bathrooms,
            "covered_m2": row.covered_m2,
            "total_m2": row.total_m2,
            "parking_spaces": row.parking_spaces,
            "features": None,
            "description": row.description,
            "images": [],
            "commercial_status": CATALOG_STATUS_TO_COMMERCIAL.get(row.status),
        }

    def normalize(self, record):
        if hasattr(record, "mlsid"):
            record = self.record_from_source_row(record)
        return normalize_listing(
            source=SOURCE_REMAX,
            external_id=record.get("external_id") or record.get("mlsid"),
            external_url=record.get("url") or record.get("external_url"),
            address=record.get("address"),
            neighborhood=record.get("neighborhood") or record.get("locality"),
            jurisdiction=record.get("jurisdiction"),
            property_type=record.get("property_type"),
            purpose=record.get("listing_purpose") or record.get("purpose"),
            price=record.get("listing_price") or record.get("price"),
            currency=record.get("listing_currency") or record.get("currency"),
            rooms=record.get("rooms"),
            bedrooms=record.get("bedrooms"),
            bathrooms=record.get("bathrooms"),
            covered_m2=record.get("covered_m2"),
            total_m2=record.get("total_m2"),
            parking_spaces=record.get("parking_spaces"),
            features=record.get("features"),
            description=record.get("description"),
            images=record.get("images"),
            commercial_status=record.get("commercial_status"),
        )
