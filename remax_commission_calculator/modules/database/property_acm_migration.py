"""Idempotent ACM tables for SQLite (FASE 4G)."""

from __future__ import annotations

from .connection import get_connection


PROPERTY_ACMS_SQL = """
CREATE TABLE IF NOT EXISTS property_acms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    property_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    currency TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finalized_at TEXT,
    created_by_user_id INTEGER,
    estimated_value TEXT,
    suggested_min_value TEXT,
    suggested_max_value TEXT,
    median_price_per_m2 TEXT,
    average_price_per_m2 TEXT,
    notes TEXT,
    explanation TEXT,
    area_basis TEXT,
    metrics_json TEXT,
    subject_snapshot_json TEXT,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,

    CHECK (status IN ('draft', 'ready', 'finalized', 'archived'))
)
"""

PROPERTY_ACM_COMPARABLES_SQL = """
CREATE TABLE IF NOT EXISTS property_acm_comparables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    acm_id INTEGER NOT NULL,
    comparable_property_id INTEGER,
    source_type TEXT NOT NULL,
    external_reference TEXT,
    selected INTEGER NOT NULL DEFAULT 1,
    exclusion_reason TEXT,
    snapshot_price TEXT,
    snapshot_currency TEXT,
    snapshot_total_area TEXT,
    snapshot_covered_area TEXT,
    snapshot_rooms INTEGER,
    snapshot_bedrooms INTEGER,
    snapshot_property_type TEXT,
    snapshot_location TEXT,
    snapshot_price_per_m2 TEXT,
    snapshot_listing_purpose TEXT,
    snapshot_price_kind TEXT,
    snapshot_operation_id INTEGER,
    snapshot_operation_date TEXT,
    score TEXT,
    is_outlier INTEGER NOT NULL DEFAULT 0,
    distance_meters TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (acm_id)
        REFERENCES property_acms(id) ON DELETE RESTRICT,
    FOREIGN KEY (comparable_property_id)
        REFERENCES properties(id) ON DELETE SET NULL
)
"""

ACM_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_property_acms_agent
    ON property_acms (organization_id, agent_id, status, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_property_acm_comparables_acm
    ON property_acm_comparables (organization_id, acm_id, selected)
    """,
)

ACM_EXTRA_COLUMNS = (
    ("explanation", "TEXT"),
    ("area_basis", "TEXT"),
    ("metrics_json", "TEXT"),
    ("subject_snapshot_json", "TEXT"),
)

COMPARABLE_EXTRA_COLUMNS = (
    ("snapshot_listing_purpose", "TEXT"),
    ("snapshot_price_kind", "TEXT"),
    ("snapshot_operation_id", "INTEGER"),
    ("snapshot_operation_date", "TEXT"),
    ("score", "TEXT"),
    ("is_outlier", "INTEGER NOT NULL DEFAULT 0"),
    ("distance_meters", "TEXT"),
    ("notes", "TEXT"),
    ("snapshot_bathrooms", "INTEGER"),
    ("snapshot_parking", "INTEGER"),
    ("snapshot_url", "TEXT"),
    ("snapshot_observed_at", "TEXT"),
    ("area_source", "TEXT"),
    ("area_override_by_user_id", "INTEGER"),
    ("score_reasons_json", "TEXT"),
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_property_acm_sqlite():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(PROPERTY_ACMS_SQL)
        cursor.execute(PROPERTY_ACM_COMPARABLES_SQL)
        for column_name, column_sql in ACM_EXTRA_COLUMNS:
            if not _column_exists(cursor, "property_acms", column_name):
                cursor.execute(
                    f"ALTER TABLE property_acms ADD COLUMN {column_name} {column_sql}"
                )
        for column_name, column_sql in COMPARABLE_EXTRA_COLUMNS:
            if not _column_exists(cursor, "property_acm_comparables", column_name):
                cursor.execute(
                    f"ALTER TABLE property_acm_comparables ADD COLUMN {column_name} {column_sql}"
                )
        for statement in ACM_INDEXES:
            cursor.execute(statement)
        for column_name, column_sql in (
            ("external_source", "TEXT"),
            ("external_updated_at", "TEXT"),
            ("sync_status", "TEXT"),
        ):
            if not _column_exists(cursor, "properties", column_name):
                cursor.execute(
                    f"ALTER TABLE properties ADD COLUMN {column_name} {column_sql}"
                )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_properties_org_source_external
            ON properties (
                organization_id,
                external_source,
                external_id
            )
            WHERE external_source IS NOT NULL
              AND TRIM(external_source) != ''
              AND external_id IS NOT NULL
              AND TRIM(external_id) != ''
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
