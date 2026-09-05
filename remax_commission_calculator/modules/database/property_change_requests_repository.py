from datetime import datetime

from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    require_organization_id
)


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _build_change_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "property_id": row[2],
        "requested_by_user_id": row[3],
        "proposed_address": row[4],
        "proposed_jurisdiction": row[5],
        "proposed_agent_id": row[6],
        "proposed_property_type": row[7],
        "proposed_listing_price": row[8],
        "proposed_listing_purpose": row[9],
        "status": row[10],
        "rejection_reason": row[11],
        "reviewed_by_user_id": row[12],
        "reviewed_at": row[13],
        "created_at": row[14],
        "current_address": row[15] if len(row) > 15 else None,
        "current_jurisdiction": row[16] if len(row) > 16 else None,
        "current_agent_id": row[17] if len(row) > 17 else None,
        "current_property_type": row[18] if len(row) > 18 else None,
        "current_listing_price": row[19] if len(row) > 19 else None,
        "current_listing_purpose": row[20] if len(row) > 20 else None,
        "current_agent_name": row[21] if len(row) > 21 else None,
        "proposed_agent_name": row[22] if len(row) > 22 else None,
        "requester_name": row[23] if len(row) > 23 else None,
        "proposed_listing_currency": row[24] if len(row) > 24 else None,
        "current_listing_currency": row[25] if len(row) > 25 else None,
    }


CHANGE_DETAIL_QUERY = """
    SELECT
        property_change_requests.id,
        property_change_requests.organization_id,
        property_change_requests.property_id,
        property_change_requests.requested_by_user_id,
        property_change_requests.proposed_address,
        property_change_requests.proposed_jurisdiction,
        property_change_requests.proposed_agent_id,
        property_change_requests.proposed_property_type,
        property_change_requests.proposed_listing_price,
        property_change_requests.proposed_listing_purpose,
        property_change_requests.status,
        property_change_requests.rejection_reason,
        property_change_requests.reviewed_by_user_id,
        property_change_requests.reviewed_at,
        property_change_requests.created_at,
        properties.address,
        properties.jurisdiction,
        properties.agent_id,
        properties.property_type,
        properties.listing_price,
        properties.listing_purpose,
        current_agents.name,
        proposed_agents.name,
        requester.username,
        property_change_requests.proposed_listing_currency,
        properties.listing_currency
    FROM property_change_requests
    JOIN properties
        ON properties.id = property_change_requests.property_id
        AND properties.organization_id
            = property_change_requests.organization_id
    LEFT JOIN agents AS current_agents
        ON current_agents.id = properties.agent_id
        AND current_agents.organization_id
            = properties.organization_id
    LEFT JOIN agents AS proposed_agents
        ON proposed_agents.id
            = property_change_requests.proposed_agent_id
        AND proposed_agents.organization_id
            = property_change_requests.organization_id
    LEFT JOIN users AS requester
        ON requester.id
            = property_change_requests.requested_by_user_id
"""


def count_pending_property_changes(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM property_change_requests
        WHERE organization_id = ?
            AND status = ?
        """,
        (
            organization_id,
            STATUS_PENDING
        )
    )

    count = cursor.fetchone()[0]
    connection.close()

    return count


def get_pending_change_for_property(
    property_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        CHANGE_DETAIL_QUERY
        + """
        WHERE property_change_requests.property_id = ?
            AND property_change_requests.organization_id = ?
            AND property_change_requests.status = ?
        LIMIT 1
        """,
        (
            property_id,
            organization_id,
            STATUS_PENDING
        )
    )

    row = cursor.fetchone()
    connection.close()

    return _build_change_dict(row)


def create_property_change_request(
    property_id,
    organization_id,
    requested_by_user_id,
    proposed_address,
    proposed_jurisdiction,
    proposed_agent_id,
    proposed_property_type=None,
    proposed_listing_price=None,
    proposed_listing_purpose=None,
    proposed_listing_currency=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM property_change_requests
        WHERE property_id = ?
            AND organization_id = ?
            AND status = ?
        """,
        (
            property_id,
            organization_id,
            STATUS_PENDING
        )
    )

    if cursor.fetchone() is not None:
        connection.close()
        raise TenantError(
            "A pending change request already exists for this property."
        )

    request_id = execute_insert(
        cursor,
        """
        INSERT INTO property_change_requests (
            organization_id,
            property_id,
            requested_by_user_id,
            proposed_address,
            proposed_jurisdiction,
            proposed_agent_id,
            proposed_property_type,
            proposed_listing_price,
            proposed_listing_purpose,
            proposed_listing_currency,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            organization_id,
            property_id,
            requested_by_user_id,
            proposed_address.strip(),
            proposed_jurisdiction.strip(),
            proposed_agent_id,
            proposed_property_type,
            proposed_listing_price,
            proposed_listing_purpose,
            proposed_listing_currency,
            STATUS_PENDING,
            _now_iso()
        )
    )

    connection.commit()
    connection.close()

    return request_id


def get_property_change_request(
    request_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        CHANGE_DETAIL_QUERY
        + """
        WHERE property_change_requests.id = ?
            AND property_change_requests.organization_id = ?
        """,
        (
            request_id,
            organization_id
        )
    )

    row = cursor.fetchone()
    connection.close()

    return _build_change_dict(row)


def approve_property_change_request(
    request_id,
    organization_id,
    reviewed_by_user_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            property_id,
            proposed_address,
            proposed_jurisdiction,
            proposed_agent_id,
            proposed_property_type,
            proposed_listing_price,
            proposed_listing_purpose,
            proposed_listing_currency
        FROM property_change_requests
        WHERE id = ?
            AND organization_id = ?
            AND status = ?
        """,
        (
            request_id,
            organization_id,
            STATUS_PENDING
        )
    )

    row = cursor.fetchone()

    if row is None:
        connection.close()
        raise TenantError(
            "Property change request was not found or is not pending."
        )

    property_id = row[0]
    now = _now_iso()

    cursor.execute(
        """
        UPDATE properties
        SET
            address = ?,
            jurisdiction = ?,
            agent_id = ?,
            property_type = ?,
            listing_price = ?,
            listing_purpose = ?,
            listing_currency = COALESCE(?, listing_currency)
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            property_id,
            organization_id
        )
    )

    cursor.execute(
        """
        UPDATE property_change_requests
        SET
            status = ?,
            reviewed_by_user_id = ?,
            reviewed_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            STATUS_APPROVED,
            reviewed_by_user_id,
            now,
            request_id,
            organization_id
        )
    )

    connection.commit()
    connection.close()


def reject_property_change_request(
    request_id,
    organization_id,
    reviewed_by_user_id,
    rejection_reason
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    now = _now_iso()

    cursor.execute(
        """
        UPDATE property_change_requests
        SET
            status = ?,
            rejection_reason = ?,
            reviewed_by_user_id = ?,
            reviewed_at = ?
        WHERE id = ?
            AND organization_id = ?
            AND status = ?
        """,
        (
            STATUS_REJECTED,
            rejection_reason.strip(),
            reviewed_by_user_id,
            now,
            request_id,
            organization_id,
            STATUS_PENDING
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property change request was not found or is not pending."
        )

    connection.commit()
    connection.close()
