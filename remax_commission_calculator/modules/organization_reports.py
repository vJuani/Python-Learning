"""
Organization-wide reports view-model and filter presets.
"""

from __future__ import annotations

import calendar
from datetime import datetime

from modules.config import BASE_DIR
from modules.database.agents_repository import get_agents
from modules.database.operations_repository import filter_operations
from modules.database.organization_settings_repository import (
    get_organization_settings,
)
from modules.database.reports_repository import (
    aggregate_agent_ranking,
    aggregate_monthly_series,
    aggregate_report_metrics,
    aggregate_status_counts,
)
from modules.formatting import CURRENCIES
from modules.i18n import translate
from modules.operation_summary import DEFAULT_BRAND_LOGO
from modules.validators import (
    AGENT_TYPES,
    JURISDICTIONS,
    date_display_to_iso,
    date_to_sortable,
    parse_optional_date,
)
from modules.workflow import is_valid_status


REPORT_VIEWS = (
    "general",
    "monthly",
    "invoiced",
    "uninvoiced",
)

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


def _month_name(month, language):
    if month is None or month < 1 or month > 12:
        return ""

    return _t(f"month_{month:02d}", language)


def month_options(language):
    return [
        {"value": month, "label": _month_name(month, language)}
        for month in range(1, 13)
    ]


def _year_bounds(year):
    return f"{year:04d}0101", f"{year:04d}1231"


def _resolve_logo_path(logo_path):
    if not logo_path:
        return None

    candidate = (BASE_DIR / "static" / logo_path).resolve()

    try:
        candidate.relative_to((BASE_DIR / "static").resolve())
    except ValueError:
        return None

    if candidate.is_file():
        return candidate

    return None


def _brand_logo_path(organization_logo_path):
    org_logo = _resolve_logo_path(organization_logo_path)

    if org_logo is not None:
        return org_logo

    if DEFAULT_BRAND_LOGO.is_file():
        return DEFAULT_BRAND_LOGO

    return None


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _as_int(value):
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _month_bounds(year, month):
    last_day = calendar.monthrange(year, month)[1]
    date_from = f"{year:04d}{month:02d}01"
    date_to = f"{year:04d}{month:02d}{last_day:02d}"
    return date_from, date_to


def _current_year_month():
    now = datetime.utcnow()
    return now.year, now.month


