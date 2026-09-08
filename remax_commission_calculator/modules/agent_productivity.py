"""Agent productivity metrics and goals. Counts come from existing tables."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from modules.agent_tasks import count_overdue_tasks
from modules.auth import ROLE_AGENT
from modules.database.agent_goals_repository import (
    deactivate_agent_goal,
    list_agent_goals,
    upsert_agent_goal,
)
from modules.database.agent_productivity_queries import (
    count_acms_created,
    count_active_operations,
    count_completed_tasks,
    count_new_contacts,
    count_properties_captured,
    list_closed_operation_dates,
    sum_invoices_by_currency,
    sum_movements_by_currency,
)
from modules.database.agent_tasks_repository import list_agent_tasks
from modules.database.tenant import require_organization_id
from modules.formatting import format_money
from modules.i18n import translate
from modules.organization_time import (
    local_date_bounds_utc,
    now_utc,
    organization_timezone,
    to_local,
)


class ProductivityError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


COUNT_METRICS = (
    "contacts_called",
    "followups_completed",
    "visits_completed",
    "meetings_completed",
    "tasks_completed",
    "new_contacts",
    "acms_created",
    "properties_captured",
    "operations_closed",
)
MONEY_METRICS = (
    "commissions_credited",
    "invoiced_amount",
    "collected_amount",
)
SUPPORTED_METRICS = COUNT_METRICS + MONEY_METRICS
PERIOD_TYPES = ("daily", "weekly", "monthly")
TASK_TYPE_BY_METRIC = {
    "contacts_called": "call",
    "followups_completed": "follow_up",
    "visits_completed": "visit",
    "meetings_completed": "meeting",
}
METRIC_SOURCES = {
    "contacts_called": "agent_tasks.task_type=call AND status=completed, counted on completed_at",
    "followups_completed": "agent_tasks.task_type=follow_up AND status=completed, counted on completed_at",
    "visits_completed": "agent_tasks.task_type=visit AND status=completed, counted on completed_at. Google overlay is not stored in agent_tasks.",
    "meetings_completed": "agent_tasks.task_type=meeting AND status=completed, counted on completed_at",
    "tasks_completed": "agent_tasks.status=completed, counted on completed_at, all task types",
    "new_contacts": "contacts.created_at for the agent",
    "acms_created": "property_acms.created_at, excluding archived",
    "properties_captured": "properties.status=approved using reviewed_at or submitted_at",
    "operations_closed": "operations.status=approved AND was_invoiced=yes, period uses operation_date",
    "commissions_credited": "agent_account_movements.movement_type=commission status=confirmed, grouped by currency",
    "invoiced_amount": "invoices.total_amount with cancelled_at IS NULL, grouped by currency",
    "collected_amount": "agent_account_movements.movement_type=payment status=confirmed, grouped by currency",
}


def require_productivity_agent(user, organization_id=None):
    if not user or user.get("role") != ROLE_AGENT or not user.get("agent_id"):
        raise ProductivityError("prod_err_agent_only", 403)
    user_org = user.get("organization_id")
    if organization_id is not None and user_org not in (None, ""):
        if int(user_org) != int(organization_id):
            raise ProductivityError("prod_err_agent_only", 403)
    return user


def _as_decimal(value):
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _local_today(tz, now=None):
    instant = now or now_utc()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=now_utc().tzinfo)
    return instant.astimezone(tz).date()


def period_bounds(period_type, tz, *, now=None, on_date=None):
    today = on_date or _local_today(tz, now)
    if period_type == "daily":
        start = today
        days = 1
    elif period_type == "weekly":
        start = today - timedelta(days=today.weekday())
        days = 7
    elif period_type == "monthly":
        start = today.replace(day=1)
        if start.month == 12:
            nxt = start.replace(year=start.year + 1, month=1)
        else:
            nxt = start.replace(month=start.month + 1)
        days = (nxt - start).days
    else:
        raise ProductivityError("prod_err_period")
    start_utc, end_utc = local_date_bounds_utc(start, tz, days=days)
    return {
        "period_type": period_type,
        "start_local": start.isoformat(),
        "end_local": (start + timedelta(days=days - 1)).isoformat(),
        "start_utc": start_utc,
        "end_utc": end_utc,
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=days - 1)).isoformat(),
    }


def _parse_operation_date(value):
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _count_closed_operations(organization_id, agent_id, bounds):
    start = date.fromisoformat(bounds["start_date"])
    end = date.fromisoformat(bounds["end_date"])
    total = 0
    for raw in list_closed_operation_dates(organization_id, agent_id):
        parsed = _parse_operation_date(raw)
        if parsed and start <= parsed <= end:
            total += 1
    return total


def collect_activity(organization_id, agent_id, bounds):
    start = bounds["start_utc"]
    end = bounds["end_utc"]
    return {
        "contacts_called": count_completed_tasks(
            organization_id, agent_id, completed_from=start, completed_to=end, task_type="call"
        ),
        "followups_completed": count_completed_tasks(
            organization_id, agent_id, completed_from=start, completed_to=end, task_type="follow_up"
        ),
        "visits_completed": count_completed_tasks(
            organization_id, agent_id, completed_from=start, completed_to=end, task_type="visit"
        ),
        "meetings_completed": count_completed_tasks(
            organization_id, agent_id, completed_from=start, completed_to=end, task_type="meeting"
        ),
        "tasks_completed": count_completed_tasks(
            organization_id, agent_id, completed_from=start, completed_to=end
        ),
        "new_contacts": count_new_contacts(
            organization_id, agent_id, created_from=start, created_to=end
        ),
        "acms_created": count_acms_created(
            organization_id, agent_id, created_from=start, created_to=end
        ),
        "properties_captured": count_properties_captured(
            organization_id, agent_id, captured_from=start, captured_to=end
        ),
        "operations_closed": _count_closed_operations(organization_id, agent_id, bounds),
        "operations_active": count_active_operations(organization_id, agent_id),
        "commissions_credited": sum_movements_by_currency(
            organization_id,
            agent_id,
            movement_type="commission",
            date_from=bounds["start_date"],
            date_to=bounds["end_date"],
        ),
        "invoiced_amount": sum_invoices_by_currency(
            organization_id, agent_id, created_from=start, created_to=end
        ),
        "collected_amount": sum_movements_by_currency(
            organization_id,
            agent_id,
            movement_type="payment",
            date_from=bounds["start_date"],
            date_to=bounds["end_date"],
        ),
    }


def metric_current(activity, metric_key, currency=None):
    value = activity.get(metric_key)
    if metric_key in MONEY_METRICS:
        amounts = value or {}
        if currency:
            return _as_decimal(amounts.get(currency))
        return amounts
    return _as_decimal(value)


def progress_ratio(current, target):
    target = _as_decimal(target)
    current = _as_decimal(current)
    if target <= 0:
        return Decimal("0")
    return (current / target * Decimal("100")).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )


def _progress_row(goal, activity, language):
    currency = goal.get("currency")
    current = metric_current(activity, goal["metric_key"], currency)
    if isinstance(current, dict):
        current = Decimal("0")
    target = _as_decimal(goal["target_value"])
    pct = progress_ratio(current, target)
    remaining = target - current
    if remaining < 0:
        remaining = Decimal("0")
    return {
        "goal": goal,
        "metric_key": goal["metric_key"],
        "period_type": goal["period_type"],
        "label": translate(f"prod_metric_{goal['metric_key']}", language=language),
        "current": current,
        "target": target,
        "remaining": remaining,
        "percent": pct,
        "bar_percent": min(int(pct), 100),
        "done": current >= target and target > 0,
        "currency": currency,
        "current_label": (
            format_money(current, currency=currency, language=language)
            if currency
            else str(int(current) if current == current.to_integral_value() else current)
        ),
        "target_label": (
            format_money(target, currency=currency, language=language)
            if currency
            else str(int(target) if target == target.to_integral_value() else target)
        ),
    }


def _money_lines(amounts, language):
    if not amounts:
        return []
    lines = []
    for currency in ("USD", "ARS"):
        if currency in amounts:
            lines.append(format_money(amounts[currency], currency=currency, language=language))
    for currency, amount in amounts.items():
        if currency not in ("USD", "ARS"):
            lines.append(format_money(amount, currency=currency, language=language))
    return lines


def _next_visit_today(organization_id, agent_id, tz, today):
    start_utc, end_utc = local_date_bounds_utc(today, tz, days=1)
    tasks = list_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=["pending"],
        due_from=start_utc,
        due_to=end_utc,
    )
    visits = [item for item in tasks if item.get("task_type") == "visit"]
    visits.sort(key=lambda item: item.get("due_at") or "")
    if not visits:
        return None
    local = to_local(visits[0].get("due_at"), tz)
    return {
        "id": visits[0]["id"],
        "title": visits[0].get("title") or "",
        "time": local.strftime("%H:%M") if local else "",
    }


def _gaps_for_period(rows, overdue_count, next_visit, language):
    gaps = []
    for row in rows:
        if row["done"] or row["remaining"] <= 0:
            continue
        amount = row["remaining"]
        amount_label = (
            row["current_label"]
            if row["currency"]
            else str(int(amount) if amount == amount.to_integral_value() else amount)
        )
        if not row["currency"]:
            amount_label = str(int(amount) if amount == amount.to_integral_value() else amount)
        gaps.append(
            translate(
                "prod_gap_metric",
                language=language,
                n=amount_label,
                metric=row["label"].lower(),
            )
        )
    if overdue_count:
        gaps.append(
            translate("prod_gap_overdue", language=language, n=overdue_count)
        )
    if next_visit and next_visit.get("time"):
        gaps.append(
            translate("prod_gap_visit", language=language, time=next_visit["time"])
        )
    return gaps


def _streak(organization_id, agent_id, daily_goals, tz, now, language):
    if not daily_goals:
        return None
    today = _local_today(tz, now)
    streak = 0
    for offset in range(0, 14):
        day = today - timedelta(days=offset)
        bounds = period_bounds("daily", tz, now=now, on_date=day)
        activity = collect_activity(organization_id, agent_id, bounds)
        met = True
        for goal in daily_goals:
            current = metric_current(activity, goal["metric_key"], goal.get("currency"))
            if isinstance(current, dict):
                current = Decimal("0")
            if current < _as_decimal(goal["target_value"]):
                met = False
                break
        if not met:
            if offset == 0:
                continue
            break
        streak += 1
    if streak < 2:
        return None
    return {
        "days": streak,
        "label": translate("prod_streak_calls", language=language, n=streak),
    }


def _week_series(organization_id, agent_id, tz, now):
    today = _local_today(tz, now)
    points = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        bounds = period_bounds("daily", tz, now=now, on_date=day)
        activity = collect_activity(organization_id, agent_id, bounds)
        points.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d/%m"),
                "contacts_called": int(activity["contacts_called"]),
                "followups_completed": int(activity["followups_completed"]),
            }
        )
    peak = max(
        (item["contacts_called"] + item["followups_completed"] for item in points),
        default=0,
    ) or 1
    for item in points:
        item["call_h"] = int(round(item["contacts_called"] / peak * 100))
        item["follow_h"] = int(round(item["followups_completed"] / peak * 100))
    return points


def build_productivity_view(
    organization_id,
    *,
    user,
    language="es",
    period="daily",
    now=None,
):
    organization_id = require_organization_id(organization_id)
    user = require_productivity_agent(user, organization_id)
    agent_id = user["agent_id"]
    language = language if language in ("es", "en") else "es"
    period = period if period in PERIOD_TYPES else "daily"
    instant = now or now_utc()
    tz = organization_timezone(organization_id)
    bounds = period_bounds(period, tz, now=instant)
    activity = collect_activity(organization_id, agent_id, bounds)
    goals = list_agent_goals(organization_id, agent_id, active_only=True)
    period_goals = [goal for goal in goals if goal["period_type"] == period]
    rows = [_progress_row(goal, activity, language) for goal in period_goals]
    overdue_count = count_overdue_tasks(organization_id, agent_id=agent_id, now=instant)
    today = _local_today(tz, instant)
    next_visit = _next_visit_today(organization_id, agent_id, tz, today)
    daily_bounds = period_bounds("daily", tz, now=instant)
    daily_activity = activity if period == "daily" else collect_activity(
        organization_id, agent_id, daily_bounds
    )
    daily_rows = [
        _progress_row(goal, daily_activity, language)
        for goal in goals
        if goal["period_type"] == "daily"
    ]
    gaps = _gaps_for_period(daily_rows, overdue_count, next_visit, language)
    suggestions = list(gaps)
    visits_week = [
        row
        for row in (
            _progress_row(goal, collect_activity(organization_id, agent_id, period_bounds("weekly", tz, now=instant)), language)
            if goal["period_type"] == "weekly" and goal["metric_key"] == "visits_completed"
            else None
            for goal in goals
        )
        if row
    ]
    for row in visits_week:
        if row["done"]:
            suggestions.append(
                translate("prod_tip_visits_done", language=language)
            )
    return {
        "period": period,
        "bounds": bounds,
        "timezone": str(tz),
        "has_goals": bool(goals),
        "goals": goals,
        "rows": rows,
        "activity": activity,
        "activity_labels": {
            key: translate(f"prod_metric_{key}", language=language)
            for key in COUNT_METRICS
        },
        "money": {
            "commissions": _money_lines(activity["commissions_credited"], language),
            "invoiced": _money_lines(activity["invoiced_amount"], language),
            "collected": _money_lines(activity["collected_amount"], language),
        },
        "operations_active": activity["operations_active"],
        "overdue_count": overdue_count,
        "next_visit": next_visit,
        "gaps": gaps,
        "suggestions": suggestions,
        "streak": _streak(
            organization_id,
            agent_id,
            [goal for goal in goals if goal["period_type"] == "daily"],
            tz,
            instant,
            language,
        ),
        "week_series": _week_series(organization_id, agent_id, tz, instant),
        "unfollowed_contacts": {
            "available": False,
            "reason": "prod_contacts_followup_hook",
        },
        "sources": METRIC_SOURCES,
        "supported_metrics": SUPPORTED_METRICS,
    }


def save_goals(organization_id, *, user, items):
    organization_id = require_organization_id(organization_id)
    user = require_productivity_agent(user, organization_id)
    saved = []
    for item in items or []:
        metric_key = (item.get("metric_key") or "").strip()
        period_type = (item.get("period_type") or "").strip()
        if metric_key not in SUPPORTED_METRICS or period_type not in PERIOD_TYPES:
            continue
        target = _as_decimal(item.get("target_value"))
        if target <= 0:
            continue
        currency = None
        if metric_key in MONEY_METRICS:
            currency = str(item.get("currency") or "USD").strip().upper()
            if currency not in ("USD", "ARS"):
                continue
        saved.append(
            upsert_agent_goal(
                organization_id,
                user["agent_id"],
                metric_key=metric_key,
                period_type=period_type,
                target_value=target,
                currency=currency,
            )
        )
    return saved


def disable_goal(organization_id, *, user, goal_id):
    organization_id = require_organization_id(organization_id)
    user = require_productivity_agent(user, organization_id)
    return deactivate_agent_goal(goal_id, organization_id, user["agent_id"])


def jrh_productivity_answer(
    organization_id,
    *,
    user,
    language="es",
    period="daily",
    now=None,
):
    view = build_productivity_view(
        organization_id,
        user=user,
        language=language,
        period=period,
        now=now,
    )
    activity = view["activity"]
    if view["gaps"]:
        message = translate(
            "prod_jrh_gaps",
            language=language,
            gaps=" ".join(view["gaps"]),
        )
    elif view["has_goals"]:
        message = translate("prod_jrh_on_track", language=language)
    else:
        message = translate(
            "prod_jrh_activity_only",
            language=language,
            calls=activity["contacts_called"],
            followups=activity["followups_completed"],
            visits=activity["visits_completed"],
        )
    return {
        "message": message,
        "view": view,
        "period": period,
    }
