"""Single AgentBranding source for ACM, brochure, and future social content."""

from __future__ import annotations

import logging
import re

from modules.agent_photo import image_has_alpha, resolve_agent_photo_path
from modules.auth import ROLE_AGENT
from modules.database.agents_repository import get_agent_record
from modules.database.organization_settings_repository import get_organization_settings
from modules.database.users_repository import get_agent_login_user
from modules.i18n import translate
from modules.operation_summary import _brand_logo_path

logger = logging.getLogger(__name__)

INSTAGRAM_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


AGENT_TITLE_KEYS = {
    "alto": "agent_branding_title_agent",
    "junior": "agent_branding_title_agent",
    "team leader": "agent_branding_title_tl",
}


def format_whatsapp_display(raw):
    """Visual WhatsApp number. Never invents digits that are not in the source."""
    original = " ".join(str(raw or "").split())
    digits = re.sub(r"\D", "", original)
    if not digits:
        return ""
    local = digits
    if local.startswith("54"):
        local = local[2:]
    if local.startswith("9") and len(local) >= 11:
        local = local[1:]
    if len(local) == 10:
        return f"+54 9 {local[:2]} {local[2:6]} {local[6:]}"
    if original.startswith("+") and len(digits) >= 8:
        return original
    return f"+{digits}"


def format_instagram_handle(raw):
    value = " ".join(str(raw or "").split())
    if not value:
        return ""
    value = value.split("?")[0].strip()
    value = re.sub(r"^https?://(www\.)?instagram\.com/", "", value, flags=re.I)
    value = value.strip("/").lstrip("@")
    if "/" in value:
        value = value.split("/", 1)[0]
    if not INSTAGRAM_HANDLE_RE.fullmatch(value):
        return ""
    return f"@{value}"


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
        "whatsapp": format_whatsapp_display((user or {}).get("phone")),
        "instagram": format_instagram_handle(agent.get("instagram_handle")),
        "instagram_handle": format_instagram_handle(agent.get("instagram_handle")),
        "linkedin": None,
        "location": None,
        "organization": settings.get("display_name") or None,
        "organization_logo": _brand_logo_path(settings.get("logo_path")),
        "greeting_name": greeting_name,
        "user_id": (user or {}).get("id"),
        "user_role": (user or {}).get("role"),
    }
    for key in (
        "phone",
        "email",
        "whatsapp",
        "instagram",
        "instagram_handle",
        "linkedin",
        "location",
        "organization",
    ):
        value = branding.get(key)
        if isinstance(value, str) and not value.strip():
            branding[key] = None
    return branding


def get_agent_presentation_asset(agent_id, organization_id, *, language="es", agent_login_only=True):
    """Same professional photo ACM and ficha already resolve. Never current_user."""
    branding = get_agent_branding(
        agent_id,
        organization_id,
        language=language,
        agent_login_only=agent_login_only,
    )
    if not branding:
        return None
    agent = get_agent_record(branding.get("agent_id"), branding.get("organization_id"))
    photo = resolve_agent_photo_path(agent) if agent else None
    original = resolve_agent_photo_path(agent, original=True) if agent else None
    if photo and image_has_alpha(photo):
        variant = "transparent"
    elif photo and branding.get("profile_photo_key"):
        variant = "display"
    elif original:
        variant = "original"
    else:
        variant = "none"
    branding["photo_path"] = str(photo) if photo else None
    branding["photo_original_path"] = str(original) if original else None
    branding["photo_variant"] = variant
    branding["has_photo"] = bool(photo)
    branding["profile_photo"] = branding.get("photo_path")
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