def parse_report_filters(raw_filters, scoped_agent_id=None):
    """
    Parse shared report filters and apply view presets.

    Returns (errors, parsed) where parsed includes:
    - view, form values, and SQL-ready filter kwargs
    """
    errors = []
    view = (raw_filters.get("view") or "general").strip()

    if view not in REPORT_VIEWS:
        errors.append("Invalid report view.")
        view = "general"

    year = _as_int(raw_filters.get("year"))
    month = _as_int(raw_filters.get("month"))
    month_year_raw = (
        raw_filters.get("month_year") or ""
    ).strip()

    if month_year_raw:
        try:
            parsed_month_year = datetime.strptime(
                month_year_raw,
                "%Y-%m",
            )
            year = parsed_month_year.year
            month = parsed_month_year.month
        except ValueError:
            errors.append("Invalid month.")

    agent_id = _as_int(raw_filters.get("agent_id"))

    if scoped_agent_id is not None:
        agent_id = scoped_agent_id

    currency = (raw_filters.get("currency") or "").strip()
    agent_type = (raw_filters.get("agent_type") or "").strip()
    jurisdiction = (raw_filters.get("jurisdiction") or "").strip()
    was_invoiced = (raw_filters.get("was_invoiced") or "").strip()
    status = (raw_filters.get("status") or "").strip()
    status_explicit = status != ""
    period_mode = (raw_filters.get("period_mode") or "").strip()

    date_from_raw = raw_filters.get("date_from", "")
    date_to_raw = raw_filters.get("date_to", "")

    date_from = None
    date_to = None
    display_from = ""
    display_to = ""

    if currency != "":
        if currency not in CURRENCIES:
            errors.append("Invalid currency.")
            currency = ""

    if agent_type != "":
        if agent_type not in AGENT_TYPES:
            errors.append("Invalid agent type.")
            agent_type = ""

    if jurisdiction != "":
        if jurisdiction not in JURISDICTIONS:
            errors.append("Invalid jurisdiction.")
            jurisdiction = ""

    if was_invoiced != "":
        if was_invoiced not in ("yes", "no"):
            errors.append("Invalid invoiced option.")
            was_invoiced = ""

    if status_explicit and not is_valid_status(status):
        errors.append("Invalid operation status.")
        status = ""
        status_explicit = False

    current_year, current_month = _current_year_month()

    # Monthly preset always uses named month + year.
    if view == "monthly":
        period_mode = PERIOD_MODE_MONTH

        if year is None:
            year = current_year

        if month is None:
            month = current_month

        if year < 2000 or year > 2100:
            errors.append("Invalid year.")
        elif month < 1 or month > 12:
            errors.append("Invalid month.")
        else:
            date_from, date_to = _month_bounds(year, month)
    else:
        if period_mode == "":
            # Infer from submitted fields for backwards compatibility.
            if date_from_raw or date_to_raw:
                period_mode = PERIOD_MODE_RANGE
            elif year is not None and month is not None:
                period_mode = PERIOD_MODE_MONTH
            elif year is not None:
                period_mode = PERIOD_MODE_YEAR
            else:
                period_mode = PERIOD_MODE_ALL

        if period_mode not in PERIOD_MODES:
            errors.append("Invalid period mode.")
            period_mode = PERIOD_MODE_ALL

        if period_mode == PERIOD_MODE_MONTH:
            if year is None:
                year = current_year

            if month is None:
                month = current_month

            if year < 2000 or year > 2100:
                errors.append("Invalid year.")
            elif month < 1 or month > 12:
                errors.append("Invalid month.")
            else:
                date_from, date_to = _month_bounds(year, month)
        elif period_mode == PERIOD_MODE_YEAR:
            if year is None:
                year = current_year

            month = None

            if year < 2000 or year > 2100:
                errors.append("Invalid year.")
            else:
                date_from, date_to = _year_bounds(year)
        elif period_mode == PERIOD_MODE_RANGE:
            parsed_from, from_error = parse_optional_date(
                date_from_raw,
                "Start date",
            )

            if from_error:
                errors.append(from_error)
            elif parsed_from is not None:
                date_from = date_to_sortable(parsed_from)
                display_from = parsed_from

            parsed_to, to_error = parse_optional_date(
                date_to_raw,
                "End date",
            )

            if to_error:
                errors.append(to_error)
            elif parsed_to is not None:
                date_to = date_to_sortable(parsed_to)
                display_to = parsed_to

            if (
                date_from is not None
                and date_to is not None
                and date_from > date_to
            ):
                errors.append(
                    "Start date cannot be after end date."
                )
        else:
            # all: ignore date/month/year fields for querying
            date_from = None
            date_to = None
            year = None
            month = None

    # Preset locks.
    if view == "invoiced":
        was_invoiced = "yes"
    elif view == "uninvoiced":
        was_invoiced = "no"
        status = "approved"
        status_explicit = True

    # Official metrics default to approved unless user picked status.
    metrics_status = status if status_explicit else "approved"

    month_year = ""

    if year is not None and month is not None:
        month_year = f"{year:04d}-{month:02d}"

    form = {
        "view": view,
        "period_mode": period_mode,
        "year": year if year is not None else "",
        "month": month if month is not None else "",
        "month_year": month_year,
        "agent_id": agent_id if agent_id is not None else "",
        "currency": currency,
        "agent_type": agent_type,
        "jurisdiction": jurisdiction,
        "was_invoiced": was_invoiced,
        "status": status if status_explicit else "",
        "date_from": date_display_to_iso(display_from),
        "date_to": date_display_to_iso(display_to),
        "status_explicit": status_explicit,
    }

    if period_mode != PERIOD_MODE_RANGE:
        form["date_from"] = ""
        form["date_to"] = ""

    if period_mode == PERIOD_MODE_YEAR:
        form["month"] = ""
        form["month_year"] = ""

    if period_mode == PERIOD_MODE_ALL:
        form["year"] = ""
        form["month"] = ""
        form["month_year"] = ""

    if view == "monthly":
        form["year"] = year
        form["month"] = month
        form["month_year"] = month_year
        form["period_mode"] = PERIOD_MODE_MONTH

    query_filters = {
        "organization_id": None,  # filled by loader
        "agent_id": agent_id,
        "date_from": date_from,
        "date_to": date_to,
        "was_invoiced": was_invoiced or None,
        "jurisdiction": jurisdiction or None,
        "currency": currency or None,
        "agent_type": agent_type or None,
        "status": metrics_status,
    }

    return errors, {
        "view": view,
        "form": form,
        "query_filters": query_filters,
        "year": year,
        "month": month,
        "period_mode": period_mode,
        "metrics_status": metrics_status,
        "status_explicit": status_explicit,
    }


