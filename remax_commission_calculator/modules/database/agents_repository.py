from .connection import get_connection
from .tenant import (
    TenantError,
    require_organization_id
)


def get_agents(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            name,
            type,
            organization_id
        FROM agents
        WHERE organization_id = ?
        ORDER BY id
        """,
        (
            organization_id,
        )
    )

    rows = cursor.fetchall()
    connection.close()

    agents = []

    for row in rows:
        agents.append({
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "organization_id": row[3]
        })

    return agents


def get_agent_record(agent_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            name,
            type,
            organization_id
        FROM agents
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            agent_id,
            organization_id
        )
    )

    row = cursor.fetchone()
    connection.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "type": row[2],
        "organization_id": row[3]
    }


def add_agent(
    name,
    agent_type,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO agents (
            name,
            type,
            organization_id
        )
        VALUES (?, ?, ?)
        """,
        (
            name,
            agent_type,
            organization_id
        )
    )

    agent_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return agent_id


def update_agent(
    agent_id,
    name,
    agent_type,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE agents
        SET
            name = ?,
            type = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            name,
            agent_type,
            agent_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Agent was not found in this organization."
        )

    connection.commit()
    connection.close()


def delete_agent(
    agent_id,
    organization_id
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM agents
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            agent_id,
            organization_id
        )
    )

    if cursor.rowcount == 0:
        connection.close()
        raise TenantError(
            "Agent was not found in this organization."
        )

    connection.commit()
    connection.close()
