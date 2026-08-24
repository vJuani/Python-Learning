from .connection import get_connection
from .tenant import require_organization_id


def get_dashboard_metrics(
    organization_id,
    agent_id=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if agent_id is None:
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
            WHERE organization_id = ?
                AND status = 'approved'
            """,
            (
                organization_id,
            )
        )
    else:
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
            WHERE organization_id = ?
                AND agent_id = ?
                AND status = 'approved'
            """,
            (
                organization_id,
                agent_id
            )
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
    organization_id,
    limit=3,
    agent_id=None
):
    organization_id = require_organization_id(
        organization_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    if agent_id is None:
        cursor.execute(
            """
            SELECT
                agents.name,
                SUM(operations.total_commission)
            FROM operations
            JOIN agents
                ON operations.agent_id = agents.id
                AND agents.organization_id
                    = operations.organization_id
            WHERE operations.organization_id = ?
                AND operations.status = 'approved'
            GROUP BY
                agents.id,
                agents.name
            ORDER BY
                SUM(operations.total_commission) DESC
            LIMIT ?
            """,
            (
                organization_id,
                limit
            )
        )
    else:
        cursor.execute(
            """
            SELECT
                agents.name,
                SUM(operations.total_commission)
            FROM operations
            JOIN agents
                ON operations.agent_id = agents.id
                AND agents.organization_id
                    = operations.organization_id
            WHERE operations.organization_id = ?
                AND operations.agent_id = ?
                AND operations.status = 'approved'
            GROUP BY
                agents.id,
                agents.name
            ORDER BY
                SUM(operations.total_commission) DESC
            LIMIT ?
            """,
            (
                organization_id,
                agent_id,
                limit
            )
        )

    rows = cursor.fetchall()
    connection.close()

    return [
        (row[0], row[1])
        for row in rows
    ]
