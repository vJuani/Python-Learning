"""Contextual Property agent-card actions. Identity-based, never hardcoded names."""

from __future__ import annotations

import re

from modules.auth import ROLE_ADMIN, ROLE_AGENT


def _digits(value):
    return re.sub(r"\D+", "", str(value or ""))


def _same_agent(user, agent_id):
    if not user or agent_id in (None, ""):
        return False
    try:
        return (
            user.get("role") == ROLE_AGENT
            and int(user.get("agent_id") or 0) == int(agent_id)
        )
    except (TypeError, ValueError):
        return False


def get_property_agent_actions(current_user, property_row, *, is_guest=False, branding=None):
    """Return viewer-aware actions for the Property agent card."""
    agent_id = (property_row or {}).get("agent_id")
    phone = (branding or {}).get("phone")
    email = (branding or {}).get("email")
    empty = {
        "viewer": "none",
        "show_contact": False,
        "contact_href": None,
        "contact_key": None,
        "whatsapp_href": None,
        "profile_endpoint": None,
        "profile_args": None,
        "profile_key": None,
        "edit_endpoint": None,
        "edit_args": None,
        "edit_key": None,
    }
    if agent_id in (None, ""):
        return empty

    if is_guest or (current_user or {}).get("role") == "guest":
        digits = _digits(phone)
        return {
            **empty,
            "viewer": "guest",
            "show_contact": bool(phone or email),
            "contact_href": (
                f"https://wa.me/{digits}"
                if digits
                else (f"mailto:{email}" if email else None)
            ),
            "contact_key": "property_agent_contact_guest" if (phone or email) else None,
            "whatsapp_href": f"https://wa.me/{digits}" if digits else None,
        }

    user = current_user or {}
    profile = {
        "profile_endpoint": "agents_detail",
        "profile_args": {"agent_id": int(agent_id)},
    }

    if _same_agent(user, agent_id):
        return {
            **empty,
            **profile,
            "viewer": "owner",
            "profile_key": "property_agent_my_profile",
        }

    if user.get("role") == ROLE_ADMIN:
        return {
            **empty,
            **profile,
            "viewer": "staff",
            "profile_key": "property_agent_view_staff",
            "edit_endpoint": "agents_edit",
            "edit_args": {"agent_id": int(agent_id)},
            "edit_key": "property_agent_edit",
        }

    if user.get("role") == ROLE_AGENT:
        return {
            **empty,
            **profile,
            "viewer": "peer",
            "profile_key": "property_agent_profile",
        }

    return {**empty, **profile, "viewer": "other", "profile_key": "property_agent_profile"}
