"""
Resolve ARCA issuer profile from an invoice record.
"""

from __future__ import annotations

from modules.database.agent_billing_profiles_repository import (
    get_by_agent as get_agent_billing_profile,
)
from modules.database.billing_issuer_profiles_repository import (
    get_profile as get_billing_issuer_profile,
)


def resolve_invoice_issuer_profile(
    organization_id,
    invoice: dict,
) -> dict | None:
    profile_id = invoice.get("issuer_profile_id")
    if profile_id:
        profile = get_billing_issuer_profile(
            organization_id,
            int(profile_id),
        )
        if profile is None:
            return None
        profile = dict(profile)
        profile["issuer_key"] = invoice.get("issuer_key")
        return profile

    agent_id = invoice.get("agent_id")
    if agent_id is None:
        return None

    profile = get_agent_billing_profile(
        organization_id,
        agent_id,
    )
    if profile is None:
        return None
    profile = dict(profile)
    profile["issuer_key"] = invoice.get("issuer_key")
    profile["agent_id"] = agent_id
    return profile
