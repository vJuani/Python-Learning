"""
SQL aggregations for organization-wide reports.
"""

from .connection import get_connection
from .operations_repository import (
    MONTH_KEY_SQL,
    build_operation_filter_conditions,
)
from .tenant import require_organization_id


OPERATIONS_FROM = """
    FROM operations
    JOIN agents
        ON operations.agent_id = agents.id
        AND agents.organization_id
            = operations.organization_id
    JOIN properties
        ON operations.property_id = properties.id
        AND properties.organization_id
            = operations.organization_id
"""


def _where_clause(filters):
    conditions, params = build_operation_filter_conditions(
        **filters
    )
    return " WHERE " + " AND ".join(conditions), params


def aggregate_report_metrics(filters):
    require_organization_id(filters["organization_id"])
    where_sql, params = _where_clause(filters)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        f"""
        SELECT
            COUNT(*),
            COALESCE(SUM(operations.sale_price), 0),
            COALESCE(SUM(operations.total_commission), 0),
            COALESCE(SUM(operations.agent_payment), 0),
            COALESCE(SUM(operations.office_payment), 0),
            COALESCE(SUM(operations.vat_amount), 0),
            COALESCE(SUM(operations.office_total), 0),
            COALESCE(SUM(operations.abao), 0),
            COALESCE(SUM(operations.martillero), 0),
            COUNT(DISTINCT operations.property_id),
            COALESCE(AVG(operations.total_commission), 0),
            SUM(
                CASE
                    WHEN operations.was_invoiced = 'yes'
                    THEN 1 ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN operations.was_invoiced = 'no'
                    THEN 1 ELSE 0
                END
            )
        {OPERATIONS_FROM}
        {where_sql}
        """,
        params,
    )
    row = cursor.fetchone()
    connection.close()

    return {
        "operations_count": int(row[0] or 0),
        "volume_usd": float(row[1] or 0),
        "total_commission": float(row[2] or 0),
        "agent_payments": float(row[3] or 0),
        "office_net": float(row[4] or 0),
        "vat_total": float(row[5] or 0),
        "office_total": float(row[6] or 0),
        "abao_total": float(row[7] or 0),
        "martillero_total": float(row[8] or 0),
        "properties_count": int(row[9] or 0),
        "average_commission": float(row[10] or 0),
        "invoiced_count": int(row[11] or 0),
        "not_invoiced_count": int(row[12] or 0),
    }


def aggregate_status_counts(filters):
    require_organization_id(filters["organization_id"])
    # Status distribution ignores status filter so the chart
    # remains useful when official metrics default to approved.
    status_free = dict(filters)
    status_free["status"] = None
    where_sql, params = _where_clause(status_free)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        f"""
        SELECT
            COALESCE(operations.status, 'approved'),
            COUNT(*)
        {OPERATIONS_FROM}
        {where_sql}
        GROUP BY COALESCE(operations.status, 'approved')
        """,
        params,
    )
    rows = cursor.fetchall()
    connection.close()

    counts = {
        "draft": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
    }

    for status, count in rows:
        key = status if status in counts else "approved"
        counts[key] = int(count or 0)

    return counts


def aggregate_monthly_series(filters):
    require_organization_id(filters["organization_id"])
    where_sql, params = _where_clause(filters)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        f"""
        SELECT
            {MONTH_KEY_SQL} AS month_key,
            COUNT(*),
            COALESCE(SUM(operations.total_commission), 0),
            COALESCE(SUM(operations.sale_price), 0),
            COALESCE(SUM(operations.agent_payment), 0),
            COALESCE(SUM(operations.office_payment), 0)
        {OPERATIONS_FROM}
        {where_sql}
        GROUP BY month_key
        ORDER BY month_key ASC
        """,
        params,
    )
    rows = cursor.fetchall()
    connection.close()

    series = []

    for row in rows:
        series.append(
            {
                "month": row[0],
                "operations_count": int(row[1] or 0),
                "total_commission": float(row[2] or 0),
                "volume_usd": float(row[3] or 0),
                "agent_payments": float(row[4] or 0),
                "office_net": float(row[5] or 0),
            }
        )

    return series


def aggregate_agent_ranking(filters, limit=10):
    require_organization_id(filters["organization_id"])
    where_sql, params = _where_clause(filters)
    connection = get_connection()
    cursor = connection.cursor()
    query_params = list(params)

    limit_sql = ""

    if limit is not None:
        limit_sql = " LIMIT ?"
        query_params.append(int(limit))

    cursor.execute(
        f"""
        SELECT
            agents.id,
            agents.name,
            agents.type,
            COUNT(*),
            COALESCE(SUM(operations.total_commission), 0),
            COALESCE(SUM(operations.agent_payment), 0),
            COALESCE(SUM(operations.office_payment), 0),
            COALESCE(SUM(operations.sale_price), 0)
        {OPERATIONS_FROM}
        {where_sql}
        GROUP BY
            agents.id,
            agents.name,
            agents.type
        ORDER BY
            SUM(operations.total_commission) DESC,
            agents.name ASC
        {limit_sql}
        """,
        query_params,
    )
    rows = cursor.fetchall()
    connection.close()

    ranking = []

    for index, row in enumerate(rows, start=1):
        ranking.append(
            {
                "rank": index,
                "agent_id": row[0],
                "agent_name": row[1],
                "agent_type": row[2],
                "operations_count": int(row[3] or 0),
                "total_commission": float(row[4] or 0),
                "agent_payments": float(row[5] or 0),
                "office_net": float(row[6] or 0),
                "volume_usd": float(row[7] or 0),
            }
        )

    return ranking
