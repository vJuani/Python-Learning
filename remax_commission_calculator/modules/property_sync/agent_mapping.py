"""Staff mapping of RedREMAX associates onto JRH Agents.

Unlink deletes only the mapping row. Agents and Properties are never deleted.
Existing Property.agent_id is kept after unlink; future imports stop auto-assigning
until Staff maps the associate again. Manual property reassignments are never
overwritten by mapping repair or sync.
"""

from __future__ import annotations

import json
import unicodedata

from modules.database.agents_repository import get_agent_record, get_agents
from modules.database.properties_repository import (
    ASSIGNMENT_SOURCE_MANUAL,
    apply_external_agent_assignment,
    get_properties,
)
from modules.database.property_sync_hub_repository import (
    delete_external_agent_mapping,
    get_external_agent_mapping,
    list_external_agent_mappings,
    upsert_external_agent_mapping,
)
from modules.database.tenant import TenantError, require_organization_id
from modules.database.users_repository import get_user_by_email, get_users
from modules.property_sync.agents import suggest_agents_by_name
from modules.property_sync.redremax.mapping import PROVIDER_REDREMAX

AGENT_SEARCH_LIMIT = 20
EXAMPLE_ADDRESS_LIMIT = 3
SUGGESTION_EMAIL = "email_exact"
SUGGESTION_NAME = "name_exact"
SUGGESTION_FUZZY = "fuzzy"


