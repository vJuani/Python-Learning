"""ACM persistence. Organization-scoped. No physical deletes."""

from __future__ import annotations

import json
from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


STATUS_DRAFT = "draft"
STATUS_READY = "ready"
STATUS_FINALIZED = "finalized"
STATUS_ARCHIVED = "archived"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _json_load(raw):
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_dump(payload):
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False, default=str)


def _acm_dict(row):
    if not row:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "property_id": row[3],
        "status": row[4],
        "currency": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "finalized_at": row[8],
        "created_by_user_id": row[9],
        "estimated_value": row[10],
        "suggested_min_value": row[11],
        "suggested_max_value": row[12],
        "median_price_per_m2": row[13],
        "average_price_per_m2": row[14],
        "notes": row[15],
        "explanation": row[16] if len(row) > 16 else None,
        "area_basis": row[17] if len(row) > 17 else None,
        "metrics": _json_load(row[18] if len(row) > 18 else None),
        "subject_snapshot": _json_load(row[19] if len(row) > 19 else None),
    }


def _comp_dict(row):
    if not row:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "acm_id": row[2],
        "comparable_property_id": row[3],
        "source_type": row[4],
        "external_reference": row[5],
        "selected": bool(row[6]),
        "exclusion_reason": row[7],
        "snapshot_price": row[8],
        "snapshot_currency": row[9],
        "snapshot_total_area": row[10],
        "snapshot_covered_area": row[11],
        "snapshot_rooms": row[12],
        "snapshot_bedrooms": row[13],
        "snapshot_property_type": row[14],
        "snapshot_location": row[15],
        "snapshot_price_per_m2": row[16],
        "snapshot_listing_purpose": row[17] if len(row) > 17 else None,
        "snapshot_price_kind": row[18] if len(row) > 18 else None,
        "snapshot_operation_id": row[19] if len(row) > 19 else None,
        "snapshot_operation_date": row[20] if len(row) > 20 else None,
        "score": row[21] if len(row) > 21 else None,
        "is_outlier": bool(row[22]) if len(row) > 22 else False,
        "distance_meters": row[23] if len(row) > 23 else None,
        "notes": row[24] if len(row) > 24 else None,
        "created_at": row[25] if len(row) > 25 else None,
        "snapshot_bathrooms": row[26] if len(row) > 26 else None,
        "snapshot_parking": row[27] if len(row) > 27 else None,
        "snapshot_url": row[28] if len(row) > 28 else None,
        "snapshot_observed_at": row[29] if len(row) > 29 else None,
        "area_source": row[30] if len(row) > 30 else None,
        "area_override_by_user_id": row[31] if len(row) > 31 else None,
        "score_reasons": _json_load(row[32] if len(row) > 32 else None),
    }


ACM_SELECT = """
    SELECT id, organization_id, agent_id, property_id, status, currency,
           created_at, updated_at, finalized_at, created_by_user_id,
           estimated_value, suggested_min_value, suggested_max_value,
           median_price_per_m2, average_price_per_m2, notes,
           explanation, area_basis, metrics_json, subject_snapshot_json
    FROM property_acms
"""

COMP_SELECT = """
    SELECT id, organization_id, acm_id, comparable_property_id, source_type,
           external_reference, selected, exclusion_reason, snapshot_price,
           snapshot_currency, snapshot_total_area, snapshot_covered_area,
           snapshot_rooms, snapshot_bedrooms, snapshot_property_type,
           snapshot_location, snapshot_price_per_m2, snapshot_listing_purpose,
           snapshot_price_kind, snapshot_operation_id, snapshot_operation_date,
           score, is_outlier, distance_meters, notes, created_at,
           snapshot_bathrooms, snapshot_parking, snapshot_url, snapshot_observed_at,
           area_source, area_override_by_user_id, score_reasons_json
    FROM property_acm_comparables
"""


