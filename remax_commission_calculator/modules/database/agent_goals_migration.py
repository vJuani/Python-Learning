"""Idempotent agent goals table (FASE 5A). No physical deletes."""

from __future__ import annotations

from .connection import get_connection


AGENT_GOALS_SQL = """
CREATE TABLE IF NOT EXISTS agent_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id INTEGER NOT NULL,
    agent_id INTEGER NOT NULL,
    metric_key TEXT NOT NULL,
    period_type TEXT NOT NULL,
    target_value TEXT NOT NULL,
    currency TEXT,
    start_date TEXT,
    end_date TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (organization_id)
        REFERENCES organizations(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE RESTRICT,

    CHECK (period_type IN ('daily', 'weekly', 'monthly')),
    CHECK (is_active IN (0, 1))
)
"""

EXTRA_COLUMNS = (
    ("currency", "TEXT"),
    ("start_date", "TEXT"),
    ("end_date", "TEXT"),
)


def migrate_agent_goals_sqlite():
    connection = get_connection()
    try:
        connection.execute(AGENT_GOALS_SQL)
        existing = {
            row[1]
            for row in connection.execute("PRAGMA table_info(agent_goals)").fetchall()
        }
        for name, definition in EXTRA_COLUMNS:
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE agent_goals ADD COLUMN {name} {definition}"
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_agent_goals_owner
            ON agent_goals (organization_id, agent_id, is_active, period_type)
            """
        )
        connection.commit()
    finally:
        connection.close()
