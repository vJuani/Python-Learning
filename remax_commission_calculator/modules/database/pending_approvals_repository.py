from datetime import datetime

from .connection import get_connection
from .property_change_requests_repository import (
    STATUS_PENDING as CHANGE_PENDING
)
from .registration_repository import (
    STATUS_PENDING_APPROVAL
)
from .tenant import require_organization_id


def count_pending_approvals(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM properties
        WHERE organization_id = ?
            AND status = 'pending'
        """,
        (
            organization_id,
        )
    )
    property_new_count = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM property_change_requests
        WHERE organization_id = ?
            AND status = ?
        """,
        (
            organization_id,
            CHANGE_PENDING
        )
    )
    property_change_count = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM operations
        WHERE organization_id = ?
            AND status = 'pending'
        """,
        (
            organization_id,
        )
    )
    operation_count = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM registration_requests
        WHERE organization_id = ?
            AND status = ?
        """,
        (
            organization_id,
            STATUS_PENDING_APPROVAL
        )
    )
    registration_count = cursor.fetchone()[0]

    connection.close()

    return (
        property_new_count
        + property_change_count
        + operation_count
        + registration_count
    )


def _relative_minutes(created_at):
    if not created_at:
        return None

    try:
        created = datetime.fromisoformat(created_at)
        delta = datetime.utcnow() - created
        minutes = int(delta.total_seconds() // 60)

        if minutes < 1:
            return 0

        return minutes
    except ValueError:
        return None


def list_pending_approval_items(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()
    items = []

    cursor.execute(
        """
        SELECT
            properties.id,
            properties.address,
            properties.submitted_at,
            properties.created_by_user_id,
            agents.name,
            users.username
        FROM properties
        LEFT JOIN agents
            ON agents.id = properties.agent_id
            AND agents.organization_id = properties.organization_id
        LEFT JOIN users
            ON users.id = properties.created_by_user_id
        WHERE properties.organization_id = ?
            AND properties.status = 'pending'
        ORDER BY COALESCE(properties.submitted_at, '') DESC, properties.id DESC
        """,
        (
            organization_id,
        )
    )

    for row in cursor.fetchall():
        agent_name = row[4] or row[5] or "—"
        created_at = row[2]

        items.append({
            "item_type": "property_new",
            "entity_id": row[0],
            "title_key": "approval_item_property_new",
            "agent_name": agent_name,
            "summary": row[1],
            "created_at": created_at,
            "minutes_ago": _relative_minutes(created_at)
        })

    cursor.execute(
        """
        SELECT
            property_change_requests.id,
            properties.address,
            property_change_requests.created_at,
            property_change_requests.proposed_address,
            property_change_requests.proposed_jurisdiction,
            properties.jurisdiction,
            agents.name
        FROM property_change_requests
        JOIN properties
            ON properties.id = property_change_requests.property_id
            AND properties.organization_id
                = property_change_requests.organization_id
        LEFT JOIN agents
            ON agents.id = properties.agent_id
            AND agents.organization_id = properties.organization_id
        WHERE property_change_requests.organization_id = ?
            AND property_change_requests.status = ?
        ORDER BY property_change_requests.created_at DESC, property_change_requests.id DESC
        """,
        (
            organization_id,
            CHANGE_PENDING
        )
    )

    for row in cursor.fetchall():
        summary_parts = []

        if row[1] != row[3]:
            summary_parts.append(f"{row[1]} → {row[3]}")
        else:
            summary_parts.append(row[3])

        if row[5] != row[4]:
            summary_parts.append(f"{row[5]} → {row[4]}")

        items.append({
            "item_type": "property_change",
            "entity_id": row[0],
            "title_key": "approval_item_property_change",
            "agent_name": row[6] or "—",
            "summary": " · ".join(summary_parts),
            "created_at": row[2],
            "minutes_ago": _relative_minutes(row[2])
        })

    cursor.execute(
        """
        SELECT
            operations.id,
            operations.operation_date,
            operations.total_commission,
            operations.currency,
            agents.name,
            properties.address
        FROM operations
        LEFT JOIN agents
            ON agents.id = operations.agent_id
            AND agents.organization_id = operations.organization_id
        LEFT JOIN properties
            ON properties.id = operations.property_id
            AND properties.organization_id = operations.organization_id
        WHERE operations.organization_id = ?
            AND operations.status = 'pending'
        ORDER BY operations.operation_date DESC, operations.id DESC
        """,
        (
            organization_id,
        )
    )

    for row in cursor.fetchall():
        created_at = row[1]
        currency = row[3] or "USD"
        commission = row[2] or 0

        items.append({
            "item_type": "operation",
            "entity_id": row[0],
            "title_key": "approval_item_operation",
            "agent_name": row[4] or "—",
            "summary": (
                f"{currency} {commission:,.2f} · {row[5] or '—'}"
            ),
            "created_at": created_at,
            "minutes_ago": _relative_minutes(created_at)
        })

    cursor.execute(
        """
        SELECT
            registration_requests.id,
            registration_requests.created_at,
            registration_requests.first_name,
            registration_requests.last_name,
            registration_requests.email
        FROM registration_requests
        WHERE registration_requests.organization_id = ?
            AND registration_requests.status = ?
        ORDER BY registration_requests.created_at DESC, registration_requests.id DESC
        """,
        (
            organization_id,
            STATUS_PENDING_APPROVAL
        )
    )

    for row in cursor.fetchall():
        created_at = row[1]

        items.append({
            "item_type": "registration",
            "entity_id": row[0],
            "title_key": "approval_item_registration",
            "agent_name": f"{row[2]} {row[3]}".strip(),
            "summary": row[4],
            "created_at": created_at,
            "minutes_ago": _relative_minutes(created_at)
        })

    connection.close()

    items.sort(
        key=lambda item: item.get("created_at") or "",
        reverse=True
    )

    return items
