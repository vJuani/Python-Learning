"""Mercado Libre connector. Fixtures only — no live HTTP."""

from __future__ import annotations

from modules.listing_connectors.base import BaseListingConnector, ListingSearchResult
from modules.listing_sources import (
    SEARCH_NOT_AUTHORIZED,
    SOURCE_MERCADOLIBRE,
)
from modules.listings_normalize import attach_listing_identity, normalize_listing


ML_FIXTURES = (
    {
        "id": "MLA111",
        "permalink": "https://departamento.mercadolibre.com.ar/MLA111-cabildo",
        "title": "Departamento en Belgrano",
        "price": 185000,
        "currency_id": "USD",
        "location": {"neighborhood": "Belgrano", "address_line": "Av. Cabildo 3200"},
        "attributes": [
            {"id": "ROOMS", "value_name": "3"},
            {"id": "BEDROOMS", "value_name": "2"},
            {"id": "FULL_BATHROOMS", "value_name": "1"},
            {"id": "COVERED_AREA", "value_name": "84"},
        ],
        "pictures": [{"url": "https://http2.mlstatic.com/fixture-cabildo.jpg"}],
        "neighborhood": "Belgrano",
        "address": "Av. Cabildo 3200",
        "property_type": "apartment",
        "purpose": "sale",
        "features": {"balcony": True},
    },
)


def _attribute_map(record):
    values = {}
    for item in record.get("attributes") or []:
        key = str(item.get("id") or "").strip()
        if key:
            values[key] = item.get("value_name") or item.get("value_struct")
    return values


class MercadoLibreConnector(BaseListingConnector):
    source = SOURCE_MERCADOLIBRE

    def __init__(self, fixtures=None):
        self.fixtures = list(fixtures if fixtures is not None else ML_FIXTURES)

    def search(self, criteria, *, organization_id, agent_id=None):
        return ListingSearchResult(
            status=SEARCH_NOT_AUTHORIZED,
            listings=[],
        )

    def fetch(self, external_id, *, organization_id):
        for record in self.fixtures:
            if str(record.get("id")) == str(external_id):
                return self.normalize(record)
        return None

    def normalize(self, record):
        attrs = _attribute_map(record)
        location = record.get("location") or {}
        listing = normalize_listing(
            source=SOURCE_MERCADOLIBRE,
            external_id=record.get("id") or record.get("external_id"),
            external_url=record.get("permalink") or record.get("external_url"),
            address=record.get("address") or location.get("address_line"),
            neighborhood=record.get("neighborhood") or location.get("neighborhood"),
            jurisdiction=record.get("jurisdiction"),
            property_type=record.get("property_type") or "apartment",
            purpose=record.get("purpose") or "sale",
            price=record.get("price"),
            currency=record.get("currency") or record.get("currency_id"),
            rooms=record.get("rooms") or attrs.get("ROOMS"),
            bedrooms=record.get("bedrooms") or attrs.get("BEDROOMS"),
            bathrooms=record.get("bathrooms") or attrs.get("FULL_BATHROOMS"),
            covered_m2=record.get("covered_m2") or attrs.get("COVERED_AREA"),
            total_m2=record.get("total_m2") or attrs.get("TOTAL_AREA"),
            parking_spaces=record.get("parking_spaces") or attrs.get("PARKING_LOTS"),
            features=record.get("features"),
            description=record.get("description") or record.get("title"),
            images=[
                item.get("url")
                for item in (record.get("pictures") or record.get("images") or [])
                if isinstance(item, dict) and item.get("url")
            ],
            commercial_status=record.get("commercial_status") or "available",
        )
        return attach_listing_identity(listing)
