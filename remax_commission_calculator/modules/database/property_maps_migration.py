"""SQLite location columns for properties (Maps V1). Idempotent."""

from __future__ import annotations

from .connection import get_connection


LOCATION_COLUMNS = (
    ("formatted_address", "TEXT"),
    ("locality", "TEXT"),
    ("administrative_area", "TEXT"),
    ("country", "TEXT"),
    ("postal_code", "TEXT"),
    ("google_place_id", "TEXT"),
    ("latitude", "REAL"),
    ("longitude", "REAL"),
    ("geocoded_at", "TEXT"),
    ("geocode_status", "TEXT"),
)

LOCATION_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_properties_org_coords
    ON properties (organization_id, latitude, longitude)
    """,
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_property_maps_sqlite():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        for column_name, definition in LOCATION_COLUMNS:
            if not _column_exists(cursor, "properties", column_name):
                cursor.execute(
                    f"ALTER TABLE properties ADD COLUMN {column_name} {definition}"
                )
        for statement in LOCATION_INDEXES:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
