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


def _format_day_label(day, language):
    months = _MONTHS_ES if language == "es" else _MONTHS_EN
    if language == "es":
        return f"{day.day} {months[day.month - 1][:3]}"
    return f"{months[day.month - 1][:3]} {day.day}"


def _format_today_label(language, today=None):
    today = today or date.today()
    months = _MONTHS_ES if language == "es" else _MONTHS_EN

    if language == "es":
        return f"{today.day} de {months[today.month - 1]} de {today.year}"

    return f"{months[today.month - 1]} {today.day}, {today.year}"


def _day_net_flow(organization_id, day_from, day_to, currency="ARS"):
    income = sum_movements_by_type(
        organization_id,
        currency=currency,
        movement_type=TYPE_INCOME,
        date_from=day_from,
        date_to=day_to,
    ) + sum_movements_by_type(
        organization_id,
        currency=currency,
        movement_type=TYPE_OPENING,
        date_from=day_from,
        date_to=day_to,
    )
    expense = sum_movements_by_type(
        organization_id,
        currency=currency,
        movement_type=TYPE_EXPENSE,
        date_from=day_from,
        date_to=day_to,
    )
    return income, expense, income - expense


def _day_totals(organization_id, day, currency="ARS"):
    iso = day.isoformat()
    income, expense, net = _day_net_flow(
        organization_id,
        iso,
        iso,
        currency=currency,
    )
    return income, expense, net


def _count_movements_for_day(organization_id, day, movement_type):
    iso = day.isoformat()
    movements = list_cash_movements(
        organization_id,
        movement_type=movement_type,
        date_from=iso,
        date_to=iso,
        limit=200,
    )
    return len(movements)


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
        confidence = _confidence_pct(draft.get("confidence"))
        status = draft.get("status") or ""

        if status == "confirmed":
            text = _t(
                "cash_ai_page_activity_registered",
                language,
                name=name,
            )
        elif draft.get("attachment_original_name"):
            text = _t(
                "cash_ai_page_activity_analyzed",
                language,
                name=name,
            )
        else:
            text = _t(
                "cash_ai_page_activity_extracted",
                language,
                count=7,
            )

        feed.append(
            {
                "text": text,
                "time": (draft.get("updated_at") or draft.get("created_at") or "")[
                    11:16
                ],
                "confidence": confidence,
                "tone": "success" if status == "confirmed" else "info",
            }
        )

    return feed


def _balance_evolution(organization_id, today, currency="ARS"):
    balances = get_balances(organization_id)
    current_balance = float(balances.get(currency, 0.0) or 0.0)
    labels = []
    values = []
    period_start = today - timedelta(days=6)

    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        labels.append(day.strftime("%d/%m"))

        if day >= today:
            values.append(round(current_balance, 2))
            continue

        next_day = (day + timedelta(days=1)).isoformat()
        today_iso = today.isoformat()
        _, _, net_after = _day_net_flow(
            organization_id,
            next_day,
            today_iso,
            currency=currency,
        )
        values.append(round(current_balance - net_after, 2))

    return labels, values, period_start


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

    income_today, expense_today, net_today = _day_totals(
        organization_id,
        today,
    )
    income_yesterday, expense_yesterday, net_yesterday = _day_totals(
        organization_id,
        yesterday,
    )

    income_count = _count_movements_for_day(
        organization_id,
        today,
        TYPE_INCOME,
    ) + _count_movements_for_day(
        organization_id,
        today,
        TYPE_OPENING,
    )
    expense_count = _count_movements_for_day(
        organization_id,
        today,
        TYPE_EXPENSE,
    )

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

    evolution_labels, evolution_values, period_start_day = _balance_evolution(
        organization_id,
        today,
    )
    balances = get_balances(organization_id)
    period_start_balance = (
        evolution_values[0] if evolution_values else balances.get("ARS", 0.0)
    )
    period_end_balance = balances.get("ARS", 0.0)
    period_change = _pct_change(period_end_balance, period_start_balance)
    period_change_amount = period_end_balance - period_start_balance

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
                "merchant": movement.get("merchant") or movement.get("description") or "—",
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
            }
        )

    return {
        "date_label": _format_today_label(language, today),
        "kpis": {
            "income_today": income_today,
            "income_change": _pct_change(income_today, income_yesterday),
            "income_count": income_count,
            "expense_today": expense_today,
            "expense_change": _pct_change(expense_today, expense_yesterday),
            "expense_count": expense_count,
            "net_today": net_today,
            "net_yesterday": net_yesterday,
            "net_change": _pct_change(net_today, net_yesterday),
            "pending_amount": pending_amount,
            "pending_count": len(pending_drafts),
            "pending_receipts": len(pending_drafts),
        },
        "evolution": {
            "labels": evolution_labels,
            "values": evolution_values,
        },
        "balances": {
            "ARS": balances.get("ARS", 0.0),
            "USD": balances.get("USD", 0.0),
            "period_start": period_start_balance,
            "period_end": period_end_balance,
            "period_start_label": _format_day_label(
                period_start_day,
                language,
            ),
            "period_end_label": _format_day_label(today, language),
            "period_change": period_change,
            "period_change_amount": period_change_amount,
        },
        "recent_receipts": receipt_rows,
        "recent_movements": movement_rows,
        "activity": _activity_feed(drafts, language),
    }
