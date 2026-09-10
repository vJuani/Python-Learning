"""Idempotent Marketing IA tables. SQLite + PostgreSQL."""

from __future__ import annotations

from .connection import get_connection, get_database_backend
from modules.config import BACKEND_POSTGRES


MARKETING_ASSETS_SQL = """
CREATE TABLE IF NOT EXISTS marketing_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER,
    created_by_user_id INTEGER,
    property_id INTEGER NOT NULL,
    generation_id TEXT NOT NULL,
    format TEXT NOT NULL,
    style TEXT NOT NULL,
    tone TEXT NOT NULL,
    template TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'generated',
    copy_snapshot_json TEXT,
    property_snapshot_json TEXT,
    agent_branding_snapshot_json TEXT,
    options_json TEXT,
    storage_key TEXT,
    pdf_storage_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE SET NULL,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,

    CHECK (status IN ('generated', 'selected', 'archived'))
)
"""

INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_assets_org_agent
    ON marketing_assets (organization_id, agent_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_assets_generation
    ON marketing_assets (organization_id, generation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_assets_property
    ON marketing_assets (organization_id, property_id, created_at)
    """,
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_marketing_sqlite():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(MARKETING_ASSETS_SQL)
        for statement in INDEXES:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_marketing_postgres(cursor):
    sql = (
        MARKETING_ASSETS_SQL.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("agent_id INTEGER,", "agent_id BIGINT,")
        .replace("created_by_user_id INTEGER,", "created_by_user_id BIGINT,")
        .replace("property_id INTEGER NOT NULL", "property_id BIGINT NOT NULL")
    )
    cursor.execute(sql)
    for statement in INDEXES:
        cursor.execute(statement.replace("TRIM(", "BTRIM("))


def migrate_marketing():
    if get_database_backend() == BACKEND_POSTGRES:
        connection = get_connection()
        cursor = connection.cursor()
        try:
            migrate_marketing_postgres(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    migrate_marketing_sqlite()