def _active_filter_labels(parsed, language, agents_by_id):
    labels = []
    form = parsed["form"]
    view = parsed["view"]
    period_mode = parsed.get("period_mode") or form.get("period_mode")

    if view == "monthly" and parsed["year"] and parsed["month"]:
        labels.append(
            f"{_month_name(parsed['month'], language)} {parsed['year']}"
        )
    elif period_mode == PERIOD_MODE_YEAR and parsed.get("year"):
        labels.append(str(parsed["year"]))
    elif period_mode == PERIOD_MODE_MONTH and parsed.get("year") and parsed.get("month"):
        labels.append(
            f"{_month_name(parsed['month'], language)} {parsed['year']}"
        )
    elif period_mode == PERIOD_MODE_RANGE:
        if form.get("date_from"):
            labels.append(
                f"{_t('reports_from', language)}: "
                f"{form['date_from']}"
            )

        if form.get("date_to"):
            labels.append(
                f"{_t('reports_to', language)}: "
                f"{form['date_to']}"
            )

    agent_id = form.get("agent_id")

    if agent_id:
        agent = agents_by_id.get(int(agent_id))

        if agent:
            labels.append(agent["name"])

    if form.get("currency"):
        labels.append(form["currency"])

    if form.get("agent_type"):
        labels.append(form["agent_type"])

    if form.get("jurisdiction"):
        labels.append(form["jurisdiction"])

    if parsed["view"] == "invoiced":
        labels.append(_t("reports_invoiced_only", language))
    elif parsed["view"] == "uninvoiced":
        labels.append(_t("reports_not_invoiced_only", language))
    elif form.get("was_invoiced") == "yes":
        labels.append(_t("yes", language))
    elif form.get("was_invoiced") == "no":
        labels.append(_t("no", language))

    if parsed["status_explicit"]:
        labels.append(
            _t(f"status_{parsed['metrics_status']}", language)
        )
    else:
        labels.append(_t("reports_official_approved", language))

    return labels


def build_download_basename(parsed, language):
    view = parsed["view"]
    language = language if language in ("es", "en") else "es"

    if view == "monthly" and parsed["year"] and parsed["month"]:
        stamp = f"{parsed['year']}-{parsed['month']:02d}"

        if language == "en":
            return f"monthly_report_{stamp}"

        return f"reporte_mensual_{stamp}"

    period_mode = parsed.get("period_mode")
    stamp_parts = []
    qf = parsed["query_filters"]

    if period_mode == PERIOD_MODE_YEAR and parsed.get("year"):
        stamp_parts.append(str(parsed["year"]))
    elif period_mode == PERIOD_MODE_MONTH and parsed.get("year") and parsed.get("month"):
        stamp_parts.append(
            f"{parsed['year']}-{parsed['month']:02d}"
        )
    elif qf.get("date_from") and qf.get("date_to"):
        stamp_parts.append(
            f"{qf['date_from'][:4]}-{qf['date_from'][4:6]}"
            f"_to_{qf['date_to'][:4]}-{qf['date_to'][4:6]}"
        )
    elif qf.get("date_from"):
        stamp_parts.append(qf["date_from"])
    elif qf.get("date_to"):
        stamp_parts.append(qf["date_to"])

    stamp = "_".join(stamp_parts) if stamp_parts else "all"

    names = {
        "general": (
            "reporte_general" if language == "es" else "general_report"
        ),
        "invoiced": (
            "reporte_facturadas"
            if language == "es"
            else "invoiced_report"
        ),
        "uninvoiced": (
            "reporte_no_facturadas"
            if language == "es"
            else "not_invoiced_report"
        ),
    }
    prefix = names.get(view, names["general"])
    return f"{prefix}_{stamp}"


