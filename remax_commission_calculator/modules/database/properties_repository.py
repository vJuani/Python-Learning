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


def get_property_ids_used_in_operations(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT DISTINCT property_id
        FROM operations
        WHERE organization_id = ?
        """,
        (organization_id,),
    )
    used_ids = {
        row[0]
        for row in cursor.fetchall()
        if row[0] is not None
    }
    connection.close()
    return used_ids


def is_property_available_for_operation(
    property_id,
    organization_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    property_data = get_property_record(
        property_id,
        organization_id,
    )
    if property_data is None:
        return False

    if property_data.get("status") != STATUS_APPROVED:
        return False

    used_ids = get_property_ids_used_in_operations(
        organization_id
    )
    return property_id not in used_ids


def list_available_properties_for_operation(
    organization_id,
    *,
    agent_id=None,
    query=None,
    limit=20,
):
    organization_id = require_organization_id(
        organization_id
    )
    limit = max(1, min(int(limit or 20), 50))

    connection = get_connection()
    cursor = connection.cursor()

    sql = (
        PROPERTIES_BASE_QUERY
        + """
        WHERE properties.organization_id = ?
            AND properties.status = ?
            AND properties.id NOT IN (
                SELECT property_id
                FROM operations
                WHERE organization_id = ?
            )
        """
    )
    params = [
        organization_id,
        STATUS_APPROVED,
        organization_id,
    ]

    if agent_id is not None:
        sql += " AND properties.agent_id = ?"
        params.append(agent_id)

    if query is not None and str(query).strip():
        needle = f"%{str(query).strip()}%"
        sql += """
            AND (
                properties.external_id LIKE ?
                OR properties.address LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM property_external_listings pel
                    WHERE pel.organization_id
                        = properties.organization_id
                        AND pel.property_id = properties.id
                        AND pel.external_id LIKE ?
                )
            )
        """
        params.extend([needle, needle, needle])

    sql += " ORDER BY properties.id LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    connection.close()

    return [
        _build_property_dict(row)
        for row in rows
    ]


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
    external_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    if agent_id is not None:
        connection = get_connection()
        cursor = connection.cursor()

        try:
            assert_agent_in_organization(
                cursor,
                agent_id,
                organization_id
            )
        finally:
            connection.close()

    cleaned_external_id = None
    if external_id is not None:
        cleaned_external_id = str(external_id).strip() or None

    connection = get_connection()
    cursor = connection.cursor()

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
            external_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            address.strip(),
            jurisdiction.strip(),
            organization_id,
            agent_id,
            status,
            created_by_user_id,
            submitted_at,
            property_type,
            listing_price,
            listing_purpose,
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

    cleaned_external_id = None
    if external_id is not None:
        cleaned_external_id = str(external_id).strip() or None

    cursor.execute(
        """
        UPDATE properties
        SET
            address = ?,
            jurisdiction = ?,
            agent_id = ?,
            property_type = COALESCE(?, property_type),
            listing_price = COALESCE(?, listing_price),
            listing_purpose = COALESCE(?, listing_purpose),
            external_id = COALESCE(?, external_id)
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            address.strip(),
            jurisdiction.strip(),
            agent_id,
            property_type,
            listing_price,
            listing_purpose,
            cleaned_external_id,
            property_id,
            organization_id,
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
        )

    connection.commit()
    connection.close()


def update_property_status(
    property_id,
    organization_id,
    status,
    rejection_reason=None,
    reviewed_by_user_id=None,
    reviewed_at=None
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
            organization_id,
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
        )

    connection.commit()
    connection.close()


def update_property_from_sync(
    property_id,
    organization_id,
    **fields
):
    organization_id = require_organization_id(
        organization_id
    )

    allowed = {
        "address",
        "jurisdiction",
        "agent_id",
        "property_type",
        "listing_price",
        "listing_purpose",
        "external_id",
        "last_synced_at",
    }
    updates = {
        key: value
        for key, value in fields.items()
        if key in allowed
    }

    if not updates:
        return

    connection = get_connection()
    cursor = connection.cursor()

    if updates.get("agent_id") is not None:
        assert_agent_in_organization(
            cursor,
            updates["agent_id"],
            organization_id,
        )

    set_parts = []
    params = []

    for key, value in updates.items():
        set_parts.append(f"{key} = ?")
        params.append(value)

    params.extend([property_id, organization_id])

    cursor.execute(
        f"""
        UPDATE properties
        SET {", ".join(set_parts)}
        WHERE id = ?
            AND organization_id = ?
        """,
        params,
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
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
            organization_id,
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Property not found in organization."
        )

    connection.commit()
    connection.close()


def filter_properties(
    organization_id,
    agent_id=None,
    jurisdiction=None,
    property_type=None,
    listing_purpose=None,
    min_price=None,
    max_price=None,
    status=None,
    include_all_statuses=False,
    only_with_operations=False,
    only_without_operations=False,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    query = PROPERTIES_BASE_QUERY
    conditions = [
        "properties.organization_id = ?"
    ]
    params = [organization_id]

    if agent_id is not None:
        conditions.append(
            "properties.agent_id = ?"
        )
        params.append(agent_id)

    if jurisdiction is not None:
        conditions.append(
            "properties.jurisdiction = ?"
        )
        params.append(jurisdiction)

    if property_type is not None:
        conditions.append(
            "properties.property_type = ?"
        )
        params.append(property_type)

    if listing_purpose is not None:
        conditions.append(
            "properties.listing_purpose = ?"
        )
        params.append(listing_purpose)

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

    if only_with_operations:
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM operations
                WHERE operations.property_id
                    = properties.id
                    AND operations.organization_id
                        = properties.organization_id
            )
            """
        )

    if only_without_operations:
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM operations
                WHERE operations.property_id
                    = properties.id
                    AND operations.organization_id
                        = properties.organization_id
            )
            """
        )

    if min_price is not None or max_price is not None:
        query += """
            LEFT JOIN operations
                ON operations.property_id
                    = properties.id
                AND operations.organization_id
                    = properties.organization_id
        """

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
