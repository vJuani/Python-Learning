"""Agent productivity metrics and goals. Counts come from existing tables."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from modules.agent_tasks import complete_task, count_overdue_tasks, create_task
from modules.database.agent_tasks_repository import list_visits_missing_outcome
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
GAUGE_METRICS = (
    "contacts_called",
    "followups_completed",
    "visits_completed",
    "meetings_completed",
    "acms_created",
    "tasks_completed",
)
LOG_CHANNELS = ("call", "meeting", "whatsapp", "visit", "email")
LOG_PURPOSES = (
    "follow_up",
    "prospecting",
    "valuation",
    "negotiation",
    "capture",
)
CHANNEL_TO_TASK = {
    "call": "call",
    "meeting": "meeting",
    "visit": "visit",
    "follow_up": "follow_up",
    "whatsapp": "other",
    "email": "other",
}
MONTHS_ES = (
    "",
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
WEEKDAYS_ES = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
WEEKDAYS_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
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


def _week_series(organization_id, agent_id, tz, now, language="es"):
    today = _local_today(tz, now)
    names = WEEKDAYS_ES if language == "es" else WEEKDAYS_EN
    points = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        bounds = period_bounds("daily", tz, now=now, on_date=day)
        activity = collect_activity(organization_id, agent_id, bounds)
        total = (
            int(activity["contacts_called"])
            + int(activity["followups_completed"])
            + int(activity["visits_completed"])
            + int(activity["meetings_completed"])
        )
        points.append(
            {
                "date": day.isoformat(),
                "label": names[day.weekday()],
                "total": total,
                "contacts_called": int(activity["contacts_called"]),
                "followups_completed": int(activity["followups_completed"]),
            }
        )
    peak = max((item["total"] for item in points), default=0) or 1
    for item in points:
        item["bar_h"] = int(round(item["total"] / peak * 100))
        item["call_h"] = int(round(item["contacts_called"] / peak * 100))
        item["follow_h"] = int(round(item["followups_completed"] / peak * 100))
    return points


def _week_delta(organization_id, agent_id, tz, now, language):
    today = _local_today(tz, now)
    this_start = today - timedelta(days=today.weekday())
    prev_start = this_start - timedelta(days=7)
    this_week = collect_activity(
        organization_id, agent_id, period_bounds("weekly", tz, now=now, on_date=this_start)
    )
    last_week = collect_activity(
        organization_id, agent_id, period_bounds("weekly", tz, now=now, on_date=prev_start)
    )
    this_total = int(this_week["tasks_completed"])
    last_total = int(last_week["tasks_completed"])
    if last_total <= 0:
        return None
    pct = int(round((this_total - last_total) / last_total * 100))
    key = "prod_week_up" if pct >= 0 else "prod_week_down"
    return {
        "percent": abs(pct),
        "up": pct >= 0,
        "label": translate(key, language=language, n=abs(pct)),
    }


def _date_label(day, language, *, is_today):
    if language == "es":
        prefix = "Hoy, " if is_today else ""
        return f"{prefix}{day.day} de {MONTHS_ES[day.month]} de {day.year}"
    prefix = "Today, " if is_today else ""
    return f"{prefix}{day.strftime('%B %d, %Y')}"


def _gauges(activity, goals, period, language):
    period_goals = [goal for goal in goals if goal["period_type"] == period]
    rows = []
    for key in GAUGE_METRICS:
        match = next((goal for goal in period_goals if goal["metric_key"] == key), None)
        if match is None:
            match = next((goal for goal in goals if goal["metric_key"] == key), None)
        current = _as_decimal(activity.get(key))
        target = _as_decimal(match["target_value"]) if match else Decimal("0")
        pct = progress_ratio(current, target) if target > 0 else Decimal("0")
        rows.append(
            {
                "metric_key": key,
                "label": translate(f"prod_metric_{key}", language=language),
                "current": int(current),
                "target": int(target) if target > 0 else None,
                "fraction": (
                    f"{int(current)}/{int(target)}" if target > 0 else str(int(current))
                ),
                "percent": pct,
                "bar_percent": min(int(pct), 100) if target > 0 else 0,
            }
        )
    return rows


def _activity_log(organization_id, agent_id, bounds, tz, language):
    tasks = list_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=["completed"],
        limit=80,
    )
    start = bounds["start_utc"]
    end = bounds["end_utc"]
    rows = []
    for task in tasks:
        completed = task.get("completed_at") or ""
        if not (start <= completed < end):
            continue
        local = to_local(completed, tz)
        try:
            outcome = json.loads(task.get("outcome_json") or "{}")
        except (TypeError, ValueError):
            outcome = {}
        if not isinstance(outcome, dict):
            outcome = {}
        purpose = (
            outcome.get("purpose")
            or (task.get("description") or "").strip()
            or "—"
        )
        result = (
            outcome.get("result")
            or outcome.get("interest")
            or translate("agent_task_status_completed", language=language)
        )
        rows.append(
            {
                "id": task["id"],
                "time": local.strftime("%H:%M") if local else "",
                "contact": task.get("contact_name") or "—",
                "channel": translate(
                    f"agent_task_type_{task['task_type']}", language=language
                ),
                "channel_key": task["task_type"],
                "purpose": purpose,
                "result": result,
            }
        )
    rows.sort(key=lambda item: item["time"] or "99:99")
    return rows


def _parse_contact_name(text):
    match = re.search(
        r"\b(?:con|a)\s+([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'-]+)",
        text or "",
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def build_proposal(channel, *, contact_name="", purpose="", language="es"):
    if channel not in LOG_CHANNELS and channel != "follow_up":
        return None
    purpose = purpose if purpose in LOG_PURPOSES else ""
    contact_name = (contact_name or "").strip()
    return {
        "channel": channel,
        "task_type": CHANNEL_TO_TASK.get(channel, "other"),
        "contact_name": contact_name,
        "purpose": purpose,
        "title": (
            f"{translate(f'prod_channel_{channel}', language=language)}"
            + (f" · {contact_name}" if contact_name else "")
        ),
        "purpose_label": (
            translate(f"prod_purpose_{purpose}", language=language) if purpose else ""
        ),
    }


def propose_logged_activity(text, *, channels=None, purposes=None, language="es"):
    """Map the agent's own words/pills to real task types. No invented contacts."""
    text = (text or "").strip()
    selected = [item for item in (channels or []) if item in LOG_CHANNELS]
    purpose_list = [item for item in (purposes or []) if item in LOG_PURPOSES]
    purpose = purpose_list[0] if purpose_list else ""
    contact = _parse_contact_name(text)
    if not selected and text:
        folded = text.lower()
        if any(word in folded for word in ("llam", "habl")):
            selected.append("call")
        if any(word in folded for word in ("reun", "junt", "café", "cafe")):
            selected.append("meeting")
        if "visita" in folded:
            selected.append("visit")
        if any(word in folded for word in ("whatsapp", "wsp")):
            selected.append("whatsapp")
        if any(word in folded for word in ("email", "mail")):
            selected.append("email")
        if "seguim" in folded and "follow_up" not in purpose_list:
            purpose = "follow_up"
    proposals = []
    for index, channel in enumerate(selected):
        item_purpose = purpose_list[index] if index < len(purpose_list) else purpose
        item = build_proposal(
            channel, contact_name=contact, purpose=item_purpose, language=language
        )
        if item:
            proposals.append(item)
    return proposals


