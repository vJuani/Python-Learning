"""Safe, publicable marketing facts. Never includes private CRM data."""

from __future__ import annotations

import re

from modules.agent_branding import get_agent_branding
from modules.agent_photo import resolve_agent_photo_path
from modules.branding import get_brand_name, resolve_brand_logo_path
from modules.database.agents_repository import get_agent_record
from modules.database.organization_settings_repository import get_organization_settings
from modules.formatting import format_listing_money
from modules.i18n import translate
from modules.operation_summary import _brand_logo_path
from modules.property_detail_view import (
    compact_property_location,
    compact_property_title,
    parse_external_metadata,
)
from modules.property_inventory import decorate_property_for_display
from modules.property_media_access import can_access_property_media
from modules.database.property_media_repository import list_property_media
from modules.property_sync.media import (
    get_property_media_url,
    get_property_media_for_generation,
    is_displayable_media,
)
from modules.property_types import normalize_listing_purpose


FORBIDDEN_FACT_KEYS = frozenset(
    {
        "clients",
        "clientsdata",
        "clients_data",
        "documents",
        "privatenotes",
        "private_notes",
        "commission",
        "fiscal",
        "invoice",
        "notes",
        "internal",
    }
)

PRICE_PRIVATE = "private"
FORMATS = ("story", "post", "status", "flyer", "pack")
STYLES = ("elegant", "modern", "minimal")
TONES = ("professional", "commercial", "warm")
TEMPLATES = ("editorial", "visual", "minimal")
PHOTO_LIMIT = 5


class MarketingError(Exception):
    def __init__(self, message_key, status_code=400):
        super().__init__(message_key)
        self.message_key = message_key
        self.status_code = status_code


def _present(value):
    if value in (None, ""):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _purpose_label(purpose, language):
    if purpose == "rental":
        return translate("marketing_purpose_rent", language=language)
    if purpose == "temporary_rental":
        return translate("marketing_purpose_temp", language=language)
    return translate("marketing_purpose_sale", language=language)


def _marketing_price_label(amount, currency, language):
    label = format_listing_money(amount, currency, language=language)
    if not label:
        return None
    return re.sub(r"[.,]00$", "", label)


def _chips(display, language):
    chips = []
    rooms = display.get("rooms")
    if rooms not in (None, ""):
        chips.append(f"{rooms} {translate('property_rooms', language=language).lower()}")
    bedrooms = display.get("bedrooms")
    if bedrooms not in (None, ""):
        chips.append(
            f"{bedrooms} {translate('property_bedrooms', language=language).lower()}"
        )
    area = display.get("covered_m2")
    if area in (None, ""):
        area = display.get("total_m2")
    if area not in (None, ""):
        number = int(area) if float(area) == int(float(area)) else area
        chips.append(f"{number} m²")
    return chips[:4]


def _price_policy(meta, display):
    exposure = str(
        meta.get("external_price_exposure")
        or display.get("external_price_exposure")
        or ""
    ).strip().lower()
    has_price = display.get("listing_price") not in (None, "")
    private = exposure == PRICE_PRIVATE
    return {
        "exposure": exposure or None,
        "private": private,
        "has_price": has_price,
        "default_show": bool(has_price and not private),
        "warning": private,
    }


def _agent_snapshot(property_data, language):
    branding = get_agent_branding(
        property_data.get("agent_id"),
        property_data.get("organization_id"),
        language=language,
        agent_login_only=True,
    )
    if not branding:
        return None
    agent = get_agent_record(branding.get("agent_id"), branding.get("organization_id"))
    photo = resolve_agent_photo_path(agent) if agent else None
    return {
        "agent_id": branding.get("agent_id"),
        "name": branding.get("name"),
        "phone": branding.get("phone"),
        "email": branding.get("email"),
        "instagram": branding.get("instagram"),
        "title": branding.get("title"),
        "has_photo": bool(branding.get("has_photo") and photo),
        "photo_path": str(photo) if photo else None,
        "organization": branding.get("organization"),
    }


def _catalog_media(property_data):
    items = list_property_media(
        property_data.get("organization_id"),
        property_data.get("id"),
    )
    return [item for item in items if is_displayable_media(item)]


def _serialize_photo(item):
    return {
        "id": item.get("id"),
        "is_cover": bool(item.get("is_cover")),
        "position": item.get("position"),
        "original_url": item.get("original_url"),
        "storage_key": item.get("storage_key"),
        "content_type": item.get("content_type"),
    }


