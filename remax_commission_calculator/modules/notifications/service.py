"""Single write path for in-app notifications and Web Push."""

from __future__ import annotations

import logging

from modules.database.notifications_repository import (
    find_notification_by_event_key,
    insert_notification,
)
from modules.database.user_notification_preferences_repository import (
    is_push_category_enabled,
)
from modules.database.users_repository import get_user_by_agent_id
from modules.notifications.catalog import (
    get_type,
    pref_key_for_type,
    resolve_priority,
    resolve_url,
)
from modules.web_push import is_safe_internal_url, safe_internal_url


logger = logging.getLogger(__name__)


def _empty_push():
    return {
        "push_targets_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "deactivated_count": 0,
    }


def _resolve_user_id(user_id, agent_id, organization_id):
    if user_id is not None:
        return user_id
    if agent_id is None or organization_id is None:
        return None
    user = get_user_by_agent_id(agent_id, organization_id)
    return None if user is None else user.get("id")


def _fanout_push(organization_id, user_id, payload):
    """Use the re-export so existing tests can still patch send_user_pushes."""
    from modules.notifications_service import send_user_pushes

    return send_user_pushes(organization_id, user_id, payload)


def notify_user(
    user_id,
    organization_id,
    type,
    title,
    body="",
    url=None,
    event_key=None,
    metadata=None,
    *,
    push=True,
    internal=True,
    priority=None,
    entity_type=None,
    entity_id=None,
    actor_user_id=None,
    agent_id=None,
    minutes_until=None,
):
    """
    Persist an in-app notification and fan out Web Push.

    ``event_key`` is the idempotency token. A repeated key never creates
    a second row and never sends a second push. Push failures never
    raise to the caller.
    """
    empty_push = _empty_push()
    resolved_user_id = _resolve_user_id(user_id, agent_id, organization_id)
    spec = get_type(type)
    resolved_priority = resolve_priority(
        type,
        minutes_until=minutes_until,
        priority=priority,
    )
    if resolved_user_id is None or organization_id is None:
        return {
            "notification_id": None,
            "created": False,
            "pushed": False,
            "deduped": False,
            "priority": resolved_priority,
            "push": empty_push,
        }

    def deduped(existing_id):
        logger.info(
            "notification_dispatch type=%s organization_id=%s user_id=%s "
            "event_key=%s push_targets_count=0 sent_count=0 failed_count=0 "
            "deduped=1",
            type,
            organization_id,
            resolved_user_id,
            event_key,
        )
        return {
            "notification_id": existing_id,
            "created": False,
            "pushed": False,
            "deduped": True,
            "priority": resolved_priority,
            "push": empty_push,
        }

    if event_key:
        existing = find_notification_by_event_key(
            organization_id,
            event_key,
            user_id=resolved_user_id,
        )
        if existing is not None:
            return deduped(existing)

    internal_url = safe_internal_url(
        resolve_url(type, entity_id=entity_id, url=url)
    )
    if url and not is_safe_internal_url(url):
        logger.warning(
            "notification_dispatch rejected_external_url type=%s "
            "organization_id=%s user_id=%s event_key=%s",
            type,
            organization_id,
            resolved_user_id,
            event_key,
        )

    payload = dict(metadata or {})
    payload["title"] = title
    payload["body"] = body
    payload["url"] = internal_url
    payload["event_key"] = event_key
    payload["priority"] = resolved_priority
    payload["type"] = type

    notification_id = None
    created = False
    if internal:
        inserted = insert_notification(
            organization_id,
            resolved_user_id,
            type,
            entity_type or spec["entity_type"],
            entity_id if entity_id is not None else 0,
            payload=payload,
            actor_user_id=actor_user_id,
            event_key=event_key,
            priority=resolved_priority,
        )
        if not inserted["created"]:
            return deduped(inserted["id"])
        notification_id = inserted["id"]
        created = True

    pref_key = pref_key_for_type(type)
    push_allowed = (
        push
        and (created or not internal)
        and is_push_category_enabled(organization_id, resolved_user_id, pref_key)
    )
    push_stats = dict(empty_push)
    if push_allowed:
        try:
            push_stats = _fanout_push(
                organization_id,
                resolved_user_id,
                {
                    "title": title,
                    "body": body,
                    "url": (
                        f"/notifications/{int(notification_id)}/open"
                        if notification_id
                        else internal_url
                    ),
                    "type": type,
                    "priority": resolved_priority,
                    "notification_id": notification_id,
                    "tag": event_key or type,
                    "icon": "/static/icons/icon-192.png",
                    "badge": "/static/icons/icon-192.png",
                },
            )
        except Exception:
            logger.warning(
                "notification_dispatch push failed type=%s organization_id=%s "
                "user_id=%s event_key=%s",
                type,
                organization_id,
                resolved_user_id,
                event_key,
                exc_info=True,
            )
            push_stats = dict(empty_push)

    logger.info(
        "notification_dispatch type=%s organization_id=%s user_id=%s "
        "event_key=%s priority=%s push_targets_count=%s sent_count=%s "
        "failed_count=%s",
        type,
        organization_id,
        resolved_user_id,
        event_key,
        resolved_priority,
        push_stats.get("push_targets_count", 0),
        push_stats.get("sent_count", 0),
        push_stats.get("failed_count", 0),
    )
    return {
        "notification_id": notification_id,
        "created": created,
        "pushed": bool(push_allowed and (push_stats.get("sent_count") or 0)),
        "deduped": False,
        "priority": resolved_priority,
        "push": push_stats,
    }


def send_user_notification(
    user_id,
    organization_id,
    notification_type,
    title,
    body,
    url,
    metadata=None,
    *,
    event_key=None,
    entity_type=None,
    entity_id=None,
    actor_user_id=None,
    push=True,
    internal=True,
    priority=None,
    minutes_until=None,
):
    """Backward-compatible wrapper used by existing callers and tests."""
    return notify_user(
        user_id,
        organization_id,
        notification_type,
        title,
        body,
        url,
        event_key=event_key,
        metadata=metadata,
        push=push,
        internal=internal,
        priority=priority,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        minutes_until=minutes_until,
    )
