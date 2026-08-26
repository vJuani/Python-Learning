"""
Executive dashboard view-model built on organization reports.
"""

from __future__ import annotations

import calendar
from datetime import date

from modules.database.operations_repository import (
    count_operations_by_status,
)
from modules.database.pending_approvals_repository import (
    count_pending_approvals,
)
from modules.database.properties_repository import (
    STATUS_APPROVED as PROPERTY_STATUS_APPROVED,
    count_properties,
)
from modules.i18n import translate
from modules.organization_reports import load_organization_report
from modules.validators import (
    date_display_to_iso,
    date_to_sortable,
    parse_optional_date,
)


PERIOD_THIS_MONTH = "this_month"
PERIOD_PREVIOUS_MONTH = "previous_month"
PERIOD_LAST_3_MONTHS = "last_3_months"
PERIOD_THIS_YEAR = "this_year"
PERIOD_CUSTOM = "custom"

PERIOD_OPTIONS = (
    PERIOD_THIS_MONTH,
    PERIOD_PREVIOUS_MONTH,
    PERIOD_LAST_3_MONTHS,
    PERIOD_THIS_YEAR,
    PERIOD_CUSTOM,
)


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _today():
    return date.today()


def _display_date(year, month, day):
    return f"{day:02d}/{month:02d}/{year:04d}"


def _month_start(year, month):
    return _display_date(year, month, 1)


