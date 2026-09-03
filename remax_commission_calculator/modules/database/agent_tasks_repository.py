"""
Repository for agenda tasks (Phase 4B).

Reads join agents, properties and operations so a list of tasks never
triggers one query per related entity. Timestamps are naive UTC ISO
strings; the organization timezone is applied when rendering.
"""

from __future__ import annotations

from datetime import datetime

from .connection import execute_insert, get_connection
from .tenant import require_organization_id


STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

STATUSES = (
    STATUS_PENDING,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
)

PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"

PRIORITIES = (PRIORITY_NORMAL, PRIORITY_HIGH)

_UNSET = object()

TASK_TYPES = (
    "call",
    "visit",
    "meeting",
    "follow_up",
    "documentation",
    "valuation",
    "reminder",
    "other",
)

_TASK_SELECT = """
    SELECT
        task.id,
        task.organization_id,
        task.agent_id,
        task.title,
        task.description,
        task.task_type,
        task.due_at,
        task.status,
        task.priority,
        task.property_id,
        task.operation_id,
        task.related_entity_type,
        task.related_entity_id,
        task.created_by_user_id,
        task.created_at,
        task.updated_at,
        task.completed_at,
        task.cancelled_at,
        agent.name,
        property.address,
        operation.id
    FROM agent_tasks AS task
    LEFT JOIN agents AS agent
        ON agent.id = task.agent_id
        AND agent.organization_id = task.organization_id
    LEFT JOIN properties AS property
        ON property.id = task.property_id
        AND property.organization_id = task.organization_id
    LEFT JOIN operations AS operation
        ON operation.id = task.operation_id
        AND operation.organization_id = task.organization_id
"""


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _build_task(row):
    if row is None:
        return None

    operation_id = row[20]

    return {
        "id": row[0],
        "organization_id": row[1],
        "agent_id": row[2],
        "title": row[3],
        "description": row[4] or "",
        "task_type": row[5],
        "due_at": row[6],
        "status": row[7],
        "priority": row[8],
        "property_id": row[9],
        "operation_id": row[10],
        "related_entity_type": row[11],
        "related_entity_id": row[12],
        "created_by_user_id": row[13],
        "created_at": row[14],
        "updated_at": row[15],
        "completed_at": row[16],
        "cancelled_at": row[17],
        "agent_name": row[18],
        "property_address": row[19],
        "operation_reference": (
            f"COM-{operation_id:06d}"
            if operation_id is not None
            else None
        ),
    }


