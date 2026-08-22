from .connection import get_connection


def get_dashboard_metrics():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(total_commission), 0),
            COALESCE(SUM(office_payment), 0),
            COALESCE(SUM(agent_payment), 0),
            COALESCE(MAX(total_commission), 0),
            COALESCE(AVG(total_commission), 0)
        FROM operations
        """
    )

    row = cursor.fetchone()

    connection.close()

    return {
        "total_operations": row[0],
        "gross_commission": row[1],
        "office_revenue": row[2],
        "agent_payments": row[3],
        "highest_commission": row[4],
        "average_commission": row[5]
    }


def get_agent_ranking(
    limit=3
):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            agents.name,
            SUM(operations.total_commission)
        FROM operations

        JOIN agents
            ON operations.agent_id = agents.id

        GROUP BY
            agents.id,
            agents.name

        ORDER BY
            SUM(operations.total_commission) DESC

        LIMIT ?
        """,
        (
            limit,
        )
    )

    rows = cursor.fetchall()

    connection.close()

    return rows
