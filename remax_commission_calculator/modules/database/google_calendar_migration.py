"""
Google Calendar connections (Phase 4B.2).

Refresh tokens are stored encrypted. Access tokens are short-lived and
cached until ``access_expires_at``. Sync tokens let a pull resume
without replaying the whole calendar.
"""

from __future__ import annotations

from .connection import get_connection


GOOGLE_CALENDAR_CONNECTIONS_SQL = """
CREATE TABLE IF NOT EXISTS google_calendar_connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    google_email TEXT,
    calendar_id TEXT NOT NULL DEFAULT 'primary',
    refresh_token_encrypted TEXT NOT NULL,
    access_token_encrypted TEXT,
    access_expires_at TEXT,
    sync_token TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_synced_at TEXT,
    last_error TEXT,
    events_cache_json TEXT,
    connected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE,

    CHECK (status IN ('active', 'error', 'revoked'))
)
"""

GOOGLE_CALENDAR_INDEXES = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    idx_google_calendar_user
    ON google_calendar_connections (organization_id, user_id)
    """,
)

GOOGLE_CALENDAR_EXTRA_COLUMNS = (
    ("events_cache_json", "TEXT"),
    ("sync_token", "TEXT"),
    ("last_error", "TEXT"),
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_google_calendar_sqlite():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(GOOGLE_CALENDAR_CONNECTIONS_SQL)

        for statement in GOOGLE_CALENDAR_INDEXES:
            cursor.execute(statement)

        for column_name, column_sql in GOOGLE_CALENDAR_EXTRA_COLUMNS:
            if not _column_exists(
                cursor,
                "google_calendar_connections",
                column_name,
            ):
                cursor.execute(
                    f"""
                    ALTER TABLE google_calendar_connections
                    ADD COLUMN {column_name} {column_sql}
                    """
                )

        if not _column_exists(cursor, "agent_tasks", "google_event_id"):
            cursor.execute(
                """
                ALTER TABLE agent_tasks
                ADD COLUMN google_event_id TEXT
                """
            )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_tasks_google_event
            ON agent_tasks (organization_id, google_event_id)
            """
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
