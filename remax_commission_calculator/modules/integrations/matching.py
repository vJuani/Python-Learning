"""
Safe matching for synced agents/properties.
Never auto-match by name alone.
"""

from __future__ import annotations

from modules.database.agents_repository import (
    find_agent_by_external_id,
)
from modules.database.property_external_listings_repository import (
    find_listing_by_external_id,
)


def match_agent_by_external_id(
    organization_id,
    external_provider,
    external_id,
):
    if not external_provider or not external_id:
        return None

    return find_agent_by_external_id(
        organization_id,
        external_provider,
        external_id,
    )


def match_listing_by_external_id(
    organization_id,
    listing_provider,
    external_id,
):
    if not listing_provider or not external_id:
        return None

    return find_listing_by_external_id(
        organization_id,
        listing_provider,
        external_id,
    )
