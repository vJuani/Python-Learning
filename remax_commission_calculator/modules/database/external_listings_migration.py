"""SQLite schema for indexed external listings (Phase 5A.1)."""

from __future__ import annotations

from .connection import get_connection


EXTERNAL_LISTINGS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS external_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_url TEXT,
    address TEXT,
    neighborhood TEXT,
    jurisdiction TEXT,
    property_type TEXT,
    purpose TEXT,
    price REAL,
    currency TEXT,
    rooms INTEGER,
    bedrooms INTEGER,
    bathrooms INTEGER,
    covered_m2 REAL,
    total_m2 REAL,
    parking_spaces INTEGER,
    features_json TEXT,
    description TEXT,
    images_json TEXT,
    commercial_status TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_updated_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT,
    duplicate_group_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,

    UNIQUE (organization_id, source, external_id),
    CHECK (source IN (
        'remax',
        'zonaprop',
        'argenprop',
        'mercadolibre'
    )),
    CHECK (currency IS NULL OR currency IN ('USD', 'ARS')),
    CHECK (is_active IN (0, 1))
)
"""

EXTERNAL_LISTINGS_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_external_listings_org_source
    ON external_listings (organization_id, source, is_active)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_external_listings_org_active
    ON external_listings (organization_id, is_active, last_seen_at)
    """,
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_external_listings_sqlite():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(EXTERNAL_LISTINGS_CREATE_SQL)
        for statement in EXTERNAL_LISTINGS_INDEXES:
            cursor.execute(statement)

        if _column_exists(cursor, "agent_tasks", "id") and not _column_exists(
            cursor,
            "agent_tasks",
            "external_listing_id",
        ):
            cursor.execute(
                """
                ALTER TABLE agent_tasks
                ADD COLUMN external_listing_id INTEGER
                """
            )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_tasks_external_listing
            ON agent_tasks (organization_id, external_listing_id)
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
