"""Columns for a public inquiry on the existing interaction row.

``contact_property_interactions`` has no metadata JSON column. The inquiry
message, origin, and shortlist stay in their own columns.
"""

from __future__ import annotations

import re

from modules.config import BACKEND_POSTGRES, get_database_backend

from .connection import get_connection


SOURCE_CHECK = (
    "'manual', 'whatsapp', 'agenda', 'operation', 'other', "
    "'public_property', 'public_shortlist'"
)

INTERACTION_COLUMNS = (
    ("shortlist_id", "INTEGER", "BIGINT"),
    ("source", "TEXT", "TEXT"),
    ("message", "TEXT", "TEXT"),
    ("listing_agent_id", "INTEGER", "BIGINT"),
    ("attention", "TEXT", "TEXT"),
    ("related_contact_id", "INTEGER", "BIGINT"),
    ("visitor_email", "TEXT", "TEXT"),
)

CONTACT_INDEXES = (
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
    CREATE INDEX IF NOT EXISTS idx_contacts_phone_norm
    ON contacts (organization_id, agent_id, phone_normalized)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_contacts_email_norm
    ON contacts (organization_id, agent_id, email_normalized)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_contacts_next_follow_up
    ON contacts (organization_id, agent_id, next_follow_up_at)
    """,
)


def _widen_contact_source_sqlite(connection):
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contacts'"
    ).fetchone()
    if row is None or not row[0] or "public_property" in row[0]:
        return
    widened = re.sub(
        r"CHECK \(source IN \([^)]*\)\)",
        f"CHECK (source IN ({SOURCE_CHECK}))",
        row[0],
        count=1,
    )
    columns = [
        info[1]
        for info in connection.execute("PRAGMA table_info(contacts)").fetchall()
    ]
    quoted = ", ".join(columns)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("ALTER TABLE contacts RENAME TO contacts_source_old")
    connection.execute(widened)
    connection.execute(
        f"INSERT INTO contacts ({quoted}) SELECT {quoted} FROM contacts_source_old"
    )
    connection.execute("DROP TABLE contacts_source_old")
    for statement in CONTACT_INDEXES:
        connection.execute(statement)
    connection.execute("PRAGMA foreign_keys = ON")


def _add_sqlite_columns(connection):
    existing = {
        info[1]
        for info in connection.execute(
            "PRAGMA table_info(contact_property_interactions)"
        ).fetchall()
    }
    if not existing:
        return
    for name, sqlite_type, _postgres_type in INTERACTION_COLUMNS:
        if name not in existing:
            connection.execute(
                f"ALTER TABLE contact_property_interactions "
                f"ADD COLUMN {name} {sqlite_type}"
            )


def migrate_inbound_inquiry_sqlite():
    connection = get_connection()
    try:
        _widen_contact_source_sqlite(connection)
        _add_sqlite_columns(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def migrate_inbound_inquiry_postgres(cursor):
    for name, _sqlite_type, postgres_type in INTERACTION_COLUMNS:
        cursor.execute(
            f"""
            ALTER TABLE contact_property_interactions
            ADD COLUMN IF NOT EXISTS {name} {postgres_type}
            """
        )
    cursor.execute(
        f"""
        DO $$
        DECLARE constraint_row RECORD;
        BEGIN
            IF to_regclass('contacts') IS NULL THEN
                RETURN;
            END IF;
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'contacts'::regclass
                    AND contype = 'c'
                    AND pg_get_constraintdef(oid) ILIKE '%public_property%'
            ) THEN
                RETURN;
            END IF;
            FOR constraint_row IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'contacts'::regclass
                    AND contype = 'c'
                    AND pg_get_constraintdef(oid) ILIKE '%source%'
                    AND pg_get_constraintdef(oid) ILIKE '%whatsapp%'
            LOOP
                EXECUTE format(
                    'ALTER TABLE contacts DROP CONSTRAINT %I',
                    constraint_row.conname
                );
            END LOOP;
            ALTER TABLE contacts
            ADD CONSTRAINT ck_contacts_source
            CHECK (source IN ({SOURCE_CHECK}));
        END
        $$;
        """
    )


def migrate_inbound_inquiry():
    if get_database_backend() == BACKEND_POSTGRES:
        return
    migrate_inbound_inquiry_sqlite()
