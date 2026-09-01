"""
Agents directory view-model (admin agents page mockup).
"""

from __future__ import annotations

import calendar
from datetime import date

from modules.database.connection import get_connection
from modules.database.properties_repository import (
    STATUS_APPROVED as PROPERTY_STATUS_APPROVED,
)
from modules.database.reports_repository import (
    aggregate_report_metrics,
)
from modules.database.tenant import require_organization_id
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.organization_dashboard import (
    PERIOD_THIS_MONTH,
    resolve_dashboard_period,
)
from modules.validators import date_to_sortable


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _today():
    return date.today()


def _display_date(year, month, day):
    return f"{day:02d}/{month:02d}/{year:04d}"


def _previous_month_bounds(today=None):
    today = today or _today()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1

    last_day = calendar.monthrange(year, month)[1]
    return (
        _display_date(year, month, 1),
        _display_date(year, month, last_day),
    )


def _pct_change(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _performance_label(score, language):
    if score >= 90:
        return _t("agents_perf_excellent", language)
    if score >= 80:
        return _t("agents_perf_very_good", language)
    if score >= 65:
        return _t("agents_perf_good", language)
    return _t("agents_perf_fair", language)


def _performance_tone(score):
    if score >= 80:
        return "high"
    if score >= 65:
        return "mid"
    return "low"


def _agent_stats_map(organization_id, date_from, date_to, *, status="approved"):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT
            operations.agent_id,
            COUNT(*),
            COALESCE(SUM(operations.total_commission), 0),
            SUM(
                CASE
                    WHEN operations.was_invoiced = 'no' THEN 1
                    ELSE 0
                END
            )
        FROM operations
        WHERE operations.organization_id = ?
          AND operations.status = ?
          AND operations.operation_date >= ?
          AND operations.operation_date <= ?
        GROUP BY operations.agent_id
        """,
        (
            organization_id,
            status,
            date_from,
            date_to,
        ),
    )
    rows = cursor.fetchall()
    connection.close()

    stats = {}
    for row in rows:
        stats[row[0]] = {
            "operations": int(row[1] or 0),
            "commission": float(row[2] or 0),
            "pending_invoice_ops": int(row[3] or 0),
        }

    return stats


def _property_counts(organization_id):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT agent_id, COUNT(*)
        FROM properties
        WHERE organization_id = ?
          AND status = ?
        GROUP BY agent_id
        """,
        (organization_id, PROPERTY_STATUS_APPROVED),
    )
    rows = cursor.fetchall()
    connection.close()
    return {row[0]: int(row[1] or 0) for row in rows}


def _team_options(agents):
    teams = {}
    for agent in agents:
        leader_id = agent.get("team_leader_agent_id")
        if leader_id is None:
            continue
        label = agent.get("team_leader_name") or f"Team {leader_id}"
        teams[leader_id] = label
    return sorted(
        [{"id": key, "label": value} for key, value in teams.items()],
        key=lambda item: item["label"].lower(),
    )