def enrich_logged_contacts(organization_id, agent_id, proposals, *, language="es"):
    from modules.contacts import match_contacts

    enriched = []
    for item in proposals or []:
        row = dict(item)
        name = (row.get("contact_name") or "").strip()
        if not name:
            row["contact_status"] = "none"
            enriched.append(row)
            continue
        matched = match_contacts(
            organization_id,
            agent_id,
            name,
            language=language,
        )
        if matched.get("status") == "single" and matched.get("contact"):
            row["contact_id"] = matched["contact"]["id"]
            row["contact_name"] = matched["contact"].get("name") or name
            row["contact_status"] = "matched"
        elif matched.get("status") == "ambiguous":
            row["contact_status"] = "ambiguous"
            row["contact_candidates"] = [
                {"id": item.get("id"), "name": item.get("name")}
                for item in (matched.get("candidates") or [])[:5]
            ]
        else:
            row["contact_status"] = "missing"
        enriched.append(row)
    return enriched


def edit_logged_activity(proposals, index, *, channel, contact_name, purpose, language="es"):
    updated = list(proposals or [])
    if index < 0 or index >= len(updated):
        return updated
    item = build_proposal(
        channel or updated[index].get("channel"),
        contact_name=contact_name if contact_name is not None else updated[index].get("contact_name"),
        purpose=purpose if purpose is not None else updated[index].get("purpose"),
        language=language,
    )
    if item:
        updated[index] = item
    return updated


