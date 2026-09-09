"""Read-only toward CRM. Upserts JRH properties by (org, source, external_id)."""

from __future__ import annotations

from datetime import datetime

from modules.database.properties_repository import (
    STATUS_APPROVED,
    add_property,
    get_property_record,
)
from modules.database.property_sync_hub_repository import (
    CONFLICT_CREATED,
    CONFLICT_LINKED,
    PROVIDER_MOCK_NETWORK,
    RUN_FAILED,
    RUN_OK,
    RUN_PARTIAL,
    STATUS_CONNECTED,
    STATUS_ERROR,
    add_sync_conflict,
    add_sync_run_item,
    count_synced_properties,
    finish_integration_state,
    finish_property_sync_run,
    get_property_integration,
    get_property_sync_run,
    get_sync_conflict,
    list_open_conflicts,
    list_property_integrations,
    resolve_sync_conflict,
    start_property_sync_run,
    try_begin_sync,
    upsert_property_integration,
    find_manual_property_by_address,
)
from modules.database.property_sync_repository import (
    SYNC_OK,
    find_property_by_external_identity,
    upsert_property_external_identity,
)
from modules.database.tenant import TenantError, require_organization_id
from modules.i18n import translate
from modules.property_sync.agents import resolve_agent_id
from modules.property_sync.connector import get_connector
from modules.property_sync.media import sync_property_media
from modules.property_sync.normalize import NormalizeError, normalize_external_property
import modules.property_sync.mock  # noqa: F401 — register mock connector


SOURCE_LABELS = {
    "mock_network": "Mock Network",
    "tokko": "Tokko",
    "remax": "RE/MAX",
    "inmoweb": "Inmoweb",
}

EXTERNALLY_MANAGED_FIELDS = (
    "address",
    "jurisdiction",
    "property_type",
    "listing_purpose",
    "listing_price",
    "listing_currency",
    "neighborhood",
    "locality",
    "formatted_address",
    "rooms",
    "bedrooms",
    "bathrooms",
    "covered_m2",
    "total_m2",
    "parking_spaces",
    "description",
    "commercial_status",
    "features_json",
    "external_url",
    "external_status",
)

INTERNAL_FIELDS = (
    "status",
    "created_by_user_id",
    "reviewed_by_user_id",
    "reviewed_at",
    "rejection_reason",
    "submitted_at",
)


class PropertySyncError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


class SyncInProgressError(PropertySyncError):
    def __init__(self):
        super().__init__("sync_err_in_progress", 409)


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def source_label(source, language="es"):
    key = f"sync_provider_{source}"
    label = translate(key, language=language)
    if label != key:
        return label
    return SOURCE_LABELS.get(source, source or translate("sync_badge_synced", language=language))


def ensure_mock_integration(organization_id):
    organization_id = require_organization_id(organization_id)
    existing = get_property_integration(organization_id, PROVIDER_MOCK_NETWORK)
    if existing:
        return existing
    return upsert_property_integration(
        organization_id,
        PROVIDER_MOCK_NETWORK,
        status=STATUS_CONNECTED,
        sync_enabled=True,
        config={},
    )


