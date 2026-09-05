"""Agent Home V2 view-model. Reuses agenda, pending and contact recommendations."""

from __future__ import annotations

from modules.agent_tasks import build_agenda_summary, greeting_for_user
from modules.contacts import FILTER_NO_NEXT, list_contact_cards
from modules.database.agent_tasks_repository import (
    STATUS_PENDING,
    list_agent_tasks,
)
from modules.database.tenant import require_organization_id
from modules.arca.connections import arca_chip_for
from modules.google_calendar import calendar_chip_for
from modules.i18n import translate
from modules.organization_time import (
    local_date_bounds_utc,
    now_utc,
    organization_timezone,
)
from modules.pending_actions import (
    build_agent_pending_actions,
    summarize_pending_actions,
)


def _t(key, language, **kwargs):
    return translate(key, language=language, **kwargs)


def build_home_recommendations(
    organization_id,
    agent_id,
    *,
    language="es",
    limit=3,
):
    cards = list_contact_cards(
        organization_id,
        agent_id=agent_id,
        language=language,
    )
    items = []
    for card in cards:
        rec = card.get("recommendation")
        if not rec:
            continue
        name = card.get("name") or ""
        key = rec.get("key")
        if key == "no_next" or key == "visit_no_next":
            text = _t("contacts_recommend_no_next", language, name=name)
            href_kind = "schedule"
        elif key == "missing_outcome":
            text = _t("contacts_recommend_missing_outcome", language)
            href_kind = "followup"
        elif key == "overdue":
            text = _t("contacts_recommend_overdue", language)
            href_kind = "agenda"
        else:
            continue
        task = rec.get("task") or {}
        items.append(
            {
                "key": key,
                "text": text,
                "contact_id": card.get("id"),
                "contact_name": name,
                "task_id": task.get("id"),
                "href_kind": href_kind,
            }
        )
        if len(items) >= limit:
            break
    if len(items) < limit:
        extras = list_contact_cards(
            organization_id,
            agent_id=agent_id,
            contact_filter=FILTER_NO_NEXT,
            language=language,
        )
        seen = {item["contact_id"] for item in items}
        for card in extras:
            if card["id"] in seen:
                continue
            items.append(
                {
                    "key": "no_next",
                    "text": _t(
                        "contacts_recommend_no_next",
                        language,
                        name=card.get("name") or "",
                    ),
                    "contact_id": card.get("id"),
                    "contact_name": card.get("name"),
                    "task_id": None,
                    "href_kind": "schedule",
                }
            )
            if len(items) >= limit:
                break
    return items


def build_agent_home(
    organization_id,
    *,
    user,
    agent_id,
    language="es",
    now=None,
):
    organization_id = require_organization_id(organization_id)
    tz = organization_timezone(organization_id)
    now = now or now_utc()
    now_local = now.astimezone(tz)
    today = now_local.date()
    day_start, day_end = local_date_bounds_utc(today, tz)

    agenda = build_agenda_summary(
        organization_id,
        agent_id,
        language=language,
        now=now,
        limit=4,
    )
    today_tasks = list_agent_tasks(
        organization_id,
        agent_id=agent_id,
        statuses=(STATUS_PENDING,),
        due_from=day_start,
        due_to=day_end,
        limit=20,
    )
    visit_count = sum(1 for task in today_tasks if task.get("task_type") == "visit")
    call_count = sum(1 for task in today_tasks if task.get("task_type") == "call")

    pending_actions = build_agent_pending_actions(
        organization_id,
        agent_id,
        user_id=(user or {}).get("id"),
    )
    pending = summarize_pending_actions(pending_actions, language=language)

    if agenda.get("overdue_count"):
        subtitle_key = "jrh_home_sub_overdue"
    elif agenda.get("today_count"):
        subtitle_key = "jrh_home_sub_today"
    else:
        subtitle_key = "jrh_home_sub_clear"

    return {
        "greeting": greeting_for_user(user, now_local=now_local, language=language),
        "subtitle": _t(subtitle_key, language),
        "subtitle_key": subtitle_key,
        "today": {
            "visits": visit_count,
            "calls": call_count,
            "tasks": agenda.get("today_count") or 0,
            "pending": pending.get("total") or 0,
            "overdue": agenda.get("overdue_count") or 0,
        },
        "upcoming": agenda.get("tasks") or [],
        "recommendations": build_home_recommendations(
            organization_id,
            agent_id,
            language=language,
        ),
        "calendar": calendar_chip_for(
            organization_id,
            user,
            agent_id=agent_id,
            can_manage=True,
        ),
        "arca": arca_chip_for(organization_id, user),
        "quick_actions": (
            {
                "key": "search",
                "label_key": "jrh_quick_search",
                "endpoint": "contacts_index",
            },
            {
                "key": "agenda",
                "label_key": "jrh_quick_agenda",
                "endpoint": "agenda_compose",
            },
            {
                "key": "contacts",
                "label_key": "jrh_quick_contacts",
                "endpoint": "contacts_index",
            },
            {
                "key": "billing",
                "label_key": "jrh_quick_billing",
                "endpoint": "billing_list",
            },
        ),
    }
