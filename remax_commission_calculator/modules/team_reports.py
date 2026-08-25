"""
Team / Team Leader reporting (read-only over ops + ledger).

Does not change commission formulas or wallet posting rules.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime

from modules.database.agent_wallet_repository import (
    MOVEMENT_OWN_COMMISSION,
    MOVEMENT_REVERSAL,
    MOVEMENT_TEAM_LEADER_INCOME,
    list_wallet_movements_for_agent,
    sum_wallet_by_type,
)
from modules.database.agents_repository import (
    get_agent_record,
    get_agents,
    list_team_juniors,
)
from modules.database.connection import get_connection
from modules.database.operations_repository import (
    count_operations_by_status,
    filter_operations,
)
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.validators import date_to_sortable
from modules.workflow import STATUS_APPROVED, STATUS_PENDING


PERIOD_MODE_ALL = "all"
PERIOD_MODE_MONTH = "month"
PERIOD_MODE_YEAR = "year"
PERIOD_MODE_RANGE = "range"
PERIOD_MODES = (
    PERIOD_MODE_ALL,
    PERIOD_MODE_MONTH,
    PERIOD_MODE_YEAR,
    PERIOD_MODE_RANGE,
)


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _today():
    return date.today()


def _display(year, month, day):
    return f"{day:02d}/{month:02d}/{year:04d}"


def _month_bounds_display(year, month):
    last = calendar.monthrange(year, month)[1]
    return (
        _display(year, month, 1),
        _display(year, month, last),
    )


def _as_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_team_period_filters(raw_filters):
    """
    Returns (errors, meta) where meta has:
    period_mode, date_from_sortable, date_to_sortable,
    date_from_display, date_to_display, form fields.
    """
    errors = []
    today = _today()
    period_mode = (raw_filters.get("period_mode") or PERIOD_MODE_MONTH).strip()

    if period_mode not in PERIOD_MODES:
        errors.append("Invalid period mode.")
        period_mode = PERIOD_MODE_MONTH

    year = _as_int(raw_filters.get("year"))
    month = None
    month_year = (raw_filters.get("month_year") or "").strip()

    if month_year:
        try:
            parsed = datetime.strptime(month_year, "%Y-%m")
            year = parsed.year
            month = parsed.month
        except ValueError:
            errors.append("Invalid month.")

    if month is None:
        month = _as_int(raw_filters.get("month"))

    date_from_display = None
    date_to_display = None
    sortable_from = None
    sortable_to = None

    if period_mode == PERIOD_MODE_ALL:
        pass
    elif period_mode == PERIOD_MODE_MONTH:
        if year is None:
            year = today.year
        if month is None:
            month = today.month
        if year < 2000 or year > 2100 or month < 1 or month > 12:
            errors.append("Invalid month.")
        else:
            date_from_display, date_to_display = _month_bounds_display(
                year,
                month,
            )
    elif period_mode == PERIOD_MODE_YEAR:
        if year is None:
            year = today.year
        if year < 2000 or year > 2100:
            errors.append("Invalid year.")
        else:
            date_from_display = _display(year, 1, 1)
            date_to_display = _display(year, 12, 31)
    else:
        from modules.validators import parse_optional_date

        parsed_from, from_error = parse_optional_date(
            raw_filters.get("date_from", ""),
            "Start date",
        )
        parsed_to, to_error = parse_optional_date(
            raw_filters.get("date_to", ""),
            "End date",
        )
        if from_error:
            errors.append(from_error)
        else:
            date_from_display = parsed_from
        if to_error:
            errors.append(to_error)
        else:
            date_to_display = parsed_to

    if date_from_display:
        sortable_from = date_to_sortable(date_from_display)
    if date_to_display:
        sortable_to = date_to_sortable(date_to_display)

    form_month_year = ""
    if year and month:
        form_month_year = f"{year:04d}-{month:02d}"
    elif period_mode == PERIOD_MODE_MONTH:
        form_month_year = f"{today.year:04d}-{today.month:02d}"

    from modules.validators import date_display_to_iso

    return errors, {
        "period_mode": period_mode,
        "date_from_sortable": sortable_from,
        "date_to_sortable": sortable_to,
        "date_from_display": date_from_display,
        "date_to_display": date_to_display,
        "year": year or today.year,
        "month": month or today.month,
        "form": {
            "period_mode": period_mode,
            "month_year": form_month_year,
            "year": str(year or today.year),
            "date_from": (
                date_display_to_iso(date_from_display)
                if date_from_display and period_mode == PERIOD_MODE_RANGE
                else ""
            ),
            "date_to": (
                date_display_to_iso(date_to_display)
                if date_to_display and period_mode == PERIOD_MODE_RANGE
                else ""
            ),
        },
    }


def list_team_leaders(organization_id):
    """Agents that currently have at least one junior assigned."""
    organization_id = require_organization_id(organization_id)
    agents = get_agents(organization_id)
    leaders = []

    for agent in agents:
        juniors = list_team_juniors(agent["id"], organization_id)
        if juniors:
            leaders.append(agent)

    return leaders


def agent_is_team_leader(organization_id, agent_id):
    return len(list_team_juniors(agent_id, organization_id)) > 0


def _agent_approved_stats(
    organization_id,
    agent_id,
    date_from_sortable=None,
    date_to_sortable=None,
):
    operations = filter_operations(
        organization_id,
        agent_id=agent_id,
        status=STATUS_APPROVED,
        date_from=date_from_sortable,
        date_to=date_to_sortable,
    )

    production = 0.0
    junior_yield = 0.0

    for operation in operations:
        production += float(operation.get("total_commission") or 0)
        junior_yield += float(operation.get("agent_payment") or 0)

    return {
        "operations_count": len(operations),
        "production": production,
        "agent_yield": junior_yield,
        "operations": operations,
    }


def _team_leader_income_by_source(
    organization_id,
    team_leader_id,
    date_from_sortable=None,
    date_to_sortable=None,
):
    """
    Net TL income per source junior (credits + reversals),
    optionally limited to operations in the period.
    """
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()

    conditions = [
        "m.organization_id = ?",
        "m.agent_id = ?",
        "m.source_agent_id IS NOT NULL",
        f"m.movement_type IN ('{MOVEMENT_TEAM_LEADER_INCOME}', '{MOVEMENT_REVERSAL}')",
    ]
    params = [organization_id, team_leader_id]

    join_ops = ""
    if date_from_sortable is not None or date_to_sortable is not None:
        join_ops = """
            JOIN operations o
                ON m.operation_id = o.id
                AND m.organization_id = o.organization_id
        """
        op_date_sortable = (
            "substr(o.operation_date, 7, 4) || "
            "substr(o.operation_date, 4, 2) || "
            "substr(o.operation_date, 1, 2)"
        )
        if date_from_sortable is not None:
            conditions.append(f"{op_date_sortable} >= ?")
            params.append(date_from_sortable)
        if date_to_sortable is not None:
            conditions.append(f"{op_date_sortable} <= ?")
            params.append(date_to_sortable)

    where_sql = " AND ".join(conditions)

    cursor.execute(
        f"""
        SELECT
            m.source_agent_id,
            COALESCE(SUM(m.amount), 0)
        FROM agent_wallet_movements AS m
        {join_ops}
        WHERE {where_sql}
        GROUP BY m.source_agent_id
        """,
        params,
    )

    rows = cursor.fetchall()
    connection.close()

    return {
        int(row[0]): float(row[1] or 0)
        for row in rows
        if row[0] is not None
    }


def _own_commission_in_period(
    organization_id,
    agent_id,
    date_from_sortable=None,
    date_to_sortable=None,
):
    organization_id = require_organization_id(organization_id)
    connection = get_connection()
    cursor = connection.cursor()

    conditions = [
        "m.organization_id = ?",
        "m.agent_id = ?",
        f"m.movement_type IN ('{MOVEMENT_OWN_COMMISSION}', '{MOVEMENT_REVERSAL}')",
        "m.source_agent_id IS NULL",
    ]
    params = [organization_id, agent_id]

    join_ops = ""
    if date_from_sortable is not None or date_to_sortable is not None:
        join_ops = """
            LEFT JOIN operations o
                ON m.operation_id = o.id
                AND m.organization_id = o.organization_id
        """
        op_date_sortable = (
            "substr(o.operation_date, 7, 4) || "
            "substr(o.operation_date, 4, 2) || "
            "substr(o.operation_date, 1, 2)"
        )
        # Own commission reversals/credits tied to ops in period
        if date_from_sortable is not None:
            conditions.append(
                f"(m.operation_id IS NULL OR {op_date_sortable} >= ?)"
            )
            params.append(date_from_sortable)
        if date_to_sortable is not None:
            conditions.append(
                f"(m.operation_id IS NULL OR {op_date_sortable} <= ?)"
            )
            params.append(date_to_sortable)

    where_sql = " AND ".join(conditions)

    cursor.execute(
        f"""
        SELECT COALESCE(SUM(m.amount), 0)
        FROM agent_wallet_movements AS m
        {join_ops}
        WHERE {where_sql}
        """,
        params,
    )

    total = cursor.fetchone()[0]
    connection.close()

    return float(total or 0)


def build_junior_team_rows(
    organization_id,
    team_leader_id,
    date_from_sortable=None,
    date_to_sortable=None,
):
    juniors = list_team_juniors(team_leader_id, organization_id)
    income_by_source = _team_leader_income_by_source(
        organization_id,
        team_leader_id,
        date_from_sortable=date_from_sortable,
        date_to_sortable=date_to_sortable,
    )

    rows = []
    for junior in juniors:
        stats = _agent_approved_stats(
            organization_id,
            junior["id"],
            date_from_sortable=date_from_sortable,
            date_to_sortable=date_to_sortable,
        )
        rows.append(
            {
                "agent": junior,
                "operations_count": stats["operations_count"],
                "production": stats["production"],
                "junior_yield": stats["agent_yield"],
                "team_leader_income": income_by_source.get(
                    junior["id"],
                    0.0,
                ),
            }
        )

    return rows


def load_team_report(
    organization_id,
    team_leader_id,
    raw_filters=None,
    language="es",
):
    organization_id = require_organization_id(organization_id)
    raw_filters = raw_filters or {}
    language = language if language in ("es", "en") else "es"

    leader = get_agent_record(team_leader_id, organization_id)
    if leader is None:
        return None

    errors, period = parse_team_period_filters(raw_filters)
    date_from = period["date_from_sortable"]
    date_to = period["date_to_sortable"]

    junior_rows = build_junior_team_rows(
        organization_id,
        team_leader_id,
        date_from_sortable=date_from,
        date_to_sortable=date_to,
    )

    leader_stats = _agent_approved_stats(
        organization_id,
        team_leader_id,
        date_from_sortable=date_from,
        date_to_sortable=date_to,
    )

    own_income = _own_commission_in_period(
        organization_id,
        team_leader_id,
        date_from_sortable=date_from,
        date_to_sortable=date_to,
    )
    # Prefer ops agent_payment for own production consistency when period set
    leader_own_production = leader_stats["production"]
    leader_own_yield = leader_stats["agent_yield"]

    juniors_production = sum(row["production"] for row in junior_rows)
    juniors_income_to_tl = sum(
        row["team_leader_income"] for row in junior_rows
    )
    team_production = leader_own_production + juniors_production
    combined_income = leader_own_yield + juniors_income_to_tl

    team_operations = list(leader_stats["operations"])
    for row in junior_rows:
        team_operations.extend(
            filter_operations(
                organization_id,
                agent_id=row["agent"]["id"],
                status=STATUS_APPROVED,
                date_from=date_from,
                date_to=date_to,
            )
        )

    team_operations.sort(
        key=lambda item: item.get("db_id") or 0,
        reverse=True,
    )

    years = list(range(_today().year, _today().year - 8, -1))

    return {
        "errors": errors,
        "leader": leader,
        "junior_rows": junior_rows,
        "metrics": {
            "team_production": team_production,
            "leader_production": leader_own_production,
            "juniors_production": juniors_production,
            "leader_own_income": leader_own_yield,
            "juniors_income_to_leader": juniors_income_to_tl,
            "combined_income": combined_income,
            "operations_count": len(team_operations),
            "juniors_count": len(junior_rows),
            "wallet_own_period": own_income,
        },
        "operations": team_operations,
        "period": period,
        "form": {
            **period["form"],
            "team_leader_id": str(team_leader_id),
        },
        "years": years,
        "team_leaders": list_team_leaders(organization_id),
        "download_basename": (
            f"team-report-{leader['id']}-"
            f"{period['form'].get('month_year') or period['year']}"
        ),
        "labels": {
            "title": _t("team_report_title", language),
            "subtitle": _t("team_report_subtitle", language),
            "team_production": _t("team_metric_team_production", language),
            "juniors_production": _t(
                "team_metric_juniors_production", language
            ),
            "leader_own": _t("team_metric_leader_own", language),
            "juniors_income": _t("team_metric_juniors_income", language),
            "combined": _t("team_metric_combined", language),
            "operations": _t("team_metric_operations", language),
        },
        "language": language,
    }


def build_agent_profile_view(
    organization_id,
    agent_id,
    *,
    include_wallet=True,
    include_team_stats=True,
):
    agent = get_agent_record(agent_id, organization_id)
    if agent is None:
        return None

    juniors = list_team_juniors(agent_id, organization_id)
    is_leader = len(juniors) > 0

    junior_rows = []
    if include_team_stats and is_leader:
        junior_rows = build_junior_team_rows(
            organization_id,
            agent_id,
        )

    own_stats = _agent_approved_stats(organization_id, agent_id)

    wallet_totals = None
    wallet_movements = []
    if include_wallet:
        wallet_totals = sum_wallet_by_type(organization_id, agent_id)
        wallet_movements = list_wallet_movements_for_agent(
            organization_id,
            agent_id,
            limit=40,
        )

    return {
        "agent": agent,
        "is_team_leader": is_leader,
        "juniors": juniors,
        "junior_rows": junior_rows,
        "own_stats": own_stats,
        "totals": wallet_totals,
        "movements": wallet_movements,
        "team_leader": (
            {
                "id": agent["team_leader_agent_id"],
                "name": agent.get("team_leader_name"),
                "type": agent.get("team_leader_type"),
            }
            if agent.get("team_leader_agent_id")
            else None
        ),
    }


def build_dashboard_team_block(organization_id, team_leader_id, language="es"):
    today = _today()
    date_from_display, date_to_display = _month_bounds_display(
        today.year,
        today.month,
    )
    date_from = date_to_sortable(date_from_display)
    date_to = date_to_sortable(date_to_display)

    report = load_team_report(
        organization_id,
        team_leader_id,
        {
            "period_mode": PERIOD_MODE_MONTH,
            "month_year": f"{today.year:04d}-{today.month:02d}",
        },
        language=language,
    )

    if report is None:
        return None

    # Pending across leader + juniors
    pending = count_operations_by_status(
        organization_id,
        agent_id=team_leader_id,
    ).get(STATUS_PENDING, 0)

    for row in report["junior_rows"]:
        pending += count_operations_by_status(
            organization_id,
            agent_id=row["agent"]["id"],
        ).get(STATUS_PENDING, 0)

    return {
        "leader_id": team_leader_id,
        "juniors_active": report["metrics"]["juniors_count"],
        "month_production": report["metrics"]["team_production"],
        "team_income": report["metrics"]["juniors_income_to_leader"],
        "pending_count": pending,
        "labels": {
            "title": _t("dashboard_my_team_title", language),
            "production": _t("dashboard_my_team_production", language),
            "juniors": _t("dashboard_my_team_juniors", language),
            "income": _t("dashboard_my_team_income", language),
            "pending": _t("dashboard_my_team_pending", language),
            "open_report": _t("dashboard_my_team_open_report", language),
        },
    }