def _build_labels(language):
    return {
        "title": _t("reports_title", language),
        "subtitle": _t("reports_subtitle", language),
        "official_note": _t("reports_official_note", language),
        "view_general": _t("reports_view_general", language),
        "view_monthly": _t("reports_view_monthly", language),
        "view_invoiced": _t("reports_view_invoiced", language),
        "view_uninvoiced": _t("reports_view_uninvoiced", language),
        "download_pdf": _t("reports_download_pdf", language),
        "download_xlsx": _t("reports_download_xlsx", language),
        "operations_count": _t("reports_metric_operations", language),
        "approved_count": _t("reports_metric_approved", language),
        "pending_count": _t("reports_metric_pending", language),
        "volume_usd": _t("reports_metric_volume", language),
        "total_commission": _t("reports_metric_commission", language),
        "agent_payments": _t("reports_metric_agent_payments", language),
        "office_net": _t("reports_metric_office_net", language),
        "vat_total": _t("reports_metric_vat", language),
        "properties_count": _t("reports_metric_properties", language),
        "average_commission": _t(
            "reports_metric_avg_commission", language
        ),
        "invoiced_count": _t("reports_metric_invoiced", language),
        "not_invoiced_count": _t(
            "reports_metric_not_invoiced", language
        ),
        "ranking_title": _t("reports_ranking_title", language),
        "monthly_title": _t("reports_monthly_title", language),
        "operations_title": _t("reports_operations_title", language),
        "charts_title": _t("reports_charts_title", language),
        "charts_unavailable": _t(
            "reports_charts_unavailable", language
        ),
        "chart_commissions_month": _t(
            "reports_chart_commissions_month", language
        ),
        "chart_operations_month": _t(
            "reports_chart_operations_month", language
        ),
        "chart_agent_ranking": _t(
            "reports_chart_agent_ranking", language
        ),
        "chart_status": _t("reports_chart_status", language),
        "sheet_summary": _t("reports_sheet_summary", language),
        "sheet_by_month": _t("reports_sheet_by_month", language),
        "sheet_agents": _t("reports_sheet_agents", language),
        "sheet_operations": _t("reports_sheet_operations", language),
        "col_month": _t("reports_col_month", language),
        "col_rank": _t("rank", language),
        "col_agent": _t("agent", language),
        "col_agent_type": _t("agent_type", language),
        "col_operations": _t("operations", language),
        "col_commission": _t("total_commission", language),
        "col_agent_payment": _t("agent_payment", language),
        "col_office_net": _t("office_net_payment", language),
        "col_volume": _t("reports_metric_volume", language),
        "col_id": _t("id", language),
        "col_date": _t("date", language),
        "col_property": _t("property", language),
        "col_jurisdiction": _t("jurisdiction", language),
        "col_status": _t("status", language),
        "col_invoiced": _t("invoiced", language),
        "col_currency": _t("currency", language),
        "col_original_amount": _t("original_amount", language),
        "col_exchange_rate": _t("exchange_rate", language),
        "col_sale_price_usd": _t("reports_col_sale_price_usd", language),
        "col_vat": _t("vat", language),
        "col_office_total": _t("office_total", language),
        "empty": _t("op_summary_empty", language),
        "no_operations": _t("reports_no_operations", language),
        "filters_applied": _t("reports_filters_applied", language),
        "status_draft": _t("status_draft", language),
        "status_pending": _t("status_pending", language),
        "status_approved": _t("status_approved", language),
        "status_rejected": _t("status_rejected", language),
        "yes": _t("yes", language),
        "no": _t("no", language),
        "period_mode": _t("reports_period_mode", language),
        "period_all": _t("reports_period_all", language),
        "period_month": _t("reports_period_month", language),
        "period_year": _t("reports_period_year", language),
        "period_range": _t("reports_period_range", language),
        "pdf_title": _t("reports_pdf_title", language),
        "pdf_subtitle": _t("reports_pdf_subtitle", language),
    }


