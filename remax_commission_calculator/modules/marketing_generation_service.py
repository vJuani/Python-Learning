"""Orchestrate marketing_generations without talking to providers in routes."""

from __future__ import annotations

import logging
from datetime import datetime

from modules.auth import is_agent, scoped_agent_id
from modules.database.marketing_generations_repository import (
    CONTENT_TYPES,
    ORIGINS,
    STATUS_COMPLETED,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    create_marketing_generation,
    get_marketing_generation,
    list_marketing_generations,
    update_marketing_generation,
)
from modules.database.organizations_repository import get_organization_by_id
from modules.database.organization_settings_repository import get_organization_settings
from modules.database.properties_repository import get_properties, get_property_record
from modules.database.tenant import require_organization_id
from modules.marketing_ai import (
    MarketingAIError,
    generate_content,
    public_error_message,
)
from modules.marketing_branding import resolve_marketing_branding
from modules.marketing_context import MarketingError, assert_marketing_access, build_property_marketing_context


logger = logging.getLogger(__name__)

OBJECTIVES = (
    "sell_property",
    "rent_property",
    "capture_owner",
    "generate_leads",
    "personal_brand",
)
OBJECTIVE_ALIASES = {
    "sale": "sell_property",
    "rent": "rent_property",
    "brand": "personal_brand",
    "lead": "generate_leads",
}
STYLES = ("premium", "modern", "minimal", "elegant", "dynamic", "corporate")
STYLE_ALIASES = {"warm": "elegant"}
TONES = ("formal", "close", "commercial", "aspirational", "exclusive")
TONE_ALIASES = {"professional": "formal"}


def _choice(value, allowed, aliases=None):
    value = (value or "").strip().lower()
    if aliases:
        value = aliases.get(value, value)
    return value if value in allowed else None


def can_view_generation(user, generation):
    if not user or not generation:
        return False
    if generation.get("organization_id") != user.get("organization_id"):
        return False
    agent_id = user.get("agent_id")
    if is_agent(user) and agent_id and generation.get("agent_id") == agent_id:
        return True
    return False


def require_generation(organization_id, user, generation_id):
    organization_id = require_organization_id(organization_id)
    generation = get_marketing_generation(generation_id, organization_id)
    if generation is None:
        raise MarketingError("marketing_ia_err_missing", 404)
    if not can_view_generation(user, generation):
        raise MarketingError("access_denied", 403)
    return generation


def _property_for_origin(organization_id, user, origin, property_id):
    if origin != "property":
        return None
    if not property_id:
        raise MarketingError("marketing_ia_err_property", 400)
    record = get_property_record(property_id, organization_id)
    if record is None:
        raise MarketingError("marketing_ia_err_property", 404)
    assert_marketing_access(user, record)
    return record


def _brand_facts(organization_id, language):
    settings = get_organization_settings(organization_id) or {}
    organization = get_organization_by_id(organization_id) or {}
    branding = resolve_marketing_branding(
        settings,
        language=language,
        organization_name=organization.get("name"),
    )
    return {
        "brand_name": branding.get("brand_name"),
        "office_name": branding.get("office_name") or organization.get("name"),
        "phone": branding.get("marketing_phone"),
        "whatsapp": branding.get("marketing_whatsapp"),
        "instagram": branding.get("marketing_instagram"),
        "email": branding.get("marketing_email"),
    }


def _agent_facts_from_snapshot(snapshot):
    if not snapshot:
        return {}
    return {
        "name": snapshot.get("name"),
        "title": snapshot.get("title"),
        "profile_name": snapshot.get("name") or snapshot.get("organization"),
        "whatsapp": snapshot.get("whatsapp") if snapshot.get("whatsapp_enabled") else None,
        "instagram": snapshot.get("instagram") if snapshot.get("instagram_enabled") else None,
        "email": snapshot.get("email"),
    }


