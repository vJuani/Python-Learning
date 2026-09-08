"""Reusable property geocoding helpers. No network calls in V1."""

from __future__ import annotations

from datetime import datetime

from modules.database.properties_repository import UNSET, update_property
from modules.database.tenant import require_organization_id
from modules.maps.location import LOCATION_FIELDS, apply_place_to_location


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def geocode_property(
    property_id,
    organization_id,
    *,
    place=None,
    location=None,
    address=None,
    jurisdiction=None,
    agent_id=UNSET,
):
    """
    Persist a selected place onto an existing property.

    Does not call Google. Callers must already have a place payload
    (autocomplete selection or a future queue worker).
    """
    organization_id = require_organization_id(organization_id)
    payload = location or apply_place_to_location(place, geocoded_at=_now_iso())
    kwargs = {field: payload.get(field) for field in LOCATION_FIELDS}
    from modules.database.properties_repository import get_property_record

    current = get_property_record(property_id, organization_id)
    if current is None:
        return None
    update_property(
        property_id,
        address if address is not None else current.get("address") or "",
        jurisdiction if jurisdiction is not None else current.get("jurisdiction") or "",
        organization_id,
        agent_id=current.get("agent_id") if agent_id is UNSET else agent_id,
        **kwargs,
    )
    return get_property_record(property_id, organization_id)
