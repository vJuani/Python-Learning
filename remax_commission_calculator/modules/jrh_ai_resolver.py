"""Resolve named entities against real organization data. Never invent IDs."""

from __future__ import annotations

from modules.auth import is_agent
from modules.contacts import match_contacts
from modules.database.tenant import require_organization_id
from modules.entity_match import (
    RANK_POOL_LIMIT,
    UNIQUE_MIN,
    decide_entity_matches,
    log_entity_match,
    rank_entity_candidates,
)
from modules.search import search_agents_flexible, search_operations


def _viewer_agent_id(user, agent_id):
    if is_agent(user) and agent_id:
        return int(agent_id)
    return None


def resolve_agents(organization_id, query, *, user, agent_id=None, limit=8):
    organization_id = require_organization_id(organization_id)
    scoped = _viewer_agent_id(user, agent_id)
    matches = search_agents_flexible(query or "", organization_id, limit=limit)
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
        return []
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
                "kind": "contact",
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
    center_lat=None,
    center_lng=None,
    radius_km=None,
    exclude_property_id=None,
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
        address=None,
        neighborhood=resolved_neighborhood or None,
        jurisdiction=resolved_jurisdiction or None,
        property_type=normalized_type or None,
        listing_purpose=normalized_purpose or None,
        commercial_status=availability or None,
        max_listing_price=max_price,
        min_listing_price=min_price,
        listing_currency=currency or None,
        include_all_statuses=False,
        center_lat=center_lat,
        center_lng=center_lng,
        radius_m=(float(radius_km) * 1000.0 if radius_km else None),
        exclude_property_id=exclude_property_id,
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
    text_query = (address or "").strip()
    geo_active = center_lat is not None and center_lng is not None and radius_km
    if text_query and not geo_active:
        ranked = rank_entity_candidates(
            text_query,
            rows[:RANK_POOL_LIMIT],
            text_fields=("address", "neighborhood", "external_id"),
            code_fields=("external_id", "id"),
            limit=max(int(limit or 8), 8),
        )
        status, chosen, _confidence = decide_entity_matches(ranked)
        log_entity_match(text_query, ranked, status, kind="property")
        rows = chosen or ranked
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
            "match_score": row.get("match_score"),
            "distance_meters": row.get("distance_meters"),
            "distance_label": row.get("distance_label"),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
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
            agent_id=scoped,
        )
        if not rows:
            rows = search_operations(query, organization_id)
            if scoped is not None:
                rows = [
                    row
                    for row in rows
                    if row.get("agent_id") == scoped or row.get("agent_db_id") == scoped
                ]
        ranked = rank_entity_candidates(
            query,
            rows[:RANK_POOL_LIMIT],
            text_fields=("property", "property_address", "address", "id"),
            code_fields=("id", "db_id", "property_external_id"),
            limit=max(int(limit or 5), 5),
        )
        status, chosen, _confidence = decide_entity_matches(ranked)
        log_entity_match(query, ranked, status, kind="operation")
        rows = chosen or ranked
    return [
        {
            "id": row.get("db_id") or row.get("id"),
            "name": row.get("property")
            or row.get("property_address")
            or row.get("address")
            or row.get("match_label")
            or query,
            "kind": "operation",
            "property_id": row.get("property_db_id"),
            "match_score": row.get("match_score"),
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
    if hint:
        ranked = rank_entity_candidates(
            hint,
            charges[:RANK_POOL_LIMIT],
            text_fields=("description", "charge_category", "period_label", "name"),
            code_fields=("id",),
            limit=12,
        )
        status, chosen, _confidence = decide_entity_matches(ranked)
        log_entity_match(hint, ranked, status, kind="charge")
        charges = chosen or ranked
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
    if any(item.get("match_score") is not None for item in matches):
        status, chosen, _confidence = decide_entity_matches(matches)
        if status == "unique":
            return "unique", chosen[0], matches
        if status == "empty":
            return "empty", None, []
        return "ambiguous", None, chosen or matches
    if len(matches) == 1:
        score = matches[0].get("match_score")
        if score is not None and score < UNIQUE_MIN:
            return "ambiguous", None, matches
        return "unique", matches[0], matches
    if confidence < 0.7:
        return "ambiguous", None, matches
    return "ambiguous", None, matches


def resolve_geo_center(
    organization_id,
    *,
    user,
    agent_id=None,
    entities=None,
    context=None,
):
    """Resolve a search center from Property context, inventory, or Places.

    Never invents coordinates. Places is only used for an explicit
    radius search around free text that is not an existing Property.
    """
    from modules.database.properties_repository import get_property_record
    from modules.maps.geo import parse_radius_km
    from modules.maps.location import has_coordinates
    from modules.maps.provider import find_place

    entities = entities or {}
    context = context or {}
    last = context.get("last_entity") or {}
    radius_km = parse_radius_km(entities.get("radius_km"))
    near_this = bool(entities.get("near_this_property"))
    center_text = (entities.get("center_text") or "").strip()
    property_id = None
    if near_this and last.get("kind") == "property" and last.get("id"):
        property_id = last["id"]
    elif center_text:
        from modules.database.properties_repository import filter_properties

        scoped = _viewer_agent_id(user, agent_id)
        rows = filter_properties(
            organization_id,
            agent_id=scoped,
            address=center_text,
            include_all_statuses=False,
        )
        property_id = None
        if len(rows) == 1:
            property_id = rows[0].get("id")
        elif rows:
            ranked = rank_entity_candidates(
                center_text,
                rows[:RANK_POOL_LIMIT],
                text_fields=("address", "neighborhood", "formatted_address", "external_id"),
                code_fields=("external_id", "id"),
                limit=5,
            )
            status, chosen, _confidence = decide_entity_matches(ranked)
            log_entity_match(center_text, ranked, status, kind="property_geo")
            if status == "unique" and chosen:
                property_id = chosen[0].get("id")
            elif ranked:
                return {
                    "status": "ambiguous",
                    "matches": ranked,
                    "radius_km": radius_km,
                    "label": center_text,
                }
        if property_id is None:
            matches, _total = resolve_properties(
                organization_id,
                user=user,
                agent_id=agent_id,
                address=center_text,
                limit=5,
            )
            if len(matches) == 1:
                property_id = matches[0].get("id")
            elif matches:
                return {
                    "status": "ambiguous",
                    "matches": matches,
                    "radius_km": radius_km,
                    "label": center_text,
                }

    if property_id:
        row = get_property_record(property_id, organization_id)
        if row is None:
            return {"status": "missing", "radius_km": radius_km}
        if not has_coordinates(row):
            return {
                "status": "unlocated",
                "property": row,
                "property_id": property_id,
                "label": row.get("address"),
                "radius_km": radius_km,
            }
        return {
            "status": "ok",
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "property_id": property_id,
            "label": row.get("address"),
            "radius_km": radius_km,
        }

    if center_text and radius_km:
        place = find_place(center_text)
        if place and place.get("latitude") is not None:
            return {
                "status": "ok",
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "label": place.get("formatted_address") or center_text,
                "radius_km": radius_km,
                "transient": True,
            }
        return {
            "status": "unresolved",
            "label": center_text,
            "radius_km": radius_km,
        }

    if entities.get("ask_radius") and last.get("kind") == "property":
        return {
            "status": "ask_radius",
            "property_id": last.get("id"),
            "label": last.get("label"),
        }
    return {"status": "none", "radius_km": radius_km}
