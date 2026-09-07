"""Resolve named entities against real organization data. Never invent IDs."""

from __future__ import annotations

from modules.auth import is_admin, is_agent
from modules.contacts import match_contacts
from modules.database.tenant import require_organization_id
from modules.search import search_agents, search_operations, search_properties


def _viewer_agent_id(user, agent_id):
    if is_agent(user) and agent_id:
        return int(agent_id)
    return None


def resolve_agents(organization_id, query, *, user, agent_id=None, limit=8):
    organization_id = require_organization_id(organization_id)
    scoped = _viewer_agent_id(user, agent_id)
    matches = search_agents(query or "", organization_id, limit=limit)
    if scoped is not None:
        matches = [item for item in matches if int(item.get("id") or 0) == scoped]
        if not matches and not (query or "").strip():
            from modules.database.agents_repository import get_agent_record

            own = get_agent_record(scoped, organization_id)
            if own:
                matches = [own]
    return [
        {
            "id": item.get("id"),
            "name": item.get("name") or "",
            "kind": "agent",
        }
        for item in matches
    ]


def resolve_contacts(organization_id, query, *, user, agent_id=None):
    organization_id = require_organization_id(organization_id)
    scoped = _viewer_agent_id(user, agent_id)
    if is_agent(user) and scoped is None:
        return {"status": "empty", "matches": []}
    result = match_contacts(
        organization_id,
        scoped if is_agent(user) else None,
        query or "",
    )
    matches = []
    if result.get("contact"):
        matches.append(result["contact"])
    matches.extend(result.get("candidates") or [])
    seen = set()
    unique = []
    for item in matches:
        key = item.get("id")
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or "",
                "kind": "need",
            }
        )
    return unique


def resolve_properties(
    organization_id,
    *,
    user,
    agent_id=None,
    address="",
    neighborhood="",
    property_type="",
    max_price=None,
    rooms=None,
    limit=8,
):
    from modules.database.properties_repository import filter_properties
    from modules.property_types import normalize_property_type

    organization_id = require_organization_id(organization_id)
    scoped = _viewer_agent_id(user, agent_id)
    normalized_type = (
        normalize_property_type(property_type) if property_type else None
    )
    rows = filter_properties(
        organization_id,
        agent_id=scoped,
        address=address or None,
        neighborhood=neighborhood or None,
        property_type=normalized_type or None,
        max_listing_price=max_price,
        include_all_statuses=False,
    )
    if rooms:
        filtered = []
        for row in rows:
            value = row.get("rooms") or row.get("bedrooms")
            try:
                if int(value or 0) == int(rooms):
                    filtered.append(row)
            except (TypeError, ValueError):
                continue
        rows = filtered
    if not rows and (address or neighborhood):
        rows = search_properties(address or neighborhood, organization_id)
        if scoped is not None:
            rows = [row for row in rows if row.get("agent_id") == scoped]
    return [
        {
            "id": row.get("id") or row.get("db_id"),
            "name": row.get("address") or row.get("name") or "",
            "kind": "property",
            "neighborhood": row.get("neighborhood") or "",
            "listing_price": row.get("listing_price"),
            "listing_currency": row.get("listing_currency") or "",
        }
        for row in rows[:limit]
    ]


def resolve_operations(
    organization_id,
    query,
    *,
    user,
    agent_id=None,
    limit=5,
):
    organization_id = require_organization_id(organization_id)
    scoped = _viewer_agent_id(user, agent_id)
    rows = []
    if query:
        from modules.database.operations_repository import filter_operations

        rows = filter_operations(
            organization_id,
            property_address=query,
            agent_id=scoped,
        )
        if not rows:
            rows = search_operations(query, organization_id)
            if scoped is not None:
                rows = [
                    row
                    for row in rows
                    if row.get("agent_id") == scoped
                ]
    return [
        {
            "id": row.get("db_id") or row.get("id"),
            "name": row.get("property_address") or row.get("address") or query,
            "kind": "operation",
        }
        for row in rows[:limit]
        if row.get("db_id") or row.get("id")
    ]


def resolve_pending_charges(
    organization_id,
    agent_id,
    *,
    currency=None,
    hint="",
):
    from modules.database.agent_account_repository import (
        CURRENCIES,
        list_pending_charges,
    )

    organization_id = require_organization_id(organization_id)
    currencies = [currency] if currency in CURRENCIES else list(CURRENCIES)
    charges = []
    for code in currencies:
        charges.extend(
            list_pending_charges(organization_id, agent_id, code)
        )
    hint_fold = (hint or "").strip().lower()
    if hint_fold:
        charges = [
            item
            for item in charges
            if hint_fold in (item.get("description") or "").lower()
            or hint_fold in (item.get("charge_category") or "").lower()
            or hint_fold in (item.get("period_label") or "").lower()
        ]
    return [
        {
            "id": item.get("id"),
            "name": item.get("description") or item.get("period_label") or "",
            "kind": "charge",
            "amount": item.get("pending_amount") or item.get("remaining_amount"),
            "currency": item.get("currency"),
            "period_label": item.get("period_label") or "",
        }
        for item in charges
    ]


def pick_unique(matches, *, confidence=1.0):
    if not matches:
        return "empty", None, matches
    if len(matches) == 1:
        return "unique", matches[0], matches
    if confidence < 0.7:
        return "ambiguous", None, matches
    return "ambiguous", None, matches
