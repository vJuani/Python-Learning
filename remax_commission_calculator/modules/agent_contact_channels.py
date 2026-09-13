"""Personal WhatsApp / Instagram channels for an agent. Not office branding."""

from __future__ import annotations

from modules.agent_branding import format_instagram_handle, format_whatsapp_display
from modules.database.agent_contact_channels_repository import (
    get_agent_contact_channels,
    upsert_agent_contact_channels,
)
from modules.database.agents_repository import (
    get_agent_record,
    update_agent_instagram_handle,
    update_agent_whatsapp_number,
)
from modules.database.users_repository import get_agent_login_user


class AgentContactError(Exception):
    def __init__(self, message_key):
        super().__init__(message_key)
        self.message_key = message_key


def _now_iso():
    from datetime import datetime

    return datetime.utcnow().replace(microsecond=0).isoformat()


def suggested_whatsapp_number(agent_id, organization_id):
    user = get_agent_login_user(agent_id, organization_id) or {}
    return format_whatsapp_display(user.get("phone")) or ""


def resolve_agent_contact_channels(agent_id, organization_id):
    agent = get_agent_record(agent_id, organization_id) or {}
    stored = get_agent_contact_channels(agent_id, organization_id)
    whatsapp = format_whatsapp_display(
        stored.get("whatsapp_number") or agent.get("whatsapp_number")
    )
    instagram = format_instagram_handle(
        stored.get("instagram_handle") or agent.get("instagram_handle")
    )
    whatsapp_enabled = bool(stored.get("whatsapp_enabled") and whatsapp)
    instagram_enabled = bool(stored.get("instagram_enabled") and instagram)
    return {
        **stored,
        "whatsapp_number": whatsapp or None,
        "instagram_handle": instagram or None,
        "whatsapp_connected": bool(whatsapp),
        "instagram_connected": bool(instagram),
        "whatsapp_enabled": whatsapp_enabled,
        "instagram_enabled": instagram_enabled,
        "whatsapp_publishable": whatsapp_enabled,
        "instagram_publishable": instagram_enabled,
        "suggested_whatsapp": suggested_whatsapp_number(agent_id, organization_id),
    }


def publishable_agent_contacts(agent_id, organization_id):
    channels = resolve_agent_contact_channels(agent_id, organization_id)
    return {
        "whatsapp": channels["whatsapp_number"] if channels["whatsapp_enabled"] else None,
        "whatsapp_enabled": channels["whatsapp_enabled"],
        "instagram": channels["instagram_handle"] if channels["instagram_enabled"] else None,
        "instagram_enabled": channels["instagram_enabled"],
    }


def link_agent_whatsapp(agent_id, organization_id, number, *, enable_marketing=False):
    display = format_whatsapp_display(number)
    if not display:
        raise AgentContactError("agent_contact_err_whatsapp")
    now = _now_iso()
    current = get_agent_contact_channels(agent_id, organization_id)
    upsert_agent_contact_channels(
        agent_id,
        organization_id,
        whatsapp_number=display,
        whatsapp_enabled=bool(enable_marketing),
        whatsapp_connected_at=current.get("whatsapp_connected_at") or now,
        instagram_handle=current.get("instagram_handle"),
        instagram_enabled=current.get("instagram_enabled"),
        instagram_connected_at=current.get("instagram_connected_at"),
    )
    update_agent_whatsapp_number(agent_id, organization_id, display)
    return resolve_agent_contact_channels(agent_id, organization_id)


def unlink_agent_whatsapp(agent_id, organization_id):
    current = get_agent_contact_channels(agent_id, organization_id)
    upsert_agent_contact_channels(
        agent_id,
        organization_id,
        whatsapp_number=None,
        whatsapp_enabled=False,
        whatsapp_connected_at=None,
        instagram_handle=current.get("instagram_handle"),
        instagram_enabled=current.get("instagram_enabled"),
        instagram_connected_at=current.get("instagram_connected_at"),
    )
    update_agent_whatsapp_number(agent_id, organization_id, None)
    return resolve_agent_contact_channels(agent_id, organization_id)


def set_whatsapp_marketing_enabled(agent_id, organization_id, enabled):
    current = resolve_agent_contact_channels(agent_id, organization_id)
    if enabled and not current.get("whatsapp_number"):
        raise AgentContactError("agent_contact_err_whatsapp_missing")
    stored = get_agent_contact_channels(agent_id, organization_id)
    upsert_agent_contact_channels(
        agent_id,
        organization_id,
        whatsapp_number=current.get("whatsapp_number"),
        whatsapp_enabled=bool(enabled),
        whatsapp_connected_at=stored.get("whatsapp_connected_at"),
        instagram_handle=stored.get("instagram_handle"),
        instagram_enabled=stored.get("instagram_enabled"),
        instagram_connected_at=stored.get("instagram_connected_at"),
    )
    return resolve_agent_contact_channels(agent_id, organization_id)


def link_agent_instagram(agent_id, organization_id, handle, *, enable_marketing=False):
    display = format_instagram_handle(handle)
    if not display:
        raise AgentContactError("agent_contact_err_instagram")
    now = _now_iso()
    current = get_agent_contact_channels(agent_id, organization_id)
    upsert_agent_contact_channels(
        agent_id,
        organization_id,
        whatsapp_number=current.get("whatsapp_number"),
        whatsapp_enabled=current.get("whatsapp_enabled"),
        whatsapp_connected_at=current.get("whatsapp_connected_at"),
        instagram_handle=display,
        instagram_enabled=bool(enable_marketing),
        instagram_connected_at=current.get("instagram_connected_at") or now,
    )
    update_agent_instagram_handle(agent_id, organization_id, display)
    return resolve_agent_contact_channels(agent_id, organization_id)


def unlink_agent_instagram(agent_id, organization_id):
    current = get_agent_contact_channels(agent_id, organization_id)
    upsert_agent_contact_channels(
        agent_id,
        organization_id,
        whatsapp_number=current.get("whatsapp_number"),
        whatsapp_enabled=current.get("whatsapp_enabled"),
        whatsapp_connected_at=current.get("whatsapp_connected_at"),
        instagram_handle=None,
        instagram_enabled=False,
        instagram_connected_at=None,
    )
    update_agent_instagram_handle(agent_id, organization_id, None)
    return resolve_agent_contact_channels(agent_id, organization_id)


def set_instagram_marketing_enabled(agent_id, organization_id, enabled):
    current = resolve_agent_contact_channels(agent_id, organization_id)
    if enabled and not current.get("instagram_handle"):
        raise AgentContactError("agent_contact_err_instagram_missing")
    stored = get_agent_contact_channels(agent_id, organization_id)
    upsert_agent_contact_channels(
        agent_id,
        organization_id,
        whatsapp_number=stored.get("whatsapp_number"),
        whatsapp_enabled=stored.get("whatsapp_enabled"),
        whatsapp_connected_at=stored.get("whatsapp_connected_at"),
        instagram_handle=current.get("instagram_handle"),
        instagram_enabled=bool(enabled),
        instagram_connected_at=stored.get("instagram_connected_at"),
    )
    return resolve_agent_contact_channels(agent_id, organization_id)