def create_acm(
    organization_id,
    *,
    agent_id,
    property_id,
    created_by_user_id,
    currency="",
    subject_snapshot=None,
    area_basis="",
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        acm_id = execute_insert(
            cursor,
            """
            INSERT INTO property_acms (
                organization_id, agent_id, property_id, status, currency,
                created_at, updated_at, created_by_user_id, area_basis,
                subject_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                agent_id,
                property_id,
                STATUS_DRAFT,
                currency or None,
                now,
                now,
                created_by_user_id,
                area_basis or None,
                _json_dump(subject_snapshot),
            ),
        )
        connection.commit()
        return acm_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_acm(acm_id, organization_id, *, agent_id=None):
    organization_id = require_organization_id(organization_id)
    sql = ACM_SELECT + " WHERE id = ? AND organization_id = ?"
    params = [acm_id, organization_id]
    if agent_id is not None:
        sql += " AND agent_id = ?"
        params.append(agent_id)
    connection = get_connection()
    try:
        row = connection.execute(sql, params).fetchone()
        return _acm_dict(row)
    finally:
        connection.close()


def list_acms(organization_id, *, agent_id, limit=50):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            ACM_SELECT
            + " WHERE organization_id = ? AND agent_id = ?"
            + " AND status != ?"
            + " ORDER BY created_at DESC LIMIT ?",
            (organization_id, agent_id, STATUS_ARCHIVED, int(limit)),
        ).fetchall()
        return [_acm_dict(row) for row in rows]
    finally:
        connection.close()


def update_acm(acm_id, organization_id, **fields):
    organization_id = require_organization_id(organization_id)
    allowed = {
        "status",
        "currency",
        "finalized_at",
        "estimated_value",
        "suggested_min_value",
        "suggested_max_value",
        "median_price_per_m2",
        "average_price_per_m2",
        "notes",
        "explanation",
        "area_basis",
        "metrics_json",
        "subject_snapshot_json",
    }
    assignments = []
    params = []
    for key, value in fields.items():
        if key == "metrics":
            key = "metrics_json"
            value = _json_dump(value)
        if key == "subject_snapshot":
            key = "subject_snapshot_json"
            value = _json_dump(value)
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        params.append(value)
    if not assignments:
        return
    assignments.append("updated_at = ?")
    params.append(_now_iso())
    params.extend([acm_id, organization_id])
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"UPDATE property_acms SET {', '.join(assignments)} "
            "WHERE id = ? AND organization_id = ?",
            params,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def add_comparable(organization_id, acm_id, payload):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        comp_id = execute_insert(
            cursor,
            """
            INSERT INTO property_acm_comparables (
                organization_id, acm_id, comparable_property_id, source_type,
                external_reference, selected, exclusion_reason, snapshot_price,
                snapshot_currency, snapshot_total_area, snapshot_covered_area,
                snapshot_rooms, snapshot_bedrooms, snapshot_property_type,
                snapshot_location, snapshot_price_per_m2, snapshot_listing_purpose,
                snapshot_price_kind, snapshot_operation_id, snapshot_operation_date,
                score, is_outlier, distance_meters, notes, created_at,
                snapshot_bathrooms, snapshot_parking, snapshot_url, snapshot_observed_at,
                area_source, area_override_by_user_id, score_reasons_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                organization_id,
                acm_id,
                payload.get("comparable_property_id"),
                payload.get("source_type"),
                payload.get("external_reference"),
                1 if payload.get("selected", True) else 0,
                payload.get("exclusion_reason"),
                payload.get("snapshot_price"),
                payload.get("snapshot_currency"),
                payload.get("snapshot_total_area"),
                payload.get("snapshot_covered_area"),
                payload.get("snapshot_rooms"),
                payload.get("snapshot_bedrooms"),
                payload.get("snapshot_property_type"),
                payload.get("snapshot_location"),
                payload.get("snapshot_price_per_m2"),
                payload.get("snapshot_listing_purpose"),
                payload.get("snapshot_price_kind"),
                payload.get("snapshot_operation_id"),
                payload.get("snapshot_operation_date"),
                payload.get("score"),
                1 if payload.get("is_outlier") else 0,
                payload.get("distance_meters"),
                payload.get("notes"),
                now,
                payload.get("snapshot_bathrooms"),
                payload.get("snapshot_parking"),
                payload.get("snapshot_url"),
                payload.get("snapshot_observed_at"),
                payload.get("area_source"),
                payload.get("area_override_by_user_id"),
                _json_dump(payload.get("score_reasons")),
            ),
        )
        connection.commit()
        return comp_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_comparables(acm_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            COMP_SELECT + " WHERE acm_id = ? AND organization_id = ? ORDER BY id",
            (acm_id, organization_id),
        ).fetchall()
        return [_comp_dict(row) for row in rows]
    finally:
        connection.close()


def get_comparable(comp_id, organization_id, *, acm_id=None):
    organization_id = require_organization_id(organization_id)
    sql = COMP_SELECT + " WHERE id = ? AND organization_id = ?"
    params = [comp_id, organization_id]
    if acm_id is not None:
        sql += " AND acm_id = ?"
        params.append(acm_id)
    connection = get_connection()
    try:
        return _comp_dict(connection.execute(sql, params).fetchone())
    finally:
        connection.close()


def update_comparable(comp_id, organization_id, **fields):
    organization_id = require_organization_id(organization_id)
    allowed = {
        "selected",
        "exclusion_reason",
        "snapshot_price",
        "snapshot_currency",
        "snapshot_total_area",
        "snapshot_covered_area",
        "snapshot_rooms",
        "snapshot_bedrooms",
        "snapshot_property_type",
        "snapshot_location",
        "snapshot_price_per_m2",
        "score",
        "is_outlier",
        "notes",
        "snapshot_bathrooms",
        "snapshot_parking",
        "snapshot_url",
        "snapshot_observed_at",
        "area_source",
        "area_override_by_user_id",
        "score_reasons_json",
    }
    assignments = []
    params = []
    for key, value in fields.items():
        if key == "score_reasons":
            key = "score_reasons_json"
            value = _json_dump(value)
        if key not in allowed:
            continue
        if key in {"selected", "is_outlier"}:
            value = 1 if value else 0
        assignments.append(f"{key} = ?")
        params.append(value)
    if not assignments:
        return
    params.extend([comp_id, organization_id])
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"UPDATE property_acm_comparables SET {', '.join(assignments)} "
            "WHERE id = ? AND organization_id = ?",
            params,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_internal_candidates(
    organization_id,
    *,
    exclude_property_id,
    listing_purpose=None,
    property_type=None,
    currency=None,
    neighborhood=None,
    jurisdiction=None,
    min_area=None,
    max_area=None,
    area_column="covered_m2",
    limit=80,
):
    organization_id = require_organization_id(organization_id)
    if area_column not in {"covered_m2", "total_m2"}:
        area_column = "covered_m2"
    clauses = [
        "p.organization_id = ?",
        "p.id != ?",
        "p.status = 'approved'",
    ]
    params = [organization_id, exclude_property_id]
    if listing_purpose:
        clauses.append("(p.listing_purpose = ? OR p.listing_purpose IS NULL)")
        params.append(listing_purpose)
    if property_type:
        clauses.append("(p.property_type = ? OR p.property_type IS NULL)")
        params.append(property_type)
    if currency:
        clauses.append("(p.listing_currency = ? OR p.listing_currency IS NULL)")
        params.append(currency)
    zone_bits = []
    if neighborhood:
        zone_bits.append("LOWER(COALESCE(p.neighborhood, '')) = LOWER(?)")
        params.append(neighborhood)
    elif jurisdiction:
        zone_bits.append("p.jurisdiction = ?")
        params.append(jurisdiction)
    if zone_bits:
        clauses.append("(" + " OR ".join(zone_bits) + ")")
    if min_area is not None:
        clauses.append(f"(p.{area_column} IS NULL OR p.{area_column} >= ?)")
        params.append(str(min_area))
    if max_area is not None:
        clauses.append(f"(p.{area_column} IS NULL OR p.{area_column} <= ?)")
        params.append(str(max_area))
    params.append(int(limit))
    sql = f"""
        SELECT p.id, p.address, p.jurisdiction, p.organization_id, p.agent_id,
               p.property_type, p.listing_price, p.listing_purpose,
               p.listing_currency, p.neighborhood, p.rooms, p.bedrooms,
               p.covered_m2, p.total_m2, p.parking_spaces, p.commercial_status,
               p.features_json, p.last_synced_at, p.bathrooms, p.external_id
        FROM properties p
        WHERE {' AND '.join(clauses)}
        LIMIT ?
    """
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
        results = []
        for row in rows:
            results.append(
                {
                    "id": row[0],
                    "address": row[1],
                    "jurisdiction": row[2],
                    "organization_id": row[3],
                    "agent_id": row[4],
                    "property_type": row[5],
                    "listing_price": row[6],
                    "listing_purpose": row[7],
                    "listing_currency": row[8],
                    "neighborhood": row[9],
                    "rooms": row[10],
                    "bedrooms": row[11],
                    "covered_m2": row[12],
                    "total_m2": row[13],
                    "parking_spaces": row[14],
                    "commercial_status": row[15],
                    "features_json": row[16],
                    "last_synced_at": row[17],
                    "bathrooms": row[18],
                    "external_id": row[19],
                }
            )
        return results
    finally:
        connection.close()


def list_closed_candidates(
    organization_id,
    *,
    exclude_property_id,
    listing_purpose=None,
    property_type=None,
    currency=None,
    neighborhood=None,
    jurisdiction=None,
    limit=40,
):
    organization_id = require_organization_id(organization_id)
    clauses = [
        "o.organization_id = ?",
        "p.organization_id = ?",
        "p.id != ?",
        "o.sale_price IS NOT NULL",
    ]
    params = [organization_id, organization_id, exclude_property_id]
    if property_type:
        clauses.append("(p.property_type = ? OR p.property_type IS NULL)")
        params.append(property_type)
    if currency:
        clauses.append("(o.currency = ? OR p.listing_currency = ?)")
        params.extend([currency, currency])
    if neighborhood:
        clauses.append(
            "(LOWER(p.neighborhood) = LOWER(?) OR p.jurisdiction = ?)"
        )
        params.extend([neighborhood, jurisdiction or ""])
    elif jurisdiction:
        clauses.append("p.jurisdiction = ?")
        params.append(jurisdiction)
    params.append(int(limit))
    sql = f"""
        SELECT p.id, p.address, p.jurisdiction, p.organization_id, p.agent_id,
               p.property_type, p.listing_price, p.listing_purpose,
               p.listing_currency, p.neighborhood, p.rooms, p.bedrooms,
               p.covered_m2, p.total_m2, p.parking_spaces, p.commercial_status,
               p.features_json, o.sale_price, o.currency, o.id, o.operation_date,
               p.bathrooms, p.external_id
        FROM operations o
        JOIN properties p ON p.id = o.property_id
        WHERE {' AND '.join(clauses)}
        ORDER BY o.id DESC
        LIMIT ?
    """
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
        results = []
        for row in rows:
            results.append(
                {
                    "id": row[0],
                    "address": row[1],
                    "jurisdiction": row[2],
                    "organization_id": row[3],
                    "agent_id": row[4],
                    "property_type": row[5],
                    "listing_price": row[6],
                    "listing_purpose": row[7],
                    "listing_currency": row[8],
                    "neighborhood": row[9],
                    "rooms": row[10],
                    "bedrooms": row[11],
                    "covered_m2": row[12],
                    "total_m2": row[13],
                    "parking_spaces": row[14],
                    "commercial_status": row[15],
                    "features_json": row[16],
                    "sale_price": row[17],
                    "operation_currency": row[18],
                    "operation_id": row[19],
                    "operation_date": row[20],
                    "bathrooms": row[21],
                    "external_id": row[22],
                }
            )
        return results
    finally:
        connection.close()
