class TenantError(ValueError):
    pass


def require_organization_id(organization_id):
    if organization_id is None:
        raise TenantError(
            "organization_id is required"
        )

    try:
        value = int(organization_id)
    except (TypeError, ValueError) as error:
        raise TenantError(
            "organization_id must be a valid id"
        ) from error

    if value <= 0:
        raise TenantError(
            "organization_id must be greater than zero"
        )

    return value


def assert_agent_in_organization(
    cursor,
    agent_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    cursor.execute(
        """
        SELECT organization_id
        FROM agents
        WHERE id = ?
        """,
        (
            agent_id,
        )
    )

    row = cursor.fetchone()

    if row is None:
        raise TenantError(
            "Selected agent was not found."
        )

    if row[0] != organization_id:
        raise TenantError(
            "Agent does not belong to this organization."
        )


def assert_property_in_organization(
    cursor,
    property_id,
    organization_id,
    require_approved=False
):
    organization_id = require_organization_id(
        organization_id
    )

    cursor.execute(
        """
        SELECT
            organization_id,
            agent_id,
            status
        FROM properties
        WHERE id = ?
        """,
        (
            property_id,
        )
    )

    row = cursor.fetchone()

    if row is None:
        raise TenantError(
            "Selected property was not found."
        )

    if row[0] != organization_id:
        raise TenantError(
            "Property does not belong to this organization."
        )

    status = row[2] or "approved"

    if require_approved and status != "approved":
        raise TenantError(
            "Property is not approved yet."
        )

    return row[1]


def assert_property_owned_by_agent(
    cursor,
    property_id,
    organization_id,
    agent_id
):
    property_agent_id = assert_property_in_organization(
        cursor,
        property_id,
        organization_id
    )

    if property_agent_id != agent_id:
        raise TenantError(
            "Property does not belong to this agent."
        )


def assert_operation_pair_in_organization(
    cursor,
    agent_id,
    property_id,
    organization_id,
    require_property_owner=False
):
    assert_agent_in_organization(
        cursor,
        agent_id,
        organization_id
    )
    property_agent_id = assert_property_in_organization(
        cursor,
        property_id,
        organization_id,
        require_approved=require_property_owner
    )

    if require_property_owner:
        if property_agent_id != agent_id:
            raise TenantError(
                "Property does not belong to this agent."
            )
    elif (
        property_agent_id is not None
        and property_agent_id != agent_id
    ):
        raise TenantError(
            "Property is assigned to a different agent."
        )
