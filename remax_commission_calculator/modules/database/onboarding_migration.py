"""Onboarding columns and the persistent invite rate table."""

from __future__ import annotations

from modules.config import BACKEND_POSTGRES, get_database_backend

from .connection import get_connection


SETTINGS_COLUMNS = (
    ("country", "TEXT"),
    ("region", "TEXT"),
    ("city", "TEXT"),
    ("marketing_website", "TEXT"),
    ("onboarding_checklist_dismissed_at", "TEXT"),
)


def _sqlite_column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def _ensure_settings_columns(cursor, postgres):
    for column_name, column_sql in SETTINGS_COLUMNS:
        if postgres:
            cursor.execute(
                f"""
                ALTER TABLE organization_settings
                ADD COLUMN IF NOT EXISTS {column_name} {column_sql}
                """
            )
            continue
        if not _sqlite_column_exists(
            cursor,
            "organization_settings",
            column_name,
        ):
            cursor.execute(
                f"""
                ALTER TABLE organization_settings
                ADD COLUMN {column_name} {column_sql}
                """
            )


def _ensure_rate_table(cursor, postgres):
    if postgres:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS onboarding_rate_events (
                id BIGSERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS onboarding_rate_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_onboarding_rate_events_type
        ON onboarding_rate_events (event_type)
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_organization_settings_registration_code
        ON organization_settings (registration_code_hash)
        WHERE registration_code_hash IS NOT NULL
            AND registration_code_hash != ''
        """
    )


def migrate_onboarding():
    postgres = get_database_backend() == BACKEND_POSTGRES
    connection = get_connection()
    cursor = connection.cursor()
    try:
        _ensure_settings_columns(cursor, postgres)
        _ensure_rate_table(cursor, postgres)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
