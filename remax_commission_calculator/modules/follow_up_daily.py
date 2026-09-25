"""One grouped CRM follow-up push per agent, at the hour they chose."""

from __future__ import annotations

import logging
from datetime import timedelta

from modules.contact_follow_up import build_daily_follow_up_list
from modules.database.agent_tasks_repository import (
    STATUS_COMPLETED,
    STATUS_PENDING,
    list_agent_tasks,
)
from modules.database.agents_repository import get_agents
from modules.database.contacts_repository import list_contacts
from modules.database.organizations_repository import get_organizations
from modules.database.user_notification_preferences_repository import (
    get_follow_up_notification_settings,
    normalize_follow_up_digest_time,
)
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.organization_time import (
    local_date_bounds_utc,
    now_utc,
    organization_timezone,
    parse_utc_iso,
    to_local,
)


logger = logging.getLogger(__name__)

DIGEST_KIND = "crm_daily_follow_up"
INDIVIDUAL_KIND = "crm_follow_up_due"
DIGEST_WINDOW_MINUTES = 90
CONTACT_SCAN_LIMIT = 1000


def digest_window_open(now, tz, time_text):
    """True from the chosen local time until 90 minutes later."""
    local = to_local(now, tz)
    if local is None:
        return False
    normalized = normalize_follow_up_digest_time(time_text)
    hour, minute = (int(part) for part in normalized.split(":"))
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return scheduled <= local < scheduled + timedelta(minutes=DIGEST_WINDOW_MINUTES)


def _org_language(organization_id):
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )

    settings = get_organization_settings(organization_id) or {}
    return settings.get("default_language") or "es"


def _digest_copy(items, language):
    count = len(items)
    title_key = (
        "followup_digest_title_one" if count == 1 else "followup_digest_title"
    )
    names = [item["name"] for item in items if item.get("name")]
    shown = names[:3]
    label = ", ".join(shown)
    if len(names) > 3:
        label = f"{label}…"
    return (
        translate(title_key, language, count=count),
        translate("followup_digest_body", language, names=label),
    )


def collect_agent_follow_ups(organization_id, agent_id, *, now=None):
    """The same list the morning push is built from."""
    instant = now or now_utc()
    tz = organization_timezone(organization_id)
    local = to_local(instant, tz)
    end = None
    if local is not None:
        _start, end = local_date_bounds_utc(local.date(), tz)
    contacts = list_contacts(
        organization_id,
        agent_id=agent_id,
        limit=CONTACT_SCAN_LIMIT,
    )
    pending = []
    visits = []
    if end:
        pending = list_agent_tasks(
            organization_id,
            agent_id=agent_id,
            statuses=(STATUS_PENDING,),
            due_to=end,
            limit=300,
        )
        visits = list_agent_tasks(
            organization_id,
            agent_id=agent_id,
            statuses=(STATUS_COMPLETED,),
            task_type="visit",
            order="desc",
            limit=80,
        )
    return build_daily_follow_up_list(
        contacts,
        now=instant,
        tasks=pending,
        visits=visits,
        day_end=parse_utc_iso(end) if end else None,
    )


def dispatch_daily_follow_ups(organization_id, *, now=None):
    instant = now or now_utc()
    tz = organization_timezone(organization_id)
    language = _org_language(organization_id)
    local = to_local(instant, tz)
    day = local.date().isoformat() if local else ""
    summary = {
        "organization_id": organization_id,
        "agents": 0,
        "created": 0,
        "individuals": 0,
        "skipped_window": 0,
        "skipped_empty": 0,
        "skipped_user": 0,
    }

    for agent in get_agents(organization_id):
        agent_id = agent.get("id")
        if not agent_id:
            continue
        summary["agents"] += 1
        user = get_user_by_agent_id(agent_id, organization_id)
        if user is None:
            summary["skipped_user"] += 1
            continue
        settings = get_follow_up_notification_settings(
            organization_id, user["id"]
        )
        if not digest_window_open(
            instant, tz, settings["follow_up_digest_time"]
        ):
            summary["skipped_window"] += 1
            continue

        items = collect_agent_follow_ups(
            organization_id,
            agent_id,
            now=instant,
        )
        if not items:
            summary["skipped_empty"] += 1
            continue

        title, body = _digest_copy(items, language)
        from modules.notifications.events import emit_event

        digest = emit_event(
            "crm.daily_follow_up",
            {
                "organization_id": organization_id,
                "user_id": user["id"],
                "agent_id": agent_id,
                "type": DIGEST_KIND,
                "title": title,
                "body": body,
                "url": "/contacts/follow-ups",
                "event_key": f"crm_daily_follow_up:{agent_id}:{day}",
                "entity_type": "follow_up_digest",
                "entity_id": 0,
                "priority": "important",
                "metadata": {
                    "count": len(items),
                    "contact_ids": [item["contact_id"] for item in items],
                    "reasons": [item["reason"] for item in items],
                },
            },
        )
        if digest.get("created"):
            summary["created"] += 1

        if not settings["follow_up_individual_alerts"]:
            continue
        for item in items:
            if not item.get("individual"):
                continue
            reason = translate(item["reason_key"], language)
            individual = emit_event(
                "crm.follow_up_due",
                {
                    "organization_id": organization_id,
                    "user_id": user["id"],
                    "agent_id": agent_id,
                    "type": INDIVIDUAL_KIND,
                    "title": translate(
                        "followup_individual_title",
                        language,
                        name=item["name"],
                    ),
                    "body": translate(
                        "followup_individual_body",
                        language,
                        reason=reason,
                    ),
                    "url": f"/contacts/{int(item['contact_id'])}",
                    "event_key": (
                        f"crm_follow_up_due:{item['contact_id']}:{day}"
                    ),
                    "entity_type": "contact",
                    "entity_id": item["contact_id"],
                    "priority": (
                        "urgent"
                        if item["reason"] == "very_overdue"
                        else "important"
                    ),
                    "metadata": {
                        "contact_id": item["contact_id"],
                        "reason": item["reason"],
                    },
                },
            )
            if individual.get("created"):
                summary["individuals"] += 1
    return summary


def dispatch_daily_follow_ups_all(*, now=None):
    instant = now or now_utc()
    results = []
    for organization in get_organizations():
        if not organization.get("is_active", True):
            continue
        try:
            results.append(
                dispatch_daily_follow_ups(organization["id"], now=instant)
            )
        except Exception:
            logger.warning(
                "follow_up_daily_org_failed organization_id=%s",
                organization.get("id"),
                exc_info=True,
            )
    return results