def apply_synced_property_fields(property_id, organization_id, normalized, *, agent_id=None):
    """Update externally managed fields only. Never touches approval/internal data."""
    organization_id = require_organization_id(organization_id)
    current = get_property_record(property_id, organization_id)
    if current is None:
        raise TenantError("Property not found in organization.")

    from modules.database.connection import get_connection
    from modules.property_features import features_to_json

    assignments = []
    params = []
    for field in EXTERNALLY_MANAGED_FIELDS:
        if field == "features_json":
            value = features_to_json(normalized.get("features"))
        else:
            value = normalized.get(field)
        assignments.append(f"{field} = ?")
        params.append(value)

    location_source = current.get("location_source")
    if normalized.get("latitude") is not None and location_source in (None, "", "external"):
        assignments.extend(
            [
                "latitude = ?",
                "longitude = ?",
                "location_source = ?",
            ]
        )
        params.extend(
            [
                normalized.get("latitude"),
                normalized.get("longitude"),
                "external",
            ]
        )

    if agent_id is not None:
        assignments.append("agent_id = ?")
        params.append(agent_id)

    assignments.extend(
        [
            "last_synced_at = ?",
            "external_updated_at = ?",
            "sync_status = ?",
            "sync_hash = ?",
            "sync_error = ?",
            "is_externally_managed = ?",
            "external_source = ?",
            "external_id = ?",
        ]
    )
    params.extend(
        [
            _now_iso(),
            normalized.get("external_updated_at") or _now_iso(),
            "synced",
            normalized.get("sync_hash"),
            None,
            1,
            normalized["external_source"],
            normalized["external_id"],
        ]
    )
    params.extend([property_id, organization_id])

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE properties
            SET {", ".join(assignments)}
            WHERE id = ? AND organization_id = ?
            """,
            params,
        )
        if cursor.rowcount == 0:
            raise TenantError("Property not found in organization.")
        connection.commit()
    finally:
        connection.close()


def _fields_changed(current, normalized):
    if (current.get("sync_hash") or "") == (normalized.get("sync_hash") or ""):
        return False
    checks = (
        "address",
        "listing_price",
        "listing_currency",
        "description",
        "rooms",
        "bedrooms",
        "bathrooms",
        "covered_m2",
        "total_m2",
        "commercial_status",
        "neighborhood",
    )
    for key in checks:
        if current.get(key) != normalized.get(key):
            return True
    return True


def preview_source_conflicts(organization_id, normalized_items, source):
    previews = []
    for item in normalized_items:
        existing = find_property_by_external_identity(
            organization_id,
            external_source=source,
            external_id=item["external_id"],
        )
        if existing:
            continue
        manual = find_manual_property_by_address(organization_id, item["address"])
        if manual:
            previews.append(
                {
                    "external_id": item["external_id"],
                    "address_external": item["address"],
                    "existing_property_id": manual["id"],
                    "address_existing": manual["address"],
                }
            )
    return previews


def sync_external_property(
    organization_id,
    normalized,
    *,
    integration=None,
    run_id=None,
    connector=None,
    allow_create=True,
    ignore_conflicts=False,
):
    organization_id = require_organization_id(organization_id)
    source = normalized["external_source"]
    external_id = normalized["external_id"]

    existing = find_property_by_external_identity(
        organization_id,
        external_source=source,
        external_id=external_id,
    )
    agent_id, _reason = resolve_agent_id(organization_id, source, normalized.get("agent"))

    if existing is None and allow_create and not ignore_conflicts:
        manual = find_manual_property_by_address(organization_id, normalized["address"])
        if manual:
            conflict = add_sync_conflict(
                organization_id,
                source,
                external_id,
                existing_property_id=manual["id"],
                address_external=normalized["address"],
                address_existing=manual["address"],
            )
            if run_id:
                add_sync_run_item(
                    organization_id,
                    run_id,
                    external_id=external_id,
                    outcome="conflict",
                    property_id=manual["id"],
                    message="possible_duplicate",
                )
            return {"outcome": "conflict", "property_id": manual["id"], "conflict": conflict}

    if existing is None:
        if not allow_create:
            return {"outcome": "skipped", "property_id": None}
        if normalized.get("deleted"):
            return {"outcome": "unchanged", "property_id": None}

        property_id = add_property(
            normalized["address"],
            normalized["jurisdiction"],
            organization_id,
            agent_id=agent_id,
            status=STATUS_APPROVED,
            property_type=normalized.get("property_type"),
            listing_price=normalized.get("listing_price"),
            listing_purpose=normalized.get("listing_purpose"),
            external_id=external_id,
            last_synced_at=_now_iso(),
            listing_currency=normalized.get("listing_currency"),
            neighborhood=normalized.get("neighborhood"),
            rooms=normalized.get("rooms"),
            bedrooms=normalized.get("bedrooms"),
            bathrooms=normalized.get("bathrooms"),
            covered_m2=normalized.get("covered_m2"),
            total_m2=normalized.get("total_m2"),
            parking_spaces=normalized.get("parking_spaces"),
            description=normalized.get("description"),
            commercial_status=normalized.get("commercial_status"),
            features=normalized.get("features"),
            formatted_address=normalized.get("formatted_address"),
            locality=normalized.get("locality"),
            latitude=normalized.get("latitude"),
            longitude=normalized.get("longitude"),
            geocode_status="resolved" if normalized.get("latitude") is not None else None,
        )
        apply_synced_property_fields(
            property_id, organization_id, normalized, agent_id=agent_id
        )
        outcome = "created"
    else:
        if int(existing["organization_id"]) != int(organization_id):
            raise TenantError("Property belongs to another organization.")
        property_id = existing["id"]
        current = get_property_record(property_id, organization_id)
        if not _fields_changed(current, normalized) and not normalized.get("deleted"):
            upsert_property_external_identity(
                organization_id,
                property_id,
                external_source=source,
                external_id=external_id,
                sync_status="synced",
                external_updated_at=normalized.get("external_updated_at"),
            )
            outcome = "unchanged"
        else:
            apply_synced_property_fields(
                property_id, organization_id, normalized, agent_id=agent_id
            )
            outcome = "updated"

    if connector and connector.capabilities.supports_media:
        media_items = normalized.get("media") or connector.list_property_media(
            integration or {}, external_id
        )
        sync_property_media(
            organization_id,
            property_id,
            source,
            media_items,
            capabilities={
                "media_strategy": connector.capabilities.media_strategy,
                "media_url_kind": connector.capabilities.media_url_kind,
            },
        )

    if run_id:
        add_sync_run_item(
            organization_id,
            run_id,
            external_id=external_id,
            outcome=outcome,
            property_id=property_id,
        )
    return {"outcome": outcome, "property_id": property_id}


def run_property_sync(organization_id, provider=PROVIDER_MOCK_NETWORK, *, language="es"):
    organization_id = require_organization_id(organization_id)
    if provider == PROVIDER_MOCK_NETWORK:
        ensure_mock_integration(organization_id)
    integration = try_begin_sync(organization_id, provider)
    if integration is None:
        existing = get_property_integration(organization_id, provider)
        if existing and existing["status"] == "syncing":
            raise SyncInProgressError()
        raise PropertySyncError("sync_err_not_configured", 400)

    connector = get_connector(provider)
    run_id = start_property_sync_run(organization_id, integration["id"], provider)
    stats = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "warnings": 0,
        "failed": 0,
        "conflicts": 0,
    }
    fatal_error = None

    try:
        raw_items = connector.list_properties(integration)
    except Exception as error:
        fatal_error = "provider_unavailable"
        finish_property_sync_run(
            run_id,
            organization_id,
            status=RUN_FAILED,
            error_summary=fatal_error,
        )
        finish_integration_state(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error=fatal_error,
        )
        return get_property_sync_run(run_id, organization_id)

    for raw in raw_items:
        try:
            normalized = normalize_external_property(raw, source=provider)
            result = sync_external_property(
                organization_id,
                normalized,
                integration=integration,
                run_id=run_id,
                connector=connector,
            )
            key = {
                "created": "created",
                "updated": "updated",
                "unchanged": "unchanged",
                "conflict": "conflicts",
            }.get(result["outcome"], "warnings")
            stats[key] += 1
        except NormalizeError as error:
            stats["failed"] += 1
            add_sync_run_item(
                organization_id,
                run_id,
                external_id=(raw or {}).get("external_id"),
                outcome="failed",
                message=error.message_key,
            )
        except Exception:
            stats["failed"] += 1
            add_sync_run_item(
                organization_id,
                run_id,
                external_id=(raw or {}).get("external_id"),
                outcome="failed",
                message="sync_item_failed",
            )

    if stats["failed"] and (stats["created"] or stats["updated"] or stats["unchanged"]):
        run_status = RUN_PARTIAL
        integration_status = STATUS_CONNECTED
    elif stats["failed"] and not (
        stats["created"] or stats["updated"] or stats["unchanged"]
    ):
        run_status = RUN_FAILED
        integration_status = STATUS_ERROR
    else:
        run_status = RUN_OK
        integration_status = STATUS_CONNECTED

    finish_property_sync_run(
        run_id,
        organization_id,
        status=run_status,
        created_count=stats["created"],
        updated_count=stats["updated"],
        unchanged_count=stats["unchanged"],
        warning_count=stats["warnings"],
        failed_count=stats["failed"],
        conflict_count=stats["conflicts"],
    )
    finish_integration_state(
        organization_id,
        provider,
        status=integration_status,
        last_error=None if run_status != RUN_FAILED else "sync_partial_or_failed",
        success=run_status in {RUN_OK, RUN_PARTIAL},
    )
    return get_property_sync_run(run_id, organization_id)


def link_external_identity(organization_id, property_id, source, external_id):
    organization_id = require_organization_id(organization_id)
    target = get_property_record(property_id, organization_id)
    if target is None:
        raise TenantError("Property not found in organization.")
    existing = find_property_by_external_identity(
        organization_id, external_source=source, external_id=external_id
    )
    if existing and int(existing["id"]) != int(property_id):
        raise PropertySyncError("sync_err_identity_taken", 409)
    upsert_property_external_identity(
        organization_id,
        property_id,
        external_source=source,
        external_id=external_id,
        sync_status=SYNC_OK,
    )
    from modules.database.connection import get_connection

    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE properties
            SET is_externally_managed = 1
            WHERE id = ? AND organization_id = ?
            """,
            (property_id, organization_id),
        )
        connection.commit()
    finally:
        connection.close()
    return get_property_record(property_id, organization_id)


