"""Persistence for recurring agent-account charge configurations."""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import assert_agent_in_organization, require_organization_id


RECURRENCE_ACTIVE = "active"
RECURRENCE_PAUSED = "paused"
RECURRENCE_ENDED = "ended"


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _build(row):
    if row is None:
        return None
    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "charge_category": row[3],
        "description": row[4] or "",
        "currency": row[5],
        "input_amount": float(row[6]),
        "vat_mode": row[7],
        "net_amount": float(row[8]),
        "vat_rate": float(row[9]),
        "vat_amount": float(row[10]),
        "gross_amount": float(row[11]),
        "recurrence_type": row[12],
        "billing_day": row[13],
        "start_date": row[14],
        "end_date": row[15],
        "next_run_date": row[16],
        "status": row[17],
        "created_by_user_id": row[18],
        "created_at": row[19],
        "updated_by_user_id": row[20],
        "updated_at": row[21],
        "last_generated_at": row[22],
        "paused_at": row[23],
        "paused_by_user_id": row[24],
        "ended_at": row[25],
        "ended_by_user_id": row[26],
        "agent_name": row[27] if len(row) > 27 else None,
    }


SELECT_SQL = """
    SELECT
        r.id, r.organization_id, r.agent_id, r.charge_category,
        r.description, r.currency, r.input_amount, r.vat_mode,
        r.net_amount, r.vat_rate, r.vat_amount, r.gross_amount,
        r.recurrence_type, r.billing_day, r.start_date, r.end_date,
        r.next_run_date, r.status, r.created_by_user_id, r.created_at,
        r.updated_by_user_id, r.updated_at, r.last_generated_at,
        r.paused_at, r.paused_by_user_id, r.ended_at,
        r.ended_by_user_id, a.name
    FROM agent_recurring_charges r
    INNER JOIN agents a
        ON a.id = r.agent_id
        AND a.organization_id = r.organization_id
"""


def get_recurring_charge(organization_id, recurring_charge_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            SELECT_SQL
            + """
            WHERE r.organization_id = ? AND r.id = ?
            """,
            (organization_id, recurring_charge_id),
        ).fetchone()
        return _build(row)
    finally:
        connection.close()


def list_recurring_charges(
    organization_id,
    *,
    agent_id=None,
    include_ended=True,
):
    organization_id = require_organization_id(organization_id)
    clauses = ["r.organization_id = ?"]
    params = [organization_id]
    if agent_id is not None:
        clauses.append("r.agent_id = ?")
        params.append(agent_id)
    if not include_ended:
        clauses.append("r.status != 'ended'")
    connection = get_connection()
    try:
        rows = connection.execute(
            SELECT_SQL
            + f"""
            WHERE {" AND ".join(clauses)}
            ORDER BY
                CASE r.status
                    WHEN 'active' THEN 0
                    WHEN 'paused' THEN 1
                    ELSE 2
                END,
                r.next_run_date,
                r.id
            """,
            params,
        ).fetchall()
        return [_build(row) for row in rows]
    finally:
        connection.close()


def list_due_recurring_charges(
    organization_id,
    *,
    as_of,
    limit=100,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            SELECT_SQL
            + """
            WHERE r.organization_id = ?
                AND r.status = 'active'
                AND r.next_run_date <= ?
                AND (r.end_date IS NULL OR r.next_run_date <= r.end_date)
            ORDER BY r.next_run_date, r.id
            LIMIT ?
            """,
            (organization_id, as_of, int(limit)),
        ).fetchall()
        return [_build(row) for row in rows]
    finally:
        connection.close()


