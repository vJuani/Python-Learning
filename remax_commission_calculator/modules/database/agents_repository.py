from .connection import get_connection


def get_agents():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            name,
            type
        FROM agents
        ORDER BY id
        """
    )

    rows = cursor.fetchall()

    connection.close()

    agents = []

    for row in rows:
        agents.append({
            "id": row[0],
            "name": row[1],
            "type": row[2]
        })

    return agents


def add_agent(
    name,
    agent_type
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO agents (
            name,
            type
        )
        VALUES (?, ?)
        """,
        (
            name,
            agent_type
        )
    )

    connection.commit()
    connection.close()


def update_agent(
    agent_id,
    name,
    agent_type
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE agents
        SET
            name = ?,
            type = ?
        WHERE id = ?
        """,
        (
            name,
            agent_type,
            agent_id
        )
    )

    connection.commit()
    connection.close()


def delete_agent(
    agent_id
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM agents
        WHERE id = ?
        """,
        (
            agent_id,
        )
    )

    connection.commit()
    connection.close()
