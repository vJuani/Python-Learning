"""
Cash AI workspace view-model (Caja IA page mockup).
"""

from __future__ import annotations

from datetime import date, timedelta

from modules.database.cash_ai_drafts_repository import (
    STATUS_REVIEW,
    list_cash_ai_drafts,
)
from modules.database.cash_treasury_repository import (
    list_cash_movements,
    sum_movements_by_type,
)
from modules.database.tenant import require_organization_id
from modules.cash_treasury import (
    TYPE_EXPENSE,
    TYPE_INCOME,
    TYPE_OPENING,
    get_balances,
)
from modules.i18n import translate


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


def _day_totals(organization_id, day, currency="ARS"):
    iso = day.isoformat()
    income = sum_movements_by_type(
        organization_id,
        currency=currency,
        movement_type=TYPE_INCOME,
        date_from=iso,
        date_to=iso,
    ) + sum_movements_by_type(
        organization_id,
        currency=currency,
        movement_type=TYPE_OPENING,
        date_from=iso,
        date_to=iso,
    )
    expense = sum_movements_by_type(
        organization_id,
        currency=currency,
        movement_type=TYPE_EXPENSE,
        date_from=iso,
        date_to=iso,
    )
    return income, expense


def _format_today_label(language, today=None):
    today = today or date.today()
    months = _MONTHS_ES if language == "es" else _MONTHS_EN

    if language == "es":
        return f"{today.day} de {months[today.month - 1]} de {today.year}"

    return f"{months[today.month - 1]} {today.day}, {today.year}"


def _confidence_pct(confidence):
    if confidence == "high":
        return 96
    if confidence == "low":
        return 45
    try:
        value = float(confidence)
        if value <= 1:
            return int(round(value * 100))
        return int(round(value))
    except (TypeError, ValueError):
        return None


def _draft_status_label(draft, language):
    status = draft.get("status") or ""
    if status == "review":
        return _t("cash_ai_page_status_analyzed", language)
    if status == "processing":
        return _t("cash_ai_page_status_pending", language)
    if status == "confirmed":
        return _t("cash_ai_page_status_registered", language)
    if status == "failed":
        return _t("cash_ai_page_status_failed", language)
    return status or "—"


def _draft_status_tone(draft):
    status = draft.get("status") or ""
    if status in ("review", "confirmed"):
        return "success"
    if status == "failed":
        return "danger"
    return "warning"


def _activity_feed(drafts, language):
    feed = []

    for draft in drafts[:8]:
        payload = draft.get("draft_payload") or {}
        name = (
            draft.get("attachment_original_name")
            or payload.get("merchant")
            or _t("cash_ai_page_activity_receipt", language)
        )
        confidence = draft.get("confidence")
        if draft.get("status") == "confirmed":
            text = _t(
                "cash_ai_page_activity_registered",
                language,
                name=name,
            )
        else:
            text = _t(
                "cash_ai_page_activity_analyzed",
                language,
                name=name,
            )

        feed.append(
            {
                "text": text,
                "time": draft.get("updated_at") or draft.get("created_at"),
                "confidence": _confidence_pct(confidence),
            }
        )

    return feed


def build_cash_ai_workspace(
    organization_id,
    *,
    language="es",
    today=None,
):
    language = language if language in ("es", "en") else "es"
    organization_id = require_organization_id(organization_id)
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    income_today, expense_today = _day_totals(
        organization_id,
        today,
    )
    income_yesterday, expense_yesterday = _day_totals(
        organization_id,
        yesterday,
    )
    net_today = income_today - expense_today
    net_yesterday = income_yesterday - expense_yesterday

    drafts = list_cash_ai_drafts(organization_id, limit=12)
    pending_drafts = [
        item
        for item in drafts
        if item.get("status") in (STATUS_REVIEW, "processing")
    ]
    pending_amount = sum(
        float((item.get("draft_payload") or {}).get("amount") or 0)
        for item in pending_drafts
    )

    evolution_labels = []
    evolution_values = []
    balances = get_balances(organization_id)
    running_net = 0.0

    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        income, expense = _day_totals(organization_id, day)
        running_net += income - expense
        evolution_labels.append(day.strftime("%d/%m"))
        evolution_values.append(round(running_net, 2))

    period_start = balances.get("ARS", 0.0) - running_net
    period_end = balances.get("ARS", 0.0)
    period_change = _pct_change(period_end, period_start)

    recent_movements = list_cash_movements(
        organization_id,
        limit=8,
    )

    movement_rows = []
    for movement in recent_movements:
        movement_rows.append(
            {
                "id": movement.get("id"),
                "display_id": movement.get("display_id"),
                "date": movement.get("movement_date"),
                "type": movement.get("movement_type"),
                "description": movement.get("description") or "—",
                "category": movement.get("category"),
                "payment_method": movement.get("payment_method"),
                "amount": movement.get("amount"),
                "currency": movement.get("currency"),
                "status": movement.get("status"),
            }
        )

    receipt_rows = []
    for draft in drafts[:5]:
        payload = draft.get("draft_payload") or {}
        receipt_rows.append(
            {
                "id": draft.get("id"),
                "name": draft.get("attachment_original_name")
                or _t("cash_ai_page_receipt_unnamed", language),
                "date": (draft.get("created_at") or "")[:10],
                "amount": payload.get("amount"),
                "currency": payload.get("currency") or "ARS",
                "status_label": _draft_status_label(draft, language),
                "status_tone": _draft_status_tone(draft),
                "review_url": draft.get("id"),
            }
        )

    return {
        "date_label": _format_today_label(language, today),
        "kpis": {
            "income_today": income_today,
            "income_change": _pct_change(income_today, income_yesterday),
            "expense_today": expense_today,
            "expense_change": _pct_change(expense_today, expense_yesterday),
            "net_today": net_today,
            "net_change": _pct_change(net_today, net_yesterday),
            "pending_amount": pending_amount,
            "pending_count": len(pending_drafts),
        },
        "evolution": {
            "labels": evolution_labels,
            "values": evolution_values,
        },
        "balances": {
            "ARS": balances.get("ARS", 0.0),
            "USD": balances.get("USD", 0.0),
            "period_start": period_start,
            "period_end": period_end,
            "period_change": period_change,
        },
        "recent_receipts": receipt_rows,
        "recent_movements": movement_rows,
        "activity": _activity_feed(drafts, language),
        "active_step": 1,
    }