def _property_facts(property_data, language):
    packed = build_property_marketing_context(property_data, language=language)
    facts = packed.get("facts") or {}
    amenity_flags = facts.get("amenity_flags") or {}
    payload = {
        "address": facts.get("title") or property_data.get("address"),
        "neighborhood": facts.get("neighborhood") or facts.get("locality"),
        "locality": facts.get("locality"),
        "jurisdiction": facts.get("jurisdiction"),
        "zone": facts.get("zone_line"),
        "operation_type": facts.get("operation_type") or facts.get("purpose"),
        "purpose": facts.get("purpose_label"),
        "property_type": facts.get("type_label"),
        "rooms": facts.get("rooms"),
        "bedrooms": facts.get("bedrooms"),
        "bathrooms": facts.get("bathrooms"),
        "garages": facts.get("garages") or facts.get("parking_spaces"),
        "total_m2": facts.get("total_m2"),
        "covered_m2": facts.get("covered_m2"),
        "uncovered_m2": facts.get("uncovered_m2"),
        "floor": facts.get("floor"),
        "orientation": facts.get("orientation"),
        "age": facts.get("age"),
        "condition": facts.get("condition"),
        "expenses": facts.get("expenses"),
        "amenities": facts.get("amenities") or [],
        "balcony": amenity_flags.get("balcony"),
        "terrace": amenity_flags.get("terrace"),
        "patio": amenity_flags.get("patio"),
        "pool": amenity_flags.get("pool"),
        "grill": amenity_flags.get("grill"),
        "security": amenity_flags.get("security"),
        "elevator": amenity_flags.get("elevator"),
        "description": facts.get("description"),
    }
    if facts.get("price_label"):
        payload["price"] = facts.get("price_label")
        payload["currency"] = facts.get("listing_currency")
    return payload, _agent_facts_from_snapshot(packed.get("agent"))


def build_generation_context(generation, *, language, property_data=None, user=None, include_agent=True):
    organization_id = generation["organization_id"]
    brand_facts = _brand_facts(organization_id, language)
    property_facts = {}
    agent_facts = {}
    if property_data:
        property_facts, agent_facts = _property_facts(property_data, language)
    if include_agent is False:
        agent_facts = {}
    elif not agent_facts and user:
        from modules.agent_branding import get_agent_presentation_asset

        snapshot = get_agent_presentation_asset(
            user.get("agent_id") or generation.get("agent_id"),
            organization_id,
            language=language,
            agent_login_only=True,
        )
        agent_facts = _agent_facts_from_snapshot(snapshot)
    return {
        "language": language,
        "content_type": generation["content_type"],
        "origin": generation["origin"],
        "objective": generation.get("objective"),
        "style": generation.get("style"),
        "tone": generation.get("tone"),
        "format": generation.get("format"),
        "prompt_input": generation.get("prompt_input"),
        "property_id": generation.get("property_id"),
        "property_address": property_facts.get("address") or generation.get("property_address"),
        "organization_id": organization_id,
        "property_facts": property_facts,
        "brand_facts": brand_facts,
        "agent_facts": agent_facts,
    }


