"""Safe, publicable marketing facts. Never includes private CRM data."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

from modules.agent_branding import get_agent_presentation_asset
from modules.database.organization_settings_repository import get_organization_settings
from modules.database.organizations_repository import get_organization_by_id
from modules.formatting import format_listing_money
from modules.i18n import translate
from modules.marketing_branding import resolve_marketing_branding
from modules.marketing_language import (
    default_kicker,
    listing_benefit_line,
    marketing_zone_line,
    operation_headline,
)
from modules.property_detail_view import (
    compact_property_location,
    compact_property_title,
    maintenance_line,
    parse_external_metadata,
)
from modules.property_features import FEATURE_KEYS
from modules.property_inventory import decorate_property_for_display
from modules.property_media_access import can_access_property_media
from modules.database.property_media_repository import list_property_media
from modules.listing_photo_origin import (
    ORIGINAL_SOURCE_TYPES,
    eligible_listing_photos,
    photo_source_type,
)
from modules.property_sync.media import (
    get_property_media_url,
    get_property_original_media,
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

AMENITY_LABELS = {
    "es": {
        "balcony": "balcón",
        "terrace": "terraza",
        "garden": "jardín",
        "pool": "pileta",
        "grill": "parrilla",
        "laundry": "lavadero",
        "storage": "baulera",
        "elevator": "ascensor",
        "security": "seguridad",
        "furnished": "amoblado",
    },
    "en": {
        "balcony": "balcony",
        "terrace": "terrace",
        "garden": "garden",
        "pool": "pool",
        "grill": "grill",
        "laundry": "laundry",
        "storage": "storage",
        "elevator": "elevator",
        "security": "security",
        "furnished": "furnished",
    },
}


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


def _count_chip(value, singular, plural):
    if value in (None, ""):
        return None
    try:
        number = int(value) if float(value) == int(float(value)) else value
    except (TypeError, ValueError):
        number = value
    label = singular if str(number) == "1" else plural
    return f"{number} {label}"


def _chips(display, language):
    chips = []
    rooms = _count_chip(
        display.get("rooms"),
        "ambiente",
        translate("property_rooms", language=language).lower(),
    )
    if rooms:
        chips.append(rooms)
    bedrooms = _count_chip(
        display.get("bedrooms"),
        "dormitorio",
        translate("property_bedrooms", language=language).lower(),
    )
    if bedrooms:
        chips.append(bedrooms)
    bathrooms = _count_chip(
        display.get("bathrooms"),
        "baño",
        translate("property_bathrooms", language=language).lower(),
    )
    if bathrooms:
        chips.append(bathrooms)
    area = display.get("covered_m2")
    if area in (None, ""):
        area = display.get("total_m2")
    if area not in (None, ""):
        value = float(area)
        if value == int(value):
            number = str(int(value))
        elif language == "es":
            number = f"{value:.2f}".replace(".", ",")
        else:
            number = f"{value:.2f}"
        chips.append(f"{number} m²")
    return chips[:4]


def _as_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number == int(number) else number


def _amenity_facts(display, language):
    features = display.get("features") if isinstance(display.get("features"), dict) else {}
    labels = AMENITY_LABELS.get("en" if str(language).lower().startswith("en") else "es")
    amenities = []
    flags = {}
    for key in FEATURE_KEYS:
        if features.get(key):
            flags[key] = True
            amenities.append(labels.get(key, key))
    garden = bool(features.get("garden"))
    flags["patio"] = garden
    flags["balcony"] = bool(features.get("balcony"))
    flags["terrace"] = bool(features.get("terrace"))
    flags["pool"] = bool(features.get("pool"))
    flags["grill"] = bool(features.get("grill"))
    flags["security"] = bool(features.get("security"))
    flags["elevator"] = bool(features.get("elevator"))
    return amenities, {key: value for key, value in flags.items() if value}


def _listing_detail_facts(display):
    meta = parse_external_metadata(display)
    dimensions = meta.get("dimensions") if isinstance(meta.get("dimensions"), dict) else {}
    covered = _as_number(display.get("covered_m2")) or _as_number(dimensions.get("covered"))
    total = _as_number(display.get("total_m2")) or _as_number(dimensions.get("total_built"))
    uncovered = _as_number(dimensions.get("uncovered"))
    if uncovered is None and covered is not None and total is not None and total > covered:
        uncovered = _as_number(total - covered)
    floor = str(meta.get("floor") or "").strip() or None
    orientation = str(meta.get("orientation") or "").strip() or None
    condition = str(meta.get("property_condition") or "").strip() or None
    year_build = meta.get("year_build")
    try:
        year_build = int(year_build)
    except (TypeError, ValueError):
        year_build = None
    age = None
    if year_build and 1800 <= year_build <= 2100:
        from datetime import datetime

        age = datetime.utcnow().year - year_build
        if age < 0 or age > 200:
            age = None
    expenses = maintenance_line(display, language="es")
    return {
        "covered_m2": covered,
        "total_m2": total,
        "uncovered_m2": uncovered,
        "floor": floor,
        "orientation": orientation,
        "condition": condition,
        "age": age,
        "year_build": year_build,
        "expenses": expenses,
    }


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
    branding = get_agent_presentation_asset(
        property_data.get("agent_id"),
        property_data.get("organization_id"),
        language=language,
        agent_login_only=True,
    )
    if not branding:
        return None
    whatsapp = branding.get("whatsapp") if branding.get("whatsapp_enabled") else None
    instagram = branding.get("instagram") if branding.get("instagram_enabled") else None
    if not whatsapp and not instagram:
        logger.warning(
            "marketing agent contact missing whatsapp and instagram agent=%s",
            branding.get("agent_id"),
        )
    return {
        "agent_id": branding.get("agent_id"),
        "organization_id": branding.get("organization_id"),
        "name": branding.get("name"),
        "phone": branding.get("phone"),
        "whatsapp": whatsapp,
        "whatsapp_enabled": bool(branding.get("whatsapp_enabled") and whatsapp),
        "email": branding.get("email"),
        "instagram": instagram,
        "instagram_enabled": bool(branding.get("instagram_enabled") and instagram),
        "linkedin": branding.get("linkedin"),
        "title": branding.get("title"),
        "has_photo": bool(branding.get("has_photo")),
        "photo_path": branding.get("photo_path"),
        "photo_variant": branding.get("photo_variant") or "none",
        "profile_photo_key": branding.get("profile_photo_key"),
        "organization": branding.get("organization"),
    }


def _catalog_media(property_data):
    items = list_property_media(
        property_data.get("organization_id"),
        property_data.get("id"),
    )
    return eligible_listing_photos([item for item in items if is_displayable_media(item)])


def _serialize_photo(item, *, order=None, property_id=None):
    source_type = photo_source_type(item)
    return {
        "id": item.get("id"),
        "is_cover": bool(item.get("is_cover")),
        "position": item.get("position"),
        "order": order if order is not None else item.get("position"),
        "source": item.get("source"),
        "url_kind": item.get("url_kind"),
        "storage_strategy": item.get("storage_strategy"),
        "original_url": item.get("original_url"),
        "storage_key": item.get("storage_key"),
        "path": item.get("storage_key") or item.get("path"),
        "url": get_property_media_url(item, property_id or item.get("property_id"))
        or item.get("original_url")
        or item.get("remote_url"),
        "content_type": item.get("content_type"),
        "media_type": item.get("media_type") or "photo",
        "width": item.get("width"),
        "height": item.get("height"),
        "source_type": source_type,
        "is_original": source_type in ORIGINAL_SOURCE_TYPES,
    }


def _media_items(property_data, selected_ids=None):
    records = get_property_original_media(
        property_data.get("id"),
        property_data.get("organization_id"),
        limit=PHOTO_LIMIT,
        property_row=property_data,
    )
    items = [record["media"] for record in records]
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
    return [
        _serialize_photo(item, order=index, property_id=property_data.get("id"))
        for index, item in enumerate(items[:PHOTO_LIMIT])
    ]


def list_wizard_photos(property_data, selected_ids=None):
    selected = {item["id"] for item in _media_items(property_data, selected_ids)}
    photos = []
    for item in _catalog_media(property_data):
        photos.append(
            {
                **_serialize_photo(item, property_id=property_data.get("id")),
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
    organization = get_organization_by_id(display.get("organization_id")) or {}
    branding = resolve_marketing_branding(
        settings,
        language=language,
        organization_name=organization.get("name"),
    )
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
    details = _listing_detail_facts(display)
    amenities, amenity_flags = _amenity_facts(display, language)
    description = (display.get("description") or "").strip() or None
    if description and len(description) > 900:
        description = description[:900].rstrip()
    facts = {
        "property_id": display.get("id"),
        "organization_id": display.get("organization_id"),
        "agent_id": display.get("agent_id"),
        "title": compact_property_title(display),
        "location_line": compact_property_location(display),
        "postal_code": display.get("postal_code") or None,
        "neighborhood": display.get("neighborhood") or None,
        "locality": display.get("locality") or locality or None,
        "jurisdiction": display.get("jurisdiction"),
        "purpose": purpose,
        "purpose_label": _purpose_label(purpose, language),
        "operation_type": purpose,
        "type_label": type_label,
        "price_label": price_label,
        "listing_price": display.get("listing_price"),
        "listing_currency": display.get("listing_currency"),
        "rooms": display.get("rooms"),
        "bedrooms": display.get("bedrooms"),
        "bathrooms": display.get("bathrooms"),
        "garages": display.get("parking_spaces"),
        "parking_spaces": display.get("parking_spaces"),
        "covered_m2": details["covered_m2"] if details["covered_m2"] is not None else display.get("covered_m2"),
        "total_m2": details["total_m2"] if details["total_m2"] is not None else display.get("total_m2"),
        "uncovered_m2": details["uncovered_m2"],
        "floor": details["floor"],
        "orientation": details["orientation"],
        "age": details["age"],
        "condition": details["condition"],
        "expenses": details["expenses"],
        "amenities": amenities,
        "chips": _chips(display, language),
        "description": description,
        "price_policy": price_policy,
        "brand_name": branding["brand_name"],
        "office_name": branding["office_name"],
        "organization_name": branding["office_name"],
        "office_logo": branding["logo_path"],
        "organization_logo": branding["logo_path"],
        "logo_path": branding.get("logo_path"),
        "marketing_logo_url": branding.get("logo_url") or settings.get("marketing_logo_url"),
        "marketing_logo_path": settings.get("marketing_logo_path") or branding.get("logo_logical_path"),
        "marketing_logo": settings.get("marketing_logo") or branding.get("logo_url"),
        "logo_source": branding.get("logo_source"),
        "logo_url": branding.get("logo_url"),
        "has_logo": branding["has_logo"],
        "wordmark_text": branding.get("wordmark_text") or branding["brand_name"],
        "show_wordmark": bool(branding.get("wordmark_text") or not branding["has_logo"]),
        "property_type": display.get("property_type"),
        "kicker": default_kicker(language, {"purpose": purpose}),
        "benefit_line": listing_benefit_line(language, {
            "purpose": purpose,
            "property_type": display.get("property_type"),
            "type_label": type_label,
            "rooms": display.get("rooms"),
            "covered_m2": display.get("covered_m2"),
            "total_m2": display.get("total_m2"),
            "locality": locality,
            "amenities": amenities,
            "patio": amenity_flags.get("patio"),
        }),
        "operation_title": operation_headline(language, {
            "type_label": type_label,
            "property_type": display.get("property_type"),
            "purpose": purpose,
            "rooms": display.get("rooms"),
            "locality": locality,
        }),
        "zone_line": marketing_zone_line({
            "locality": locality,
            "jurisdiction": display.get("jurisdiction"),
            "location_line": compact_property_location(display),
        }),
        "legal_broker_name": branding["legal_broker_name"],
        "legal_broker_license": branding["legal_broker_license"],
        "broker_name": branding.get("broker_name") or branding["legal_broker_name"],
        "broker_license": branding.get("broker_license") or branding["legal_broker_license"],
        "broker_footer_text": branding["legal_footer_line"],
        "legal_footer_line": branding["legal_footer_line"],
        "marketing_phone": branding["marketing_phone"],
        "marketing_instagram": branding["marketing_instagram"],
        "marketing_whatsapp": branding["marketing_whatsapp"],
        "marketing_email": branding["marketing_email"],
        "legal_complete": branding["legal_complete"],
        "requires_legal_review": branding["requires_legal_review"],
        "used_demo_fallback": branding["used_demo_fallback"],
        "publishable": branding["publishable"],
    }
    if amenity_flags:
        facts["amenity_flags"] = amenity_flags
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
