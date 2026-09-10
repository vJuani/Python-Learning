"""Read-only toward CRM. Upserts JRH properties by (org, source, external_id)."""

from __future__ import annotations

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

from modules.database.properties_repository import (
    ASSIGNMENT_SOURCE_MANUAL,
    STATUS_APPROVED,
    add_property,
    get_property_record,
)
from modules.database.external_price_history_repository import (
    upsert_external_price_history,
)
from modules.database.property_sync_hub_repository import (
    CONFLICT_CREATED,
    CONFLICT_LINKED,
    PROVIDER_MOCK_NETWORK,
    RUN_FAILED,
    RUN_OK,
    RUN_PARTIAL,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
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
    set_integration_status,
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
from modules.property_sync.agents import resolve_agent_id, summarize_agent_resolution
from modules.property_sync.connector import get_connector
from modules.property_sync.media import sync_property_media
from modules.property_sync.normalize import NormalizeError, normalize_external_property
import modules.property_sync.mock  # noqa: F401 — register mock connector
import modules.property_sync.redremax.connector  # noqa: F401 — register redremax

from modules.property_sync.redremax.auth import default_auth_provider
from modules.property_sync.redremax.connector import ListingBatch
from modules.property_sync.redremax.errors import (
    RedRemaxAuthError,
    RedRemaxConfigError,
    RedRemaxError,
    RedRemaxPartialError,
)
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX

SOURCE_LABELS = {
    "mock_network": "Mock Network",
    "tokko": "Tokko",
    "remax": "RE/MAX",
    "redremax": "RedREMAX",
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
    "title",
    "country",
    "postal_code",
    "administrative_area",
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


def _conflict_snapshot(normalized):
    """JSON-safe listing snapshot. Never persist tokens or binary media."""
    if not isinstance(normalized, dict):
        return None
    payload = json.loads(json.dumps(normalized, ensure_ascii=False, default=str))
    media = []
    for item in list(payload.get("media") or payload.get("photos") or []):
        item = dict(item or {})
        item.pop("content_bytes", None)
        media.append(item)
    payload["media"] = media
    payload["photos"] = media
    return payload


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


def ensure_redremax_integration(organization_id):
    organization_id = require_organization_id(organization_id)
    existing = get_property_integration(organization_id, PROVIDER_REDREMAX)
    if existing:
        return existing
    return upsert_property_integration(
        organization_id,
        PROVIDER_REDREMAX,
        status=STATUS_DISCONNECTED,
        sync_enabled=False,
        config={"external_office_id": ""},
    )


def update_redremax_office(organization_id, office_id):
    organization_id = require_organization_id(organization_id)
    existing = ensure_redremax_integration(organization_id)
    config = dict(existing.get("config") or {})
    config["external_office_id"] = str(office_id or "").strip()
    status = existing["status"]
    if status == "syncing":
        status = STATUS_DISCONNECTED
    return upsert_property_integration(
        organization_id,
        PROVIDER_REDREMAX,
        status=status,
        sync_enabled=False,
        config=config,
    )


def compute_auth_state(integration, auth_provider=None):
    """Never report connected just because RedRemaxConnector exists."""
    item = integration or {}
    provider = item.get("provider")
    if provider != PROVIDER_REDREMAX:
        if item.get("status") == STATUS_CONNECTED:
            return "connected"
        if item.get("status") == STATUS_ERROR:
            return "error"
        return item.get("status") or "not_configured"

    auth_provider = auth_provider or default_auth_provider()
    office_id = str((item.get("config") or {}).get("external_office_id") or "").strip()
    last_error = item.get("last_error")
    last_ok = bool((item.get("config") or {}).get("last_connection_ok"))
    if not office_id:
        return "not_configured"
    if last_error in {"redremax_err_auth", "redremax_err_auth_401"}:
        return "auth_expired"
    if item.get("status") == STATUS_ERROR:
        return "error"
    if item.get("status") == STATUS_CONNECTED and (
        last_ok or item.get("last_success_at")
    ):
        return "connected"
    if auth_provider.is_production_ready() and auth_provider.is_configured() and last_ok:
        return "connected"
    return "authentication_pending"


def test_property_source_connection(organization_id, provider):
    organization_id = require_organization_id(organization_id)
    if provider == PROVIDER_REDREMAX:
        ensure_redremax_integration(organization_id)
    integration = get_property_integration(organization_id, provider)
    if integration is None:
        raise PropertySyncError("sync_err_not_configured", 400)
    connector = get_connector(provider)
    office_id = str((integration.get("config") or {}).get("external_office_id") or "").strip()
    logger.info(
        "RedREMAX test_connection start org_id=%s office_id=%s",
        organization_id,
        office_id or "-",
    )
    try:
        result = connector.test_connection(integration)
    except RedRemaxAuthError as error:
        logger.exception(
            "RedREMAX test_connection failed org_id=%s office_id=%s error_type=%s",
            organization_id,
            office_id or "-",
            type(error).__name__,
        )
        set_integration_status(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error=error.message_key,
            config_updates={
                "last_connection_ok": False,
                "last_diagnostic": _diagnostic_from_error(
                    connector, office_id, error
                ),
            },
        )
        raise PropertySyncError(error.message_key, error.status_code)
    except RedRemaxConfigError as error:
        logger.exception(
            "RedREMAX test_connection failed org_id=%s office_id=%s error_type=%s",
            organization_id,
            office_id or "-",
            type(error).__name__,
        )
        set_integration_status(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error=error.message_key,
            config_updates={
                "last_connection_ok": False,
                "last_diagnostic": _diagnostic_from_error(
                    connector, office_id, error
                ),
            },
        )
        raise PropertySyncError(error.message_key, 400)
    except RedRemaxError as error:
        logger.exception(
            "RedREMAX test_connection failed org_id=%s office_id=%s error_type=%s",
            organization_id,
            office_id or "-",
            type(error).__name__,
        )
        set_integration_status(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error=error.message_key,
            config_updates={
                "last_connection_ok": False,
                "last_diagnostic": _diagnostic_from_error(
                    connector, office_id, error
                ),
            },
        )
        raise PropertySyncError(error.message_key, error.status_code)
    except Exception:
        logger.exception(
            "RedREMAX test_connection failed org_id=%s office_id=%s error_type=Exception",
            organization_id,
            office_id or "-",
        )
        set_integration_status(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error="redremax_err_http",
            config_updates={"last_connection_ok": False},
        )
        raise PropertySyncError("redremax_err_http", 502)
    set_integration_status(
        organization_id,
        provider,
        status=STATUS_CONNECTED,
        last_error=None,
        config_updates={
            "last_connection_ok": True,
            "last_connection_test_at": _now_iso(),
            "last_diagnostic": {
                "base_url": getattr(connector, "client", None)
                and connector.client.base_url,
                "endpoint": "/listings/api/listings",
                "office_id": office_id,
                "token_configured": True,
                "http_status": 200,
                "safe_response_message": "HTTP 200",
                "at": _now_iso(),
            },
        },
    )
    return result


def diagnose_redremax_connection(organization_id):
    organization_id = require_organization_id(organization_id)
    ensure_redremax_integration(organization_id)
    integration = get_property_integration(organization_id, PROVIDER_REDREMAX)
    if integration is None:
        raise PropertySyncError("sync_err_not_configured", 400)
    connector = get_connector(PROVIDER_REDREMAX)
    office_id = str((integration.get("config") or {}).get("external_office_id") or "").strip()
    logger.info(
        "RedREMAX diagnose start org_id=%s office_id=%s",
        organization_id,
        office_id or "-",
    )
    report = connector.diagnose_connection(integration)
    report = {
        "base_url": report.get("base_url") or "",
        "endpoint": report.get("endpoint") or "/listings/api/listings",
        "office_id": report.get("office_id") or office_id,
        "token_configured": bool(report.get("token_configured")),
        "http_status": report.get("http_status"),
        "safe_response_message": report.get("safe_response_message") or "",
        "at": _now_iso(),
    }
    set_integration_status(
        organization_id,
        PROVIDER_REDREMAX,
        status=integration.get("status") or STATUS_DISCONNECTED,
        last_error=integration.get("last_error"),
        config_updates={"last_diagnostic": report},
    )
    return report


def _diagnostic_from_error(connector, office_id, error):
    base_url = ""
    client = getattr(connector, "client", None)
    if client is not None:
        base_url = getattr(client, "base_url", "") or ""
    return {
        "base_url": base_url,
        "endpoint": "/listings/api/listings",
        "office_id": office_id or "",
        "token_configured": bool(getattr(client, "resolved_token", lambda: None)()),
        "http_status": getattr(error, "status_code", None),
        "safe_response_message": getattr(error, "safe_message", None)
        or getattr(error, "message_key", ""),
        "at": _now_iso(),
    }


def dry_run_property_sync(organization_id, provider):
    """Call source + normalize. Never writes Property / media / price history."""
    organization_id = require_organization_id(organization_id)
    if provider == PROVIDER_REDREMAX:
        ensure_redremax_integration(organization_id)
    integration = get_property_integration(organization_id, provider)
    if integration is None:
        raise PropertySyncError("sync_err_not_configured", 400)
    connector = get_connector(provider)
    try:
        listed = _as_listing_batch(connector.list_properties(integration))
    except RedRemaxAuthError as error:
        logger.exception(
            "RedREMAX dry_run failed org_id=%s error_type=%s",
            organization_id,
            type(error).__name__,
        )
        raise PropertySyncError(error.message_key, error.status_code)
    except RedRemaxPartialError as error:
        listed = ListingBatch(
            items=error.items,
            source_total=error.source_total or 0,
            pages_fetched=error.pages_fetched,
            incomplete=True,
        )
    except RedRemaxError as error:
        raise PropertySyncError(error.message_key, error.status_code)

    valid = 0
    warnings = 0
    errors = 0
    agent_refs = []
    for raw in listed.items:
        if raw.get("_skipped"):
            warnings += 1
            continue
        try:
            normalized = normalize_external_property(raw, source=provider)
        except NormalizeError:
            errors += 1
            continue
        valid += 1
        item_warnings = list(normalized.get("warnings") or [])
        agent_ref = normalized.get("agent") or {}
        agent_refs.append(agent_ref)
        agent_id, _reason = resolve_agent_id(organization_id, provider, agent_ref)
        if agent_ref.get("external_agent_id") and agent_id is None:
            item_warnings.append("redremax_warn_unmapped_agent")
        warnings += len(item_warnings)

    resolution = summarize_agent_resolution(organization_id, provider, agent_refs)
    summary = {
        "found": listed.source_total or len(listed.items),
        "valid": valid,
        "warnings": warnings,
        "errors": errors,
        "mapped_agents": resolution["mapped_agents"],
        "unmapped_agents": resolution["unmapped_agents"],
        "pages_fetched": listed.pages_fetched,
        "incomplete": listed.incomplete,
        "wrote": False,
        "at": _now_iso(),
    }
    if provider == PROVIDER_REDREMAX:
        set_integration_status(
            organization_id,
            provider,
            status=integration.get("status") or STATUS_DISCONNECTED,
            last_error=integration.get("last_error"),
            config_updates={"last_dry_run": summary},
        )
    return summary


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
    if normalized.get("latitude") is not None and location_source in (
        None,
        "",
        "external",
        "external_redremax",
    ):
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
                normalized.get("location_source") or "external",
            ]
        )

    if (
        agent_id is not None
        and current.get("agent_assignment_source") != ASSIGNMENT_SOURCE_MANUAL
    ):
        assignments.append("agent_id = ?")
        params.append(agent_id)
        assignments.append("agent_assignment_source = ?")
        params.append(normalized.get("external_source") or current.get("external_source"))

    metadata = normalized.get("external_metadata")
    if isinstance(metadata, dict):
        import json

        assignments.append("external_metadata_json = ?")
        params.append(json.dumps(metadata, ensure_ascii=False, sort_keys=True))

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
                payload_snapshot=_conflict_snapshot(normalized),
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
            administrative_area=normalized.get("administrative_area"),
            country=normalized.get("country"),
            postal_code=normalized.get("postal_code"),
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

    media_stats = {"created": 0, "unchanged": 0, "removed": 0}
    if connector and connector.capabilities.supports_media:
        media_items = normalized.get("media") or connector.list_property_media(
            integration or {}, external_id
        )
        media_stats = sync_property_media(
            organization_id,
            property_id,
            source,
            media_items,
            capabilities={
                "media_strategy": connector.capabilities.media_strategy,
                "media_url_kind": connector.capabilities.media_url_kind,
            },
        ) or media_stats

    if property_id and normalized.get("price_history"):
        upsert_external_price_history(
            organization_id,
            property_id,
            source,
            normalized.get("price_history"),
        )

    warnings = list(normalized.get("warnings") or [])
    if (
        (normalized.get("agent") or {}).get("external_agent_id")
        and agent_id is None
    ):
        warnings.append("redremax_warn_unmapped_agent")

    if run_id:
        add_sync_run_item(
            organization_id,
            run_id,
            external_id=external_id,
            outcome=outcome,
            property_id=property_id,
            message=";".join(warnings) if warnings else None,
        )
    return {
        "outcome": outcome,
        "property_id": property_id,
        "warnings": warnings,
        "media": media_stats,
    }


