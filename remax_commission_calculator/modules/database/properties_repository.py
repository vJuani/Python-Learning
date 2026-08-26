from datetime import datetime

from .connection import (
    execute_insert,
    get_connection,
)
from .tenant import (
    TenantError,
    assert_agent_in_organization,
    require_organization_id
)


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _build_property_dict(row):
    external_id = None
    if len(row) > 16 and row[16]:
        external_id = str(row[16]).strip() or None

    return {
        "id": row[0],
        "address": row[1],
        "jurisdiction": row[2],
        "organization_id": row[3],
        "agent_id": row[4],
        "agent_name": row[5],
        "status": row[6] if len(row) > 6 else STATUS_APPROVED,
        "rejection_reason": row[7] if len(row) > 7 else None,
        "reviewed_by_user_id": row[8] if len(row) > 8 else None,
        "reviewed_at": row[9] if len(row) > 9 else None,
        "created_by_user_id": row[10] if len(row) > 10 else None,
        "submitted_at": row[11] if len(row) > 11 else None,
        "property_type": row[12] if len(row) > 12 else None,
        "listing_price": row[13] if len(row) > 13 else None,
        "listing_purpose": row[14] if len(row) > 14 else None,
        "last_synced_at": row[15] if len(row) > 15 else None,
        "external_id": external_id,
    }


PROPERTIES_BASE_QUERY = """
    SELECT
        properties.id,
        properties.address,
        properties.jurisdiction,
        properties.organization_id,
        properties.agent_id,
        agents.name,
        properties.status,
        properties.rejection_reason,
        properties.reviewed_by_user_id,
        properties.reviewed_at,
        properties.created_by_user_id,
        properties.submitted_at,
        properties.property_type,
        properties.listing_price,
        properties.listing_purpose,
        properties.last_synced_at,
        properties.external_id
    FROM properties
    LEFT JOIN agents
        ON properties.agent_id = agents.id
        AND agents.organization_id
            = properties.organization_id
"""


