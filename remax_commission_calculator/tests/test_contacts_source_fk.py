"""SQLite must not leave child FKs pointing at contacts_source_old."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "contacts_source_fk.db")
os.environ.pop("DATABASE_URL", None)

from modules.database import add_agent, add_organization, create_tables
from modules.database.connection import get_connection
from modules.database.inbound_inquiry_migration import (
    migrate_inbound_inquiry_sqlite,
)


_OLD_CONTACTS = """
CREATE TABLE organizations (id INTEGER PRIMARY KEY);
CREATE TABLE agents (id INTEGER PRIMARY KEY);
INSERT INTO organizations (id) VALUES (1);
INSERT INTO agents (id) VALUES (1);
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'lead',
    source TEXT NOT NULL DEFAULT 'manual',
    visibility TEXT NOT NULL DEFAULT 'private',
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    phone_normalized TEXT,
    email_normalized TEXT,
    next_follow_up_at TEXT,
    CHECK (source IN ('manual', 'whatsapp', 'agenda', 'operation', 'other')),
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);
CREATE TABLE contact_property_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    property_id INTEGER,
    interaction_type TEXT NOT NULL,
    activity_id INTEGER,
    label TEXT,
    created_at TEXT NOT NULL,
    shortlist_id INTEGER,
    source TEXT,
    message TEXT,
    listing_agent_id INTEGER,
    attention TEXT,
    related_contact_id INTEGER,
    visitor_email TEXT,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE RESTRICT,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE RESTRICT
);
CREATE TABLE contact_notes (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE RESTRICT
);
"""


def _use_db(name):
    path = Path(_TEST_TMP.name) / name
    if path.exists():
        path.unlink()
    os.environ["DATABASE_PATH"] = str(path)
    os.environ.pop("DATABASE_URL", None)
    return path


def _fk_parent(connection, table, column="contact_id"):
    rows = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    for row in rows:
        if row[3] == column:
            return row[2]
    return None


def _old_name_rows(connection):
    return connection.execute(
        """
        SELECT type, name, sql
        FROM sqlite_master
        WHERE sql LIKE '%contacts_source_old%'
        """
    ).fetchall()


def _break_like_production(connection):
    """Rename contacts the way the old migration did, without legacy_alter_table."""
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.commit()
    connection.execute("PRAGMA legacy_alter_table = OFF")
    connection.commit()
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'contacts'"
    ).fetchone()[0]
    connection.execute("BEGIN")
    connection.execute("ALTER TABLE contacts RENAME TO contacts_source_old")
    renamed = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'contacts_source_old'"
    ).fetchone()[0]
    create_sql = renamed.replace("contacts_source_old", "contacts")
    if "CREATE TABLE contacts" not in create_sql and 'CREATE TABLE "contacts"' not in create_sql:
        create_sql = sql
    connection.execute(create_sql)
    columns = [
        info[1] for info in connection.execute("PRAGMA table_info(contacts_source_old)")
    ]
    quoted = ", ".join(f'"{column}"' for column in columns)
    connection.execute(
        f"INSERT INTO contacts ({quoted}) SELECT {quoted} FROM contacts_source_old"
    )
    connection.execute("DROP TABLE contacts_source_old")
    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")
    connection.commit()


class ContactsSourceForeignKeyTests(unittest.TestCase):
    def test_widen_keeps_child_foreign_keys_on_contacts(self):
        _use_db("widen.db")
        connection = sqlite3.connect(os.environ["DATABASE_PATH"])
        connection.executescript(_OLD_CONTACTS)
        connection.execute(
            """
            INSERT INTO contacts (
                organization_id, agent_id, name, source, created_at, updated_at
            ) VALUES (1, 1, 'Martín', 'manual', '2026-01-01', '2026-01-01')
            """
        )
        connection.execute(
            """
            INSERT INTO contact_property_interactions (
                organization_id, agent_id, contact_id, property_id,
                interaction_type, created_at, shortlist_id, source, message,
                listing_agent_id, attention, related_contact_id, visitor_email
            ) VALUES (1, 1, 1, 4, 'interested', '2026-01-02', 9, 'manual',
                      'quiero verla', 3, 'alta', 8, 'visita@example.com')
            """
        )
        connection.execute(
            "INSERT INTO contact_notes (id, contact_id, body) VALUES (1, 1, 'nota')"
        )
        connection.commit()
        connection.close()

        migrate_inbound_inquiry_sqlite()
        migrate_inbound_inquiry_sqlite()

        connection = get_connection()
        try:
            self.assertEqual(
                _fk_parent(connection, "contact_property_interactions"),
                "contacts",
            )
            self.assertEqual(_fk_parent(connection, "contact_notes"), "contacts")
            self.assertEqual(_old_name_rows(connection), [])
            kept = connection.execute(
                """
                SELECT interaction_type, shortlist_id, source, message,
                       listing_agent_id, attention, related_contact_id,
                       visitor_email, property_id, contact_id
                FROM contact_property_interactions
                """
            ).fetchone()
            self.assertEqual(
                tuple(kept),
                (
                    "interested",
                    9,
                    "manual",
                    "quiero verla",
                    3,
                    "alta",
                    8,
                    "visita@example.com",
                    4,
                    1,
                ),
            )
            self.assertEqual(
                connection.execute("SELECT body FROM contact_notes").fetchone()[0],
                "nota",
            )
            connection.execute(
                """
                INSERT INTO contact_property_interactions (
                    organization_id, agent_id, contact_id, property_id,
                    interaction_type, created_at
                ) VALUES (1, 1, 1, 4, 'shared', '2026-02-02')
                """
            )
            connection.commit()
            shared = connection.execute(
                """
                SELECT COUNT(*) FROM contact_property_interactions
                WHERE interaction_type = 'shared'
                """
            ).fetchone()[0]
            self.assertEqual(shared, 1)
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
        finally:
            connection.close()

    def test_create_tables_repairs_broken_production_without_losing_rows(self):
        _use_db("broken.db")
        create_tables(create_backup=False)
        org = add_organization("FK Repair")
        agent = add_agent("Agente", "Oficina", org)
        connection = get_connection()
        try:
            connection.execute(
                """
                INSERT INTO contacts (
                    organization_id, agent_id, name, source, status,
                    visibility, created_at, updated_at
                ) VALUES (?, ?, 'Martín', 'manual', 'lead', 'private',
                          '2026-01-01', '2026-01-01')
                """,
                (org, agent),
            )
            contact_id = connection.execute(
                "SELECT id FROM contacts WHERE name = 'Martín'"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO contact_property_interactions (
                    organization_id, agent_id, contact_id, property_id,
                    interaction_type, activity_id, label, created_at,
                    shortlist_id, source, message, listing_agent_id,
                    attention, related_contact_id, visitor_email
                ) VALUES (?, ?, ?, 15, 'inquiry_received', 2, 'ficha',
                          '2026-03-03', 11, 'whatsapp', 'mensaje', 6,
                          'pending', 12, 'martin@example.com')
                """,
                (org, agent, contact_id),
            )
            connection.execute(
                """
                CREATE TABLE contact_notes (
                    id INTEGER PRIMARY KEY,
                    contact_id INTEGER NOT NULL,
                    body TEXT NOT NULL,
                    FOREIGN KEY (contact_id) REFERENCES contacts(id)
                )
                """
            )
            connection.execute(
                "INSERT INTO contact_notes (id, contact_id, body) VALUES (4, ?, 'seguir')",
                (contact_id,),
            )
            connection.commit()
            before = connection.execute(
                "SELECT COUNT(*) FROM contact_property_interactions"
            ).fetchone()[0]
            _break_like_production(connection)
            broken = _old_name_rows(connection)
            broken_tables = sorted(row[1] for row in broken if row[0] == "table")
            self.assertIn("contact_property_interactions", broken_tables)
            self.assertIn("contact_notes", broken_tables)
            self.assertEqual(
                _fk_parent(connection, "contact_property_interactions"),
                "contacts_source_old",
            )
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    """
                    INSERT INTO contact_property_interactions (
                        organization_id, agent_id, contact_id,
                        interaction_type, created_at
                    ) VALUES (?, ?, ?, 'shared', '2026-04-04')
                    """,
                    (org, agent, contact_id),
                )
        finally:
            connection.close()

        create_tables(create_backup=False)
        create_tables(create_backup=False)

        connection = get_connection()
        try:
            self.assertEqual(_old_name_rows(connection), [])
            self.assertEqual(
                _fk_parent(connection, "contact_property_interactions"),
                "contacts",
            )
            self.assertEqual(_fk_parent(connection, "contact_notes"), "contacts")
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM contact_property_interactions"
                ).fetchone()[0],
                before,
            )
            kept = connection.execute(
                """
                SELECT interaction_type, activity_id, label, shortlist_id,
                       source, message, listing_agent_id, attention,
                       related_contact_id, visitor_email, property_id,
                       contact_id, organization_id, agent_id
                FROM contact_property_interactions
                """
            ).fetchone()
            self.assertEqual(kept[0], "inquiry_received")
            self.assertEqual(kept[1], 2)
            self.assertEqual(kept[2], "ficha")
            self.assertEqual(kept[3], 11)
            self.assertEqual(kept[4], "whatsapp")
            self.assertEqual(kept[5], "mensaje")
            self.assertEqual(kept[6], 6)
            self.assertEqual(kept[7], "pending")
            self.assertEqual(kept[8], 12)
            self.assertEqual(kept[9], "martin@example.com")
            self.assertEqual(kept[10], 15)
            self.assertEqual(kept[11], contact_id)
            self.assertEqual(kept[12], org)
            self.assertEqual(kept[13], agent)
            self.assertEqual(
                connection.execute("SELECT body FROM contact_notes").fetchone()[0],
                "seguir",
            )
            connection.execute(
                """
                INSERT INTO contact_property_interactions (
                    organization_id, agent_id, contact_id, property_id,
                    interaction_type, created_at
                ) VALUES (?, ?, ?, 15, 'shared', '2026-04-04')
                """,
                (org, agent, contact_id),
            )
            connection.commit()
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            related = [
                row
                for row in violations
                if "contacts_source_old" in row
                or row[0] in ("contact_property_interactions", "contact_notes")
            ]
            self.assertEqual(related, [])
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM contact_property_interactions
                    WHERE interaction_type = 'shared'
                    """
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
