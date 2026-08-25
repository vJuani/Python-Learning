"""
Sync engine: apply external DTOs to local tenant data.
"""

from __future__ import annotations

from datetime import datetime

from modules.database.agents_repository import (
    add_agent,
    update_agent_from_sync,
)
from modules.database.organization_integrations_repository import (
    SCOPE_AGENT,
    SCOPE_ORGANIZATION,
    SYNC_STATUS_FAILED,
    SYNC_STATUS_OK,
    finish_integration_sync_run,
    start_integration_sync_run,
    update_integration_sync_state,
    STATUS_CONNECTED,
    STATUS_ERROR,
)
from modules.database.properties_repository import (
    STATUS_APPROVED,
    add_property,
    update_property_from_sync,
)
from modules.database.property_external_listings_repository import (
    create_property_external_listing,
    list_synced_listings_for_provider,
    mark_listing_inactive,
    update_property_external_listing,
)
from modules.database.tenant import TenantError
from modules.integrations.matching import (
    match_agent_by_external_id,
    match_listing_by_external_id,
)
from modules.integrations.registry import get_adapter
from modules.integrations.types import SyncResult


DEFAULT_AGENT_TYPE = "Alto"


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _listing_key(listing_provider, external_id):
    return f"{listing_provider}:{external_id}"


