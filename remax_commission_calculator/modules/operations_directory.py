"""
Operations directory view-model (operations page mockup).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from modules.dashboard_home import _stage_for_operation
from modules.database.tenant import require_organization_id
from modules.i18n import translate


TAB_BUCKETS = (
    "all",
    "active",
    "reserved",
    "pending",
    "closed",
    "cancelled",
)


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _today():
    return date.today()


def _pct_change(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    if previous <= 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _parse_operation_date(value):
    if not value:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    if "T" in raw:
        raw = raw.split("T", 1)[0]

    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    return None


def _operation_date_sort_key(value):
    parsed = _parse_operation_date(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y%m%d")


def _operation_bucket(operation, stage):
    status = operation.get("status") or "approved"

    if status == "rejected":
        return "cancelled"
    if status in ("pending", "draft"):
        return "pending"

    invoiced = (operation.get("was_invoiced") or "no") == "yes"
    tone = stage.get("tone") or "neutral"

    if invoiced or tone == "closing":
        return "closed"
    if tone == "reservation":
        return "reserved"
    return "active"


def _workflow_status(operation, bucket, language):
    status = operation.get("status") or "approved"

    if status == "pending":
        return {
            "label": _t("operations_status_pending", language),
            "tone": "pending",
        }
    if status == "draft":
        return {
            "label": _t("operations_status_in_progress", language),
            "tone": "progress",
        }
    if bucket == "closed":
        return {
            "label": _t("operations_status_closed", language),
            "tone": "closed",
        }
    if bucket == "reserved":
        return {
            "label": _t("operations_status_reserved", language),
            "tone": "reserved",
        }
    if bucket == "cancelled":
        return {
            "label": _t("operations_status_cancelled", language),
            "tone": "rejected",
        }
    return {
        "label": _t("operations_status_active", language),
        "tone": "active",
    }


def _operation_type_label(operation, language):
  # No dedicated operation type in DB; default to sale.
    return _t("operations_type_sale", language)


def build_operations_directory(
    organization_id,
    operations,
    raw_filters,
    *,
    agents=None,
    language="es",
):
    language = language if language in ("es", "en") else "es"
    organization_id = require_organization_id(organization_id)
    agents = agents or []

    rows = []
    bucket_counts = {bucket: 0 for bucket in TAB_BUCKETS}

    for operation in operations:
        stage = _stage_for_operation(operation, language)
        bucket = _operation_bucket(operation, stage)
        bucket_counts["all"] += 1
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        agent_name = operation.get("agent") or "—"
        parts = agent_name.split()
        stage_tone = stage.get("tone") or "neutral"
        css_tone = stage_tone
        if css_tone in ("proposal", "negotiation"):
            css_tone = "progress"
        elif css_tone == "closing":
            css_tone = "closed"
        elif css_tone == "reservation":
            css_tone = "reserved"

        rows.append(
            {
                **operation,
                "display_id": operation.get("id") or f"OP-{operation.get('db_id'):04d}",
                "property_title": operation.get("property") or "—",
                "property_subtitle": operation.get("jurisdiction") or "—",
                "client_name": "—",
                "type_label": _operation_type_label(operation, language),
                "type_tone": "sale",
                "stage_label": stage.get("label") or "—",
                "stage_tone": css_tone,
                "workflow_status": _workflow_status(
                    operation,
                    bucket,
                    language,
                ),
                "bucket": bucket,
                "agent_initials": (
                    f"{parts[0][0:1].upper()}"
                    f"{parts[1][0:1].upper() if len(parts) > 1 else ''}"
                ),
                "currency": operation.get("currency") or "USD",
            }
        )

    today = _today()
    yesterday = today - timedelta(days=1)

    def _created_on_day(day):
        count = 0
        for item in operations:
            if _parse_operation_date(item.get("date")) == day:
                count += 1
        return count

    tab_filter = (raw_filters.get("tab") or "all").strip() or "all"
    search_q = (raw_filters.get("q") or raw_filters.get("property") or "").strip().lower()
    agent_filter = (raw_filters.get("agent_id") or "").strip()
    stage_filter = (raw_filters.get("stage") or "").strip()
    status_filter = (raw_filters.get("status") or "").strip()
    type_filter = (raw_filters.get("type") or "").strip()
    currency_filter = (raw_filters.get("currency") or "").strip()

    if tab_filter != "all":
        rows = [row for row in rows if row["bucket"] == tab_filter]

    if search_q:
        rows = [
            row
            for row in rows
            if search_q in (row.get("property") or "").lower()
            or search_q in row["display_id"].lower()
            or search_q in (row.get("agent") or "").lower()
        ]

    if agent_filter:
        try:
            agent_id = int(agent_filter)
            rows = [
                row for row in rows
                if row.get("agent_db_id") == agent_id
            ]
        except (TypeError, ValueError):
            pass

    if stage_filter:
        rows = [
            row for row in rows
            if row.get("stage_tone") == stage_filter
            or row.get("stage_label", "").lower() == stage_filter.lower()
        ]

    if status_filter:
        rows = [
            row for row in rows
            if row.get("status") == status_filter
            or row["bucket"] == status_filter
        ]

    if currency_filter:
        rows = [
            row for row in rows
            if (row.get("currency") or "").upper() == currency_filter.upper()
        ]

    if type_filter == "sale":
        rows = [row for row in rows if row.get("type_tone") == "sale"]
    elif type_filter == "rental":
        rows = [row for row in rows if row.get("type_tone") == "rental"]

    rows = sorted(
        rows,
        key=lambda item: (
            _operation_date_sort_key(item.get("date")),
            item.get("db_id") or 0,
        ),
        reverse=True,
    )

    page_size = int(raw_filters.get("per_page") or 10)
    page_size = max(5, min(page_size, 50))
    page = int(raw_filters.get("page") or 1)
    page = max(1, page)
    total_rows = len(rows)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    tab_labels = {
        "all": _t("operations_tab_all", language),
        "active": _t("operations_tab_active", language),
        "reserved": _t("operations_tab_reserved", language),
        "pending": _t("operations_tab_pending", language),
        "closed": _t("operations_tab_closed", language),
        "cancelled": _t("operations_tab_cancelled", language),
    }

    tabs = []
    for bucket in TAB_BUCKETS:
        tabs.append(
            {
                "key": bucket,
                "label": tab_labels[bucket],
                "count": bucket_counts.get(bucket, 0),
            }
        )

    return {
        "kpis": {
            "active": bucket_counts.get("active", 0),
            "active_trend": _pct_change(
                bucket_counts.get("active", 0),
                bucket_counts.get("active", 0),
            ),
            "reserved": bucket_counts.get("reserved", 0),
            "reserved_trend": _pct_change(
                _created_on_day(today),
                _created_on_day(yesterday),
            ),
            "closed": bucket_counts.get("closed", 0),
            "closed_trend": _pct_change(
                _created_on_day(today),
                _created_on_day(yesterday),
            ),
            "pending": bucket_counts.get("pending", 0),
            "pending_trend": _pct_change(
                bucket_counts.get("pending", 0),
                bucket_counts.get("pending", 0),
            ),
        },
        "rows": page_rows,
        "tabs": tabs,
        "pagination": {
            "page": page,
            "per_page": page_size,
            "total": total_rows,
            "total_pages": total_pages,
            "from_row": start + 1 if total_rows else 0,
            "to_row": min(start + page_size, total_rows),
        },
        "filters": {
            "q": raw_filters.get("q") or raw_filters.get("property") or "",
            "tab": tab_filter,
            "agent_id": agent_filter,
            "stage": stage_filter,
            "status": status_filter,
            "type": type_filter,
            "currency": currency_filter,
            "property": raw_filters.get("property") or "",
            "jurisdiction": raw_filters.get("jurisdiction") or "",
            "min_amount": raw_filters.get("min_amount") or "",
            "max_amount": raw_filters.get("max_amount") or "",
            "date_from": raw_filters.get("date_from") or "",
            "date_to": raw_filters.get("date_to") or "",
            "was_invoiced": raw_filters.get("was_invoiced") or "",
            "per_page": page_size,
        },
        "agent_options": agents,
        "currencies": ["USD", "ARS"],
    }
