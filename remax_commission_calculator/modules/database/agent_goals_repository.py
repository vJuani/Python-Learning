"""Agent goals persistence. Soft-deactivate only; no physical delete."""

from __future__ import annotations

from .connection import get_connection
from .tenant import require_organization_id
from modules.organization_time import now_utc_iso


PERIOD_TYPES = ("daily", "weekly", "monthly")


def _row(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "metric_key": row[3],
        "period_type": row[4],
        "target_value": row[5],
        "currency": row[6],
        "start_date": row[7],
        "end_date": row[8],
        "is_active": bool(row[9]),
        "created_at": row[10],
        "updated_at": row[11],
    }


def list_agent_goals(organization_id, agent_id, *, active_only=True):
    organization_id = require_organization_id(organization_id)
    clauses = ["organization_id = ?", "agent_id = ?"]
    params = [organization_id, agent_id]
    if active_only:
        clauses.append("is_active = 1")
    connection = get_connection()
    try:
        rows = connection.execute(
            f"""
            SELECT id, organization_id, agent_id, metric_key, period_type,
                   target_value, currency, start_date, end_date, is_active,
                   created_at, updated_at
            FROM agent_goals
            WHERE {" AND ".join(clauses)}
            ORDER BY period_type, metric_key, id
            """,
            params,
        ).fetchall()
    finally:
        connection.close()
    return [_row(row) for row in rows]


def get_agent_goal(goal_id, organization_id, agent_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT id, organization_id, agent_id, metric_key, period_type,
                   target_value, currency, start_date, end_date, is_active,
                   created_at, updated_at
            FROM agent_goals
            WHERE id = ? AND organization_id = ? AND agent_id = ?
            """,
            (goal_id, organization_id, agent_id),
        ).fetchone()
    finally:
        connection.close()
    return _row(row)


def upsert_agent_goal(
    organization_id,
    agent_id,
    *,
    metric_key,
    period_type,
    target_value,
    currency=None,
):
    organization_id = require_organization_id(organization_id)
    if period_type not in PERIOD_TYPES:
        raise ValueError("invalid_period_type")
    now = now_utc_iso()
    currency = (currency or "").strip().upper() or None
    connection = get_connection()
    try:
        existing = connection.execute(
            """
            SELECT id
            FROM agent_goals
            WHERE organization_id = ?
              AND agent_id = ?
              AND metric_key = ?
              AND period_type = ?
              AND COALESCE(currency, '') = ?
              AND is_active = 1
            """,
            (organization_id, agent_id, metric_key, period_type, currency or ""),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE agent_goals
                SET target_value = ?, updated_at = ?
                WHERE id = ? AND organization_id = ? AND agent_id = ?
                """,
                (str(target_value), now, existing[0], organization_id, agent_id),
            )
            goal_id = existing[0]
        else:
            cursor = connection.execute(
                """
                INSERT INTO agent_goals (
                    organization_id, agent_id, metric_key, period_type,
                    target_value, currency, start_date, end_date, is_active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 1, ?, ?)
                """,
                (
                    organization_id,
                    agent_id,
                    metric_key,
                    period_type,
                    str(target_value),
                    currency,
                    now,
                    now,
                ),
            )
            goal_id = cursor.lastrowid
        connection.commit()
    finally:
        connection.close()
    return get_agent_goal(goal_id, organization_id, agent_id)


def deactivate_agent_goal(goal_id, organization_id, agent_id):
    organization_id = require_organization_id(organization_id)
    now = now_utc_iso()
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE agent_goals
            SET is_active = 0, updated_at = ?
            WHERE id = ? AND organization_id = ? AND agent_id = ?
            """,
            (now, goal_id, organization_id, agent_id),
        )
        connection.commit()
    finally:
        connection.close()
    return get_agent_goal(goal_id, organization_id, agent_id)
