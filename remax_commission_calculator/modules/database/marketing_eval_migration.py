"""Idempotent internal Marketing IA evaluation tables. Not user-facing."""

from __future__ import annotations

from .connection import get_connection, get_database_backend
from modules.config import BACKEND_POSTGRES


RUNS_SQL = """
CREATE TABLE IF NOT EXISTS marketing_eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    property_id INTEGER,
    property_label TEXT,
    case_slug TEXT,
    origin TEXT NOT NULL DEFAULT 'listing',
    format TEXT NOT NULL,
    style TEXT NOT NULL,
    tone TEXT NOT NULL,
    notes TEXT,
    variant_label TEXT,
    group_id TEXT,
    prompt_version TEXT,
    model TEXT,
    provider TEXT,
    input_summary_json TEXT,
    creative_brief TEXT,
    output_json TEXT,
    rendered_text TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    duration_ms INTEGER,
    error TEXT,
    score TEXT,
    issues_json TEXT
)
"""

INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_eval_runs_org
    ON marketing_eval_runs (organization_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_eval_runs_group
    ON marketing_eval_runs (organization_id, group_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_marketing_eval_runs_property
    ON marketing_eval_runs (organization_id, property_id, created_at)
    """,
)


def migrate_marketing_eval_sqlite():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(RUNS_SQL)
        for statement in INDEXES:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_marketing_eval_postgres(cursor):
    sql = (
        RUNS_SQL.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        .replace("organization_id INTEGER NOT NULL", "organization_id BIGINT NOT NULL")
        .replace("created_by_user_id INTEGER,", "created_by_user_id BIGINT,")
        .replace("property_id INTEGER,", "property_id BIGINT,")
    )
    cursor.execute(sql)
    for statement in INDEXES:
        cursor.execute(statement)


def migrate_marketing_eval():
    if get_database_backend() == BACKEND_POSTGRES:
        connection = get_connection()
        cursor = connection.cursor()
        try:
            migrate_marketing_eval_postgres(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    migrate_marketing_eval_sqlite()
