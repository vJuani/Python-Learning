"""SQLite inventory enrichment for properties (Phase 4A)."""

from __future__ import annotations

from .connection import get_connection


INVENTORY_COLUMNS = (
    (
        "listing_currency",
        "TEXT CHECK (listing_currency IS NULL OR listing_currency IN ('USD', 'ARS'))",
    ),
    ("neighborhood", "TEXT"),
    ("rooms", "INTEGER"),
    ("bedrooms", "INTEGER"),
    ("bathrooms", "INTEGER"),
    ("covered_m2", "REAL"),
    ("total_m2", "REAL"),
    ("parking_spaces", "INTEGER"),
    ("description", "TEXT"),
    (
        "commercial_status",
        "TEXT CHECK (commercial_status IS NULL OR commercial_status IN "
        "('available', 'reserved', 'sold', 'rented', 'withdrawn'))",
    ),
    ("features_json", "TEXT"),
)

INVENTORY_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_properties_org_commercial
    ON properties (organization_id, commercial_status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_properties_org_neighborhood
    ON properties (organization_id, neighborhood)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_properties_org_purpose_currency
    ON properties (organization_id, listing_purpose, listing_currency)
    """,
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def backfill_unique_listing_currency(cursor):
    """
    Copy listing_currency from external listings only when exactly
    one reliable currency exists for that property.
    """
    if not _column_exists(cursor, "properties", "listing_currency"):
        return 0
    if not _column_exists(cursor, "property_external_listings", "listing_currency"):
        return 0

    rows = cursor.execute(
        """
        SELECT
            property_id,
            listing_currency
        FROM property_external_listings
        WHERE listing_currency IN ('USD', 'ARS')
        """
    ).fetchall()

    by_property = {}
    for property_id, currency in rows:
        by_property.setdefault(property_id, set()).add(currency)

    updated = 0
    for property_id, currencies in by_property.items():
        if len(currencies) != 1:
            continue
        currency = next(iter(currencies))
        cursor.execute(
            """
            UPDATE properties
            SET listing_currency = ?
            WHERE id = ?
                AND (listing_currency IS NULL OR listing_currency = '')
            """,
            (currency, property_id),
        )
        updated += cursor.rowcount
    return updated


def migrate_property_inventory_sqlite():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        for column_name, definition in INVENTORY_COLUMNS:
            if not _column_exists(cursor, "properties", column_name):
                cursor.execute(
                    f"ALTER TABLE properties ADD COLUMN {column_name} {definition}"
                )

        if _column_exists(cursor, "property_change_requests", "id"):
            if not _column_exists(
                cursor,
                "property_change_requests",
                "proposed_listing_currency",
            ):
                cursor.execute(
                    """
                    ALTER TABLE property_change_requests
                    ADD COLUMN proposed_listing_currency TEXT
                    CHECK (
                        proposed_listing_currency IS NULL
                        OR proposed_listing_currency IN ('USD', 'ARS')
                    )
                    """
                )

        for statement in INVENTORY_INDEXES:
            cursor.execute(statement)

        backfill_unique_listing_currency(cursor)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
