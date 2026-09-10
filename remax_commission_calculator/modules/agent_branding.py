"""Single AgentBranding source for ACM, brochure, and future social content."""

from __future__ import annotations

from modules.auth import ROLE_AGENT
from modules.database.agents_repository import get_agent_record
from modules.database.organization_settings_repository import get_organization_settings
from modules.database.users_repository import get_agent_login_user
from modules.i18n import translate
from modules.operation_summary import _brand_logo_path


AGENT_TITLE_KEYS = {
    "alto": "agent_branding_title_agent",
    "junior": "agent_branding_title_agent",
    "team leader": "agent_branding_title_tl",
}


def _full_name(user, agent):
    if user:
        joined = " ".join(
            part for part in (user.get("first_name"), user.get("last_name")) if part
        ).strip()
        if joined:
            return joined
        if user.get("username"):
            return user.get("username")
    return (agent or {}).get("name") or ""


def get_agent_branding(agent_id, organization_id, *, language="es", agent_login_only=False):
    """Contact + photo for the Agent record. Never uses current_user.

    When agent_login_only=True, ignore staff/admin users that share agent_id
    so a listing never inherits the wrong email or phone.
    """
    if agent_id in (None, ""):
        return None
    agent = get_agent_record(agent_id, organization_id)
    if agent is None:
        return None
    user = get_agent_login_user(agent_id, organization_id)
    if agent_login_only and user and user.get("role") != ROLE_AGENT:
        user = None
    settings = get_organization_settings(organization_id) or {}
    agent_type = str(agent.get("type") or "").strip().lower()
    title_key = AGENT_TITLE_KEYS.get(agent_type, "agent_branding_title_agent")
    first = ((user or {}).get("first_name") or "").strip()
    greeting_name = first or _full_name(user, agent)
    branding = {
        "agent_id": agent["id"],
        "organization_id": agent["organization_id"],
        "name": _full_name(user, agent),
        "first_name": first or None,
        "title": translate(title_key, language=language),
        "role": ROLE_AGENT,
        "profile_photo_key": agent.get("profile_photo_key"),
        "profile_photo_original_key": agent.get("profile_photo_original_key"),
        "profile_photo_mime": agent.get("profile_photo_mime"),
        "has_photo": bool(agent.get("profile_photo_key")),
        "phone": (user or {}).get("phone") or None,
        "email": (user or {}).get("email") or None,
        "instagram": None,
        "linkedin": None,
        "location": None,
        "organization": settings.get("display_name") or None,
        "organization_logo": _brand_logo_path(settings.get("logo_path")),
        "greeting_name": greeting_name,
        "user_id": (user or {}).get("id"),
        "user_role": (user or {}).get("role"),
    }
    for key in ("phone", "email", "instagram", "linkedin", "location", "organization"):
        value = branding.get(key)
        if isinstance(value, str) and not value.strip():
            branding[key] = None
    return branding


def can_edit_agent_photo(user, agent):
    if not user or not agent:
        return False
    try:
        if int(user.get("organization_id")) != int(agent.get("organization_id")):
            return False
    except (TypeError, ValueError):
        return False
    if user.get("role") == "admin":
        return True
    try:
        return (
            user.get("role") == ROLE_AGENT
            and int(user.get("agent_id") or 0) == int(agent.get("id"))
        )
    except (TypeError, ValueError):
        return False