def run_sync(integration: dict) -> SyncResult:
    organization_id = integration["organization_id"]
    integration_id = integration["id"]
    provider = integration["provider"]
    scope_type = integration["scope_type"]
    synced_at = _now_iso()

    run_id = start_integration_sync_run(
        organization_id,
        integration_id,
    )

    result = SyncResult(
        integration_id=integration_id,
        organization_id=organization_id,
        run_id=run_id,
        status=SYNC_STATUS_OK,
    )

    try:
        adapter = get_adapter(provider)
        agent_map = {}

        if scope_type == SCOPE_ORGANIZATION:
            for external_agent in adapter.list_agents(
                integration
            ):
                local = match_agent_by_external_id(
                    organization_id,
                    provider,
                    external_agent.external_id,
                )

                if local is None:
                    agent_id = add_agent(
                        external_agent.full_name,
                        DEFAULT_AGENT_TYPE,
                        organization_id,
                        external_provider=provider,
                        external_id=external_agent.external_id,
                        last_synced_at=synced_at,
                    )
                    result.agents_created += 1
                else:
                    agent_id = local["id"]
                    update_agent_from_sync(
                        agent_id,
                        organization_id,
                        name=external_agent.full_name,
                        external_provider=provider,
                        external_id=external_agent.external_id,
                        last_synced_at=synced_at,
                    )
                    result.agents_updated += 1

                agent_map[external_agent.external_id] = (
                    agent_id
                )

        elif scope_type == SCOPE_AGENT:
            anchor_id = integration.get("agent_id")

            if anchor_id is None:
                raise ValueError("agent_scope_missing_agent")

            agent_map["__anchor__"] = anchor_id

        else:
            raise ValueError("invalid_integration_scope")

        for external_property in adapter.list_properties(
            integration
        ):
            if scope_type == SCOPE_ORGANIZATION:
                local_agent_id = agent_map.get(
                    external_property.agent_external_id
                )

                if local_agent_id is None:
                    # Property refers to unknown remote agent:
                    # skip rather than inventing ownership.
                    continue
            else:
                local_agent_id = agent_map["__anchor__"]

            existing_listing = match_listing_by_external_id(
                organization_id,
                external_property.listing_provider,
                external_property.external_id,
            )

            if existing_listing is None:
                property_id = add_property(
                    external_property.address,
                    external_property.jurisdiction,
                    organization_id,
                    agent_id=local_agent_id,
                    status=STATUS_APPROVED,
                    property_type=external_property.property_type,
                    listing_price=external_property.listing_price,
                    listing_purpose=(
                        external_property.listing_purpose
                    ),
                    last_synced_at=synced_at,
                )
                result.properties_created += 1

                create_property_external_listing(
                    organization_id,
                    property_id,
                    external_property.listing_provider,
                    external_property.url,
                    external_property.listing_status,
                    external_id=external_property.external_id,
                    last_synced_at=synced_at,
                    listing_currency=(
                        external_property.listing_currency
                    ),
                    buyer_side_commission_percent=(
                        external_property.buyer_side_commission_percent
                    ),
                    seller_side_commission_percent=(
                        external_property.seller_side_commission_percent
                    ),
                )
                result.listings_created += 1
            else:
                if (
                    existing_listing["organization_id"]
                    != organization_id
                ):
                    raise TenantError(
                        "Listing belongs to another organization."
                    )

                property_id = existing_listing["property_id"]
                update_property_from_sync(
                    property_id,
                    organization_id,
                    address=external_property.address,
                    jurisdiction=external_property.jurisdiction,
                    agent_id=local_agent_id,
                    property_type=(
                        external_property.property_type
                    ),
                    listing_price=(
                        external_property.listing_price
                    ),
                    listing_purpose=(
                        external_property.listing_purpose
                    ),
                    last_synced_at=synced_at,
                )
                result.properties_updated += 1

                update_property_external_listing(
                    existing_listing["id"],
                    organization_id,
                    provider=external_property.listing_provider,
                    url=external_property.url,
                    status=external_property.listing_status,
                    external_id=external_property.external_id,
                    last_synced_at=synced_at,
                    listing_currency=(
                        external_property.listing_currency
                    ),
                    buyer_side_commission_percent=(
                        external_property.buyer_side_commission_percent
                    ),
                    seller_side_commission_percent=(
                        external_property.seller_side_commission_percent
                    ),
                )
                result.listings_updated += 1

            result.seen_listing_keys.add(
                _listing_key(
                    external_property.listing_provider,
                    external_property.external_id,
                )
            )

        config = integration.get("config") or {}
        deactivate_missing = config.get(
            "deactivate_missing_listings",
            True,
        )

        if deactivate_missing:
            # Deactivate listings missing from this sync
            # (same listing provider family).
            listing_providers_seen = {
                key.split(":", 1)[0]
                for key in result.seen_listing_keys
            }

            agent_scope_id = (
                integration.get("agent_id")
                if scope_type == SCOPE_AGENT
                else None
            )

            for listing_provider in listing_providers_seen:
                existing = list_synced_listings_for_provider(
                    organization_id,
                    listing_provider,
                    agent_id=agent_scope_id,
                )

                for listing in existing:
                    key = _listing_key(
                        listing["provider"],
                        listing["external_id"],
                    )

                    if key in result.seen_listing_keys:
                        continue

                    if listing["status"] == "inactive":
                        continue

                    mark_listing_inactive(
                        listing["id"],
                        organization_id,
                        last_synced_at=synced_at,
                    )
                    result.listings_deactivated += 1

        finish_integration_sync_run(
            run_id,
            organization_id,
            status=SYNC_STATUS_OK,
            agents_created=result.agents_created,
            agents_updated=result.agents_updated,
            properties_created=result.properties_created,
            properties_updated=result.properties_updated,
            listings_created=result.listings_created,
            listings_updated=result.listings_updated,
            listings_deactivated=result.listings_deactivated,
        )
        update_integration_sync_state(
            integration_id,
            organization_id,
            last_synced_at=synced_at,
            last_sync_status=SYNC_STATUS_OK,
            last_sync_error=None,
            status=STATUS_CONNECTED,
        )

    except Exception as error:
        result.status = SYNC_STATUS_FAILED
        result.error_summary = str(error)

        finish_integration_sync_run(
            run_id,
            organization_id,
            status=SYNC_STATUS_FAILED,
            agents_created=result.agents_created,
            agents_updated=result.agents_updated,
            properties_created=result.properties_created,
            properties_updated=result.properties_updated,
            listings_created=result.listings_created,
            listings_updated=result.listings_updated,
            listings_deactivated=result.listings_deactivated,
            error_summary=result.error_summary,
        )
        update_integration_sync_state(
            integration_id,
            organization_id,
            last_synced_at=synced_at,
            last_sync_status=SYNC_STATUS_FAILED,
            last_sync_error=result.error_summary,
            status=STATUS_ERROR,
        )
        raise

    return result
