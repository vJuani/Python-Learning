"""Idempotent Contactos V2 columns and helper tables. No data wipe."""

from __future__ import annotations

from .connection import get_connection


EXTRA_COLUMNS = (
    ("first_name", "TEXT"),
    ("last_name", "TEXT"),
    ("company", "TEXT"),
    ("contact_type", "TEXT"),
    ("archived_at", "TEXT"),
    ("phone_normalized", "TEXT"),
    ("email_normalized", "TEXT"),
    ("source_type", "TEXT"),
)

INTERACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS contact_property_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    property_id INTEGER,
    interaction_type TEXT NOT NULL,
    activity_id INTEGER,
    label TEXT,
    created_at TEXT NOT NULL,

    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE RESTRICT,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE RESTRICT
)
"""

IMPORTS_SQL = """
CREATE TABLE IF NOT EXISTS contact_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    import_token TEXT NOT NULL,
    source_type TEXT NOT NULL,
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (organization_id, agent_id, import_token)
)
"""


def migrate_contacts_v2_sqlite():
    connection = get_connection()
    cursor = connection.cursor()
    try:
        existing = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(contacts)").fetchall()
        }
        if existing:
            for name, definition in EXTRA_COLUMNS:
                if name not in existing:
                    cursor.execute(f"ALTER TABLE contacts ADD COLUMN {name} {definition}")
        cursor.execute(INTERACTIONS_SQL)
        cursor.execute(IMPORTS_SQL)
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contacts_phone_norm
            ON contacts (organization_id, agent_id, phone_normalized)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contacts_email_norm
            ON contacts (organization_id, agent_id, email_normalized)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contact_interactions_owner
            ON contact_property_interactions (organization_id, contact_id, created_at)
            """
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
