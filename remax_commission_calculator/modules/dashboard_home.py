"""
Agent / executive home panel view-model (dashboard mockup).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from modules.database.connection import get_connection
from modules.database.notifications_repository import list_notifications
from modules.database.operations_repository import filter_operations
from modules.database.pending_approvals_repository import (
    list_pending_approval_items,
)
from modules.database.properties_repository import (
    STATUS_APPROVED as PROPERTY_STATUS_APPROVED,
    get_properties,
)
from modules.i18n import translate
from modules.invoicing import billing_kpis
from modules.validators import date_to_sortable


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def _today():
    return date.today()


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


def _format_today_label(language):
    today = _today()

    if language == "en":
        months = _MONTHS_EN
        return f"{months[today.month - 1]} {today.day}, {today.year}"

    months = _MONTHS_ES
    return f"{today.day} de {months[today.month - 1]} de {today.year}"


def _parse_display_date(value):
    if not value:
        return None

    try:
        parts = value.split("/")
        if len(parts) != 3:
            return None

        day, month, year = (int(p) for p in parts)
        return date(year, month, day)
    except (TypeError, ValueError):
        return None


def _relative_label(value, language):
    parsed = _parse_display_date(value)
    if parsed is None:
        return "—"

    today = _today()
    delta = (today - parsed).days

    if delta <= 0:
        return _t("dashboard_relative_today", language)
    if delta == 1:
        return _t("dashboard_relative_yesterday", language)
    if delta < 7:
        return _t("dashboard_relative_days_ago", language, days=delta)

    return value


def _stage_for_operation(operation, language):
    status = operation.get("status") or "approved"
    invoiced = (operation.get("was_invoiced") or "no") == "yes"

    if status == "draft":
        return {
            "label": _t("dashboard_stage_proposal", language),
            "tone": "proposal",
        }
    if status == "pending":
        return {
            "label": _t("dashboard_stage_negotiation", language),
            "tone": "negotiation",
        }
    if status == "approved" and not invoiced:
        return {
            "label": _t("dashboard_stage_reservation", language),
            "tone": "reservation",
        }
    if status == "approved":
        return {
            "label": _t("dashboard_stage_closing", language),
            "tone": "closing",
        }
    if status == "rejected":
        return {
            "label": _t("status_rejected", language),
            "tone": "rejected",
        }

    return {
        "label": _t(f"status_{status}", language),
        "tone": "neutral",
    }


def _next_action_for_operation(operation, language):
    status = operation.get("status") or "approved"
    invoiced = (operation.get("was_invoiced") or "no") == "yes"

    if status == "draft":
        return _t("dashboard_action_complete_operation", language)
    if status == "pending":
        return _t("dashboard_action_wait_approval", language)
    if status == "approved" and not invoiced:
        return _t("dashboard_action_issue_invoice", language)
    if status == "approved":
        return _t("dashboard_action_track_collection", language)
    if status == "rejected":
        return _t("dashboard_action_review_rejection", language)

    return _t("dashboard_action_follow_up", language)


def _format_property_specs(property_row, language):
    parts = []

    if property_row.get("property_type"):
        parts.append(str(property_row["property_type"]))

    if property_row.get("listing_price"):
        currency = property_row.get("listing_currency") or "USD"
        amount = float(property_row["listing_price"] or 0)
        parts.append(f"{currency} {amount:,.0f}")

    if not parts:
        jurisdiction = property_row.get("jurisdiction")
        if jurisdiction:
            parts.append(jurisdiction)

    return " · ".join(parts) if parts else "—"


def _aggregate_daily_commissions(
    organization_id,
    *,
    agent_id=None,
    days=7,
    today=None,
    language="es",
):
    today = today or _today()
    start = today - timedelta(days=days - 1)
    start_display = f"{start.day:02d}/{start.month:02d}/{start.year:04d}"
    end_display = f"{today.day:02d}/{today.month:02d}/{today.year:04d}"

    connection = get_connection()
    cursor = connection.cursor()

    query = """
        SELECT operation_date, SUM(total_commission)
        FROM operations
        WHERE organization_id = ?
          AND status = 'approved'
    """
    params = [organization_id]

    if agent_id is not None:
        query += " AND agent_id = ?"
        params.append(agent_id)

    query += " GROUP BY operation_date"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    connection.close()

    totals_by_date = {}
    for row in rows:
        parsed = _parse_display_date(row[0])
        if parsed is None:
            continue

        if parsed < start or parsed > today:
            continue

        totals_by_date[parsed] = float(row[1] or 0)

    labels = []
    values = []
    cursor_day = start

    month_short_es = (
        "ene", "feb", "mar", "abr", "may", "jun",
        "jul", "ago", "sep", "oct", "nov", "dic",
    )

    while cursor_day <= today:
        if language == "en":
            label = f"{cursor_day.day:02d} {cursor_day.strftime('%b')}"
        else:
            label = (
                f"{cursor_day.day:02d} "
                f"{month_short_es[cursor_day.month - 1]}"
            )
        labels.append(label)
        values.append(totals_by_date.get(cursor_day, 0.0))
        cursor_day += timedelta(days=1)

    return {
        "labels": labels,
        "values": values,
        "start": start_display,
        "end": end_display,
    }


def _count_properties_recent(organization_id, *, agent_id=None, days=7):
    connection = get_connection()
    cursor = connection.cursor()

    query = """
        SELECT COUNT(*)
        FROM properties
        WHERE organization_id = ?
          AND status = ?
    """
    params = [organization_id, PROPERTY_STATUS_APPROVED]

    if agent_id is not None:
        query += " AND agent_id = ?"
        params.append(agent_id)

    query += """
        AND (
            COALESCE(submitted_at, '') != ''
            OR COALESCE(reviewed_at, '') != ''
        )
    """

    cursor.execute(query, params)
    total_with_dates = cursor.fetchone()[0]
    connection.close()

    properties = get_properties(
        organization_id,
        agent_id=agent_id,
        status=PROPERTY_STATUS_APPROVED,
    )
    cutoff = _today() - timedelta(days=days)
    recent = 0

    for item in properties:
        for field in ("submitted_at", "reviewed_at"):
            parsed = None
            raw = item.get(field)
            if raw:
                try:
                    parsed = datetime.fromisoformat(raw).date()
                except ValueError:
                    parsed = _parse_display_date(raw)

            if parsed and parsed >= cutoff:
                recent += 1
                break

    if recent == 0 and total_with_dates == 0 and properties:
        recent = min(len(properties), 1) if len(properties) > 0 else 0

    return recent


def _count_operations_updated_today(organization_id, *, agent_id=None):
    today_display = (
        f"{_today().day:02d}/{_today().month:02d}/{_today().year:04d}"
    )
    active_statuses = ("draft", "pending", "approved")
    count = 0

    for status in active_statuses:
        operations = filter_operations(
            organization_id,
            agent_id=agent_id,
            status=status,
        )
        for operation in operations:
            if operation.get("date") == today_display:
                count += 1
            elif operation.get("reviewed_at"):
                try:
                    reviewed = datetime.fromisoformat(
                        operation["reviewed_at"]
                    ).date()
                    if reviewed == _today():
                        count += 1
                except ValueError:
                    pass

    return count


def _pending_invoice_amount(operations):
    total = 0.0

    for operation in operations:
        if (operation.get("was_invoiced") or "no") != "yes":
            total += float(operation.get("total_commission") or 0)

    return total


def _build_active_operations(organization_id, language, *, agent_id=None):
    rows = []
    seen_ids = set()

    for status in ("draft", "pending", "approved"):
        for operation in filter_operations(
            organization_id,
            agent_id=agent_id,
            status=status,
        ):
            db_id = operation.get("db_id")
            if db_id in seen_ids:
                continue

            seen_ids.add(db_id)
            stage = _stage_for_operation(operation, language)
            rows.append(
                {
                    "id": operation.get("id"),
                    "db_id": db_id,
                    "property": operation.get("property") or "—",
                    "client": operation.get("agent") or "—",
                    "stage_label": stage["label"],
                    "stage_tone": stage["tone"],
                    "next_action": _next_action_for_operation(
                        operation,
                        language,
                    ),
                    "updated_label": _relative_label(
                        operation.get("date"),
                        language,
                    ),
                    "sort_date": date_to_sortable(
                        operation.get("date") or ""
                    ),
                }
            )

    rows.sort(key=lambda item: item["sort_date"], reverse=True)
    return rows[:8]


def _property_status_label(property_row, operation_map, language):
    property_id = property_row.get("id")
    operation = operation_map.get(property_id)

    if operation:
        stage = _stage_for_operation(operation, language)
        return stage["label"]

    return _t("dashboard_property_active", language)


def _property_status_tone(property_row, operation_map, language):
    property_id = property_row.get("id")
    operation = operation_map.get(property_id)

    if not operation:
        return "active"

    return _stage_for_operation(operation, language)["tone"]


def _build_active_properties(organization_id, language, *, agent_id=None):
    properties = get_properties(
        organization_id,
        agent_id=agent_id,
        status=PROPERTY_STATUS_APPROVED,
    )[:6]

    operation_map = {}
    for status in ("draft", "pending", "approved"):
        for operation in filter_operations(
            organization_id,
            agent_id=agent_id,
            status=status,
        ):
            property_db_id = operation.get("property_db_id")
            if property_db_id is None:
                continue

            current = operation_map.get(property_db_id)
            if current is None:
                operation_map[property_db_id] = operation
                continue

            if date_to_sortable(operation.get("date") or "") > date_to_sortable(
                current.get("date") or ""
            ):
                operation_map[property_db_id] = operation

    rows = []
    for item in properties:
        title = item.get("address") or "—"
        rows.append(
            {
                "id": item.get("id"),
                "title": title,
                "specs": _format_property_specs(item, language),
                "status_label": _property_status_label(
                    item,
                    operation_map,
                    language,
                ),
                "status_tone": _property_status_tone(
                    item,
                    operation_map,
                    language,
                ),
                "external_id": item.get("external_id"),
            }
        )

    return rows


def _build_tasks(
    organization_id,
    language,
    *,
    can_manage_approvals=False,
    agent_id=None,
):
    tasks = []

    if can_manage_approvals:
        for item in list_pending_approval_items(organization_id)[:5]:
            title = _t(item["title_key"], language)
            summary = item.get("summary") or item.get("agent_name") or "—"
            tasks.append(
                {
                    "label": f"{title}: {summary}",
                    "due_label": _relative_label(
                        item.get("created_at"),
                        language,
                    ),
                    "done": False,
                }
            )
    elif agent_id is not None:
        pending_ops = filter_operations(
            organization_id,
            agent_id=agent_id,
            status="pending",
        )
        for operation in pending_ops[:5]:
            tasks.append(
                {
                    "label": _t(
                        "dashboard_task_pending_operation",
                        language,
                        property=operation.get("property") or "—",
                    ),
                    "due_label": _relative_label(
                        operation.get("date"),
                        language,
                    ),
                    "done": False,
                }
            )

        draft_ops = filter_operations(
            organization_id,
            agent_id=agent_id,
            status="draft",
        )
        for operation in draft_ops[:3]:
            tasks.append(
                {
                    "label": _t(
                        "dashboard_task_draft_operation",
                        language,
                        property=operation.get("property") or "—",
                    ),
                    "due_label": _relative_label(
                        operation.get("date"),
                        language,
                    ),
                    "done": False,
                }
            )

    return tasks[:5]


def _notification_message(notification, language):
    kind = notification.get("kind") or ""
    payload = notification.get("payload") or {}
    key = f"notification_{kind}"

    if kind == "operation_invoice_amount_ready":
        return _t(
            key,
            language,
            operation_id=payload.get("operation_id", "—"),
        )
    if kind == "operation_side_ready_to_invoice":
        return _t(
            key,
            language,
            side=payload.get("side_label", "—"),
            property=payload.get("property", "—"),
        )

    translated = _t(key, language)
    if translated != key:
        return translated

    return payload.get("message") or kind.replace("_", " ").title()


def _build_reminders(user_id, organization_id, language):
    if user_id is None:
        return []

    reminders = []
    for item in list_notifications(user_id, organization_id, limit=5):
        created_at = item.get("created_at")
        when_label = "—"

        if created_at:
            try:
                created = datetime.fromisoformat(created_at)
                when_label = created.strftime("%d/%m/%Y - %H:%M")
            except ValueError:
                when_label = created_at

        reminders.append(
            {
                "message": _notification_message(item, language),
                "when_label": when_label,
                "tone": "blue" if not item.get("is_read") else "muted",
            }
        )

    return reminders


def _commission_trend_vs_previous(metrics, report):
    current = float(metrics.get("total_commission") or 0)
    monthly = report.get("monthly_series") or []

    if len(monthly) < 2:
        return None

    previous = float(monthly[-2].get("total_commission") or 0)
    if previous <= 0:
        return None

    change = ((current - previous) / previous) * 100
    return round(change, 1)


def build_home_panel(
    organization_id,
    dashboard,
    *,
    language="es",
    scoped_agent_id=None,
    role="admin",
    can_manage_approvals=False,
    can_write=False,
    can_create_operations=False,
    user_id=None,
):
    language = language if language in ("es", "en") else "es"
    metrics = dashboard.get("metrics") or {}
    report = dashboard.get("report") or {}
    operations = report.get("operations") or [] if report else []
    workflow = dashboard.get("workflow_counts") or {}

    is_agent = role == "agent"
    title_key = (
        "dashboard_agent_panel_title"
        if is_agent
        else "dashboard_executive_panel_title"
    )

    empty_shell = {
        "title": _t(title_key, language),
        "subtitle": _t("dashboard_agent_panel_subtitle", language),
        "today_label": _format_today_label(language),
        "kpis": {
            "commission_month": 0,
            "commission_change": None,
            "pending_invoices_count": 0,
            "pending_invoices_amount": 0,
            "properties_active": 0,
            "properties_recent": 0,
            "operations_active": 0,
            "operations_today": 0,
        },
        "active_operations": [],
        "active_properties": [],
        "commissions": {
            "total": 0,
            "accrued": 0,
            "paid": 0,
            "pending": 0,
            "change": None,
            "trend": {"labels": [], "values": []},
        },
        "tasks": [],
        "reminders": [],
        "quick_actions": [],
        "period_form": dashboard.get("form") or {},
        "period": dashboard.get("period"),
        "period_options": [
            ("this_month", _t("dashboard_period_this_month", language)),
            ("previous_month", _t("dashboard_period_previous_month", language)),
            ("last_3_months", _t("dashboard_period_last_3_months", language)),
            ("this_year", _t("dashboard_period_this_year", language)),
        ],
    }

    if not organization_id:
        return empty_shell

    billing = billing_kpis(
        organization_id,
        agent_id=scoped_agent_id,
    )

    active_operations = _build_active_operations(
        organization_id,
        language,
        agent_id=scoped_agent_id,
    )
    active_properties = _build_active_properties(
        organization_id,
        language,
        agent_id=scoped_agent_id,
    )
    commission_trend = _aggregate_daily_commissions(
        organization_id,
        agent_id=scoped_agent_id,
        language=language,
    )

    total_commission = float(metrics.get("total_commission") or 0)
    agent_payments = float(metrics.get("agent_payments") or 0)
    pending_commission = max(total_commission - agent_payments, 0.0)

    pending_invoice_count = int(billing.get("pending") or 0) + int(
        billing.get("drafts") or 0
    )
    pending_invoice_amount = _pending_invoice_amount(operations)

    active_ops_count = sum(
        int(workflow.get(key) or 0)
        for key in ("draft", "pending", "approved")
    )
    properties_recent = _count_properties_recent(
        organization_id,
        agent_id=scoped_agent_id,
    )
    operations_today = _count_operations_updated_today(
        organization_id,
        agent_id=scoped_agent_id,
    )
    commission_change = _commission_trend_vs_previous(metrics, report)

    is_agent = role == "agent"
    title_key = (
        "dashboard_agent_panel_title"
        if is_agent
        else "dashboard_executive_panel_title"
    )

    quick_actions = []

    if can_write and can_create_operations:
        quick_actions.append(
            {
                "label": _t("dashboard_quick_new_property", language),
                "endpoint": "properties_new",
                "icon": "property",
            }
        )
        quick_actions.append(
            {
                "label": _t("dashboard_quick_new_operation", language),
                "endpoint": "operations_new",
                "icon": "operation",
            }
        )

    if can_manage_approvals:
        quick_actions.append(
            {
                "label": _t("dashboard_quick_approvals", language),
                "endpoint": "approvals_list",
                "icon": "client",
            }
        )

    quick_actions.extend(
        [
            {
                "label": _t("dashboard_quick_issue_invoice", language),
                "endpoint": "billing_list",
                "icon": "invoice",
            },
        ]
    )

    if can_manage_approvals:
        quick_actions.append(
            {
                "label": _t("dashboard_quick_register_payment", language),
                "endpoint": "cash_list",
                "icon": "payment",
            }
        )

    quick_actions.append(
        {
            "label": _t("dashboard_quick_view_reports", language),
            "endpoint": "reports_index",
            "icon": "reports",
        }
    )

    return {
        "title": _t(title_key, language),
        "subtitle": _t("dashboard_agent_panel_subtitle", language),
        "today_label": _format_today_label(language),
        "kpis": {
            "commission_month": total_commission,
            "commission_change": commission_change,
            "pending_invoices_count": pending_invoice_count,
            "pending_invoices_amount": pending_invoice_amount,
            "properties_active": int(metrics.get("properties_active") or 0),
            "properties_recent": properties_recent,
            "operations_active": active_ops_count,
            "operations_today": operations_today,
        },
        "active_operations": active_operations,
        "active_properties": active_properties,
        "commissions": {
            "total": total_commission,
            "accrued": total_commission,
            "paid": agent_payments,
            "pending": pending_commission,
            "change": commission_change,
            "trend": commission_trend,
        },
        "tasks": _build_tasks(
            organization_id,
            language,
            can_manage_approvals=can_manage_approvals,
            agent_id=scoped_agent_id,
        ),
        "reminders": _build_reminders(
            user_id,
            organization_id,
            language,
        ),
        "quick_actions": quick_actions[:6],
        "period_form": dashboard.get("form") or {},
        "period": dashboard.get("period"),
        "period_options": [
            ("this_month", _t("dashboard_period_this_month", language)),
            ("previous_month", _t("dashboard_period_previous_month", language)),
            ("last_3_months", _t("dashboard_period_last_3_months", language)),
            ("this_year", _t("dashboard_period_this_year", language)),
        ],
    }