def _fold(value):
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _parse_metadata(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _external_agent_id_from_property(row):
    meta = _parse_metadata((row or {}).get("external_metadata_json"))
    return str(meta.get("associate") or "").strip()


def _example_address(row):
    meta = _parse_metadata((row or {}).get("external_metadata_json"))
    street = " ".join(
        part
        for part in (meta.get("street"), meta.get("street_number"))
        if part not in (None, "")
    ).strip()
    if street:
        return street
    title = str((row or {}).get("title") or "").strip()
    if title:
        return title
    address = str((row or {}).get("address") or "").strip()
    return address.split(",")[0].strip() if address else ""


def _users_by_agent(organization_id):
    by_agent = {}
    for user in get_users(organization_id):
        agent_id = user.get("agent_id")
        if agent_id is None:
            continue
        email = str(user.get("email") or user.get("username") or "").strip()
        current = by_agent.get(agent_id)
        if current is None or user.get("role") == "agent":
            by_agent[agent_id] = {
                "email": email,
                "name": " ".join(
                    part
                    for part in (
                        user.get("first_name"),
                        user.get("last_name"),
                    )
                    if part
                ).strip(),
            }
    return by_agent


def organization_agent_choices(organization_id, *, query="", limit=AGENT_SEARCH_LIMIT):
    organization_id = require_organization_id(organization_id)
    needle = _fold(query)
    users = _users_by_agent(organization_id)
    choices = []
    for agent in get_agents(organization_id):
        email = (users.get(agent["id"]) or {}).get("email") or ""
        name = str(agent.get("name") or "").strip()
        hay = _fold(f"{name} {email}")
        if needle and needle not in hay:
            continue
        choices.append(
            {
                "id": agent["id"],
                "name": name,
                "email": email,
            }
        )
        if len(choices) >= int(limit):
            break
    return choices


def suggest_jrh_agent(organization_id, *, email=None, name=None):
    """Suggestions only. Never persist. Fuzzy never auto-assigns."""
    organization_id = require_organization_id(organization_id)
    suggestions = []
    seen = set()
    email = str(email or "").strip()
    name = " ".join(str(name or "").split())
    if email:
        user = get_user_by_email(email, organization_id)
        agent_id = (user or {}).get("agent_id")
        if agent_id is not None:
            agent = get_agent_record(agent_id, organization_id)
            if agent:
                seen.add(int(agent["id"]))
                suggestions.append(
                    {
                        "agent_id": agent["id"],
                        "name": agent.get("name"),
                        "email": email,
                        "strength": SUGGESTION_EMAIL,
                    }
                )
    if name:
        needle = name.lower()
        for agent in get_agents(organization_id):
            hay = " ".join(str(agent.get("name") or "").lower().split())
            if hay != needle or int(agent["id"]) in seen:
                continue
            seen.add(int(agent["id"]))
            suggestions.append(
                {
                    "agent_id": agent["id"],
                    "name": agent.get("name"),
                    "email": (_users_by_agent(organization_id).get(agent["id"]) or {}).get(
                        "email"
                    )
                    or "",
                    "strength": SUGGESTION_NAME,
                }
            )
        for agent in suggest_agents_by_name(organization_id, name):
            if int(agent["id"]) in seen:
                continue
            seen.add(int(agent["id"]))
            suggestions.append(
                {
                    "agent_id": agent["id"],
                    "name": agent.get("name"),
                    "email": (_users_by_agent(organization_id).get(agent["id"]) or {}).get(
                        "email"
                    )
                    or "",
                    "strength": SUGGESTION_FUZZY,
                }
            )
    return suggestions


def repair_properties_for_mapping(
    organization_id,
    source,
    external_agent_id,
    agent_id,
):
    organization_id = require_organization_id(organization_id)
    identity = str(external_agent_id or "").strip()
    repaired = 0
    skipped_manual = 0
    for row in get_properties(organization_id, include_all_statuses=True):
        if str(row.get("external_source") or "") != source:
            continue
        if _external_agent_id_from_property(row) != identity:
            continue
        if row.get("agent_assignment_source") == ASSIGNMENT_SOURCE_MANUAL:
            skipped_manual += 1
            continue
        if apply_external_agent_assignment(
            row["id"], organization_id, agent_id, source
        ):
            repaired += 1
    return {"repaired": repaired, "skipped_manual": skipped_manual}


def save_external_agent_mapping(
    organization_id,
    source,
    external_agent_id,
    agent_id,
    *,
    metadata=None,
):
    organization_id = require_organization_id(organization_id)
    identity = str(external_agent_id or "").strip()
    if not identity:
        raise ValueError("external agent identity required")
    mapping = upsert_external_agent_mapping(
        organization_id,
        source,
        identity,
        agent_id,
        metadata=metadata,
    )
    repair = repair_properties_for_mapping(
        organization_id, source, identity, agent_id
    )
    return {"mapping": mapping, **repair}


def save_external_agent_mappings(organization_id, source, pairs, *, metadata_by_id=None):
    organization_id = require_organization_id(organization_id)
    saved = 0
    repaired = 0
    skipped_manual = 0
    metadata_by_id = metadata_by_id or {}
    for external_agent_id, agent_id in pairs:
        identity = str(external_agent_id or "").strip()
        if not identity or agent_id in (None, ""):
            continue
        result = save_external_agent_mapping(
            organization_id,
            source,
            identity,
            int(agent_id),
            metadata=metadata_by_id.get(identity),
        )
        saved += 1
        repaired += int(result.get("repaired") or 0)
        skipped_manual += int(result.get("skipped_manual") or 0)
    return {
        "saved": saved,
        "repaired": repaired,
        "skipped_manual": skipped_manual,
    }


def unlink_external_agent_mapping(organization_id, source, external_agent_id):
    """Remove the mapping only. Properties keep their current agent_id."""
    organization_id = require_organization_id(organization_id)
    return delete_external_agent_mapping(organization_id, source, external_agent_id)


def list_detected_external_agents(organization_id, source=PROVIDER_REDREMAX):
    organization_id = require_organization_id(organization_id)
    buckets = {}
    for row in get_properties(organization_id, include_all_statuses=True):
        if str(row.get("external_source") or "") != source:
            continue
        identity = _external_agent_id_from_property(row)
        if not identity:
            continue
        meta = _parse_metadata(row.get("external_metadata_json"))
        bucket = buckets.setdefault(
            identity,
            {
                "external_agent_id": identity,
                "associate_qrid": str(meta.get("associate_qrid") or "").strip() or None,
                "external_name": str(meta.get("associate_name") or "").strip() or None,
                "external_email": str(meta.get("associate_email") or "").strip() or None,
                "property_count": 0,
                "example_addresses": [],
            },
        )
        if not bucket.get("associate_qrid"):
            bucket["associate_qrid"] = (
                str(meta.get("associate_qrid") or "").strip() or None
            )
        if not bucket.get("external_name"):
            bucket["external_name"] = (
                str(meta.get("associate_name") or "").strip() or None
            )
        if not bucket.get("external_email"):
            bucket["external_email"] = (
                str(meta.get("associate_email") or "").strip() or None
            )
        bucket["property_count"] += 1
        example = _example_address(row)
        if example and example not in bucket["example_addresses"]:
            if len(bucket["example_addresses"]) < EXAMPLE_ADDRESS_LIMIT:
                bucket["example_addresses"].append(example)
    mappings = {
        item["external_agent_id"]: item
        for item in list_external_agent_mappings(organization_id, source)
    }
    rows = []
    for identity in sorted(buckets):
        detected = buckets[identity]
        mapped = mappings.get(identity)
        suggestions = suggest_jrh_agent(
            organization_id,
            email=detected.get("external_email"),
            name=detected.get("external_name"),
        )
        email_suggestion = next(
            (item for item in suggestions if item["strength"] == SUGGESTION_EMAIL),
            None,
        )
        rows.append(
            {
                **detected,
                "mapped": mapped is not None,
                "agent_id": (mapped or {}).get("agent_id"),
                "agent_name": (mapped or {}).get("agent_name"),
                "suggestions": suggestions,
                "email_suggestion": email_suggestion,
            }
        )
    unmapped = [row for row in rows if not row["mapped"]]
    return {
        "source": source,
        "rows": rows,
        "detected_count": len(rows),
        "mapped_count": len(rows) - len(unmapped),
        "unmapped_count": len(unmapped),
        "unmapped_rows": unmapped,
    }


def redremax_agent_mapping_dashboard(organization_id):
    organization_id = require_organization_id(organization_id)
    view = list_detected_external_agents(organization_id, PROVIDER_REDREMAX)
    view["jrh_agents"] = organization_agent_choices(
        organization_id, limit=500
    )
    return view


def parse_mapping_form(form):
    pairs = []
    for key, value in (form or {}).items():
        if not str(key).startswith("map__"):
            continue
        identity = str(key)[5:].strip()
        raw = str(value or "").strip()
        if not identity or not raw:
            continue
        pairs.append((identity, int(raw)))
    identity = str((form or {}).get("external_agent_id") or "").strip()
    raw_agent = str((form or {}).get("agent_id") or "").strip()
    if identity and raw_agent:
        pairs.append((identity, int(raw_agent)))
    unique = {}
    for identity, agent_id in pairs:
        unique[identity] = agent_id
    return list(unique.items())
