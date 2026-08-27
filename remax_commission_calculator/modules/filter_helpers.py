"""
Shared helpers for list filters (agent scope, IDs).
"""

from __future__ import annotations

from modules.database import get_agents


def parse_filter_agent_id(
    raw_value,
    organization_id,
):
    """
    Parse an agent_id filter value.

    Returns None for empty, invalid, or out-of-organization IDs
    without raising.
    """
    text = str(raw_value or "").strip()
    if text == "":
        return None

    try:
        candidate = int(text)
    except (TypeError, ValueError):
        return None

    valid_ids = {
        agent["id"]
        for agent in get_agents(organization_id)
    }
    if candidate not in valid_ids:
        return None

    return candidate


def resolve_scoped_agent_id(scope_agent_id, filter_agent_id):
    """Agent-scoped users always keep their scope."""
    if scope_agent_id is not None:
        return scope_agent_id
    return filter_agent_id