def create_recurring_charge(organization_id, agent_id, *, fields):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    now = _now_iso()
    try:
        assert_agent_in_organization(cursor, agent_id, organization_id)
        recurring_id = execute_insert(
            cursor,
            """
            INSERT INTO agent_recurring_charges (
                organization_id, agent_id, charge_category, description,
                currency, input_amount, vat_mode, net_amount, vat_rate,
                vat_amount, gross_amount, recurrence_type, billing_day,
                start_date, end_date, next_run_date, status,
                created_by_user_id, created_at, updated_by_user_id,
                updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active',
                ?, ?, ?, ?
            )
            """,
            (
                organization_id, agent_id, fields["charge_category"],
                fields.get("description"), fields["currency"],
                fields["input_amount"], fields["vat_mode"],
                fields["net_amount"], fields["vat_rate"],
                fields["vat_amount"], fields["gross_amount"],
                fields["recurrence_type"], fields.get("billing_day"),
                fields["start_date"], fields.get("end_date"),
                fields["next_run_date"], fields.get("actor_user_id"),
                now, fields.get("actor_user_id"), now,
            ),
        )
        connection.commit()
        return get_recurring_charge(organization_id, recurring_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_recurring_charge(
    organization_id,
    recurring_charge_id,
    *,
    fields,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    now = _now_iso()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE agent_recurring_charges
            SET charge_category = ?, description = ?, currency = ?,
                input_amount = ?, vat_mode = ?, net_amount = ?,
                vat_rate = ?, vat_amount = ?, gross_amount = ?,
                recurrence_type = ?, billing_day = ?, start_date = ?,
                end_date = ?, next_run_date = ?, updated_by_user_id = ?,
                updated_at = ?
            WHERE organization_id = ? AND id = ?
            """,
            (
                fields["charge_category"], fields.get("description"),
                fields["currency"], fields["input_amount"],
                fields["vat_mode"], fields["net_amount"],
                fields["vat_rate"], fields["vat_amount"],
                fields["gross_amount"], fields["recurrence_type"],
                fields.get("billing_day"), fields["start_date"],
                fields.get("end_date"), fields["next_run_date"],
                fields.get("actor_user_id"), now, organization_id,
                recurring_charge_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("recurring_charge_not_found")
        connection.commit()
        return get_recurring_charge(organization_id, recurring_charge_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_recurring_charge_status(
    organization_id,
    recurring_charge_id,
    *,
    status,
    actor_user_id,
    next_run_date=None,
):
    organization_id = require_organization_id(organization_id)
    if status not in (
        RECURRENCE_ACTIVE,
        RECURRENCE_PAUSED,
        RECURRENCE_ENDED,
    ):
        raise ValueError("invalid_recurring_charge_status")
    now = _now_iso()
    clauses = [
        "status = ?",
        "updated_by_user_id = ?",
        "updated_at = ?",
    ]
    params = [status, actor_user_id, now]
    if next_run_date is not None:
        clauses.append("next_run_date = ?")
        params.append(next_run_date)
    if status == RECURRENCE_PAUSED:
        clauses.extend(["paused_at = ?", "paused_by_user_id = ?"])
        params.extend([now, actor_user_id])
    elif status == RECURRENCE_ACTIVE:
        clauses.extend(["paused_at = NULL", "paused_by_user_id = NULL"])
    elif status == RECURRENCE_ENDED:
        clauses.extend(["ended_at = ?", "ended_by_user_id = ?"])
        params.extend([now, actor_user_id])
    params.extend([organization_id, recurring_charge_id])
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            UPDATE agent_recurring_charges
            SET {", ".join(clauses)}
            WHERE organization_id = ? AND id = ?
            """,
            params,
        )
        if cursor.rowcount != 1:
            raise ValueError("recurring_charge_not_found")
        connection.commit()
        return get_recurring_charge(organization_id, recurring_charge_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_recurring_charge_generated(
    organization_id,
    recurring_charge_id,
    *,
    expected_run_date,
    next_run_date,
    actor_user_id,
):
    """Advance only if another worker has not already advanced the schedule."""
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE agent_recurring_charges
            SET next_run_date = ?, last_generated_at = ?,
                updated_by_user_id = ?, updated_at = ?
            WHERE organization_id = ? AND id = ?
                AND next_run_date = ?
            """,
            (
                next_run_date, now, actor_user_id, now,
                organization_id, recurring_charge_id, expected_run_date,
            ),
        )
        connection.commit()
        return cursor.rowcount == 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