def confirm_logged_activity(
    organization_id,
    *,
    user,
    proposals,
    language="es",
    now=None,
):
    organization_id = require_organization_id(organization_id)
    user = require_productivity_agent(user, organization_id)
    tz = organization_timezone(organization_id)
    instant = now or now_utc()
    local = instant.astimezone(tz)
    created = []
    for item in proposals or []:
        if item.get("create_contact") and item.get("contact_name") and not item.get("contact_id"):
            from modules.contacts import create_agent_contact

            created_contact = create_agent_contact(
                organization_id,
                user["agent_id"],
                {
                    "name": item.get("contact_name"),
                    "status": "lead",
                    "source": "other",
                    "source_type": "other",
                },
            )
            item = dict(item)
            item["contact_id"] = created_contact["id"]
        task_type = CHANNEL_TO_TASK.get(item.get("channel"))
        if task_type not in ("call", "meeting", "visit", "follow_up", "other"):
            continue
        task = create_task(
            organization_id,
            user["agent_id"],
            {
                "title": item.get("title") or translate(
                    f"prod_channel_{item.get('channel')}", language=language
                ),
                "task_type": task_type,
                "priority": "normal",
                "due_date": local.date().isoformat(),
                "due_time": local.strftime("%H:%M"),
                "description": item.get("purpose_label") or item.get("purpose") or "",
                "contact_name": item.get("contact_name") or "",
                "contact_id": item.get("contact_id"),
            },
            created_by_user_id=user.get("id"),
        )
        complete_task(
            organization_id,
            task["id"],
            agent_id=user["agent_id"],
            actor_user_id=user.get("id"),
        )
        created.append(task)
    return created


def build_productivity_view(
    organization_id,
    *,
    user,
    language="es",
    period="daily",
    now=None,
    on_date=None,
    proposals=None,
):
    organization_id = require_organization_id(organization_id)
    user = require_productivity_agent(user, organization_id)
    agent_id = user["agent_id"]
    language = language if language in ("es", "en") else "es"
    period = period if period in PERIOD_TYPES else "daily"
    instant = now or now_utc()
    tz = organization_timezone(organization_id)
    today = _local_today(tz, instant)
    focus = on_date or today
    bounds = period_bounds(period, tz, now=instant, on_date=focus)
    activity = collect_activity(organization_id, agent_id, bounds)
    goals = list_agent_goals(organization_id, agent_id, active_only=True)
    period_goals = [goal for goal in goals if goal["period_type"] == period]
    rows = [_progress_row(goal, activity, language) for goal in period_goals]
    overdue_count = count_overdue_tasks(organization_id, agent_id=agent_id, now=instant)
    next_visit = _next_visit_today(organization_id, agent_id, tz, today)
    daily_bounds = period_bounds("daily", tz, now=instant, on_date=focus if period == "daily" else today)
    daily_activity = activity if period == "daily" else collect_activity(
        organization_id, agent_id, daily_bounds
    )
    daily_rows = [
        _progress_row(goal, daily_activity, language)
        for goal in goals
        if goal["period_type"] == "daily"
    ]
    gaps = _gaps_for_period(daily_rows, overdue_count, next_visit, language)
    missing_visits = list_visits_missing_outcome(
        organization_id, agent_id=agent_id, limit=5
    )
    if missing_visits:
        gaps.append(
            translate("prod_gap_visit_confirm", language=language, n=len(missing_visits))
        )
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
    step = {"daily": 1, "weekly": 7, "monthly": 31}[period]
    if period == "monthly":
        prev_day = (focus.replace(day=1) - timedelta(days=1)).replace(day=1)
        if focus.month == 12:
            next_day = focus.replace(year=focus.year + 1, month=1, day=1)
        else:
            next_day = focus.replace(month=focus.month + 1, day=1)
    else:
        prev_day = focus - timedelta(days=step)
        next_day = focus + timedelta(days=step)
    return {
        "period": period,
        "bounds": bounds,
        "timezone": str(tz),
        "focus_date": focus.isoformat(),
        "is_today": focus == today,
        "date_label": _date_label(focus, language, is_today=focus == today),
        "prev_date": prev_day.isoformat(),
        "next_date": next_day.isoformat() if next_day <= today else "",
        "has_goals": bool(goals),
        "goals": goals,
        "rows": rows,
        "gauges": _gauges(activity, goals, period, language),
        "activity": activity,
        "activity_log": _activity_log(organization_id, agent_id, bounds, tz, language),
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
        "proposals": proposals or [],
        "streak": _streak(
            organization_id,
            agent_id,
            [goal for goal in goals if goal["period_type"] == "daily"],
            tz,
            instant,
            language,
        ),
        "week_series": _week_series(organization_id, agent_id, tz, instant, language),
        "week_delta": _week_delta(organization_id, agent_id, tz, instant, language),
        "unfollowed_contacts": {
            "available": False,
            "reason": "prod_contacts_followup_hook",
        },
        "sources": METRIC_SOURCES,
        "supported_metrics": SUPPORTED_METRICS,
        "log_channels": LOG_CHANNELS,
        "log_purposes": LOG_PURPOSES,
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
