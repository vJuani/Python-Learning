"""Idempotent Property Sync Hub tables (FASE 5F). SQLite + PostgreSQL."""

from __future__ import annotations

from .connection import get_connection, get_database_backend
from modules.config import BACKEND_POSTGRES


PROPERTY_SYNC_COLUMNS = (
    ("external_url", "TEXT"),
    ("sync_error", "TEXT"),
    ("sync_hash", "TEXT"),
    ("is_externally_managed", "INTEGER NOT NULL DEFAULT 0"),
    ("external_status", "TEXT"),
    ("location_source", "TEXT"),
    ("sync_overrides_json", "TEXT"),
)

INTEGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS organization_property_integrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'disconnected',
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    config_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT
)
"""

RUNS_SQL = """
CREATE TABLE IF NOT EXISTS property_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    integration_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    stats_json TEXT,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (integration_id)
        REFERENCES organization_property_integrations(id) ON DELETE RESTRICT
)
"""

RUN_ITEMS_SQL = """
CREATE TABLE IF NOT EXISTS property_sync_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    external_id TEXT,
    property_id INTEGER,
    outcome TEXT NOT NULL,
    message TEXT,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id)
        REFERENCES property_sync_runs(id) ON DELETE RESTRICT,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE SET NULL
)
"""

AGENT_MAP_SQL = """
CREATE TABLE IF NOT EXISTS external_agent_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    external_agent_id TEXT NOT NULL,
    agent_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT
)
"""

MEDIA_SQL = """
CREATE TABLE IF NOT EXISTS property_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    property_id INTEGER NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'photo',
    source TEXT NOT NULL,
    external_media_id TEXT,
    original_url TEXT,
    storage_key TEXT,
    storage_strategy TEXT NOT NULL DEFAULT 'remote_reference',
    url_kind TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    is_cover INTEGER NOT NULL DEFAULT 0,
    width INTEGER,
    height INTEGER,
    content_type TEXT,
    content_hash TEXT,
    external_updated_at TEXT,
    last_synced_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE RESTRICT
)
"""

CONFLICTS_SQL = """
CREATE TABLE IF NOT EXISTS property_sync_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    existing_property_id INTEGER,
    address_external TEXT,
    address_existing TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (existing_property_id)
        REFERENCES properties(id) ON DELETE SET NULL
)
"""

INDEXES = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    idx_org_property_integrations_provider
    ON organization_property_integrations (organization_id, provider)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    idx_property_sync_runs_org
    ON property_sync_runs (organization_id, provider, started_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    idx_property_sync_run_items_run
    ON property_sync_run_items (organization_id, run_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    idx_external_agent_mappings_identity
    ON external_agent_mappings (organization_id, source, external_agent_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS
    idx_property_media_property
    ON property_media (organization_id, property_id, position)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    idx_property_media_external
    ON property_media (organization_id, source, external_media_id)
    WHERE external_media_id IS NOT NULL
      AND TRIM(external_media_id) != ''
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    idx_property_sync_conflicts_open
    ON property_sync_conflicts (organization_id, provider, external_id)
    WHERE status = 'open'
    """,
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_property_sync_hub_sqlite():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(INTEGRATIONS_SQL)
        cursor.execute(RUNS_SQL)
        cursor.execute(RUN_ITEMS_SQL)
        cursor.execute(AGENT_MAP_SQL)
        cursor.execute(MEDIA_SQL)
        cursor.execute(CONFLICTS_SQL)
        for column_name, column_sql in PROPERTY_SYNC_COLUMNS:
            if not _column_exists(cursor, "properties", column_name):
                cursor.execute(
                    f"ALTER TABLE properties ADD COLUMN {column_name} {column_sql}"
                )
        if not _column_exists(cursor, "properties", "external_source"):
            cursor.execute("ALTER TABLE properties ADD COLUMN external_source TEXT")
        if not _column_exists(cursor, "properties", "external_updated_at"):
            cursor.execute(
                "ALTER TABLE properties ADD COLUMN external_updated_at TEXT"
            )
        if not _column_exists(cursor, "properties", "sync_status"):
            cursor.execute("ALTER TABLE properties ADD COLUMN sync_status TEXT")
        for statement in INDEXES:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_property_sync_hub_postgres(cursor):
    pg_integrations = INTEGRATIONS_SQL.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
    ).replace("INTEGER NOT NULL", "BIGINT NOT NULL")
    pg_runs = (
        RUNS_SQL.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        .replace("integration_id INTEGER NOT NULL", "integration_id BIGINT NOT NULL")
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("INTEGER NOT NULL DEFAULT", "INTEGER NOT NULL DEFAULT")
    )
    pg_items = (
        RUN_ITEMS_SQL.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("run_id INTEGER NOT NULL", "run_id BIGINT NOT NULL")
        .replace("property_id INTEGER", "property_id BIGINT")
    )
    pg_agents = (
        AGENT_MAP_SQL.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("agent_id INTEGER NOT NULL", "agent_id BIGINT NOT NULL")
    )
    pg_media = (
        MEDIA_SQL.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("property_id INTEGER NOT NULL", "property_id BIGINT NOT NULL")
    )
    pg_conflicts = (
        CONFLICTS_SQL.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("existing_property_id INTEGER", "existing_property_id BIGINT")
    )
    for statement in (
        pg_integrations,
        pg_runs,
        pg_items,
        pg_agents,
        pg_media,
        pg_conflicts,
    ):
        cursor.execute(statement)
    for column_name, column_sql in PROPERTY_SYNC_COLUMNS + (
        ("external_source", "TEXT"),
        ("external_updated_at", "TEXT"),
        ("sync_status", "TEXT"),
    ):
        cursor.execute(
            f"""
            ALTER TABLE properties
            ADD COLUMN IF NOT EXISTS {column_name} {column_sql}
            """
        )
    for statement in INDEXES:
        cursor.execute(statement.replace("TRIM(", "BTRIM("))


def migrate_property_sync_hub():
    if get_database_backend() == BACKEND_POSTGRES:
        connection = get_connection()
        cursor = connection.cursor()
        try:
            migrate_property_sync_hub_postgres(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    migrate_property_sync_hub_sqlite()