def _as_listing_batch(raw):
    if isinstance(raw, ListingBatch):
        return raw
    return ListingBatch(items=list(raw or []))


def run_property_sync(organization_id, provider=PROVIDER_MOCK_NETWORK, *, language="es"):
    organization_id = require_organization_id(organization_id)
    if provider == PROVIDER_MOCK_NETWORK:
        ensure_mock_integration(organization_id)
    if provider == PROVIDER_REDREMAX:
        ensure_redremax_integration(organization_id)
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
    extra_stats = {"source_total": 0, "pages_fetched": 0}
    incomplete = False

    try:
        listed = _as_listing_batch(connector.list_properties(integration))
    except RedRemaxAuthError as error:
        logger.exception(
            "RedREMAX sync failed org_id=%s error_type=%s",
            organization_id,
            type(error).__name__,
        )
        finish_property_sync_run(
            run_id,
            organization_id,
            status=RUN_FAILED,
            error_summary=error.message_key,
            extra_stats=extra_stats,
        )
        finish_integration_state(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error=error.message_key,
        )
        return get_property_sync_run(run_id, organization_id)
    except RedRemaxPartialError as error:
        listed = ListingBatch(
            items=error.items,
            source_total=error.source_total or 0,
            pages_fetched=error.pages_fetched,
            incomplete=True,
        )
        incomplete = True
    except (RedRemaxConfigError, RedRemaxError) as error:
        finish_property_sync_run(
            run_id,
            organization_id,
            status=RUN_FAILED,
            error_summary=error.message_key,
            extra_stats=extra_stats,
        )
        finish_integration_state(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error=error.message_key,
        )
        return get_property_sync_run(run_id, organization_id)
    except Exception:
        finish_property_sync_run(
            run_id,
            organization_id,
            status=RUN_FAILED,
            error_summary="provider_unavailable",
            extra_stats=extra_stats,
        )
        finish_integration_state(
            organization_id,
            provider,
            status=STATUS_ERROR,
            last_error="provider_unavailable",
        )
        return get_property_sync_run(run_id, organization_id)

    extra_stats["source_total"] = listed.source_total
    extra_stats["pages_fetched"] = listed.pages_fetched
    incomplete = incomplete or listed.incomplete

    for raw in listed.items:
        if raw.get("_skipped"):
            stats["warnings"] += 1
            add_sync_run_item(
                organization_id,
                run_id,
                external_id=raw.get("external_id"),
                outcome="warning",
                message=";".join(raw.get("warnings") or []),
            )
            continue
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
            if result.get("warnings"):
                stats["warnings"] += 1
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

    processed = stats["created"] or stats["updated"] or stats["unchanged"]
    if incomplete or (stats["failed"] and processed):
        run_status = RUN_PARTIAL
        integration_status = STATUS_CONNECTED if processed else STATUS_ERROR
    elif stats["failed"] and not processed:
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
        extra_stats=extra_stats,
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
    snapshot = conflict.get("payload_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("external_id"):
        normalized = normalize_external_property(snapshot, source=provider)
    else:
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
    ensure_redremax_integration(organization_id)
    cards = []
    for item in list_property_integrations(organization_id):
        provider = item["provider"]
        conflicts = list_open_conflicts(organization_id, provider)
        config = item.get("config") or {}
        auth_state = compute_auth_state(item)
        from modules.property_sync.demo_import_service import demo_import_dashboard_fields

        demo_fields = (
            demo_import_dashboard_fields(config)
            if provider == PROVIDER_REDREMAX
            else {}
        )
        mapping_view = {}
        if provider == PROVIDER_REDREMAX:
            from modules.property_sync.agent_mapping import (
                redremax_agent_mapping_dashboard,
            )

            mapping_view = redremax_agent_mapping_dashboard(organization_id)
        cards.append(
            {
                **item,
                "label": source_label(provider, language),
                "property_count": count_synced_properties(organization_id, provider),
                "conflicts": conflicts,
                "conflict_count": len(conflicts),
                "auth_state": auth_state,
                "external_office_id": config.get("external_office_id") or "",
                "last_dry_run": config.get("last_dry_run") or {},
                "last_diagnostic": config.get("last_diagnostic") or {},
                "last_import_preview": demo_fields.get("last_import_preview") or {},
                "last_manual_import": demo_fields.get("last_manual_import") or {},
                "last_manual_import_display": demo_fields.get(
                    "last_manual_import_display"
                )
                or "",
                "architecture_ready": provider == PROVIDER_REDREMAX,
                "auth_configured": (
                    default_auth_provider().is_configured()
                    if provider == PROVIDER_REDREMAX
                    else True
                ),
                "authenticated": auth_state == "connected",
                "agent_mapping": mapping_view,
            }
        )
    return {"integrations": cards}


def require_sync_admin(user):
    from modules.auth import is_admin

    if not user or not is_admin(user):
        raise PropertySyncError("sync_err_admin_only", 403)
    return user
