"""Map external agents onto JRH agents. Fuzzy names never auto-assign."""

from __future__ import annotations

from modules.database.agents_repository import find_agent_by_external_id, get_agents
from modules.database.property_sync_hub_repository import get_external_agent_mapping
from modules.database.users_repository import get_user_by_email


def resolve_agent_id(organization_id, source, agent_ref, *, allow_email=True):
    ref = agent_ref or {}
    external_id = str(ref.get("external_agent_id") or "").strip()
    email = str(ref.get("email") or "").strip()

    if external_id:
        mapped = get_external_agent_mapping(organization_id, source, external_id)
        if mapped:
            return mapped["agent_id"], "explicit_mapping"

        by_id = find_agent_by_external_id(organization_id, source, external_id)
        if by_id:
            return by_id["id"], "external_id"

    if allow_email and email:
        user = get_user_by_email(email, organization_id)
        if user and user.get("agent_id"):
            return user["agent_id"], "email"

    return None, None


def suggest_agents_by_name(organization_id, name):
    """Manual suggestion only. Never used to assign during sync."""
    needle = " ".join(str(name or "").lower().split())
    if not needle:
        return []
    matches = []
    for agent in get_agents(organization_id):
        hay = " ".join(str(agent.get("name") or "").lower().split())
        if needle and needle in hay:
            matches.append(agent)
    return matches