def _media_items(property_data, selected_ids=None):
    items = get_property_media_for_generation(property_data, limit=PHOTO_LIMIT)
    if selected_ids:
        by_id = {
            int(item["id"]): item
            for item in _catalog_media(property_data)
            if item.get("id") is not None
        }
        ordered = []
        for raw in selected_ids:
            try:
                key = int(raw)
            except (TypeError, ValueError):
                continue
            if key in by_id:
                ordered.append(by_id[key])
        if ordered:
            items = ordered[:PHOTO_LIMIT]
    return [_serialize_photo(item) for item in items[:PHOTO_LIMIT]]


def list_wizard_photos(property_data, selected_ids=None):
    selected = {item["id"] for item in _media_items(property_data, selected_ids)}
    photos = []
    for item in _catalog_media(property_data):
        photos.append(
            {
                **_serialize_photo(item),
                "selected": item.get("id") in selected,
                "src": get_property_media_url(item, property_data.get("id")),
            }
        )
    photos.sort(key=lambda item: (not item.get("selected"), not item.get("is_cover"), item.get("position") or 0))
    return photos


def assert_marketing_access(user, property_data, *, is_guest=False):
    if not can_access_property_media(user, property_data, is_guest=is_guest):
        raise MarketingError("access_denied", 403)
    return property_data


def build_property_marketing_context(
    property_data,
    *,
    language="es",
    include_agent=True,
    selected_photo_ids=None,
):
    """Public listing facts only. Cover first, max 5 real photos."""
    if not property_data:
        raise MarketingError("marketing_err_property_missing", 404)
    display = decorate_property_for_display(property_data, language=language)
    meta = parse_external_metadata(display)
    purpose = normalize_listing_purpose(display.get("listing_purpose"))
    settings = get_organization_settings(display.get("organization_id")) or {}
    org_logo = _brand_logo_path(settings.get("logo_path")) or resolve_brand_logo_path()
    photos = _media_items(display, selected_photo_ids)
    price_policy = _price_policy(meta, display)
    price_label = None
    if price_policy["has_price"]:
        price_label = _marketing_price_label(
            display.get("listing_price"),
            display.get("listing_currency"),
            language,
        )
    type_label = None
    if display.get("property_type"):
        type_label = translate(
            f"property_type_{display['property_type']}", language=language
        )
    agent = _agent_snapshot(display, language) if include_agent else None
    locality = (
        display.get("neighborhood")
        or display.get("locality")
        or display.get("jurisdiction")
        or ""
    )
    facts = {
        "property_id": display.get("id"),
        "organization_id": display.get("organization_id"),
        "agent_id": display.get("agent_id"),
        "title": compact_property_title(display),
        "location_line": compact_property_location(display),
        "locality": locality,
        "jurisdiction": display.get("jurisdiction"),
        "purpose": purpose,
        "purpose_label": _purpose_label(purpose, language),
        "type_label": type_label,
        "price_label": price_label,
        "listing_price": display.get("listing_price"),
        "listing_currency": display.get("listing_currency"),
        "rooms": display.get("rooms"),
        "bedrooms": display.get("bedrooms"),
        "bathrooms": display.get("bathrooms"),
        "covered_m2": display.get("covered_m2"),
        "total_m2": display.get("total_m2"),
        "chips": _chips(display, language),
        "description": (display.get("description") or "").strip() or None,
        "price_policy": price_policy,
        "brand_name": get_brand_name(),
        "organization_name": (settings.get("display_name") or "").strip() or get_brand_name(),
        "organization_logo": str(org_logo) if org_logo else None,
    }
    for key in list(facts):
        if key.lower() in FORBIDDEN_FACT_KEYS:
            facts.pop(key, None)
    return {
        "facts": facts,
        "photos": photos,
        "photo_count": len(photos),
        "cover_id": photos[0]["id"] if photos else None,
        "agent": agent,
        "include_agent": bool(include_agent and agent),
        "language": language,
    }


def context_to_snapshot(context):
    payload = {
        "facts": (context or {}).get("facts") or {},
        "photos": (context or {}).get("photos") or [],
        "photo_count": (context or {}).get("photo_count") or 0,
        "cover_id": (context or {}).get("cover_id"),
        "include_agent": (context or {}).get("include_agent"),
        "language": (context or {}).get("language") or "es",
    }
    return payload


def ai_prompt_facts(context):
    facts = (context or {}).get("facts") or {}
    return {
        "title": facts.get("title"),
        "locality": facts.get("locality"),
        "type_label": facts.get("type_label"),
        "purpose_label": facts.get("purpose_label"),
        "chips": facts.get("chips") or [],
        "description": (facts.get("description") or "")[:280],
    }
