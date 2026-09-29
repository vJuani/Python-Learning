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


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _quote_ident(name):
    if not _IDENT.fullmatch(name or ""):
        raise RuntimeError(f"unsafe SQL identifier: {name}")
    return f'"{name}"'


def _suspend_foreign_keys(connection):
    """Apply outside a transaction so SQLite honors the pragma."""
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.commit()
    connection.execute("PRAGMA legacy_alter_table = ON")
    connection.commit()


def _restore_foreign_keys(connection):
    connection.execute("PRAGMA legacy_alter_table = OFF")
    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")
    connection.commit()


def _rows_mentioning_old_contacts(connection):
    return connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql LIKE '%contacts_source_old%'
        """
    ).fetchall()


def _rebuild_child_table(connection, name, create_sql):
    """Copy every row into a table whose FKs reference contacts again."""
    quoted_name = _quote_ident(name)
    columns = [
        info[1]
        for info in connection.execute(
            f"PRAGMA table_info({quoted_name})"
        ).fetchall()
    ]
    if not columns:
        raise RuntimeError(f"missing table {name}")
    indexes = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL
        """,
        (name,),
    ).fetchall()
    triggers = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'trigger' AND tbl_name = ? AND sql IS NOT NULL
        """,
        (name,),
    ).fetchall()
    match = _CREATE_TABLE.search(create_sql or "")
    if match is None:
        raise RuntimeError(f"cannot parse CREATE for {name}")
    temp = f"{name}__fk_repair"
    fixed = (
        (create_sql[: match.start(1)] + _quote_ident(temp) + create_sql[match.end(1) :])
        .replace("contacts_source_old", "contacts")
    )
    before = connection.execute(
        f"SELECT COUNT(*) FROM {quoted_name}"
    ).fetchone()[0]
    connection.execute(fixed)
    quoted_columns = ", ".join(_quote_ident(column) for column in columns)
    connection.execute(
        f"INSERT INTO {_quote_ident(temp)} ({quoted_columns}) "
        f"SELECT {quoted_columns} FROM {quoted_name}"
    )
    after = connection.execute(
        f"SELECT COUNT(*) FROM {_quote_ident(temp)}"
    ).fetchone()[0]
    if before != after:
        raise RuntimeError(
            f"row count mismatch repairing {name}: {before} != {after}"
        )
    connection.execute(f"DROP TABLE {quoted_name}")
    connection.execute(
        f"ALTER TABLE {_quote_ident(temp)} RENAME TO {quoted_name}"
    )
    for (index_sql,) in indexes:
        if index_sql:
            connection.execute(index_sql.replace("contacts_source_old", "contacts"))
    for (trigger_sql,) in triggers:
        if trigger_sql:
            connection.execute(trigger_sql.replace("contacts_source_old", "contacts"))


def repair_rewritten_contact_foreign_keys(connection):
    """Rebuild any object still pointing at the temporary contacts name.

    SQLite rewrites child FOREIGN KEY clauses when a parent is renamed.
    The source-check migration used to rename contacts to
    contacts_source_old and then drop that name, leaving children
    referencing a table that no longer exists.
    """
    if not _rows_mentioning_old_contacts(connection):
        return []
    _suspend_foreign_keys(connection)
    repaired = []
    try:
        connection.execute("BEGIN")
        for row_type, name, _table, sql in _rows_mentioning_old_contacts(connection):
            if row_type != "table":
                continue
            _rebuild_child_table(connection, name, sql)
            repaired.append(name)
        for row_type, name, _table, sql in _rows_mentioning_old_contacts(connection):
            quoted = _quote_ident(name)
            rewritten = (sql or "").replace("contacts_source_old", "contacts")
            if row_type == "index":
                connection.execute(f"DROP INDEX IF EXISTS {quoted}")
                if rewritten:
                    connection.execute(rewritten)
            elif row_type == "trigger":
                connection.execute(f"DROP TRIGGER IF EXISTS {quoted}")
                if rewritten:
                    connection.execute(rewritten)
            elif row_type == "view":
                connection.execute(f"DROP VIEW IF EXISTS {quoted}")
                if rewritten:
                    connection.execute(rewritten)
        connection.commit()
        return repaired
    except Exception:
        connection.rollback()
        raise
    finally:
        _restore_foreign_keys(connection)


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
    if widened == row[0]:
        return
    columns = [
        info[1]
        for info in connection.execute("PRAGMA table_info(contacts)").fetchall()
    ]
    quoted = ", ".join(_quote_ident(column) for column in columns)
    _suspend_foreign_keys(connection)
    try:
        connection.execute("BEGIN")
        # legacy_alter_table keeps child FKs on "contacts" during the rename.
        connection.execute("ALTER TABLE contacts RENAME TO contacts_source_old")
        connection.execute(widened)
        connection.execute(
            f"INSERT INTO contacts ({quoted}) "
            f"SELECT {quoted} FROM contacts_source_old"
        )
        connection.execute("DROP TABLE contacts_source_old")
        for statement in CONTACT_INDEXES:
            connection.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        _restore_foreign_keys(connection)


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
        repair_rewritten_contact_foreign_keys(connection)
        _widen_contact_source_sqlite(connection)
        repair_rewritten_contact_foreign_keys(connection)
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
