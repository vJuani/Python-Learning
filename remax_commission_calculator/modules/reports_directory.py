"""
Reports dashboard view-model (reports page mockup).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from modules.dashboard_home import _stage_for_operation
from modules.database.operations_repository import filter_operations
from modules.database.properties_repository import STATUS_APPROVED, get_properties
from modules.database.reports_repository import aggregate_report_metrics
from modules.database.tenant import require_organization_id
from modules.i18n import translate
from modules.organization_reports import parse_report_filters


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


_MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _pct_change(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    if previous <= 0:
        if current > 0:
            return 100.0
        return None
    return round(((current - previous) / previous) * 100, 1)


def _parse_operation_date(value):
    if not value:
        return None

    raw = str(value).strip()
    if not value:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    return None


def _iso_to_date(value):
    if not value:
        return None

    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError:
        return None


def _previous_period_bounds(date_from, date_to):
    start = _iso_to_date(date_from)
    end = _iso_to_date(date_to)
    if start is None or end is None:
        return None, None

    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start.strftime("%Y%m%d"), prev_end.strftime("%Y%m%d")


def _format_period_label(date_from, date_to, language):
    start = _iso_to_date(date_from)
    end = _iso_to_date(date_to)
    if start is None or end is None:
        return _t("reports_page_period_all", language)

    months = _MONTHS_ES if language == "es" else _MONTHS_EN

    if start.year == end.year and start.month == end.month:
        if language == "es":
            return (
                f"{start.day} – {end.day} de "
                f"{months[start.month - 1]} de {start.year}"
            )
        return (
            f"{months[start.month - 1]} {start.day} – "
            f"{end.day}, {start.year}"
        )

    if language == "es":
        return (
            f"{start.day} {months[start.month - 1]} – "
            f"{end.day} {months[end.month - 1]} {end.year}"
        )

    return (
        f"{months[start.month - 1]} {start.day} – "
        f"{months[end.month - 1]} {end.day}, {end.year}"
    )


def _kpi_card(key, label, value, display, change_pct):
    direction = None
    if change_pct is not None:
        if change_pct > 0:
            direction = "up"
        elif change_pct < 0:
            direction = "down"
        else:
            direction = "flat"

    return {
        "key": key,
        "label": label,
        "value": value,
        "display": display,
        "change_pct": change_pct,
        "change_direction": direction,
    }


def _weekly_operations(operations, language):
    buckets = defaultdict(int)
    labels = {}

    for operation in operations:
        parsed = _parse_operation_date(operation.get("date"))
        if parsed is None:
            continue

        year, week, _ = parsed.isocalendar()
        key = (year, week)
        buckets[key] += 1

    ordered = sorted(buckets.keys())
    if not ordered:
        return {"labels": [], "values": []}

    chart_labels = []
    chart_values = []

    for year, week in ordered[-4:]:
        week_start = date.fromisocalendar(year, week, 1)
        week_end = week_start + timedelta(days=6)
        if language == "es":
            label = (
                f"{week_start.day}/{week_start.month} – "
                f"{week_end.day}/{week_end.month}"
            )
        else:
            label = (
                f"{week_start.month}/{week_start.day} – "
                f"{week_end.month}/{week_end.day}"
            )
        chart_labels.append(label)
        chart_values.append(buckets[(year, week)])

    return {"labels": chart_labels, "values": chart_values}


def _stage_distribution(operations, language):
    counts = defaultdict(int)

    for operation in operations:
        stage = _stage_for_operation(operation, language)
        counts[stage["tone"]] += 1

    total = sum(counts.values()) or 1
    tone_labels = {
        "reservation": _t("dashboard_stage_reservation", language),
        "proposal": _t("dashboard_stage_proposal", language),
        "negotiation": _t("dashboard_stage_negotiation", language),
        "closing": _t("dashboard_stage_closing", language),
        "rejected": _t("status_rejected", language),
        "neutral": _t("reports_page_stage_other", language),
    }
    order = (
        "reservation",
        "proposal",
        "negotiation",
        "closing",
        "rejected",
        "neutral",
    )

    items = []
    for tone in order:
        count = counts.get(tone, 0)
        if count <= 0:
            continue
        items.append(
            {
                "tone": tone,
                "label": tone_labels.get(tone, tone),
                "count": count,
                "pct": round((count / total) * 100),
            }
        )

    return items


def _status_distribution(operations, language):
    buckets = {
        "active": 0,
        "progress": 0,
        "pending": 0,
        "cancelled": 0,
        "closed": 0,
    }

    for operation in operations:
        status = operation.get("status") or "approved"
        invoiced = (operation.get("was_invoiced") or "no") == "yes"

        if status == "rejected":
            buckets["cancelled"] += 1
        elif status == "draft":
            buckets["pending"] += 1
        elif status == "pending":
            buckets["progress"] += 1
        elif status == "approved" and invoiced:
            buckets["closed"] += 1
        elif status == "approved":
            buckets["active"] += 1

    labels = {
        "active": _t("reports_page_status_active", language),
        "progress": _t("reports_page_status_in_progress", language),
        "pending": _t("reports_page_status_pending", language),
        "cancelled": _t("reports_page_status_cancelled", language),
        "closed": _t("reports_page_status_closed", language),
    }
    total = sum(buckets.values()) or 1
    items = []

    for tone, count in buckets.items():
        if count <= 0:
            continue
        items.append(
            {
                "tone": tone,
                "label": labels[tone],
                "count": count,
                "pct": round((count / total) * 100),
            }
        )

    return items


def _count_active_properties(organization_id):
    return len(
        [
            item
            for item in get_properties(organization_id)
            if (item.get("status") or STATUS_APPROVED) == STATUS_APPROVED
        ]
    )


def _count_new_properties(organization_id, date_from, date_to):
    start = _iso_to_date(date_from)
    end = _iso_to_date(date_to)
    if start is None or end is None:
        return 0

    count = 0
    for item in get_properties(organization_id):
        submitted = _parse_operation_date(item.get("submitted_at"))
        if submitted is None:
            continue
        if start <= submitted <= end:
            count += 1

    return count


def _cash_flow_series(organization_id, date_from, date_to):
    from modules.database.cash_treasury_repository import (
        sum_movements_by_type,
    )
    from modules.cash_treasury import (
        TYPE_EXPENSE,
        TYPE_INCOME,
        TYPE_OPENING,
    )

    start = _iso_to_date(date_from)
    end = _iso_to_date(date_to)
    if start is None or end is None:
        return None

    labels = []
    inflow = []
    outflow = []
    net = []
    cumulative_in = 0.0
    cumulative_out = 0.0

    cursor_day = start
    while cursor_day <= end:
        iso = cursor_day.isoformat()
        day_income = sum_movements_by_type(
            organization_id,
            currency="ARS",
            movement_type=TYPE_INCOME,
            date_from=iso,
            date_to=iso,
        ) + sum_movements_by_type(
            organization_id,
            currency="ARS",
            movement_type=TYPE_OPENING,
            date_from=iso,
            date_to=iso,
        )
        day_expense = sum_movements_by_type(
            organization_id,
            currency="ARS",
            movement_type=TYPE_EXPENSE,
            date_from=iso,
            date_to=iso,
        )
        cumulative_in += day_income
        cumulative_out += day_expense
        labels.append(cursor_day.strftime("%d/%m"))
        inflow.append(round(cumulative_in, 2))
        outflow.append(round(cumulative_out, 2))
        net.append(round(cumulative_in - cumulative_out, 2))
        cursor_day += timedelta(days=1)

    return {
        "labels": labels,
        "inflow": inflow,
        "outflow": outflow,
        "net": net,
        "totals": {
            "inflow": cumulative_in,
            "outflow": cumulative_out,
            "net": cumulative_in - cumulative_out,
        },
    }


def _build_insights(metrics, prev_metrics, top_agent, language):
    insights = []
    volume_change = _pct_change(
        metrics.get("volume_usd"),
        prev_metrics.get("volume_usd"),
    )

    if volume_change is not None:
        insights.append(
            {
                "tone": "growth",
                "title": _t("reports_page_insight_growth_title", language),
                "text": _t(
                    "reports_page_insight_growth_text",
                    language,
                    pct=abs(volume_change),
                ),
            }
        )

    if top_agent:
        insights.append(
            {
                "tone": "star",
                "title": _t("reports_page_insight_star_title", language),
                "text": _t(
                    "reports_page_insight_star_text",
                    language,
                    agent=top_agent["name"],
                ),
            }
        )

    pending = int(metrics.get("pending_count") or 0)
    if pending > 0:
        insights.append(
            {
                "tone": "warning",
                "title": _t("reports_page_insight_warning_title", language),
                "text": _t(
                    "reports_page_insight_warning_text",
                    language,
                    count=pending,
                ),
            }
        )

    return insights


def build_reports_panel(
    organization_id,
    report,
    raw_filters,
    *,
    language="es",
    scoped_agent_id=None,
    include_cash_flow=False,
):
    language = language if language in ("es", "en") else "es"
    organization_id = require_organization_id(organization_id)

    _, parsed = parse_report_filters(
        raw_filters,
        scoped_agent_id=scoped_agent_id,
    )
    query_filters = dict(parsed["query_filters"])
    query_filters["organization_id"] = organization_id

    metrics = report.get("metrics") or {}
    prev_from, prev_to = _previous_period_bounds(
        query_filters.get("date_from"),
        query_filters.get("date_to"),
    )
    prev_metrics = {}
    if prev_from and prev_to:
        prev_filters = dict(query_filters)
        prev_filters["date_from"] = prev_from
        prev_filters["date_to"] = prev_to
        prev_metrics = aggregate_report_metrics(prev_filters)

    operations_count = int(metrics.get("operations_count") or 0)
    volume = float(metrics.get("volume_usd") or 0)
    commission = float(metrics.get("total_commission") or 0)
    avg_ticket = volume / operations_count if operations_count else 0.0
    prev_ops = int(prev_metrics.get("operations_count") or 0)
    prev_volume = float(prev_metrics.get("volume_usd") or 0)
    prev_commission = float(prev_metrics.get("total_commission") or 0)
    prev_ticket = prev_volume / prev_ops if prev_ops else 0.0

    active_properties = _count_active_properties(organization_id)
    new_properties = _count_new_properties(
        organization_id,
        query_filters.get("date_from"),
        query_filters.get("date_to"),
    )
    prev_new_properties = _count_new_properties(
        organization_id,
        prev_from,
        prev_to,
    )

    kpis = [
        _kpi_card(
            "operations",
            _t("reports_page_kpi_operations", language),
            operations_count,
            str(operations_count),
            _pct_change(operations_count, prev_ops),
        ),
        _kpi_card(
            "volume",
            _t("reports_page_kpi_volume", language),
            volume,
            None,
            _pct_change(volume, prev_volume),
        ),
        _kpi_card(
            "commission",
            _t("reports_page_kpi_commission", language),
            commission,
            None,
            _pct_change(commission, prev_commission),
        ),
        _kpi_card(
            "ticket",
            _t("reports_page_kpi_ticket", language),
            avg_ticket,
            None,
            _pct_change(avg_ticket, prev_ticket),
        ),
        _kpi_card(
            "clients",
            _t("reports_page_kpi_clients", language),
            new_properties,
            str(new_properties),
            _pct_change(new_properties, prev_new_properties),
        ),
        _kpi_card(
            "properties",
            _t("reports_page_kpi_properties", language),
            active_properties,
            str(active_properties),
            None,
        ),
    ]

    raw_operations = filter_operations(
        organization_id,
        agent_id=query_filters.get("agent_id"),
        date_from=query_filters.get("date_from"),
        date_to=query_filters.get("date_to"),
        was_invoiced=query_filters.get("was_invoiced"),
        jurisdiction=query_filters.get("jurisdiction"),
        currency=query_filters.get("currency"),
        agent_type=query_filters.get("agent_type"),
        status=query_filters.get("status"),
    )

    monthly_series = report.get("monthly_series") or []
    commission_labels = [item["month"] for item in monthly_series]
    commission_values = [
        item["total_commission"] for item in monthly_series
    ]

    evolution_current = [
        item["operations_count"] for item in monthly_series
    ]
    evolution_previous = []
    if prev_from and prev_to:
        prev_filters = dict(query_filters)
        prev_filters["date_from"] = prev_from
        prev_filters["date_to"] = prev_to
        prev_series = aggregate_report_metrics(prev_filters)
        evolution_previous = [int(prev_series.get("operations_count") or 0)]

    top_agents = []
    for item in (report.get("agent_ranking") or [])[:5]:
        name = item.get("agent_name") or "—"
        initials = "".join(part[0] for part in name.split()[:2]).upper()
        top_agents.append(
            {
                "name": name,
                "operations": item.get("operations_count", 0),
                "volume": item.get("volume_usd", 0.0),
                "commission": item.get("total_commission", 0.0),
                "initials": initials or "?",
            }
        )

    cash_flow = None
    if include_cash_flow:
        cash_flow = _cash_flow_series(
            organization_id,
            query_filters.get("date_from"),
            query_filters.get("date_to"),
        )

    return {
        "period_label": _format_period_label(
            query_filters.get("date_from"),
            query_filters.get("date_to"),
            language,
        ),
        "compare_label": _t("reports_page_compare_prev", language),
        "kpis": kpis,
        "weekly_operations": _weekly_operations(raw_operations, language),
        "operations_evolution": {
            "labels": commission_labels,
            "current": evolution_current,
            "previous": evolution_previous,
        },
        "stage_distribution": _stage_distribution(
            raw_operations,
            language,
        ),
        "status_distribution": _status_distribution(
            raw_operations,
            language,
        ),
        "commission_trend": {
            "labels": commission_labels,
            "values": commission_values,
            "total": commission,
            "change_pct": _pct_change(commission, prev_commission),
        },
        "cash_flow": cash_flow,
        "top_agents": top_agents,
        "insights": _build_insights(
            metrics,
            prev_metrics,
            top_agents[0] if top_agents else None,
            language,
        ),
        "charts": report.get("charts") or {},
        "show_charts": report.get("show_charts", False),
        "operations_total": operations_count,
        "view": report.get("view", "general"),
        "form": report.get("form") or {},
        "errors": report.get("errors") or [],
        "labels": report.get("labels") or {},
        "years": report.get("years") or [],
        "agents": report.get("agents") or [],
        "active_filters": report.get("active_filters") or [],
    }
