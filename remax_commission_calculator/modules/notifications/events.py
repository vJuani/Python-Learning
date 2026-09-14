"""Tiny in-process event bus. Modules emit; NotificationService delivers."""

from __future__ import annotations

import logging

from modules.notifications.catalog import event_type_name, get_type, resolve_url
from modules.notifications.service import notify_user


logger = logging.getLogger(__name__)


def emit_event(name, payload=None):
    """
    Map a domain event to ``notify_user``.

    Expected payload keys: organization_id, user_id or agent_id, title,
    body, url, event_key, entity_id, entity_type, metadata, actor_user_id,
    minutes_until, priority, type.
    Unknown event names still deliver if title + recipient are present.
    Failures never raise to the caller.
    """
    payload = dict(payload or {})
    type_name = payload.get("type") or event_type_name(name, payload.get("kind"))
    spec = get_type(type_name)
    try:
        return notify_user(
            payload.get("user_id"),
            payload.get("organization_id"),
            type_name,
            payload.get("title") or "",
            payload.get("body") or "",
            resolve_url(
                type_name,
                entity_id=payload.get("entity_id"),
                url=payload.get("url"),
            ),
            event_key=payload.get("event_key"),
            metadata=payload.get("metadata") or payload,
            push=payload.get("push", True),
            internal=payload.get("internal", True),
            priority=payload.get("priority"),
            entity_type=payload.get("entity_type") or spec["entity_type"],
            entity_id=payload.get("entity_id"),
            actor_user_id=payload.get("actor_user_id"),
            agent_id=payload.get("agent_id"),
            minutes_until=payload.get("minutes_until"),
        )
    except Exception:
        logger.warning(
            "notification_event_failed event=%s type=%s org=%s",
            name,
            type_name,
            payload.get("organization_id"),
            exc_info=True,
        )
        return {
            "notification_id": None,
            "created": False,
            "pushed": False,
            "deduped": False,
            "push": {
                "push_targets_count": 0,
                "sent_count": 0,
                "failed_count": 0,
                "deactivated_count": 0,
            },
        }
