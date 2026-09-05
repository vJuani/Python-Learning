"""Explicit RE/MAX catalog import → external_listings. Never writes properties."""

from __future__ import annotations

from modules.database.csv_import_batches_repository import (
    create_csv_import_batch,
    delete_csv_import_batch,
    get_csv_import_batch,
)
from modules.database.external_listings_repository import (
    UPSERT_CREATED,
    UPSERT_UNCHANGED,
    UPSERT_UPDATED,
    get_external_listing_by_source_id,
    list_active_external_listings,
    mark_external_listing_inactive,
    upsert_external_listing,
)
from modules.database.tenant import require_organization_id
from modules.integrations.remax_export import parse_remax_export_bytes
from modules.listing_connectors.remax_export import RemaxExportConnector
from modules.listing_sources import SOURCE_REMAX
from modules.listings_normalize import listing_from_external_listing


MODE_CATALOG = "remax_catalog"
IMPORT_INCREMENTAL = "incremental"
IMPORT_SNAPSHOT = "full"

CATALOG_STATUS_TO_COMMERCIAL = {
    "active": "available",
    "reserved": "reserved",
    "negotiation": "available",
}

SNAPSHOT_LIST_LIMIT = 20000


class RemaxCatalogError(ValueError):
    pass


def _file_level_errors(parse_result):
    return [
        {
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
            "row_number": issue.row_number,
            "mlsid": issue.mlsid,
        }
        for issue in parse_result.issues
        if issue.code in ("missing_column", "too_many_rows", "no_data_rows")
        or issue.row_number is None
    ]


def catalog_row_errors(row):
    errors = []
    if not row.mlsid:
        errors.append("mlsid_required")
    if not row.address:
        errors.append("address_required")
    if any("Precio inválido" in item or "negativo" in item for item in row.blockers):
        errors.append("price_invalid")
    if any("Moneda no soportada" in item for item in row.blockers):
        errors.append("currency_invalid")
    if row.price is not None and not row.currency:
        errors.append("currency_required")
    if any("MLSID duplicado" in item for item in row.blockers):
        errors.append("mlsid_duplicate")
    return errors


def row_to_catalog_record(row):
    connector = RemaxExportConnector()
    return connector.record_from_source_row(row)


def preview_remax_catalog(
    organization_id,
    raw,
    *,
    filename,
    snapshot=False,
):
    organization_id = require_organization_id(organization_id)
    parse_result = parse_remax_export_bytes(raw, filename=filename)
    file_errors = [
        item
        for item in _file_level_errors(parse_result)
        if item["severity"] == "error"
    ]
    if file_errors and not parse_result.rows:
        raise RemaxCatalogError("remax_export_parse_failed")

    rows_preview = []
    records = []
    created = 0
    updated = 0
    invalid = 0
    error_rows = []

    for row in parse_result.rows:
        errors = catalog_row_errors(row)
        if errors:
            invalid += 1
            error_rows.append(
                {
                    "row_number": row.row_number,
                    "mlsid": row.mlsid,
                    "errors": errors,
                    "messages": list(row.blockers),
                }
            )
            rows_preview.append(
                {
                    "row_number": row.row_number,
                    "mlsid": row.mlsid,
                    "address": row.address,
                    "valid": False,
                    "errors": errors,
                }
            )
            continue

        record = row_to_catalog_record(row)
        existing = get_external_listing_by_source_id(
            organization_id,
            SOURCE_REMAX,
            record["external_id"],
        )
        action = "create" if existing is None else "update"
        if action == "create":
            created += 1
        else:
            updated += 1
        records.append(record)
        rows_preview.append(
            {
                "row_number": row.row_number,
                "mlsid": record["external_id"],
                "address": record.get("address"),
                "valid": True,
                "action": action,
            }
        )

    mode = IMPORT_SNAPSHOT if snapshot else IMPORT_INCREMENTAL
    can_confirm = bool(records) and not any(
        item["code"] in ("missing_column", "too_many_rows", "no_data_rows")
        for item in file_errors
    )
    preview = {
        "mode": MODE_CATALOG,
        "snapshot": snapshot,
        "import_kind": mode,
        "filename": filename,
        "detected": len(parse_result.rows),
        "created": created,
        "updated": updated,
        "invalid": invalid,
        "destination": "external_listings",
        "can_confirm": can_confirm,
        "file_errors": file_errors,
        "error_rows": error_rows[:50],
        "rows": rows_preview[:80],
    }
    payload = {
        "mode": MODE_CATALOG,
        "snapshot": snapshot,
        "filename": filename,
        "records": records,
    }
    return create_csv_import_batch(
        organization_id,
        filename=filename or "remax-catalog",
        payload=payload,
        preview=preview,
    )


def confirm_remax_catalog(organization_id, batch_id):
    organization_id = require_organization_id(organization_id)
    batch = get_csv_import_batch(batch_id, organization_id)
    if batch is None:
        raise RemaxCatalogError("remax_export_batch_missing")
    payload = batch.get("payload") or {}
    if payload.get("mode") != MODE_CATALOG:
        raise RemaxCatalogError("remax_catalog_wrong_mode")
    if not (batch.get("preview") or {}).get("can_confirm"):
        raise RemaxCatalogError("remax_catalog_empty")

    created = 0
    updated = 0
    unchanged = 0
    seen_ids = set()
    for record in payload.get("records") or []:
        result = upsert_external_listing(organization_id, record)
        listing = result["listing"]
        seen_ids.add(listing["external_id"])
        if result["status"] == UPSERT_CREATED:
            created += 1
        elif result["status"] == UPSERT_UPDATED:
            updated += 1
        else:
            unchanged += 1

    deactivated = 0
    if payload.get("snapshot"):
        for listing in list_active_external_listings(
            organization_id,
            source=SOURCE_REMAX,
            limit=SNAPSHOT_LIST_LIMIT,
        ):
            if listing["external_id"] not in seen_ids:
                mark_external_listing_inactive(listing["id"], organization_id)
                deactivated += 1

    delete_csv_import_batch(batch_id, organization_id)
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deactivated": deactivated,
        "snapshot": bool(payload.get("snapshot")),
        "imported": created + updated + unchanged,
    }


def cancel_remax_catalog(organization_id, batch_id):
    return delete_csv_import_batch(batch_id, organization_id)


def catalog_listing_from_record(record):
    return listing_from_external_listing(record)
