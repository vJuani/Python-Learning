"""Canonical marketing logo columns on organization_settings."""

from __future__ import annotations

from modules.config import BACKEND_POSTGRES, get_database_backend
from modules.database.connection import get_connection


COLUMNS = (
    ("marketing_logo_path", "TEXT"),
    ("marketing_logo_source", "TEXT"),
    ("marketing_logo_updated_at", "TEXT"),
)


def _sqlite_column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def migrate_organization_marketing_logo_sqlite():
    connection = get_connection()
    try:
        cursor = connection.cursor()
        for column_name, column_sql in COLUMNS:
            if not _sqlite_column_exists(cursor, "organization_settings", column_name):
                cursor.execute(
                    f"ALTER TABLE organization_settings ADD COLUMN {column_name} {column_sql}"
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_organization_marketing_logo_postgres(cursor):
    for column_name, column_sql in COLUMNS:
        cursor.execute(
            f"""
            ALTER TABLE organization_settings
            ADD COLUMN IF NOT EXISTS {column_name} {column_sql}
            """
        )


def migrate_organization_marketing_logo():
    if get_database_backend() == BACKEND_POSTGRES:
        connection = get_connection()
        try:
            cursor = connection.cursor()
            migrate_organization_marketing_logo_postgres(cursor)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return
    migrate_organization_marketing_logo_sqlite()
