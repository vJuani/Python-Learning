"""Shared commercial listing assets for brochure, and later social creatives."""

from __future__ import annotations

from datetime import date

from modules.agent_branding import get_agent_branding
from modules.agent_photo import resolve_agent_photo_path
from modules.branding import get_brand_name
from modules.database.agents_repository import get_agent_record
from modules.database.organization_settings_repository import get_organization_settings
from modules.i18n import translate
from modules.organization_marketing_logo import get_organization_marketing_branding
from modules.organization_settings import normalize_accent_color
from modules.property_detail_view import (
    compact_property_location,
    compact_property_title,
    compact_property_unit,
    extra_feature_chips,
    parse_external_metadata,
)
from modules.property_inventory import decorate_property_for_display
from modules.property_types import normalize_listing_purpose


def _present(value):
    if value in (None, ""):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _purpose_eyebrow(purpose, language):
    if purpose == "rental":
        return translate("property_brochure_for_rent", language=language)
    if purpose == "temporary_rental":
        return translate("property_brochure_for_temp", language=language)
    return translate("property_brochure_for_sale", language=language)


def _highlights(display, language):
    rows = []
    pairs = (
        ("rooms", "property_rooms", None),
        ("bedrooms", "property_bedrooms", None),
        ("bathrooms", "property_bathrooms", None),
    )
    for field, key, _suffix in pairs:
        if display.get(field) in (None, ""):
            continue
        rows.append((translate(key, language=language).upper(), str(display[field])))
    area = display.get("covered_m2")
    if area in (None, ""):
        area = display.get("total_m2")
    if area not in (None, ""):
        number = int(area) if float(area) == int(float(area)) else area
        rows.append((translate("property_brochure_surface", language=language).upper(), f"{number} m²"))
    parking = display.get("parking_spaces")
    if parking not in (None, "") and int(parking) > 0:
        rows.append((translate("property_parking_spaces", language=language).upper(), str(int(parking))))
    return rows


def _chips(display, language):
    chips = []
    if display.get("property_type"):
        chips.append(translate(f"property_type_{display['property_type']}", language=language))
    if display.get("purpose_label"):
        chips.append(display["purpose_label"])
    chips.extend(display.get("fact_parts") or [])
    if display.get("area_label"):
        chips.append(display["area_label"])
    if display.get("parking_label"):
        chips.append(display["parking_label"])
    seen = set()
    clean = []
    for item in chips:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        clean.append(text)
    return clean


def _load_gallery(property_data):
    from modules.property_sync.media import (
        get_property_media_for_generation,
        resolve_media_filesystem_path,
    )
    from modules.property_sync.remote_media import fetch_allowed_image_bytes

    items = []
    cache = {}
    try:
        media_items = get_property_media_for_generation(property_data, limit=5)
    except Exception:
        return items
    for item in media_items:
        try:
            path = resolve_media_filesystem_path(item)
            if path is not None:
                items.append(str(path))
                continue
            remote = fetch_allowed_image_bytes(item.get("original_url"), cache=cache)
            if remote:
                items.append(remote)
        except Exception:
            continue
    return items


def _clean_text(value):
    text = " ".join(str(value or "").split())
    return text or None


def _organization_branding(organization_id, language):
    """Office brand for this property. Never another office and never the product logo."""
    settings = get_organization_settings(organization_id) or {}
    office = get_organization_marketing_branding(
        organization_id,
        language=language,
        materialize_url=True,
    ) or {}
    if office.get("organization_id") not in (None, "", organization_id):
        try:
            if int(office.get("organization_id")) != int(organization_id):
                office = {}
        except (TypeError, ValueError):
            office = {}
    return {
        "organization_id": organization_id,
        "name": _clean_text(office.get("brand_name") or office.get("wordmark_text")),
        "logo": office.get("logo_path") or None,
        "accent_color": normalize_accent_color(settings.get("accent_color")),
        "legal_broker_name": _clean_text(office.get("legal_broker_name")),
        "legal_broker_license": _clean_text(office.get("legal_broker_license")),
        "legal_footer_line": _clean_text(settings.get("legal_footer_line")),
    }


def _attach_photo(branding):
    if not branding or branding.get("photo_path") or not branding.get("has_photo"):
        return branding
    agent = get_agent_record(branding.get("agent_id"), branding.get("organization_id"))
    photo = resolve_agent_photo_path(agent)
    if photo:
        branding["photo_path"] = str(photo)
    return branding


def build_property_presentation_assets(
    property_data,
    *,
    language="es",
    include_agent=True,
):
    """Cover, gallery[:5], agent, org, price and features for commercial creatives."""
    display = decorate_property_for_display(property_data, language=language)
    meta = parse_external_metadata(display)
    purpose = normalize_listing_purpose(display.get("listing_purpose"))
    branding = None
    if include_agent:
        branding = get_agent_branding(
            display.get("agent_id"),
            display.get("organization_id"),
            language=language,
            agent_login_only=True,
        )
        branding = _attach_photo(branding)
        if branding and not any(
            (branding.get("name"), branding.get("phone"), branding.get("email"))
        ):
            branding = None
    gallery = _load_gallery(display)
    mls = meta.get("mlsid")
    if not _present(mls):
        mls = None
    organization_id = display.get("organization_id")
    return {
        "property": display,
        "title": compact_property_title(display),
        "unit_line": compact_property_unit(display, language),
        "location_line": compact_property_location(display),
        "full_address": display.get("formatted_address") or display.get("address") or "",
        "cover": gallery[0] if gallery else None,
        "gallery": gallery[:5],
        "agent_branding": branding,
        "organization_branding": _organization_branding(organization_id, language),
        "platform_name": get_brand_name(),
        "formatted_price": display.get("price_display"),
        "key_features": _chips(display, language),
        "highlights": _highlights(display, language),
        "extra_features": extra_feature_chips(display, language),
        "description": display.get("description") if _present(display.get("description")) else None,
        "eyebrow": _purpose_eyebrow(purpose, language),
        "mls": mls,
        "generated_on": date.today().strftime("%d/%m/%Y"),
        "sheet_label": translate("property_brochure_sheet", language=language),
        "about_label": translate("property_brochure_about", language=language),
        "features_label": translate("property_brochure_facts", language=language),
        "location_label": translate("property_brochure_location", language=language),
        "advisor_label": translate("property_brochure_responsible", language=language),
        "broker_label": translate("property_brochure_broker", language=language),
        "license_label": translate("property_brochure_license", language=language),
        "agent_role": translate("property_brochure_agent_role", language=language),
    }
