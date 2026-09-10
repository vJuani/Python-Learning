"""RedREMAX JSON demo import. Reuses the real normalizer and Property Sync Hub."""

from __future__ import annotations

from datetime import datetime

from modules.database.properties_repository import get_property_record
from modules.database.property_sync_hub_repository import (
    STATUS_DISCONNECTED,
    find_manual_property_by_address,
    get_property_integration,
    set_integration_status,
)
from modules.database.property_sync_repository import find_property_by_external_identity
from modules.database.redremax_demo_import_repository import (
    STATUS_CONFIRMED,
    STATUS_FAILED,
    claim_demo_import_session,
    create_demo_import_session,
    finish_demo_import_session,
    get_demo_import_by_token,
)
from modules.database.tenant import require_organization_id
from modules.property_sync.agents import resolve_agent_id
from modules.property_sync.connector import ConnectorCapabilities
from modules.property_sync.normalize import NormalizeError, normalize_external_property
from modules.property_sync.redremax.demo_import import (
    DemoImportError,
    parse_uploaded_json_files,
    prepare_listing_for_import,
)
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX
from modules.property_sync.redremax.normalizer import RedRemaxPropertyNormalizer
from modules.property_sync.service import (
    PropertySyncError,
    _fields_changed,
    ensure_redremax_integration,
    sync_external_property,
)


class DemoImportMediaConnector:
    """Media from the normalized listing only. Never calls RedREMAX."""

    capabilities = ConnectorCapabilities(
        supports_media=True,
        media_strategy="remote_reference",
        media_url_kind="stable",
    )

    def list_property_media(self, integration, external_id):
        return []

    def get_property(self, integration, external_id):
        return None


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _format_display_time(iso_value):
    raw = str(iso_value or "").strip()
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return moment.strftime("%d/%m/%Y %H:%M")


def _configured_office_id(organization_id):
    integration = ensure_redremax_integration(organization_id)
    return str((integration.get("config") or {}).get("external_office_id") or "").strip()


def _classify_normalized(organization_id, normalized):
    existing = find_property_by_external_identity(
        organization_id,
        external_source=PROVIDER_REDREMAX,
        external_id=normalized["external_id"],
    )
    if existing:
        current = get_property_record(existing["id"], organization_id)
        if current and not _fields_changed(current, normalized):
            return "unchanged"
        return "updated"
    manual = find_manual_property_by_address(organization_id, normalized["address"])
    if manual:
        return "conflict"
    return "created"


def preview_redremax_json_import(
    organization_id,
    files,
    *,
    created_by=None,
    normalizer=None,
):
    organization_id = require_organization_id(organization_id)
    office_id = _configured_office_id(organization_id)
    if not office_id:
        raise PropertySyncError("redremax_err_office_required", 400)

    try:
        raw_listings = parse_uploaded_json_files(files)
    except DemoImportError as error:
        raise PropertySyncError(error.message_key, error.status_code) from error

    normalizer = normalizer or RedRemaxPropertyNormalizer()
    prepared = []
    valid = 0
    created = 0
    updated = 0
    unchanged = 0
    conflicts = 0
    errors = 0
    warnings = 0
    unmapped_agents = 0

    for raw in raw_listings:
        try:
            hub = prepare_listing_for_import(
                raw, office_id=office_id, normalizer=normalizer
            )
        except DemoImportError as error:
            raise PropertySyncError(error.message_key, error.status_code) from error
        if hub.get("_skipped"):
            warnings += 1
            prepared.append(hub)
            continue
        try:
            normalized = normalize_external_property(hub, source=PROVIDER_REDREMAX)
        except NormalizeError:
            errors += 1
            prepared.append(
                {
                    "_skipped": True,
                    "warnings": ["sync_err_invalid_listing"],
                    "external_id": hub.get("external_id"),
                }
            )
            continue
        valid += 1
        item_warnings = list(normalized.get("warnings") or [])
        agent_id, _reason = resolve_agent_id(
            organization_id, PROVIDER_REDREMAX, normalized.get("agent")
        )
        if (normalized.get("agent") or {}).get("external_agent_id") and agent_id is None:
            unmapped_agents += 1
            item_warnings.append("redremax_warn_unmapped_agent")
        warnings += len(item_warnings)
        outcome = _classify_normalized(organization_id, normalized)
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        elif outcome == "unchanged":
            unchanged += 1
        elif outcome == "conflict":
            conflicts += 1
        prepared.append(hub)

    preview = {
        "found": len(raw_listings),
        "valid": valid,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "conflicts": conflicts,
        "unmapped_agents": unmapped_agents,
        "warnings": warnings,
        "errors": errors,
        "wrote": False,
        "at": _now_iso(),
    }
    session = create_demo_import_session(
        organization_id,
        created_by=created_by,
        listings=prepared,
        preview=preview,
        source_total=preview["found"],
        valid_count=valid,
        warning_count=warnings,
        failed_count=errors,
    )
    integration = get_property_integration(organization_id, PROVIDER_REDREMAX)
    set_integration_status(
        organization_id,
        PROVIDER_REDREMAX,
        status=(integration or {}).get("status") or STATUS_DISCONNECTED,
        last_error=(integration or {}).get("last_error"),
        config_updates={
            "last_import_preview": {
                **preview,
                "confirm_token": session["confirm_token"],
                "session_id": session["id"],
            }
        },
    )
    return {
        **preview,
        "confirm_token": session["confirm_token"],
        "session_id": session["id"],
        "wrote": False,
    }