def _operation_rows(operations, language):
    rows = []

    for operation in operations:
        status = operation.get("status") or "approved"
        invoiced = operation.get("was_invoiced") or "no"
        currency = operation.get("currency") or "USD"
        rows.append(
            {
                "id": operation["id"],
                "date": operation.get("date"),
                "agent": operation.get("agent"),
                "agent_type": operation.get("agent_type"),
                "property": operation.get("property"),
                "jurisdiction": operation.get("jurisdiction"),
                "status": status,
                "status_label": _t(f"status_{status}", language),
                "was_invoiced": invoiced,
                "was_invoiced_label": _t(invoiced, language),
                "currency": currency,
                "original_amount": float(
                    operation.get("original_amount") or 0
                ),
                "exchange_rate": float(
                    operation.get("exchange_rate") or 1
                ),
                "sale_price": float(operation.get("sale_price") or 0),
                "total_commission": float(
                    operation.get("total_commission") or 0
                ),
                "agent_payment": float(
                    operation.get("agent_payment") or 0
                ),
                "office_payment": float(
                    operation.get("office_payment") or 0
                ),
                "vat_amount": float(operation.get("vat_amount") or 0),
                "office_total": float(
                    operation.get("office_total") or 0
                ),
                "show_fx": currency != "USD",
                "db_id": operation.get("db_id"),
                "property_external_id": operation.get(
                    "property_external_id"
                ),
            }
        )

    return rows


def _charts_payload(monthly_series, ranking, status_counts, labels):
    months = [item["month"] for item in monthly_series]
    return {
        "commissions_by_month": {
            "labels": months,
            "values": [
                item["total_commission"] for item in monthly_series
            ],
            "title": labels["chart_commissions_month"],
        },
        "operations_by_month": {
            "labels": months,
            "values": [
                item["operations_count"] for item in monthly_series
            ],
            "title": labels["chart_operations_month"],
        },
        "agent_ranking": {
            "labels": [item["agent_name"] for item in ranking],
            "values": [item["total_commission"] for item in ranking],
            "title": labels["chart_agent_ranking"],
        },
        "status_distribution": {
            "labels": [
                labels["status_draft"],
                labels["status_pending"],
                labels["status_approved"],
                labels["status_rejected"],
            ],
            "values": [
                status_counts["draft"],
                status_counts["pending"],
                status_counts["approved"],
                status_counts["rejected"],
            ],
            "title": labels["chart_status"],
        },
    }