def resolve_conflict(organization_id, conflict_id, action):
    conflict = get_sync_conflict(conflict_id, organization_id)
    if conflict is None:
        raise PropertySyncError("sync_err_conflict_missing", 404)
    provider = conflict["provider"]
    connector = get_connector(provider)
    integration = get_property_integration(organization_id, provider) or {}
    raw = connector.get_property(integration, conflict["external_id"])
    if raw is None:
        raise PropertySyncError("sync_err_external_missing", 404)
    normalized = normalize_external_property(raw, source=provider)

    if action == "link":
        link_external_identity(
            organization_id,
            conflict["existing_property_id"],
            provider,
            conflict["external_id"],
        )
        sync_external_property(
            organization_id,
            normalized,
            integration=integration,
            connector=connector,
            allow_create=False,
        )
        resolve_sync_conflict(conflict_id, organization_id, CONFLICT_LINKED)
        return {"action": "link", "property_id": conflict["existing_property_id"]}

    if action == "create":
        result = sync_external_property(
            organization_id,
            normalized,
            integration=integration,
            connector=connector,
            allow_create=True,
            ignore_conflicts=True,
        )
        resolve_sync_conflict(conflict_id, organization_id, CONFLICT_CREATED)
        return {"action": "create", "property_id": result.get("property_id")}

    raise PropertySyncError("sync_err_invalid_conflict_action", 400)


def integration_dashboard(organization_id, language="es"):
    organization_id = require_organization_id(organization_id)
    ensure_mock_integration(organization_id)
    cards = []
    for item in list_property_integrations(organization_id):
        provider = item["provider"]
        conflicts = list_open_conflicts(organization_id, provider)
        cards.append(
            {
                **item,
                "label": source_label(provider, language),
                "property_count": count_synced_properties(organization_id, provider),
                "conflicts": conflicts,
                "conflict_count": len(conflicts),
            }
        )
    return {"integrations": cards}


def require_sync_admin(user):
    from modules.auth import is_admin

    if not user or not is_admin(user):
        raise PropertySyncError("sync_err_admin_only", 403)
    return user
