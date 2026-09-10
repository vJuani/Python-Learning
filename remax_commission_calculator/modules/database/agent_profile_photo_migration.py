"""Agent professional photo columns. Idempotent SQLite + Postgres."""

from __future__ import annotations

from modules.config import BACKEND_POSTGRES, get_database_backend
from modules.database.connection import get_connection


AGENT_PHOTO_COLUMNS = (
    ("profile_photo_key", "TEXT"),
    ("profile_photo_original_key", "TEXT"),
    ("profile_photo_mime", "TEXT"),
    ("profile_photo_width", "INTEGER"),
    ("profile_photo_height", "INTEGER"),
    ("profile_photo_updated_at", "TEXT"),
)


def _sqlite_column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def migrate_agent_profile_photo_sqlite():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        for name, sql_type in AGENT_PHOTO_COLUMNS:
            if _sqlite_column_exists(cursor, "agents", name):
                continue
            cursor.execute(f"ALTER TABLE agents ADD COLUMN {name} {sql_type}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_agent_profile_photo_postgres(cursor):
    for name, sql_type in AGENT_PHOTO_COLUMNS:
        mapped = "BIGINT" if sql_type == "INTEGER" else sql_type
        cursor.execute(
            f"ALTER TABLE agents ADD COLUMN IF NOT EXISTS {name} {mapped}"
        )


def migrate_agent_profile_photo():
    if get_database_backend() == BACKEND_POSTGRES:
        connection = get_connection()
        try:
            cursor = connection.cursor()
            migrate_agent_profile_photo_postgres(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    migrate_agent_profile_photo_sqlite()
