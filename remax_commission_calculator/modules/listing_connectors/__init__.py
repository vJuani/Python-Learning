from modules.listing_connectors.base import (
    BaseListingConnector,
    ListingConnector,
    ListingConnectorError,
    ListingSearchResult,
)
from modules.listing_connectors.internal import InternalListingConnector
from modules.listing_connectors.mercadolibre import MercadoLibreConnector
from modules.listing_connectors.remax_export import RemaxExportConnector
from modules.listing_connectors.unsupported import (
    UnsupportedSearchConnector,
    argenprop_connector,
    zonaprop_connector,
)
from modules.listing_sources import (
    SOURCE_ARGENPROP,
    SOURCE_INTERNAL,
    SOURCE_MERCADOLIBRE,
    SOURCE_REMAX,
    SOURCE_ZONAPROP,
    listing_source_capabilities,
    source_capability,
)


def get_listing_connector(source):
    if source == SOURCE_INTERNAL:
        return InternalListingConnector()
    if source == SOURCE_REMAX:
        return RemaxExportConnector()
    if source == SOURCE_MERCADOLIBRE:
        return MercadoLibreConnector()
    if source == SOURCE_ZONAPROP:
        return zonaprop_connector()
    if source == SOURCE_ARGENPROP:
        return argenprop_connector()
    return UnsupportedSearchConnector(source)


__all__ = [
    "BaseListingConnector",
    "InternalListingConnector",
    "ListingConnector",
    "ListingConnectorError",
    "ListingSearchResult",
    "MercadoLibreConnector",
    "RemaxExportConnector",
    "get_listing_connector",
    "listing_source_capabilities",
    "source_capability",
]