def create_and_run_generation(
    organization_id,
    user,
    *,
    content_type,
    origin,
    property_id=None,
    objective=None,
    style=None,
    tone=None,
    format=None,
    prompt_input=None,
    language="es",
    parent_generation_id=None,
    provider=None,
    include_agent=True,
):
    organization_id = require_organization_id(organization_id)
    content_type = (content_type or "").strip().lower()
    origin = (origin or "").strip().lower()
    if not is_agent(user) or not scoped_agent_id(user):
        raise MarketingError("access_denied", 403)
    if content_type not in CONTENT_TYPES:
        raise MarketingError("marketing_ia_err_type", 400)
    if origin not in ORIGINS:
        raise MarketingError("marketing_ia_err_origin", 400)
    property_data = _property_for_origin(organization_id, user, origin, property_id)
    agent_id = scoped_agent_id(user)
    if agent_id is None and property_data:
        agent_id = property_data.get("agent_id")
    generation = create_marketing_generation(
        organization_id,
        created_by_user_id=user.get("id"),
        agent_id=agent_id,
        property_id=property_data["id"] if property_data else None,
        parent_generation_id=parent_generation_id,
        content_type=content_type,
        origin=origin,
        status=STATUS_PROCESSING,
        objective=_choice(objective, OBJECTIVES, OBJECTIVE_ALIASES),
        style=_choice(style, STYLES, STYLE_ALIASES),
        tone=_choice(tone, TONES, TONE_ALIASES),
        format=(format or "").strip() or None,
        prompt_input=prompt_input,
    )
    return run_generation(
        generation,
        language=language,
        property_data=property_data,
        user=user,
        provider=provider,
        include_agent=include_agent,
    )


def run_generation(generation, *, language, property_data=None, user=None, provider=None, include_agent=True):
    organization_id = generation["organization_id"]
    context = build_generation_context(
        generation,
        language=language,
        property_data=property_data,
        user=user,
        include_agent=include_agent,
    )
    try:
        result = generate_content(generation["content_type"], context, provider=provider)
        return update_marketing_generation(
            generation["id"],
            organization_id,
            status=STATUS_COMPLETED,
            generated_copy=result.get("generated_copy"),
            generated_data=result,
            provider=result.get("provider"),
            model_name=result.get("model_name"),
            input_tokens=result.get("input_tokens") or 0,
            output_tokens=result.get("output_tokens") or 0,
            estimated_cost=result.get("estimated_cost") or 0,
            completed_at=datetime.utcnow().replace(microsecond=0).isoformat(),
            error_message=None,
        )
    except Exception as error:
        logger.info(
            "marketing_ai generation_failed id=%s type=%s code=%s",
            generation.get("id"),
            type(error).__name__,
            getattr(error, "code", type(error).__name__),
        )
        message = public_error_message(error, language=language)
        provider_name = None
        model_name = None
        if isinstance(error, MarketingAIError):
            provider_name = "openai" if "openai" in (error.code or "") else None
        return update_marketing_generation(
            generation["id"],
            organization_id,
            status=STATUS_FAILED,
            error_message=message[:400],
            provider=provider_name or (getattr(provider, "name", None) if provider else None),
            model_name=model_name,
        )


def regenerate_generation(organization_id, user, generation_id, *, language="es", provider=None):
    source = require_generation(organization_id, user, generation_id)
    return create_and_run_generation(
        organization_id,
        user,
        content_type=source["content_type"],
        origin=source["origin"],
        property_id=source.get("property_id"),
        objective=source.get("objective"),
        style=source.get("style"),
        tone=source.get("tone"),
        format=source.get("format"),
        prompt_input=source.get("prompt_input"),
        language=language,
        parent_generation_id=source["id"],
        provider=provider,
    )


def discard_generation(organization_id, user, generation_id):
    generation = require_generation(organization_id, user, generation_id)
    return update_marketing_generation(
        generation["id"],
        organization_id,
        status=STATUS_DISCARDED,
    )


def list_generation_views(organization_id, user, *, content_type=None, language="es"):
    organization_id = require_organization_id(organization_id)
    author_id = user.get("id")
    items = list_marketing_generations(
        organization_id,
        created_by_user_id=author_id,
        content_type=content_type or None,
    )
    return [item for item in items if can_view_generation(user, item)]


def create_form_context(organization_id, user, *, language="es"):
    organization_id = require_organization_id(organization_id)
    properties = get_properties(organization_id, agent_id=scoped_agent_id(user))
    return {
        "properties": properties,
        "content_types": CONTENT_TYPES,
        "origins": ORIGINS,
        "objectives": OBJECTIVES,
        "styles": STYLES,
        "tones": TONES,
        "language": language,
    }