def confirm_redremax_json_import(organization_id, confirm_token):
    organization_id = require_organization_id(organization_id)
    office_id = _configured_office_id(organization_id)
    if not office_id:
        raise PropertySyncError("redremax_err_office_required", 400)

    existing = get_demo_import_by_token(organization_id, confirm_token)
    if existing is None:
        raise PropertySyncError("redremax_err_import_token", 400)
    if existing["status"] == STATUS_CONFIRMED:
        raise PropertySyncError("redremax_err_import_already", 409)
    session = claim_demo_import_session(organization_id, confirm_token)
    if session is None:
        raise PropertySyncError("redremax_err_import_expired", 400)

    stats = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "conflicts": 0,
        "warnings": 0,
        "failed": 0,
        "source_total": session.get("source_total") or len(session.get("listings") or []),
        "wrote": True,
        "at": _now_iso(),
    }
    connector = DemoImportMediaConnector()
    integration = get_property_integration(organization_id, PROVIDER_REDREMAX) or {}

    try:
        for hub in session.get("listings") or []:
            if hub.get("_skipped"):
                stats["warnings"] += 1
                continue
            try:
                normalized = normalize_external_property(hub, source=PROVIDER_REDREMAX)
                result = sync_external_property(
                    organization_id,
                    normalized,
                    integration=integration,
                    connector=connector,
                )
                key = {
                    "created": "created",
                    "updated": "updated",
                    "unchanged": "unchanged",
                    "conflict": "conflicts",
                }.get(result["outcome"])
                if key:
                    stats[key] += 1
                else:
                    stats["warnings"] += 1
                if result.get("warnings"):
                    stats["warnings"] += 1
            except NormalizeError:
                stats["failed"] += 1
            except Exception:
                stats["failed"] += 1
    except Exception:
        finish_demo_import_session(
            session["id"], organization_id, status=STATUS_FAILED
        )
        raise

    finish_demo_import_session(
        session["id"],
        organization_id,
        status=STATUS_CONFIRMED,
        preview=stats,
    )
    set_integration_status(
        organization_id,
        PROVIDER_REDREMAX,
        status=integration.get("status") or STATUS_DISCONNECTED,
        last_error=integration.get("last_error"),
        config_updates={
            "last_import_preview": {},
            "last_manual_import": {
                **stats,
                "processed": stats["source_total"],
                "display_at": _format_display_time(stats["at"]),
            },
        },
    )
    return stats


def demo_import_dashboard_fields(config):
    config = config or {}
    preview = config.get("last_import_preview") or {}
    last = config.get("last_manual_import") or {}
    return {
        "last_import_preview": preview if preview.get("confirm_token") else {},
        "last_manual_import": last,
        "last_manual_import_display": last.get("display_at")
        or _format_display_time(last.get("at")),
    }