def create_agent_task(
    organization_id,
    agent_id,
    *,
    title,
    task_type,
    due_at,
    priority=PRIORITY_NORMAL,
    description=None,
    property_id=None,
    operation_id=None,
    related_entity_type=None,
    related_entity_id=None,
    created_by_user_id=None,
):
    organization_id = require_organization_id(organization_id)
    now = _now_iso()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        task_id = execute_insert(
            cursor,
            """
            INSERT INTO agent_tasks (
                organization_id,
                agent_id,
                title,
                description,
                task_type,
                due_at,
                status,
                priority,
                property_id,
                operation_id,
                related_entity_type,
                related_entity_id,
                created_by_user_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                agent_id,
                title,
                description,
                task_type,
                due_at,
                STATUS_PENDING,
                priority,
                property_id,
                operation_id,
                related_entity_type,
                related_entity_id,
                created_by_user_id,
                now,
                now,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return get_agent_task(task_id, organization_id)


def get_agent_task(task_id, organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()

    try:
        row = connection.execute(
            _TASK_SELECT
            + """
            WHERE task.id = ?
                AND task.organization_id = ?
            """,
            (task_id, organization_id),
        ).fetchone()
    finally:
        connection.close()

    return _build_task(row)


def list_agent_tasks(
    organization_id,
    *,
    agent_id=None,
    statuses=None,
    due_from=None,
    due_to=None,
    search=None,
    task_type=None,
    property_id=None,
    operation_id=None,
    related_entity_type=None,
    related_entity_id=None,
    order="asc",
    limit=100,
):
    """
    Single aggregated read for every agenda view.

    ``due_from``/``due_to`` are UTC ISO bounds, half-open on the upper
    end so day windows never overlap.
    """
    organization_id = require_organization_id(organization_id)
    clauses = ["task.organization_id = ?"]
    params = [organization_id]

    if agent_id is not None:
        clauses.append("task.agent_id = ?")
        params.append(agent_id)

    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"task.status IN ({placeholders})")
        params.extend(statuses)

    if due_from:
        clauses.append("task.due_at >= ?")
        params.append(due_from)

    if due_to:
        clauses.append("task.due_at < ?")
        params.append(due_to)

    if task_type:
        clauses.append("task.task_type = ?")
        params.append(task_type)

    if property_id is not None:
        clauses.append("task.property_id = ?")
        params.append(property_id)

    if operation_id is not None:
        clauses.append("task.operation_id = ?")
        params.append(operation_id)

    if related_entity_type:
        clauses.append("task.related_entity_type = ?")
        params.append(related_entity_type)

    if related_entity_id is not None:
        clauses.append("task.related_entity_id = ?")
        params.append(related_entity_id)

    if search:
        needle = f"%{search.strip().lower()}%"
        clauses.append(
            """
            (
                LOWER(task.title) LIKE ?
                OR LOWER(COALESCE(task.description, '')) LIKE ?
                OR LOWER(COALESCE(property.address, '')) LIKE ?
            )
            """
        )
        params.extend([needle, needle, needle])

    direction = "DESC" if order == "desc" else "ASC"
    connection = get_connection()

    try:
        rows = connection.execute(
            _TASK_SELECT
            + f"""
            WHERE {" AND ".join(clauses)}
            ORDER BY task.due_at {direction}, task.id {direction}
            LIMIT ?
            """,
            [*params, int(limit)],
        ).fetchall()
    finally:
        connection.close()

    return [_build_task(row) for row in rows]


def count_agent_tasks(
    organization_id,
    *,
    agent_id=None,
    statuses=None,
    due_from=None,
    due_to=None,
):
    """Cheap COUNT for dashboard headlines."""
    organization_id = require_organization_id(organization_id)
    clauses = ["organization_id = ?"]
    params = [organization_id]

    if agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(agent_id)

    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)

    if due_from:
        clauses.append("due_at >= ?")
        params.append(due_from)

    if due_to:
        clauses.append("due_at < ?")
        params.append(due_to)

    connection = get_connection()

    try:
        row = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM agent_tasks
            WHERE {" AND ".join(clauses)}
            """,
            params,
        ).fetchone()
    finally:
        connection.close()

    return int(row[0] or 0) if row else 0


def update_agent_task_fields(
    task_id,
    organization_id,
    *,
    title=None,
    description=None,
    task_type=None,
    due_at=None,
    priority=None,
    property_id=_UNSET,
    operation_id=_UNSET,
):
    """
    Partial update of an editable task.

    Relations accept an explicit ``None`` to clear them, so they use a
    sentinel default instead of ``None``.
    """
    organization_id = require_organization_id(organization_id)
    assignments = []
    params = []

    for column, value in (
        ("title", title),
        ("description", description),
        ("task_type", task_type),
        ("due_at", due_at),
        ("priority", priority),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            params.append(value)

    for column, value in (
        ("property_id", property_id),
        ("operation_id", operation_id),
    ):
        if value is not _UNSET:
            assignments.append(f"{column} = ?")
            params.append(value)

    if not assignments:
        return get_agent_task(task_id, organization_id)

    assignments.append("updated_at = ?")
    params.append(_now_iso())

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE agent_tasks
            SET {", ".join(assignments)}
            WHERE id = ?
                AND organization_id = ?
                AND status = ?
            """,
            [*params, task_id, organization_id, STATUS_PENDING],
        )
        updated = cursor.rowcount
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    if not updated:
        return None

    return get_agent_task(task_id, organization_id)


def set_agent_task_status(
    task_id,
    organization_id,
    *,
    status,
    actor_user_id=None,
):
    """
    Move a pending task to a terminal status.

    History is preserved: rows are never deleted, and the transition
    only applies to tasks still pending so a double POST is a no-op.
    """
    organization_id = require_organization_id(organization_id)

    if status not in (STATUS_COMPLETED, STATUS_CANCELLED):
        raise ValueError("invalid_task_status")

    now = _now_iso()

    if status == STATUS_COMPLETED:
        assignments = (
            "status = ?, completed_at = ?, "
            "completed_by_user_id = ?, updated_at = ?"
        )
    else:
        assignments = (
            "status = ?, cancelled_at = ?, "
            "cancelled_by_user_id = ?, updated_at = ?"
        )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            f"""
            UPDATE agent_tasks
            SET {assignments}
            WHERE id = ?
                AND organization_id = ?
                AND status = ?
            """,
            (
                status,
                now,
                actor_user_id,
                now,
                task_id,
                organization_id,
                STATUS_PENDING,
            ),
        )
        updated = cursor.rowcount
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    if not updated:
        return None

    return get_agent_task(task_id, organization_id)
