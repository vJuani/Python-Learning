"""Staff/admin office announcements. Same-org agents only."""

from __future__ import annotations

import logging
import uuid

from modules.auth import ROLE_AGENT
from modules.database.agents_repository import get_agent_record
from modules.database.users_repository import get_user_by_agent_id, get_users
from modules.i18n import translate
from modules.notifications_service import send_user_notification
from modules.web_push import is_safe_internal_url, safe_internal_url


logger = logging.getLogger(__name__)

MAX_TITLE = 120
MAX_BODY = 500
KIND = "office_announcement"


class OfficeAnnouncementError(Exception):
    def __init__(self, message_key):
        super().__init__(message_key)
        self.message_key = message_key


def _clean(value, max_length):
    return " ".join(str(value or "").split())[:max_length]


def resolve_announcement_recipients(organization_id, agent_ids=None):
    """Return agent users in this organization. Unknown/foreign ids are dropped."""
    if agent_ids is None:
        users = get_users(organization_id)
        return [
            user
            for user in users
            if user.get("role") == ROLE_AGENT
            and user.get("is_active")
            and user.get("agent_id") is not None
        ]

    seen = set()
    recipients = []
    for raw in agent_ids:
        try:
            agent_id = int(raw)
        except (TypeError, ValueError):
            continue
        if agent_id in seen:
            continue
        seen.add(agent_id)
        agent = get_agent_record(agent_id, organization_id)
        if agent is None:
            continue
        user = get_user_by_agent_id(agent_id, organization_id)
        if user is None or not user.get("is_active"):
            continue
        recipients.append(user)
    return recipients


def send_office_announcement(
    organization_id,
    sender_user_id,
    title,
    body,
    *,
    url=None,
    agent_ids=None,
    language="es",
):
    title = _clean(title, MAX_TITLE)
    body = _clean(body, MAX_BODY)
    if not title:
        raise OfficeAnnouncementError("office_announcement_err_title")
    if not body:
        raise OfficeAnnouncementError("office_announcement_err_body")

    raw_url = " ".join(str(url or "").split())
    if raw_url:
        if not is_safe_internal_url(raw_url):
            raise OfficeAnnouncementError("office_announcement_err_url")
        internal_url = safe_internal_url(raw_url)
    else:
        internal_url = "/notifications"

    recipients = resolve_announcement_recipients(
        organization_id,
        agent_ids=agent_ids,
    )
    if not recipients:
        raise OfficeAnnouncementError("office_announcement_err_recipients")

    announcement_id = uuid.uuid4().hex
    sent = []
    for user in recipients:
        event_key = f"office_announcement_{announcement_id}_{user['id']}"
        try:
            result = send_user_notification(
                user["id"],
                organization_id,
                KIND,
                title,
                body,
                internal_url,
                metadata={
                    "announcement_id": announcement_id,
                    "sender_user_id": sender_user_id,
                },
                event_key=event_key,
                entity_type="office_announcement",
                entity_id=0,
                actor_user_id=sender_user_id,
            )
        except Exception:
            logger.warning(
                "office_announcement_user_failed organization_id=%s user_id=%s",
                organization_id,
                user.get("id"),
                exc_info=True,
            )
            continue
        if result.get("created"):
            sent.append(result)

    logger.info(
        "office_announcement sender_user_id=%s organization_id=%s "
        "announcement_id=%s target_count=%s sent_count=%s",
        sender_user_id,
        organization_id,
        announcement_id,
        len(recipients),
        len(sent),
    )
    return {
        "announcement_id": announcement_id,
        "target_count": len(recipients),
        "sent_count": len(sent),
        "url": internal_url,
        "flash": translate(
            "office_announcement_sent",
            language,
            count=len(sent),
        ),
    }
