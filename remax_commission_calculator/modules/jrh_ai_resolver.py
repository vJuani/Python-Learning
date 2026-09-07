"""Resolve named entities against real organization data. Never invent IDs."""

from __future__ import annotations

from modules.auth import is_agent
from modules.contacts import match_contacts
from modules.database.tenant import require_organization_id
from modules.search import search_agents, search_operations


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
    jurisdiction="",
    location_group="",
    property_type="",
    listing_purpose="",
    availability="",
    max_price=None,
    min_price=None,
    currency="",
    rooms=None,
    bedrooms=None,
    bathrooms=None,
    min_area=None,
    parking=None,
    balcony=None,
    terrace=None,
    garden=None,
    limit=8,
):
    from modules.database.properties_repository import filter_properties
    from modules.jrh_ai_classify import location_group_matches, normalize_location
    from modules.property_types import (
        normalize_listing_purpose,
        normalize_property_type,
    )

    organization_id = require_organization_id(organization_id)
    scoped = _viewer_agent_id(user, agent_id)
    location = {}
    if not jurisdiction and not neighborhood and (address or location_group):
        location = {}
    if not jurisdiction or not neighborhood:
        location = normalize_location(neighborhood or jurisdiction or "")
    resolved_jurisdiction = jurisdiction or location.get("jurisdiction")
    resolved_neighborhood = neighborhood or location.get("neighborhood")
    resolved_group = location_group or location.get("location_group")
    normalized_type = (
        normalize_property_type(property_type) if property_type else None
    )
    normalized_purpose = (
        normalize_listing_purpose(listing_purpose) if listing_purpose else None
    )
    rows = filter_properties(
        organization_id,
        agent_id=scoped,
        address=address or None,
        neighborhood=resolved_neighborhood or None,
        jurisdiction=resolved_jurisdiction or None,
        property_type=normalized_type or None,
        listing_purpose=normalized_purpose or None,
        commercial_status=availability or None,
        max_listing_price=max_price,
        min_listing_price=min_price,
        listing_currency=currency or None,
        include_all_statuses=False,
    )

    def _keep(row):
        if resolved_group and not location_group_matches(
            row.get("neighborhood"),
            resolved_group,
        ):
            return False
        checks = (
            ("rooms", rooms),
            ("bedrooms", bedrooms),
            ("bathrooms", bathrooms),
        )
        for field, expected in checks:
            if expected in (None, ""):
                continue
            try:
                if int(row.get(field) or 0) < int(expected):
                    return False
            except (TypeError, ValueError):
                return False
        if min_area not in (None, ""):
            area = row.get("covered_m2") or row.get("total_m2") or 0
            try:
                if float(area or 0) < float(min_area):
                    return False
            except (TypeError, ValueError):
                return False
        features = row.get("features") or {}
        if parking and not (row.get("parking_spaces") or features.get("parking")):
            return False
        if balcony and not features.get("balcony"):
            return False
        if terrace and not features.get("terrace"):
            return False
        if garden and not features.get("garden"):
            return False
        return True

    rows = [row for row in rows if _keep(row)]
    items = [
        {
            "id": row.get("id") or row.get("db_id"),
            "name": row.get("address") or row.get("name") or "",
            "kind": "property",
            "neighborhood": row.get("neighborhood") or "",
            "jurisdiction": row.get("jurisdiction") or "",
            "property_type": row.get("property_type") or "",
            "listing_price": row.get("listing_price"),
            "listing_currency": row.get("listing_currency") or "",
            "rooms": row.get("rooms"),
            "bedrooms": row.get("bedrooms"),
            "bathrooms": row.get("bathrooms"),
            "covered_m2": row.get("covered_m2") or row.get("total_m2"),
        }
        for row in rows[:limit]
    ]
    return items, len(rows)


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
