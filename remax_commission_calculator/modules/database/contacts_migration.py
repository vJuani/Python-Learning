"""
SQLite migration for the light contacts CRM.

``visibility`` is private in v1. team/organization values are reserved
so sharing later does not require a table rebuild.
"""

from __future__ import annotations

from .connection import get_connection


CONTACTS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'lead',
    source TEXT NOT NULL DEFAULT 'manual',
    visibility TEXT NOT NULL DEFAULT 'private',
    notes TEXT,
    preferences_json TEXT,
    last_interacted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT,

    CHECK (status IN ('lead', 'active', 'inactive', 'closed')),
    CHECK (source IN ('manual', 'whatsapp', 'agenda', 'operation', 'other')),
    CHECK (visibility IN ('private', 'team', 'organization'))
)
"""

CONTACTS_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_contacts_owner
    ON contacts (organization_id, agent_id, status, updated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_contacts_org_visibility
    ON contacts (organization_id, visibility, agent_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_contacts_org_name
    ON contacts (organization_id, name)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_contact
    ON agent_tasks (organization_id, contact_id)
    """,
)


def _column_exists(cursor, table_name, column_name):
    rows = cursor.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()
    return any(row[1] == column_name for row in rows)


def migrate_contacts_sqlite():
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(CONTACTS_CREATE_SQL)

        if not _column_exists(cursor, "agent_tasks", "contact_id"):
            cursor.execute(
                """
                ALTER TABLE agent_tasks
                ADD COLUMN contact_id INTEGER
                """
            )

        for statement in CONTACTS_INDEXES:
            cursor.execute(statement)

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
