"""Read-only productivity counts from existing tables. No invented events."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .connection import get_connection
from .tenant import require_organization_id


def _decimal(value):
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def count_completed_tasks(
    organization_id,
    agent_id,
    *,
    completed_from,
    completed_to,
    task_type=None,
):
    organization_id = require_organization_id(organization_id)
    clauses = [
        "organization_id = ?",
        "agent_id = ?",
        "status = 'completed'",
        "completed_at >= ?",
        "completed_at < ?",
    ]
    params = [organization_id, agent_id, completed_from, completed_to]
    if task_type:
        clauses.append("task_type = ?")
        params.append(task_type)
    connection = get_connection()
    try:
        row = connection.execute(
            f"SELECT COUNT(*) FROM agent_tasks WHERE {' AND '.join(clauses)}",
            params,
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] or 0) if row else 0


def count_new_contacts(organization_id, agent_id, *, created_from, created_to):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM contacts
            WHERE organization_id = ?
              AND agent_id = ?
              AND created_at >= ?
              AND created_at < ?
            """,
            (organization_id, agent_id, created_from, created_to),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] or 0) if row else 0


def count_acms_created(organization_id, agent_id, *, created_from, created_to):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM property_acms
            WHERE organization_id = ?
              AND agent_id = ?
              AND status != 'archived'
              AND created_at >= ?
              AND created_at < ?
            """,
            (organization_id, agent_id, created_from, created_to),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] or 0) if row else 0


def count_properties_captured(
    organization_id,
    agent_id,
    *,
    captured_from,
    captured_to,
):
    """Approved properties whose review/submit timestamp falls in the window."""
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM properties
            WHERE organization_id = ?
              AND agent_id = ?
              AND status = 'approved'
              AND COALESCE(reviewed_at, submitted_at, '') >= ?
              AND COALESCE(reviewed_at, submitted_at, '') < ?
            """,
            (organization_id, agent_id, captured_from, captured_to),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] or 0) if row else 0


def count_active_operations(organization_id, agent_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM operations
            WHERE organization_id = ?
              AND agent_id = ?
              AND status IN ('draft', 'pending', 'approved')
              AND COALESCE(was_invoiced, 'no') != 'yes'
            """,
            (organization_id, agent_id),
        ).fetchone()
    finally:
        connection.close()
    return int(row[0] or 0) if row else 0


def list_closed_operation_dates(organization_id, agent_id):
    """Closed = approved and marked invoiced. Period uses operation_date."""
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT operation_date
            FROM operations
            WHERE organization_id = ?
              AND agent_id = ?
              AND status = 'approved'
              AND was_invoiced = 'yes'
            """,
            (organization_id, agent_id),
        ).fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def sum_movements_by_currency(
    organization_id,
    agent_id,
    *,
    movement_type,
    date_from,
    date_to,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT currency, COALESCE(SUM(amount), 0)
            FROM agent_account_movements
            WHERE organization_id = ?
              AND agent_id = ?
              AND movement_type = ?
              AND status = 'confirmed'
              AND COALESCE(is_internal_reversal, 0) = 0
              AND reversed_movement_id IS NULL
              AND movement_date >= ?
              AND movement_date <= ?
            GROUP BY currency
            """,
            (organization_id, agent_id, movement_type, date_from, date_to),
        ).fetchall()
    finally:
        connection.close()
    return {row[0]: _decimal(row[1]) for row in rows if row[0]}


def sum_invoices_by_currency(
    organization_id,
    agent_id,
    *,
    created_from,
    created_to,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT currency, COALESCE(SUM(total_amount), 0)
            FROM invoices
            WHERE organization_id = ?
              AND agent_id = ?
              AND cancelled_at IS NULL
              AND created_at >= ?
              AND created_at < ?
            GROUP BY currency
            """,
            (organization_id, agent_id, created_from, created_to),
        ).fetchall()
    finally:
        connection.close()
    return {row[0]: _decimal(row[1]) for row in rows if row[0]}
