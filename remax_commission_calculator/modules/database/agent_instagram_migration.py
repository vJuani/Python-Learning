"""Agent Instagram handle. Idempotent SQLite + Postgres."""

from __future__ import annotations

from modules.config import BACKEND_POSTGRES, get_database_backend
from modules.database.connection import get_connection


COLUMN_NAME = "instagram_handle"


def _sqlite_column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def migrate_agent_instagram_sqlite():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        if not _sqlite_column_exists(cursor, "agents", COLUMN_NAME):
            cursor.execute(f"ALTER TABLE agents ADD COLUMN {COLUMN_NAME} TEXT")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_agent_instagram_postgres(cursor):
    cursor.execute(
        f"ALTER TABLE agents ADD COLUMN IF NOT EXISTS {COLUMN_NAME} TEXT"
    )


def migrate_agent_instagram():
    if get_database_backend() == BACKEND_POSTGRES:
        connection = get_connection()
        try:
            cursor = connection.cursor()
            migrate_agent_instagram_postgres(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    migrate_agent_instagram_sqlite()