def _month_end(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return _display_date(year, month, last_day)


def resolve_dashboard_period(raw_filters, today=None):
    """
    Translate dashboard period selector into report date filters.

    Returns (errors, period, date_from_display, date_to_display).
    """
    errors = []
    today = today or _today()
    period = (raw_filters.get("period") or PERIOD_THIS_MONTH).strip()

    if period not in PERIOD_OPTIONS:
        errors.append("Invalid dashboard period.")
        period = PERIOD_THIS_MONTH

    date_from = None
    date_to = None

    if period == PERIOD_THIS_MONTH:
        date_from = _month_start(today.year, today.month)
        date_to = _month_end(today.year, today.month)
    elif period == PERIOD_PREVIOUS_MONTH:
        if today.month == 1:
            year, month = today.year - 1, 12
        else:
            year, month = today.year, today.month - 1

        date_from = _month_start(year, month)
        date_to = _month_end(year, month)
    elif period == PERIOD_LAST_3_MONTHS:
        month = today.month - 2
        year = today.year

        while month <= 0:
            month += 12
            year -= 1

        date_from = _month_start(year, month)
        date_to = _month_end(today.year, today.month)
    elif period == PERIOD_THIS_YEAR:
        date_from = _display_date(today.year, 1, 1)
        date_to = _month_end(today.year, today.month)
    else:
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
            date_from = parsed_from

        if to_error:
            errors.append(to_error)
        else:
            date_to = parsed_to

        if (
            date_from
            and date_to
            and date_to_sortable(date_from) > date_to_sortable(date_to)
        ):
            errors.append(
                "Start date cannot be after end date."
            )

        if not date_from and not date_to and not errors:
            # Custom with empty dates falls back to this month.
            date_from = _month_start(today.year, today.month)
            date_to = _month_end(today.year, today.month)

    return errors, period, date_from or "", date_to or ""


def _build_invoiced_chart(metrics, labels):
    return {
        "labels": [
            labels["invoiced"],
            labels["not_invoiced"],
        ],
        "values": [
            metrics.get("invoiced_count", 0),
            metrics.get("not_invoiced_count", 0),
        ],
        "title": labels["chart_invoiced"],
    }


def _has_chart_data(payload):
    values = payload.get("values") or []
    return any(float(value or 0) > 0 for value in values)


def load_organization_dashboard(
    organization_id,
    raw_filters,
    *,
    language="es",
    scoped_agent_id=None,
    role="admin",
    can_write=False,
    can_manage_approvals=False,
    can_create_operations=False,
):
    language = language if language in ("es", "en") else "es"
    period_errors, period, date_from, date_to = (
        resolve_dashboard_period(raw_filters)
    )

    report_filters = {
        "view": "general",
        "date_from": date_from,
        "date_to": date_to,
    }
    report = load_organization_report(
        organization_id,
        report_filters,
        language=language,
        scoped_agent_id=scoped_agent_id,
        ranking_limit=5,
    )

    errors = list(period_errors) + list(report.get("errors") or [])
    metrics = report["metrics"]
    is_agent_role = role == "agent"
    is_guest_role = role == "guest"
    show_ranking = not is_agent_role
    show_write_actions = bool(can_write) and not is_guest_role

    labels = {
        "title": _t("dashboard_title", language),
        "welcome": _t("dashboard_welcome", language),
        "welcome_to": _t("dashboard_welcome_to", language),
        "tools_title": _t("dashboard_tools_title", language),
        "summary_title": _t("dashboard_summary_title", language),
        "subtitle": _t("dashboard_executive_subtitle", language),
        "official_note": _t("dashboard_official_note", language),
        "period_label": _t("dashboard_period", language),
        "period_this_month": _t("dashboard_period_this_month", language),
        "period_previous_month": _t(
            "dashboard_period_previous_month", language
        ),
        "period_last_3_months": _t(
            "dashboard_period_last_3_months", language
        ),
        "period_this_year": _t("dashboard_period_this_year", language),
        "period_custom": _t("dashboard_period_custom", language),
        "apply": _t("dashboard_apply", language),
        "date_from": _t("reports_filter_date_from", language),
        "date_to": _t("reports_filter_date_to", language),
        "approved_ops": _t("dashboard_kpi_approved_ops", language),
        "volume": _t("reports_metric_volume", language),
        "commission": _t("reports_metric_commission", language),
        "agent_payments": _t(
            "reports_metric_agent_payments", language
        ),
        "office_net": _t("reports_metric_office_net", language),
        "invoiced": _t("reports_metric_invoiced", language),
        "not_invoiced": _t("reports_metric_not_invoiced", language),
        "properties_active": _t(
            "dashboard_kpi_properties_active", language
        ),
        "pending_approvals": _t(
            "dashboard_kpi_pending_approvals", language
        ),
        "charts_title": _t("dashboard_charts_title", language),
        "charts_unavailable": _t(
            "reports_charts_unavailable", language
        ),
        "chart_commissions": _t(
            "reports_chart_commissions_month", language
        ),
        "chart_operations": _t(
            "reports_chart_operations_month", language
        ),
        "chart_ranking": _t(
            "reports_chart_agent_ranking", language
        ),
        "chart_invoiced": _t(
            "dashboard_chart_invoiced", language
        ),
        "quick_title": _t("dashboard_quick_title", language),
        "quick_new_operation": _t(
            "dashboard_quick_new_operation", language
        ),
        "quick_new_property": _t(
            "dashboard_quick_new_property", language
        ),
        "quick_approvals": _t(
            "dashboard_quick_approvals", language
        ),
        "quick_uninvoiced": _t(
            "dashboard_quick_uninvoiced", language
        ),
        "quick_monthly_report": _t(
            "dashboard_quick_monthly_report", language
        ),
        "quick_vat": _t("dashboard_quick_vat", language),
        "ranking_title": _t("reports_ranking_title", language),
        "workflow_title": _t("workflow_section", language),
        "empty_period_title": _t(
            "dashboard_empty_period_title", language
        ),
        "empty_period_text": _t(
            "dashboard_empty_period_text", language
        ),
        "empty_ranking_title": _t("no_ranking_title", language),
        "empty_ranking_text": _t("no_ranking_text", language),
        "col_rank": _t("rank", language),
        "col_agent": _t("agent", language),
        "col_commission": _t("total_commission", language),
        "status_draft": _t("status_draft", language),
        "status_pending": _t("status_pending", language),
        "status_approved": _t("status_approved", language),
        "status_rejected": _t("status_rejected", language),
    }

    charts = {
        "commissions_by_month": {
            "labels": [
                item["month"] for item in report["monthly_series"]
            ],
            "values": [
                item["total_commission"]
                for item in report["monthly_series"]
            ],
            "title": labels["chart_commissions"],
        },
        "operations_by_month": {
            "labels": [
                item["month"] for item in report["monthly_series"]
            ],
            "values": [
                item["operations_count"]
                for item in report["monthly_series"]
            ],
            "title": labels["chart_operations"],
        },
        "invoiced_split": _build_invoiced_chart(metrics, labels),
    }

    if show_ranking:
        charts["agent_ranking"] = {
            "labels": [
                item["agent_name"] for item in report["agent_ranking"]
            ],
            "values": [
                item["total_commission"]
                for item in report["agent_ranking"]
            ],
            "title": labels["chart_ranking"],
        }

    chart_availability = {
        key: _has_chart_data(payload)
        for key, payload in charts.items()
    }
    has_period_data = metrics.get("operations_count", 0) > 0

    property_count = count_properties(
        organization_id,
        status=PROPERTY_STATUS_APPROVED,
        agent_id=scoped_agent_id,
    )

    if can_manage_approvals:
        pending_approvals = count_pending_approvals(organization_id)
    else:
        pending_approvals = 0

    workflow_counts = count_operations_by_status(
        organization_id,
        agent_id=scoped_agent_id,
    )

    if is_agent_role:
        pending_approvals = workflow_counts.get("pending", 0)

    period_label_map = {
        PERIOD_THIS_MONTH: labels["period_this_month"],
        PERIOD_PREVIOUS_MONTH: labels["period_previous_month"],
        PERIOD_LAST_3_MONTHS: labels["period_last_3_months"],
        PERIOD_THIS_YEAR: labels["period_this_year"],
        PERIOD_CUSTOM: labels["period_custom"],
    }

    quick_links = []

    if show_write_actions and can_create_operations:
        quick_links.append(
            {
                "key": "new_operation",
                "label": labels["quick_new_operation"],
                "endpoint": "operations_new",
                "variant": "primary",
            }
        )

    if show_write_actions and can_manage_approvals:
        quick_links.append(
            {
                "key": "new_property",
                "label": labels["quick_new_property"],
                "endpoint": "properties_new",
                "variant": "secondary",
            }
        )
        quick_links.append(
            {
                "key": "approvals",
                "label": labels["quick_approvals"],
                "endpoint": "approvals_list",
                "variant": "secondary",
                "badge": pending_approvals,
            }
        )

    quick_links.extend(
        [
            {
                "key": "uninvoiced",
                "label": labels["quick_uninvoiced"],
                "endpoint": "reports_index",
                "variant": "secondary",
                "query": {"view": "uninvoiced"},
            },
            {
                "key": "monthly",
                "label": labels["quick_monthly_report"],
                "endpoint": "reports_index",
                "variant": "secondary",
                "query": {"view": "monthly"},
            },
            {
                "key": "vat",
                "label": labels["quick_vat"],
                "endpoint": "vat_calculator",
                "variant": "ghost",
            },
        ]
    )

    return {
        "errors": errors,
        "period": period,
        "period_label": period_label_map.get(period, period),
        "form": {
            "period": period,
            "date_from": (
                date_display_to_iso(date_from)
                if period == PERIOD_CUSTOM else ""
            ),
            "date_to": (
                date_display_to_iso(date_to)
                if period == PERIOD_CUSTOM else ""
            ),
        },
        "date_from": date_from,
        "date_to": date_to,
        "metrics": {
            "approved_operations": metrics.get("operations_count", 0),
            "volume_usd": metrics.get("volume_usd", 0.0),
            "total_commission": metrics.get("total_commission", 0.0),
            "agent_payments": metrics.get("agent_payments", 0.0),
            "office_net": metrics.get("office_net", 0.0),
            "invoiced_count": metrics.get("invoiced_count", 0),
            "not_invoiced_count": metrics.get(
                "not_invoiced_count", 0
            ),
            "properties_active": property_count,
            "pending_approvals": pending_approvals,
        },
        "charts": charts,
        "chart_availability": chart_availability,
        "show_charts": True,
        "has_period_data": has_period_data,
        "agent_ranking": (
            report["agent_ranking"] if show_ranking else []
        ),
        "show_ranking": show_ranking,
        "show_workflow": is_agent_role,
        "workflow_counts": workflow_counts,
        "quick_links": quick_links,
        "show_write_actions": show_write_actions,
        "is_guest": is_guest_role,
        "welcome_name": "",
        "labels": labels,
        "language": language,
        "report": report,
    }


def empty_organization_dashboard(language="es"):
    language = language if language in ("es", "en") else "es"
    today = _today()
    date_from = _month_start(today.year, today.month)
    date_to = _month_end(today.year, today.month)
    empty_metrics = {
        "approved_operations": 0,
        "volume_usd": 0.0,
        "total_commission": 0.0,
        "agent_payments": 0.0,
        "office_net": 0.0,
        "invoiced_count": 0,
        "not_invoiced_count": 0,
        "properties_active": 0,
        "pending_approvals": 0,
    }

    return {
        "errors": [],
        "period": PERIOD_THIS_MONTH,
        "period_label": _t(
            "dashboard_period_this_month", language
        ),
        "form": {
            "period": PERIOD_THIS_MONTH,
            "date_from": "",
            "date_to": "",
        },
        "date_from": date_from,
        "date_to": date_to,
        "metrics": empty_metrics,
        "charts": {
            "commissions_by_month": {
                "labels": [],
                "values": [],
                "title": "",
            },
            "operations_by_month": {
                "labels": [],
                "values": [],
                "title": "",
            },
            "invoiced_split": {
                "labels": [],
                "values": [0, 0],
                "title": "",
            },
        },
        "chart_availability": {
            "commissions_by_month": False,
            "operations_by_month": False,
            "invoiced_split": False,
        },
        "show_charts": False,
        "has_period_data": False,
        "agent_ranking": [],
        "show_ranking": False,
        "show_workflow": True,
        "workflow_counts": {
            "draft": 0,
            "pending": 0,
            "approved": 0,
            "rejected": 0,
        },
        "report": {
            "monthly_series": [],
            "operations": [],
            "agent_ranking": [],
        },
        "quick_links": [
            {
                "key": "uninvoiced",
                "label": _t(
                    "dashboard_quick_uninvoiced", language
                ),
                "endpoint": "reports_index",
                "variant": "secondary",
                "query": {"view": "uninvoiced"},
            },
            {
                "key": "monthly",
                "label": _t(
                    "dashboard_quick_monthly_report", language
                ),
                "endpoint": "reports_index",
                "variant": "secondary",
                "query": {"view": "monthly"},
            },
            {
                "key": "vat",
                "label": _t("dashboard_quick_vat", language),
                "endpoint": "vat_calculator",
                "variant": "ghost",
            },
        ],
        "show_write_actions": False,
        "is_guest": False,
        "welcome_name": "",
        "labels": {
            "title": _t("dashboard_title", language),
            "welcome": _t("dashboard_welcome", language),
            "welcome_to": _t("dashboard_welcome_to", language),
            "tools_title": _t("dashboard_tools_title", language),
            "summary_title": _t("dashboard_summary_title", language),
            "subtitle": _t(
                "dashboard_executive_subtitle", language
            ),
            "official_note": _t(
                "dashboard_official_note", language
            ),
            "period_label": _t("dashboard_period", language),
            "period_this_month": _t(
                "dashboard_period_this_month", language
            ),
            "period_previous_month": _t(
                "dashboard_period_previous_month", language
            ),
            "period_last_3_months": _t(
                "dashboard_period_last_3_months", language
            ),
            "period_this_year": _t(
                "dashboard_period_this_year", language
            ),
            "period_custom": _t(
                "dashboard_period_custom", language
            ),
            "apply": _t("dashboard_apply", language),
            "date_from": _t("reports_filter_date_from", language),
            "date_to": _t("reports_filter_date_to", language),
            "approved_ops": _t(
                "dashboard_kpi_approved_ops", language
            ),
            "volume": _t("reports_metric_volume", language),
            "commission": _t(
                "reports_metric_commission", language
            ),
            "agent_payments": _t(
                "reports_metric_agent_payments", language
            ),
            "office_net": _t(
                "reports_metric_office_net", language
            ),
            "invoiced": _t("reports_metric_invoiced", language),
            "not_invoiced": _t(
                "reports_metric_not_invoiced", language
            ),
            "properties_active": _t(
                "dashboard_kpi_properties_active", language
            ),
            "pending_approvals": _t(
                "dashboard_kpi_pending_approvals", language
            ),
            "charts_title": _t(
                "dashboard_charts_title", language
            ),
            "charts_unavailable": _t(
                "reports_charts_unavailable", language
            ),
            "chart_commissions": _t(
                "reports_chart_commissions_month", language
            ),
            "chart_operations": _t(
                "reports_chart_operations_month", language
            ),
            "chart_ranking": _t(
                "reports_chart_agent_ranking", language
            ),
            "chart_invoiced": _t(
                "dashboard_chart_invoiced", language
            ),
            "quick_title": _t("dashboard_quick_title", language),
            "quick_new_operation": _t(
                "dashboard_quick_new_operation", language
            ),
            "quick_new_property": _t(
                "dashboard_quick_new_property", language
            ),
            "quick_approvals": _t(
                "dashboard_quick_approvals", language
            ),
            "quick_uninvoiced": _t(
                "dashboard_quick_uninvoiced", language
            ),
            "quick_monthly_report": _t(
                "dashboard_quick_monthly_report", language
            ),
            "quick_vat": _t("dashboard_quick_vat", language),
            "ranking_title": _t(
                "reports_ranking_title", language
            ),
            "workflow_title": _t("workflow_section", language),
            "empty_period_title": _t(
                "dashboard_empty_period_title", language
            ),
            "empty_period_text": _t(
                "dashboard_empty_period_text", language
            ),
            "empty_ranking_title": _t(
                "no_ranking_title", language
            ),
            "empty_ranking_text": _t(
                "no_ranking_text", language
            ),
            "col_rank": _t("rank", language),
            "col_agent": _t("agent", language),
            "col_commission": _t("total_commission", language),
            "status_draft": _t("status_draft", language),
            "status_pending": _t("status_pending", language),
            "status_approved": _t("status_approved", language),
            "status_rejected": _t("status_rejected", language),
        },
        "language": language,
        "report": None,
    }