def _team_distribution(agents):
    buckets = {}
    for agent in agents:
        label = (
            agent.get("team_leader_name")
            or agent.get("type")
            or "—"
        )
        buckets[label] = buckets.get(label, 0) + 1

    total = len(agents) or 1
    distribution = []

    for label, count in sorted(
        buckets.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        distribution.append(
            {
                "label": label,
                "count": count,
                "percent": round((count / total) * 100, 1),
            }
        )

    return distribution


def build_agents_directory(
    organization_id,
    agents,
    raw_filters,
    *,
    language="es",
):
    language = language if language in ("es", "en") else "es"
    organization_id = require_organization_id(organization_id)

    _, _, date_from, date_to = resolve_dashboard_period(
        {"period": PERIOD_THIS_MONTH}
    )
    prev_from, prev_to = _previous_month_bounds()

    current_stats = _agent_stats_map(
        organization_id,
        date_from,
        date_to,
    )
    previous_stats = _agent_stats_map(
        organization_id,
        prev_from,
        prev_to,
    )
    property_counts = _property_counts(organization_id)

    report_filters = {
        "organization_id": organization_id,
        "date_from": date_from,
        "date_to": date_to,
        "status": "approved",
        "agent_id": None,
        "agent_name": None,
        "property_address": None,
        "min_amount": None,
        "max_amount": None,
        "was_invoiced": None,
        "jurisdiction": None,
        "currency": None,
        "agent_type": None,
    }
    month_metrics = aggregate_report_metrics(report_filters)

    prev_filters = dict(report_filters)
    prev_filters["date_from"] = prev_from
    prev_filters["date_to"] = prev_to
    prev_metrics = aggregate_report_metrics(prev_filters)

    close_denominator = int(month_metrics.get("operations_count") or 0)
    close_rate = 0.0
    if close_denominator > 0:
        close_rate = round(
            (close_denominator / max(close_denominator, 1)) * 100,
            1,
        )

    all_ops_filters = dict(report_filters)
    all_ops_filters["status"] = None
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT
            COUNT(*),
            SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END)
        FROM operations
        WHERE organization_id = ?
          AND operation_date >= ?
          AND operation_date <= ?
          AND status IN ('approved', 'pending', 'rejected')
        """,
        (organization_id, date_from, date_to),
    )
    total_row = cursor.fetchone()
    connection.close()

    total_non_draft = int(total_row[0] or 0)
    approved_count = int(total_row[1] or 0)
    if total_non_draft > 0:
        close_rate = round((approved_count / total_non_draft) * 100, 1)

    max_commission = max(
        (stats.get("commission", 0) for stats in current_stats.values()),
        default=0.0,
    )

    rows = []
    for agent in agents:
        agent_id = agent["id"]
        stats = current_stats.get(agent_id, {})
        prev = previous_stats.get(agent_id, {})
        active_count = property_counts.get(agent_id, 0)
        commission = stats.get("commission", 0.0)
        operations = stats.get("operations", 0)

        if max_commission > 0 and commission > 0:
            score = min(
                100,
                round(55 + (45 * commission / max_commission)),
            )
        elif operations > 0:
            score = 60
        else:
            score = 0

        user = get_user_by_agent_id(agent_id, organization_id)
        email = (user or {}).get("email") or "—"
        phone = (user or {}).get("phone") or "—"

        billing_pending = stats.get("pending_invoice_ops", 0) > 0
        team_label = (
            agent.get("team_leader_name")
            or agent.get("type")
            or "—"
        )

        rows.append(
            {
                "id": agent_id,
                "code": f"AG-{agent_id:03d}",
                "name": agent["name"],
                "type": agent.get("type") or "—",
                "team_label": team_label,
                "team_leader_id": agent.get("team_leader_agent_id"),
                "email": email,
                "phone": phone,
                "active_count": active_count,
                "active_trend": _pct_change(
                    active_count,
                    active_count,
                ),
                "operations": operations,
                "operations_trend": _pct_change(
                    operations,
                    prev.get("operations", 0),
                ),
                "commission": commission,
                "commission_trend": _pct_change(
                    commission,
                    prev.get("commission", 0),
                ),
                "billing_status": (
                    "pending" if billing_pending else "ok"
                ),
                "billing_label": _t(
                    "agents_billing_pending"
                    if billing_pending
                    else "agents_billing_ok",
                    language,
                ),
                "performance_score": score,
                "performance_label": _performance_label(
                    score,
                    language,
                ),
                "performance_tone": _performance_tone(score),
            }
        )

    rows.sort(
        key=lambda item: (
            -item["performance_score"],
            -item["commission"],
            item["name"].lower(),
        )
    )

    search_q = (raw_filters.get("q") or "").strip().lower()
    team_filter = raw_filters.get("team") or ""
    status_filter = raw_filters.get("status") or ""

    if search_q:
        rows = [
            row
            for row in rows
            if search_q in row["name"].lower()
            or search_q in row["code"].lower()
            or search_q in row["email"].lower()
        ]

    if team_filter:
        try:
            team_id = int(team_filter)
            rows = [
                row
                for row in rows
                if row["team_leader_id"] == team_id
            ]
        except (TypeError, ValueError):
            pass

    if status_filter == "billing_pending":
        rows = [
            row for row in rows if row["billing_status"] == "pending"
        ]
    elif status_filter == "with_operations":
        rows = [row for row in rows if row["operations"] > 0]

    page_size = int(raw_filters.get("per_page") or 8)
    page_size = max(5, min(page_size, 50))
    page = int(raw_filters.get("page") or 1)
    page = max(1, page)
    total_rows = len(rows)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    top_performers = rows[:3]
    agents_without_billing = sum(
        1 for row in rows if row["billing_status"] == "pending"
    )

    pending_operations = 0
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM operations
        WHERE organization_id = ?
          AND status = 'pending'
        """,
        (organization_id,),
    )
    pending_operations = int(cursor.fetchone()[0] or 0)
    connection.close()

    team_options = _team_options(agents)

    return {
        "kpis": {
            "active_agents": len(agents),
            "active_agents_trend": _pct_change(
                len(agents),
                len(agents),
            ),
            "operations_month": int(
                month_metrics.get("operations_count") or 0
            ),
            "operations_trend": _pct_change(
                month_metrics.get("operations_count", 0),
                prev_metrics.get("operations_count", 0),
            ),
            "commission_month": float(
                month_metrics.get("total_commission") or 0
            ),
            "commission_trend": _pct_change(
                month_metrics.get("total_commission", 0),
                prev_metrics.get("total_commission", 0),
            ),
            "close_rate": close_rate,
            "close_rate_trend": _pct_change(
                close_rate,
                close_rate,
            ),
        },
        "rows": page_rows,
        "pagination": {
            "page": page,
            "per_page": page_size,
            "total": total_rows,
            "total_pages": total_pages,
            "from_row": start + 1 if total_rows else 0,
            "to_row": min(start + page_size, total_rows),
        },
        "filters": {
            "q": raw_filters.get("q") or "",
            "team": team_filter,
            "status": status_filter,
            "per_page": page_size,
        },
        "team_options": team_options,
        "team_report_leader_id": (
            team_options[0]["id"] if team_options else None
        ),
        "top_performers": top_performers,
        "pending_actions": [
            {
                "label": _t("agents_pending_contracts", language),
                "count": pending_operations,
            },
            {
                "label": _t(
                    "agents_pending_billing_agents",
                    language,
                ),
                "count": agents_without_billing,
            },
            {
                "label": _t(
                    "agents_pending_training",
                    language,
                ),
                "count": 0,
            },
        ],
        "team_distribution": _team_distribution(agents),
        "distribution_total": len(agents),
    }