def load_organization_report(
    organization_id,
    raw_filters,
    *,
    language="es",
    scoped_agent_id=None,
    ranking_limit=10,
):
    language = language if language in ("es", "en") else "es"
    errors, parsed = parse_report_filters(
        raw_filters,
        scoped_agent_id=scoped_agent_id,
    )

    agents = get_agents(organization_id)

    if scoped_agent_id is not None:
        agents = [
            agent
            for agent in agents
            if agent["id"] == scoped_agent_id
        ]

    agents_by_id = {agent["id"]: agent for agent in agents}
    labels = _build_labels(language)
    settings = get_organization_settings(organization_id)
    organization_name = None
    organization_logo_rel = None

    if settings is not None:
        organization_name = settings.get("display_name")
        organization_logo_rel = settings.get("logo_path")

    logo_path = _brand_logo_path(organization_logo_rel)

    empty_metrics = {
        "operations_count": 0,
        "volume_usd": 0.0,
        "total_commission": 0.0,
        "agent_payments": 0.0,
        "office_net": 0.0,
        "vat_total": 0.0,
        "office_total": 0.0,
        "abao_total": 0.0,
        "martillero_total": 0.0,
        "properties_count": 0,
        "average_commission": 0.0,
        "invoiced_count": 0,
        "not_invoiced_count": 0,
        "approved_count": 0,
        "pending_count": 0,
    }

    if errors:
        return {
            "errors": errors,
            "view": parsed["view"],
            "form": parsed["form"],
            "agents": agents,
            "agent_types": AGENT_TYPES,
            "currencies": CURRENCIES,
            "jurisdictions": JURISDICTIONS,
            "metrics": empty_metrics,
            "status_counts": {
                "draft": 0,
                "pending": 0,
                "approved": 0,
                "rejected": 0,
            },
            "monthly_series": [],
            "agent_ranking": [],
            "operations": [],
            "charts": _charts_payload([], [], {
                "draft": 0,
                "pending": 0,
                "approved": 0,
                "rejected": 0,
            }, labels),
            "show_charts": parsed["view"] in ("general", "monthly"),
            "active_filters": [],
            "labels": labels,
            "language": language,
            "download_basename": build_download_basename(
                parsed, language
            ),
            "brand": {
                "app_name": _t("app_title", language),
                "organization_name": organization_name,
                "logo_path": str(logo_path) if logo_path else None,
                "fallback_logo": str(DEFAULT_BRAND_LOGO),
            },
            "metrics_status": parsed["metrics_status"],
            "years": list(range(2020, datetime.utcnow().year + 2)),
            "months": list(range(1, 13)),
            "month_options": month_options(language),
        }

    query_filters = dict(parsed["query_filters"])
    query_filters["organization_id"] = organization_id

    metrics = aggregate_report_metrics(query_filters)
    status_counts = aggregate_status_counts(query_filters)
    monthly_series = aggregate_monthly_series(query_filters)
    agent_ranking = aggregate_agent_ranking(
        query_filters,
        limit=ranking_limit,
    )
    operations = filter_operations(
        organization_id,
        agent_id=query_filters["agent_id"],
        date_from=query_filters["date_from"],
        date_to=query_filters["date_to"],
        was_invoiced=query_filters["was_invoiced"],
        jurisdiction=query_filters["jurisdiction"],
        currency=query_filters["currency"],
        agent_type=query_filters["agent_type"],
        status=query_filters["status"],
    )

    metrics["approved_count"] = status_counts["approved"]
    metrics["pending_count"] = status_counts["pending"]

    return {
        "errors": [],
        "view": parsed["view"],
        "form": parsed["form"],
        "agents": agents,
        "agent_types": AGENT_TYPES,
        "currencies": CURRENCIES,
        "jurisdictions": JURISDICTIONS,
        "metrics": metrics,
        "status_counts": status_counts,
        "monthly_series": monthly_series,
        "agent_ranking": agent_ranking,
        "operations": _operation_rows(operations, language),
        "charts": _charts_payload(
            monthly_series,
            agent_ranking,
            status_counts,
            labels,
        ),
        "show_charts": parsed["view"] in ("general", "monthly"),
        "active_filters": _active_filter_labels(
            parsed,
            language,
            agents_by_id,
        ),
        "labels": labels,
        "language": language,
        "download_basename": build_download_basename(
            parsed, language
        ),
        "brand": {
            "app_name": _t("app_title", language),
            "organization_name": organization_name,
            "logo_path": str(logo_path) if logo_path else None,
            "fallback_logo": str(DEFAULT_BRAND_LOGO),
        },
        "metrics_status": parsed["metrics_status"],
        "years": list(range(2020, datetime.utcnow().year + 2)),
        "months": list(range(1, 13)),
        "month_options": month_options(language),
        "parsed": parsed,
    }