def count_properties(
    organization_id,
    status=None,
    agent_id=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = """
        SELECT COUNT(*)
        FROM properties
        WHERE organization_id = ?
    """
    params = [organization_id]

    if status is not None:
        query += " AND status = ?"
        params.append(status)

    if agent_id is not None:
        query += " AND agent_id = ?"
        params.append(agent_id)

    cursor.execute(query, params)
    count = cursor.fetchone()[0]
    connection.close()

    return count


def count_pending_properties(organization_id):
    return count_properties(
        organization_id,
        status=STATUS_PENDING
    )


def get_properties(
    organization_id,
    agent_id=None,
    status=None,
    include_all_statuses=False
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = PROPERTIES_BASE_QUERY + """
        WHERE properties.organization_id = ?
    """
    params = [organization_id]

    if agent_id is not None:
        query += " AND properties.agent_id = ?"
        params.append(agent_id)

    if status is not None:
        query += " AND properties.status = ?"
        params.append(status)
    elif not include_all_statuses:
        query += " AND properties.status = ?"
        params.append(STATUS_APPROVED)

    query += " ORDER BY properties.id"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()

    return [
        _build_property_dict(row)
        for row in rows
    ]


def get_property_record(
    property_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        PROPERTIES_BASE_QUERY
        + """
        WHERE properties.id = ?
            AND properties.organization_id = ?
        """,
        (
            property_id,
            organization_id
        )
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return _build_property_dict(row)


def add_property(
    address,
    jurisdiction,
    organization_id,
    agent_id=None,
    status=STATUS_APPROVED,
    created_by_user_id=None,
    submitted_at=None,
    property_type=None,
    listing_price=None,
    listing_purpose=None,
    last_synced_at=None,
    external_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if agent_id is not None:
        assert_agent_in_organization(
            cursor,
            agent_id,
            organization_id
        )

    now = _now_iso()

    if status == STATUS_PENDING and submitted_at is None:
        submitted_at = now

    cleaned_external_id = None
    if external_id is not None:
        cleaned_external_id = str(external_id).strip() or None

    property_id = execute_insert(
        cursor,
        """
        INSERT INTO properties (
            address,
            jurisdiction,
            organization_id,
            agent_id,
            status,
            created_by_user_id,
            submitted_at,
            property_type,
            listing_price,
            listing_purpose,
            last_synced_at,
            external_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            address,
            jurisdiction,
            organization_id,
            agent_id,
            status,
            created_by_user_id,
            submitted_at,
            property_type,
            listing_price,
            listing_purpose,
            last_synced_at,
            cleaned_external_id,
        )
    )

    connection.commit()
    connection.close()

    return property_id


def update_property(
    property_id,
    address,
    jurisdiction,
    organization_id,
    agent_id=None,
    property_type=None,
    listing_price=None,
    listing_purpose=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if agent_id is not None:
        assert_agent_in_organization(
            cursor,
            agent_id,
            organization_id
        )

    cursor.execute(
        """
        UPDATE properties
        SET
            address = ?,
            jurisdiction = ?,
            agent_id = ?,
            property_type = ?,
            listing_price = ?,
            listing_purpose = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            address,
            jurisdiction,
            agent_id,
            property_type,
            listing_price,
            listing_purpose,
            property_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property was not found in this organization."
        )

    connection.commit()
    connection.close()


def update_property_from_sync(
    property_id,
    organization_id,
    *,
    address,
    jurisdiction,
    agent_id,
    property_type=None,
    listing_price=None,
    listing_purpose=None,
    last_synced_at=None,
    external_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    if last_synced_at is None:
        last_synced_at = _now_iso()

    cleaned_external_id = None
    if external_id is not None:
        cleaned_external_id = str(external_id).strip() or None

    connection = get_connection()
    cursor = connection.cursor()

    if agent_id is not None:
        assert_agent_in_organization(
            cursor,
            agent_id,
            organization_id
        )

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
            last_synced_at = ?,
            external_id = COALESCE(?, external_id)
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            address,
            jurisdiction,
            agent_id,
            property_type,
            listing_price,
            listing_purpose,
            last_synced_at,
            cleaned_external_id,
            property_id,
            organization_id,
        ),
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property was not found in this organization."
        )

    connection.commit()
    connection.close()


def update_property_status(
    property_id,
    organization_id,
    status,
    reviewed_by_user_id=None,
    reviewed_at=None,
    rejection_reason=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE properties
        SET
            status = ?,
            rejection_reason = ?,
            reviewed_by_user_id = ?,
            reviewed_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            status,
            rejection_reason,
            reviewed_by_user_id,
            reviewed_at,
            property_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property was not found in this organization."
        )

    connection.commit()
    connection.close()


def delete_property(
    property_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM properties
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            property_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property was not found in this organization."
        )

    connection.commit()
    connection.close()


def filter_properties(
    organization_id,
    property_id=None,
    address=None,
    jurisdiction=None,
    agent_name=None,
    min_price=None,
    max_price=None,
    agent_id=None,
    status=None,
    include_all_statuses=False
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    needs_operations = (
        min_price is not None
        or max_price is not None
    )

    conditions = [
        "properties.organization_id = ?"
    ]
    params = [organization_id]

    if needs_operations:
        query = """
            SELECT DISTINCT
                properties.id,
                properties.address,
                properties.jurisdiction,
                properties.organization_id,
                properties.agent_id,
                agents.name,
                properties.status,
                properties.rejection_reason,
                properties.reviewed_by_user_id,
                properties.reviewed_at,
                properties.created_by_user_id,
                properties.submitted_at,
                properties.property_type,
                properties.listing_price,
                properties.listing_purpose,
                properties.last_synced_at
            FROM properties
            LEFT JOIN agents
                ON properties.agent_id = agents.id
                AND agents.organization_id
                    = properties.organization_id
            INNER JOIN operations
                ON operations.property_id
                    = properties.id
                AND operations.organization_id
                    = properties.organization_id
                AND operations.status = 'approved'
        """
    else:
        query = PROPERTIES_BASE_QUERY

    if property_id is not None:
        conditions.append(
            "properties.id = ?"
        )
        params.append(property_id)

    if address is not None:
        conditions.append(
            "LOWER(properties.address) "
            "LIKE LOWER(?)"
        )
        params.append(
            f"%{address}%"
        )

    if jurisdiction is not None:
        conditions.append(
            "properties.jurisdiction = ?"
        )
        params.append(jurisdiction)

    if agent_id is not None:
        conditions.append(
            "properties.agent_id = ?"
        )
        params.append(agent_id)

    if agent_name is not None:
        conditions.append(
            "LOWER(agents.name) LIKE LOWER(?)"
        )
        params.append(
            f"%{agent_name}%"
        )

    if status is not None:
        conditions.append(
            "properties.status = ?"
        )
        params.append(status)
    elif not include_all_statuses:
        conditions.append(
            "properties.status = ?"
        )
        params.append(STATUS_APPROVED)

    if min_price is not None:
        conditions.append(
            "operations.sale_price >= ?"
        )
        params.append(min_price)

    if max_price is not None:
        conditions.append(
            "operations.sale_price <= ?"
        )
        params.append(max_price)

    query += (
        " WHERE "
        + " AND ".join(conditions)
    )
    query += " ORDER BY properties.id"

    cursor.execute(
        query,
        params
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_property_dict(row)
        for row in rows
    ]
