"""Idempotent RedREMAX/external price snapshots. Does not touch JRH FX."""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def upsert_external_price_history(organization_id, property_id, source, rows):
    organization_id = require_organization_id(organization_id)
    created = 0
    for row in rows or []:
        price = row.get("price")
        currency = str(row.get("currency") or "").strip() or None
        observed_on = str(row.get("date") or "").strip() or None
        if price in (None, "") or not observed_on:
            continue
        connection = get_connection()
        try:
            existing = connection.execute(
                """
                SELECT id FROM external_property_price_history
                WHERE organization_id = ? AND property_id = ? AND source = ?
                  AND observed_on = ? AND price = ?
                  AND COALESCE(currency, '') = COALESCE(?, '')
                """,
                (
                    organization_id,
                    property_id,
                    source,
                    observed_on,
                    str(price),
                    currency,
                ),
            ).fetchone()
            if existing:
                continue
            execute_insert(
                connection.cursor(),
                """
                INSERT INTO external_property_price_history (
                    organization_id, property_id, source, price, currency,
                    observed_on, usd_value, local_value, local_currency,
                    exchange_rate_snapshot, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    property_id,
                    source,
                    str(price),
                    currency,
                    observed_on,
                    None if row.get("usd_value") is None else str(row.get("usd_value")),
                    None if row.get("local_value") is None else str(row.get("local_value")),
                    row.get("local_currency"),
                    None
                    if row.get("exchange_rate_snapshot") is None
                    else str(row.get("exchange_rate_snapshot")),
                    _now_iso(),
                ),
            )
            connection.commit()
            created += 1
        finally:
            connection.close()
    return created


def list_external_price_history(organization_id, property_id, source=None):
    organization_id = require_organization_id(organization_id)
    sql = """
        SELECT id, price, currency, observed_on, usd_value, local_value,
               local_currency, exchange_rate_snapshot
        FROM external_property_price_history
        WHERE organization_id = ? AND property_id = ?
    """
    params = [organization_id, property_id]
    if source:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY observed_on, id"
    connection = get_connection()
    try:
        rows = connection.execute(sql, params).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row[0],
            "price": row[1],
            "currency": row[2],
            "date": row[3],
            "usd_value": row[4],
            "local_value": row[5],
            "local_currency": row[6],
            "exchange_rate_snapshot": row[7],
        }
        for row in rows
    ]
