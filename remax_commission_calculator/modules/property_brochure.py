"""
Generate a commercial property brochure PDF.
"""

from __future__ import annotations

import re
import unicodedata

from modules.database.organization_settings_repository import (
    get_organization_settings,
)
from modules.database.properties_repository import get_property_record
from modules.database.users_repository import get_user_by_agent_id
from modules.i18n import translate
from modules.operation_summary import _brand_logo_path
from modules.property_inventory import decorate_property_for_display
from modules.property_media_access import (
    PropertyMediaError,
    require_property_media_access,
)
from modules.pdf_property_brochure import build_property_brochure_pdf


def _present(value):
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _slug_part(value, fallback="Propiedad", max_len=40):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s]+", "_", text.strip())
    text = re.sub(r"_+", "_", text).strip("._")
    return (text[:max_len] or fallback)


def brochure_filename(property_data, brand_name=None):
    prefix = _slug_part(brand_name or "JRH", fallback="JRH", max_len=16)
    if prefix.lower().startswith("jrh"):
        prefix = "JRH"
    address = _slug_part(property_data.get("address"), max_len=36)
    external_id = property_data.get("external_id")
    if _present(external_id):
        code = _slug_part(external_id, fallback="Propiedad", max_len=24)
        return f"{prefix}_{code}_{address}.pdf"
    return f"{prefix}_Propiedad_{address}.pdf"


def resolve_property_agent_contact(property_data):
    agent_id = property_data.get("agent_id")
    if not agent_id:
        return None

    name = (property_data.get("agent_name") or "").strip() or None
    user = get_user_by_agent_id(agent_id, property_data["organization_id"])
    phone = None
    email = None
    if user:
        phone = (user.get("phone") or "").strip() or None
        email = (user.get("email") or "").strip() or None
        if name is None:
            assembled = " ".join(
                part
                for part in (
                    user.get("first_name"),
                    user.get("last_name"),
                )
                if part
            ).strip()
            name = assembled or (user.get("username") or "").strip() or None

    if not any((name, phone, email)):
        return None

    return {
        "name": name,
        "phone": phone,
        "email": email,
    }


def _chip_values(property_data, language):
    chips = []
    if property_data.get("property_type"):
        chips.append(
            translate(
                f"property_type_{property_data['property_type']}",
                language=language,
            )
        )
    if property_data.get("purpose_label"):
        chips.append(property_data["purpose_label"])
    chips.extend(property_data.get("fact_parts") or [])
    if property_data.get("area_label"):
        chips.append(property_data["area_label"])
    if property_data.get("parking_label"):
        chips.append(property_data["parking_label"])
    chips.extend(property_data.get("feature_labels") or [])
    return [item for item in chips if _present(item)]


def _fact_values(property_data, language):
    facts = []
    mapping = (
        ("rooms", "property_rooms", None),
        ("bedrooms", "property_bedrooms", None),
        ("bathrooms", "property_bathrooms", None),
        ("total_m2", "property_total_m2", " m²"),
        ("covered_m2", "property_covered_m2", " m²"),
        ("parking_spaces", "property_parking_spaces", None),
    )
    for field, label_key, suffix in mapping:
        value = property_data.get(field)
        if not _present(value):
            continue
        label = translate(label_key, language=language)
        rendered = f"{label} {value}{suffix or ''}"
        facts.append(rendered)
    return facts


def generate_property_brochure(
    property_id,
    organization_id,
    include_agent_contact,
    requesting_user,
    *,
    is_guest=False,
    language="es",
):
    if not property_id:
        raise PropertyMediaError("property_brochure_not_found", 404)

    property_data = get_property_record(property_id, organization_id)
    require_property_media_access(
        requesting_user,
        property_data,
        is_guest=is_guest,
    )

    display = decorate_property_for_display(property_data, language=language)
    settings = get_organization_settings(organization_id) or {}
    brand_name = (
        (settings.get("display_name") or "").strip()
        or None
    )
    logo_path = _brand_logo_path(settings.get("logo_path"))
    zone_parts = [
        part
        for part in (
            display.get("neighborhood"),
            display.get("jurisdiction"),
        )
        if _present(part)
    ]
    agent_payload = None
    if include_agent_contact:
        contact = resolve_property_agent_contact(property_data)
        if contact:
            agent_payload = {
                **contact,
                "role": translate(
                    "property_brochure_agent_role",
                    language=language,
                ),
            }

    payload = {
        "title": display.get("address") or "Property",
        "brand_name": brand_name,
        "logo_path": str(logo_path) if logo_path else None,
        "code": (
            display.get("external_id")
            if _present(display.get("external_id"))
            else None
        ),
        "address": display.get("address"),
        "zone": " · ".join(zone_parts) if zone_parts else None,
        "price": display.get("price_display"),
        "chips": _chip_values(display, language),
        "facts": _fact_values(display, language),
        "facts_title": translate(
            "property_brochure_facts",
            language=language,
        ),
        "description": (
            display.get("description")
            if _present(display.get("description"))
            else None
        ),
        "description_title": translate(
            "property_description",
            language=language,
        ),
        "gallery_title": translate(
            "property_brochure_gallery",
            language=language,
        ),
        "agent_title": translate(
            "property_brochure_agent_title",
            language=language,
        ),
        "agent": agent_payload,
        "footer": brand_name,
        "hero_image": None,
        "gallery": [],
    }

    try:
        from modules.property_sync.media import (
            get_property_media_for_generation,
            resolve_media_filesystem_path,
        )

        media_items = get_property_media_for_generation(property_data)
        gallery_paths = []
        for item in media_items:
            path = resolve_media_filesystem_path(item)
            if path is not None:
                gallery_paths.append(str(path))
        if gallery_paths:
            payload["hero_image"] = gallery_paths[0]
            payload["gallery"] = gallery_paths[1:]
    except Exception:
        pass

    pdf_bytes = build_property_brochure_pdf(payload)
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise PropertyMediaError("property_brochure_err_generate", 500)

    return {
        "pdf_bytes": pdf_bytes,
        "filename": brochure_filename(display, brand_name),
        "property": display,
        "include_agent_contact": bool(include_agent_contact and agent_payload),
        "agent": agent_payload,
    }
