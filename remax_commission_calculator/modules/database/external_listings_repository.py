"""Indexed external listings. Never writes into properties."""

from __future__ import annotations

import json
from datetime import datetime

from modules.listing_sources import (
    SOURCE_INTERNAL,
    require_listing_source,
)
from modules.listings_normalize import listing_content_hash, listing_from_external_listing
from modules.property_features import features_to_json, normalize_property_features
from modules.property_types import (
    normalize_listing_purpose,
    normalize_property_type,
)

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


UPSERT_CREATED = "created"
UPSERT_UNCHANGED = "unchanged"
UPSERT_UPDATED = "updated"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _images_to_json(images):
    if not images:
        return None
    if isinstance(images, str):
        try:
            images = json.loads(images)
        except (TypeError, ValueError):
            return None
    if not isinstance(images, list):
        return None
    cleaned = []
    for item in images:
        if isinstance(item, str) and item.strip():
            cleaned.append(item.strip())
        elif isinstance(item, dict) and item.get("url"):
            cleaned.append({"url": str(item["url"]).strip()})
    if not cleaned:
        return None
    return json.dumps(cleaned, ensure_ascii=False)


def _images_from_json(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _optional_text(value):
    text = str(value or "").strip()
    return text or None


def _build_listing(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "source": row[2],
        "external_id": row[3],
        "external_url": row[4],
        "address": row[5],
        "neighborhood": row[6],
        "jurisdiction": row[7],
        "property_type": row[8],
        "purpose": row[9],
        "price": row[10],
        "currency": row[11],
        "rooms": row[12],
        "bedrooms": row[13],
        "bathrooms": row[14],
        "covered_m2": row[15],
        "total_m2": row[16],
        "parking_spaces": row[17],
        "features_json": row[18],
        "features": normalize_property_features(row[18]),
        "description": row[19],
        "images_json": row[20],
        "images": _images_from_json(row[20]),
        "commercial_status": row[21],
        "first_seen_at": row[22],
        "last_seen_at": row[23],
        "source_updated_at": row[24],
        "is_active": bool(row[25]),
        "content_hash": row[26],
        "duplicate_group_id": row[27],
        "created_at": row[28],
        "updated_at": row[29],
    }


_SELECT = """
    SELECT
        id,
        organization_id,
        source,
        external_id,
        external_url,
        address,
        neighborhood,
        jurisdiction,
        property_type,
        purpose,
        price,
        currency,
        rooms,
        bedrooms,
        bathrooms,
        covered_m2,
        total_m2,
        parking_spaces,
        features_json,
        description,
        images_json,
        commercial_status,
        first_seen_at,
        last_seen_at,
        source_updated_at,
        is_active,
        content_hash,
        duplicate_group_id,
        created_at,
        updated_at
    FROM external_listings
"""


def _payload_from_record(record):
    source = require_listing_source(record.get("source"))
    if source == SOURCE_INTERNAL:
        raise ValueError("internal_source_not_allowed")
    external_id = _optional_text(record.get("external_id"))
    if not external_id:
        raise ValueError("external_id_required")

    listing = listing_from_external_listing({**record, "source": source})
    return {
        "source": source,
        "external_id": external_id,
        "external_url": listing.get("external_url"),
        "address": listing.get("address"),
        "neighborhood": listing.get("neighborhood"),
        "jurisdiction": listing.get("jurisdiction"),
        "property_type": normalize_property_type(listing.get("property_type")),
        "purpose": normalize_listing_purpose(listing.get("purpose")),
        "price": listing.get("price"),
        "currency": listing.get("currency"),
        "rooms": listing.get("rooms"),
        "bedrooms": listing.get("bedrooms"),
        "bathrooms": listing.get("bathrooms"),
        "covered_m2": listing.get("covered_m2"),
        "total_m2": listing.get("total_m2"),
        "parking_spaces": listing.get("parking_spaces"),
        "features_json": features_to_json(listing.get("features")),
        "description": listing.get("description"),
        "images_json": _images_to_json(listing.get("images")),
        "commercial_status": listing.get("commercial_status"),
        "source_updated_at": _optional_text(record.get("source_updated_at")),
        "content_hash": listing_content_hash(listing),
        "duplicate_group_id": _optional_text(record.get("duplicate_group_id")),
    }


def get_external_listing(listing_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            _SELECT + " WHERE id = ? AND organization_id = ?",
            (listing_id, organization_id),
        ).fetchone()
    finally:
        connection.close()
    return _build_listing(row)


def get_external_listing_by_source_id(organization_id, source, external_id):
    organization_id = require_organization_id(organization_id)
    source = require_listing_source(source)
    connection = get_connection()
    try:
        row = connection.execute(
            _SELECT
            + """
            WHERE organization_id = ?
                AND source = ?
                AND external_id = ?
            """,
            (organization_id, source, str(external_id).strip()),
        ).fetchone()
    finally:
        connection.close()
    return _build_listing(row)


def list_external_listings(
    organization_id,
    *,
    source=None,
    is_active=None,
    limit=300,
):
    organization_id = require_organization_id(organization_id)
    clauses = ["organization_id = ?"]
    params = [organization_id]
    if source is not None:
        clauses.append("source = ?")
        params.append(require_listing_source(source))
    if is_active is not None:
        clauses.append("is_active = ?")
        params.append(1 if is_active else 0)
    connection = get_connection()
    try:
        rows = connection.execute(
            _SELECT
            + f"""
            WHERE {" AND ".join(clauses)}
            ORDER BY id
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()
    finally:
        connection.close()
    return [_build_listing(row) for row in rows]


def list_active_external_listings(organization_id, **kwargs):
    return list_external_listings(
        organization_id,
        is_active=True,
        **kwargs,
    )


def mark_external_listing_seen(listing_id, organization_id):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE external_listings
            SET last_seen_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (now, listing_id, organization_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_external_listing(listing_id, organization_id)


def mark_external_listing_inactive(listing_id, organization_id):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE external_listings
            SET is_active = 0,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (now, listing_id, organization_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_external_listing(listing_id, organization_id)


def upsert_external_listing(organization_id, record):
    organization_id = require_organization_id(organization_id)
    payload = _payload_from_record(record)
    now = _now_iso()
    existing = get_external_listing_by_source_id(
        organization_id,
        payload["source"],
        payload["external_id"],
    )

    if existing is None:
        connection = get_connection()
        try:
            listing_id = execute_insert(
                connection.cursor(),
                """
                INSERT INTO external_listings (
                    organization_id,
                    source,
                    external_id,
                    external_url,
                    address,
                    neighborhood,
                    jurisdiction,
                    property_type,
                    purpose,
                    price,
                    currency,
                    rooms,
                    bedrooms,
                    bathrooms,
                    covered_m2,
                    total_m2,
                    parking_spaces,
                    features_json,
                    description,
                    images_json,
                    commercial_status,
                    first_seen_at,
                    last_seen_at,
                    source_updated_at,
                    is_active,
                    content_hash,
                    duplicate_group_id,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, 1, ?, ?, ?, ?
                )
                """,
                (
                    organization_id,
                    payload["source"],
                    payload["external_id"],
                    payload["external_url"],
                    payload["address"],
                    payload["neighborhood"],
                    payload["jurisdiction"],
                    payload["property_type"],
                    payload["purpose"],
                    payload["price"],
                    payload["currency"],
                    payload["rooms"],
                    payload["bedrooms"],
                    payload["bathrooms"],
                    payload["covered_m2"],
                    payload["total_m2"],
                    payload["parking_spaces"],
                    payload["features_json"],
                    payload["description"],
                    payload["images_json"],
                    payload["commercial_status"],
                    now,
                    now,
                    payload["source_updated_at"],
                    payload["content_hash"],
                    payload["duplicate_group_id"],
                    now,
                    now,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {
            "status": UPSERT_CREATED,
            "listing": get_external_listing(listing_id, organization_id),
        }

    if existing.get("content_hash") == payload["content_hash"]:
        listing = mark_external_listing_seen(existing["id"], organization_id)
        if not listing.get("is_active"):
            connection = get_connection()
            try:
                connection.execute(
                    """
                    UPDATE external_listings
                    SET is_active = 1,
                        updated_at = ?
                    WHERE id = ?
                        AND organization_id = ?
                    """,
                    (now, existing["id"], organization_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            listing = get_external_listing(existing["id"], organization_id)
        return {"status": UPSERT_UNCHANGED, "listing": listing}

    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE external_listings
            SET external_url = ?,
                address = ?,
                neighborhood = ?,
                jurisdiction = ?,
                property_type = ?,
                purpose = ?,
                price = ?,
                currency = ?,
                rooms = ?,
                bedrooms = ?,
                bathrooms = ?,
                covered_m2 = ?,
                total_m2 = ?,
                parking_spaces = ?,
                features_json = ?,
                description = ?,
                images_json = ?,
                commercial_status = ?,
                last_seen_at = ?,
                source_updated_at = ?,
                is_active = 1,
                content_hash = ?,
                updated_at = ?
            WHERE id = ?
                AND organization_id = ?
            """,
            (
                payload["external_url"],
                payload["address"],
                payload["neighborhood"],
                payload["jurisdiction"],
                payload["property_type"],
                payload["purpose"],
                payload["price"],
                payload["currency"],
                payload["rooms"],
                payload["bedrooms"],
                payload["bathrooms"],
                payload["covered_m2"],
                payload["total_m2"],
                payload["parking_spaces"],
                payload["features_json"],
                payload["description"],
                payload["images_json"],
                payload["commercial_status"],
                now,
                payload["source_updated_at"],
                payload["content_hash"],
                now,
                existing["id"],
                organization_id,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "status": UPSERT_UPDATED,
        "listing": get_external_listing(existing["id"], organization_id),
    }
