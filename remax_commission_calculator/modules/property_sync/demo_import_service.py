"""RedREMAX JSON demo import. Reuses the real normalizer and Property Sync Hub."""

from __future__ import annotations

import logging
from datetime import datetime

from modules.database.properties_repository import get_property_record
from modules.database.property_media_repository import list_property_media
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

logger = logging.getLogger(__name__)

PHOTO_REASON_KEYS = {
    "empty": "redremax_demo_photo_reason_empty",
    "not_url": "redremax_demo_photo_reason_not_url",
    "http": "redremax_demo_photo_reason_http",
    "invalid_host": "redremax_demo_photo_reason_invalid_host",
}


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

    photo_preview = _aggregate_photo_preview(organization_id, prepared)
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
        **photo_preview,
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
        "photos_detected": 0,
        "photos_valid": 0,
        "photos_created": 0,
        "photos_existing": 0,
        "photos_rejected": 0,
        "photo_notes": [],
        "unknown_hosts": [],
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
                _merge_photo_confirm_stats(stats, hub, result.get("media") or {})
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

    stats["photos_new"] = stats["photos_created"]
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


def _existing_redremax_media_ids(organization_id, external_id):
    existing = find_property_by_external_identity(
        organization_id,
        external_source=PROVIDER_REDREMAX,
        external_id=external_id,
    )
    if not existing:
        return set()
    return {
        item.get("external_media_id")
        for item in list_property_media(organization_id, existing["id"])
        if item.get("source") == PROVIDER_REDREMAX and item.get("external_media_id")
    }


def _primary_reject_reason(reasons):
    reasons = reasons or {}
    if not reasons:
        return None
    return max(reasons.items(), key=lambda item: item[1])[0]


def _photo_note(hub):
    audit = hub.get("photo_audit") or {}
    external_id = hub.get("external_id")
    payload_count = int(audit.get("payload_count") or 0)
    valid_count = int(audit.get("valid_count") or 0)
    if payload_count == 0:
        return {
            "external_id": external_id,
            "code": "no_photos",
            "message_key": "redremax_warn_no_photos",
        }
    if valid_count == 0:
        reason = _primary_reject_reason(audit.get("reject_reasons"))
        return {
            "external_id": external_id,
            "code": "photos_rejected",
            "message_key": "redremax_warn_photos_rejected",
            "reason": reason,
            "reason_key": PHOTO_REASON_KEYS.get(reason),
            "unknown_hosts": list(audit.get("unknown_hosts") or []),
        }
    return None


def _log_photo_audit(external_id, audit, media_stats=None):
    media_stats = media_stats or {}
    logger.info(
        "redremax photo audit external_id=%s photos_count_payload=%s photos_valid_count=%s media_created=%s media_updated=%s media_rejected=%s reject_reasons=%s unknown_hosts=%s",
        external_id or "-",
        int((audit or {}).get("payload_count") or 0),
        int((audit or {}).get("valid_count") or 0),
        int(media_stats.get("created") or 0),
        int(media_stats.get("unchanged") or 0),
        int((audit or {}).get("rejected_count") or 0),
        dict((audit or {}).get("reject_reasons") or {}),
        list((audit or {}).get("unknown_hosts") or []),
    )


def _aggregate_photo_preview(organization_id, hubs):
    photos_detected = 0
    photos_valid = 0
    photos_new = 0
    photos_existing = 0
    photos_rejected = 0
    photo_notes = []
    unknown_hosts = []
    for hub in hubs or []:
        if hub.get("_skipped"):
            continue
        audit = hub.get("photo_audit") or {}
        photos_detected += int(audit.get("payload_count") or 0)
        photos_valid += int(audit.get("valid_count") or 0)
        photos_rejected += int(audit.get("rejected_count") or 0)
        selected_ids = {
            item.get("external_media_id")
            for item in (hub.get("media") or [])
            if item.get("external_media_id")
        }
        existing_ids = _existing_redremax_media_ids(
            organization_id, hub.get("external_id")
        )
        photos_new += len(selected_ids - existing_ids)
        photos_existing += len(selected_ids & existing_ids)
        for host in audit.get("unknown_hosts") or []:
            if host not in unknown_hosts:
                unknown_hosts.append(host)
        note = _photo_note(hub)
        if note:
            photo_notes.append(note)
        _log_photo_audit(hub.get("external_id"), audit)
    return {
        "photos_detected": photos_detected,
        "photos_valid": photos_valid,
        "photos_new": photos_new,
        "photos_existing": photos_existing,
        "photos_rejected": photos_rejected,
        "photo_notes": photo_notes,
        "unknown_hosts": unknown_hosts,
    }


def _merge_photo_confirm_stats(stats, hub, media_stats):
    audit = hub.get("photo_audit") or {}
    stats["photos_detected"] += int(audit.get("payload_count") or 0)
    stats["photos_valid"] += int(audit.get("valid_count") or 0)
    stats["photos_rejected"] += int(audit.get("rejected_count") or 0)
    stats["photos_created"] += int(media_stats.get("created") or 0)
    stats["photos_existing"] += int(media_stats.get("unchanged") or 0)
    for host in audit.get("unknown_hosts") or []:
        if host not in stats["unknown_hosts"]:
            stats["unknown_hosts"].append(host)
    note = _photo_note(hub)
    if note:
        stats["photo_notes"].append(note)
    _log_photo_audit(hub.get("external_id"), audit, media_stats)

