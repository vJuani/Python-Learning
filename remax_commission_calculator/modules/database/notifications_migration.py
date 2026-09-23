"""
Safe SQLite migration for informational notification events.

Adds the idempotency key and explicit read timestamp used by the
Pending Center. Existing rows keep their ``is_read`` flag; ``read_at``
is only backfilled for rows already marked as read.
"""

from __future__ import annotations

from .connection import get_connection


NOTIFICATION_COLUMNS = (
    ("event_key", "TEXT"),
    ("read_at", "TEXT"),
    ("priority", "TEXT"),
)

# Dedupe is per recipient: the same logical event_key may legitimately
# reach several users of one organization. The new index is strictly
# weaker than the legacy (organization_id, event_key) one, so existing
# rows always satisfy it; it is created before the legacy index is
# dropped so uniqueness is never absent. Valid on SQLite and Postgres.
USER_EVENT_KEY_INDEX_STATEMENTS = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
    idx_notifications_user_event_key
    ON notifications (
        organization_id,
        user_id,
        event_key
    )
    WHERE event_key IS NOT NULL
        AND event_key <> ''
    """,
    "DROP INDEX IF EXISTS idx_notifications_event_key",
)


def _table_exists(cursor, table_name):
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
            AND name = ?
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_notification_events_sqlite():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        if not _table_exists(cursor, "notifications"):
            return

        for column_name, column_sql in NOTIFICATION_COLUMNS:
            if not _column_exists(
                cursor,
                "notifications",
                column_name,
            ):
                cursor.execute(
                    f"""
                    ALTER TABLE notifications
                    ADD COLUMN {column_name} {column_sql}
                    """
                )

        cursor.execute(
            """
            UPDATE notifications
            SET read_at = created_at
            WHERE is_read = 1
                AND (read_at IS NULL OR read_at = '')
            """
        )

        for statement in USER_EVENT_KEY_INDEX_STATEMENTS:
            cursor.execute(statement)

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
            ON notifications (
                organization_id,
                user_id,
                is_read,
                id
            )
            """
        )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
