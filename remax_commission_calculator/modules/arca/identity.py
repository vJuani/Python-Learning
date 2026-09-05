"""Resolve fiscal identity from existing profiles. Not credentials."""

from __future__ import annotations

from modules.auth import is_admin, is_agent
from modules.database.agent_billing_profiles_repository import (
    get_by_agent as get_agent_billing_profile,
)
from modules.database.billing_issuer_profiles_repository import (
    get_profile as get_billing_issuer_profile,
    list_profiles as list_billing_issuer_profiles,
)
from modules.database.organization_settings_repository import (
    get_organization_settings,
)


def resolve_fiscal_identity(organization_id, user):
    if is_agent(user) and not is_admin(user) and user.get("agent_id"):
        profile = get_agent_billing_profile(
            organization_id,
            user["agent_id"],
        ) or {}
        return {
            "source": "agent",
            "profile_id": profile.get("id"),
            "agent_id": user.get("agent_id"),
            "legal_name": profile.get("legal_name") or "",
            "tax_id": profile.get("tax_id") or "",
            "tax_condition": profile.get("tax_condition") or "",
            "fiscal_address": profile.get("fiscal_address") or "",
            "editable": True,
        }

    settings = get_organization_settings(organization_id) or {}
    profile_id = settings.get("default_issuer_profile_id")
    profile = (
        get_billing_issuer_profile(organization_id, profile_id)
        if profile_id
        else None
    )
    if profile is None:
        profiles = list_billing_issuer_profiles(
            organization_id,
            active_only=True,
        )
        profile = profiles[0] if profiles else {}
    return {
        "source": "office",
        "profile_id": (profile or {}).get("id"),
        "agent_id": None,
        "legal_name": (profile or {}).get("legal_name") or "",
        "tax_id": (profile or {}).get("tax_id") or "",
        "tax_condition": (profile or {}).get("tax_condition") or "",
        "fiscal_address": (profile or {}).get("fiscal_address") or "",
        "editable": is_admin(user),
    }
