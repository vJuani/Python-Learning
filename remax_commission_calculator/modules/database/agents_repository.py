from datetime import datetime

from .connection import get_connection
from .tenant import (
    TenantError,
    require_organization_id,
)


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _build_agent_dict(row):
    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "type": row[2],
        "organization_id": row[3],
        "external_provider": (
            row[4] if len(row) > 4 else None
        ),
        "external_id": row[5] if len(row) > 5 else None,
        "last_synced_at": row[6] if len(row) > 6 else None,
        "team_leader_agent_id": (
            row[7] if len(row) > 7 else None
        ),
        "team_leader_name": (
            row[8] if len(row) > 8 else None
        ),
        "team_leader_type": (
            row[9] if len(row) > 9 else None
        ),
    }


AGENTS_BASE_QUERY = """
    SELECT
        agents.id,
        agents.name,
        agents.type,
        agents.organization_id,
        agents.external_provider,
        agents.external_id,
        agents.last_synced_at,
        agents.team_leader_agent_id,
        team_leader.name,
        team_leader.type
    FROM agents
    LEFT JOIN agents AS team_leader
        ON agents.team_leader_agent_id = team_leader.id
        AND agents.organization_id
            = team_leader.organization_id
"""


def get_agents(organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        AGENTS_BASE_QUERY
        + """
        WHERE agents.organization_id = ?
        ORDER BY agents.id
        """,
        (
            organization_id,
        )
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_agent_dict(row)
        for row in rows
    ]


def get_agent_record(agent_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        AGENTS_BASE_QUERY
        + """
        WHERE agents.id = ?
            AND agents.organization_id = ?
        """,
        (
            agent_id,
            organization_id
        )
    )

    row = cursor.fetchone()
    connection.close()

    return _build_agent_dict(row)


def list_team_juniors(team_leader_agent_id, organization_id):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        AGENTS_BASE_QUERY
        + """
        WHERE agents.organization_id = ?
            AND agents.team_leader_agent_id = ?
        ORDER BY agents.name
        """,
        (
            organization_id,
            team_leader_agent_id,
        ),
    )

    rows = cursor.fetchall()
    connection.close()

    return [
        _build_agent_dict(row)
        for row in rows
    ]


def find_agent_by_external_id(
    organization_id,
    external_provider,
    external_id,
):
    organization_id = require_organization_id(
        organization_id
    )

    if not external_provider or not external_id:
        return None

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        AGENTS_BASE_QUERY
        + """
        WHERE agents.organization_id = ?
            AND agents.external_provider = ?
            AND agents.external_id = ?
        LIMIT 1
        """,
        (
            organization_id,
            external_provider,
            str(external_id).strip(),
        ),
    )

    row = cursor.fetchone()
    connection.close()

    return _build_agent_dict(row)


def _assert_team_leader_in_org(
    cursor,
    team_leader_agent_id,
    organization_id,
    junior_agent_id=None,
):
    if team_leader_agent_id is None:
        return

    if (
        junior_agent_id is not None
        and int(team_leader_agent_id) == int(junior_agent_id)
    ):
        raise ValueError("team_leader_cannot_be_self")

    cursor.execute(
        """
        SELECT id
        FROM agents
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            team_leader_agent_id,
            organization_id,
        ),
    )

    if cursor.fetchone() is None:
        raise TenantError(
            "Team leader was not found in this organization."
        )


def add_agent(
    name,
    agent_type,
    organization_id,
    *,
    external_provider=None,
    external_id=None,
    last_synced_at=None,
    team_leader_agent_id=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        _assert_team_leader_in_org(
            cursor,
            team_leader_agent_id,
            organization_id,
        )

        cursor.execute(
            """
            INSERT INTO agents (
                name,
                type,
                organization_id,
                external_provider,
                external_id,
                last_synced_at,
                team_leader_agent_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                agent_type,
                organization_id,
                external_provider,
                external_id,
                last_synced_at,
                team_leader_agent_id,
            )
        )

        agent_id = cursor.lastrowid
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    return agent_id


def update_agent(
    agent_id,
    name,
    agent_type,
    organization_id,
    *,
    team_leader_agent_id=None,
    update_team_leader=False,
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        if update_team_leader:
            _assert_team_leader_in_org(
                cursor,
                team_leader_agent_id,
                organization_id,
                junior_agent_id=agent_id,
            )

            cursor.execute(
                """
                UPDATE agents
                SET
                    name = ?,
                    type = ?,
                    team_leader_agent_id = ?
                WHERE id = ?
                    AND organization_id = ?
                """,
                (
                    name,
                    agent_type,
                    team_leader_agent_id,
                    agent_id,
                    organization_id
                )
            )
        else:
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
            raise TenantError(
                "Agent was not found in this organization."
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def update_agent_from_sync(
    agent_id,
    organization_id,
    *,
    name,
    external_provider,
    external_id,
    last_synced_at=None,
):
    organization_id = require_organization_id(
        organization_id
    )

    if last_synced_at is None:
        last_synced_at = _now_iso()

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE agents
        SET
            name = ?,
            external_provider = ?,
            external_id = ?,
            last_synced_at = ?
        WHERE id = ?
            AND organization_id = ?
        """,
        (
            name,
            external_provider,
            external_id,
            last_synced_at,
            agent_id,
            organization_id,
        ),
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
