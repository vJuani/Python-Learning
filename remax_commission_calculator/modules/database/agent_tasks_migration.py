"""
Safe SQLite migration for agent agenda tasks (Phase 4B).

``due_at`` holds a naive UTC ISO timestamp. Rendering converts it to the
organization timezone, so no ambiguous local time is ever persisted.

``related_entity_type``/``related_entity_id`` are generic on purpose so
future leads or contacts can be linked without another migration.
"""

from __future__ import annotations

from .connection import get_connection


AGENT_TASKS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    task_type TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT NOT NULL DEFAULT 'normal',
    property_id INTEGER,
    operation_id INTEGER,
    related_entity_type TEXT,
    related_entity_id INTEGER,
    contact_name TEXT,
    duration_minutes INTEGER,
    reminder_minutes INTEGER,
    attendance_status TEXT,
    outcome_json TEXT,
    google_event_id TEXT,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    completed_by_user_id INTEGER,
    cancelled_at TEXT,
    cancelled_by_user_id INTEGER,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT,
    FOREIGN KEY (property_id)
        REFERENCES properties(id) ON DELETE SET NULL,
    FOREIGN KEY (operation_id)
        REFERENCES operations(id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (completed_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (cancelled_by_user_id)
        REFERENCES users(id) ON DELETE SET NULL,

    CHECK (status IN ('pending', 'completed', 'cancelled')),
    CHECK (priority IN ('normal', 'high')),
    CHECK (task_type IN (
        'call',
        'visit',
        'meeting',
        'follow_up',
        'documentation',
        'valuation',
        'reminder',
        'other'
    ))
)
"""

AGENT_TASKS_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent_due
    ON agent_tasks (organization_id, agent_id, status, due_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_org_due
    ON agent_tasks (organization_id, status, due_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_property
    ON agent_tasks (organization_id, property_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_operation
    ON agent_tasks (organization_id, operation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_related
    ON agent_tasks (
        organization_id,
        related_entity_type,
        related_entity_id
    )
    """,
)

AGENT_TASKS_EXTRA_COLUMNS = (
    ("contact_name", "TEXT"),
    ("duration_minutes", "INTEGER"),
    ("reminder_minutes", "INTEGER"),
    ("attendance_status", "TEXT"),
    ("outcome_json", "TEXT"),
    ("google_event_id", "TEXT"),
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_agent_tasks_sqlite():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(AGENT_TASKS_CREATE_SQL)

        for column_name, column_sql in AGENT_TASKS_EXTRA_COLUMNS:
            if not _column_exists(cursor, "agent_tasks", column_name):
                cursor.execute(
                    f"""
                    ALTER TABLE agent_tasks
                    ADD COLUMN {column_name} {column_sql}
                    """
                )

        for statement in AGENT_TASKS_INDEXES:
            cursor.execute(statement)

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
