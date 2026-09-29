"""
Generate a commercial property brochure PDF.
"""

from __future__ import annotations

import re
import unicodedata

from modules.i18n import translate
from modules.property_media_access import (
    PropertyMediaError,
    require_property_media_access,
)
from modules.database.properties_repository import get_property_record
from modules.pdf_property_brochure import build_property_brochure_pdf
from modules.property_presentation import build_property_presentation_assets


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
    raw = str(brand_name or "").strip()
    prefix = _slug_part(raw, fallback="Ficha", max_len=16)
    if not raw or prefix.lower().startswith("jrh"):
        prefix = "Ficha"
    address = _slug_part(property_data.get("address"), max_len=36)
    external_id = property_data.get("external_id")
    if _present(external_id):
        code = _slug_part(external_id, fallback="Propiedad", max_len=24)
        return f"{prefix}_{code}_{address}.pdf"
    return f"{prefix}_Propiedad_{address}.pdf"


def _attach_stored_whatsapp(agent):
    """Use the number saved on this agent. Do not invent one."""
    if agent.get("whatsapp"):
        return agent
    from modules.agent_contact_channels import resolve_agent_contact_channels

    channels = resolve_agent_contact_channels(
        agent.get("agent_id"),
        agent.get("organization_id"),
    )
    stored = " ".join(str(channels.get("whatsapp_number") or "").split())
    if stored:
        agent["whatsapp"] = stored
    return agent


def resolve_property_agent_contact(property_data):
    from modules.agent_branding import get_agent_branding

    branding = get_agent_branding(
        (property_data or {}).get("agent_id"),
        (property_data or {}).get("organization_id"),
        agent_login_only=True,
    )
    if not branding:
        return None
    if not any((branding.get("name"), branding.get("phone"), branding.get("email"))):
        return None
    return branding


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

    assets = build_property_presentation_assets(
        property_data,
        language=language,
        include_agent=bool(include_agent_contact),
    )
    display = assets["property"]
    org = assets["organization_branding"]
    agent = assets["agent_branding"] if include_agent_contact else None
    if agent:
        agent = {
            **agent,
            "role": translate("property_brochure_agent_role", language=language),
        }
        _attach_stored_whatsapp(agent)

    payload = {
        "title": assets["title"] or display.get("address") or "Property",
        "unit_line": assets["unit_line"] or None,
        "location_line": assets["location_line"] or None,
        "full_address": assets["full_address"],
        "eyebrow": assets["eyebrow"],
        "brand_name": org.get("name"),
        "organization": org,
        "powered_by": f"Powered by {assets.get('platform_name') or 'JRH One'}",
        "broker_label": assets.get("broker_label"),
        "license_label": assets.get("license_label"),
        "mls": assets["mls"],
        "generated_on": assets["generated_on"],
        "sheet_label": assets["sheet_label"],
        "price": assets["formatted_price"],
        "chips": assets["key_features"],
        "highlights": assets["highlights"],
        "features": assets["extra_features"],
        "about_label": assets["about_label"],
        "features_label": assets["features_label"],
        "location_label": assets["location_label"],
        "advisor_label": assets["advisor_label"],
        "description": assets["description"],
        "agent": agent,
        "hero_image": assets["cover"],
        "gallery": assets["gallery"],
    }

    pdf_bytes = build_property_brochure_pdf(payload)
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        raise PropertyMediaError("property_brochure_err_generate", 500)

    return {
        "pdf_bytes": pdf_bytes,
        "filename": brochure_filename(display, org.get("name")),
        "property": display,
        "include_agent_contact": bool(include_agent_contact and agent),
        "agent": agent,
        "assets": assets,
    }
