"""Org-scoped visit reminder scan heartbeats."""

from __future__ import annotations

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


def record_visit_reminder_run(
    organization_id,
    *,
    source,
    ran_at,
    candidate_count=0,
    dispatched=0,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        execute_insert(
            cursor,
            """
            INSERT INTO visit_reminder_runs (
                organization_id,
                source,
                ran_at,
                candidate_count,
                dispatched
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                source,
                ran_at,
                int(candidate_count or 0),
                int(dispatched or 0),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_last_visit_reminder_run(organization_id, source=None):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        sql = """
            SELECT source, ran_at, candidate_count, dispatched
            FROM visit_reminder_runs
            WHERE organization_id = ?
        """
        params = [organization_id]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY id DESC LIMIT 1"
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "source": row[0],
            "ran_at": row[1],
            "candidate_count": row[2],
            "dispatched": row[3],
        }
    finally:
        connection.close()
