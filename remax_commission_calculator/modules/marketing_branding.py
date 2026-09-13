"""Organization branding for generated marketing pieces.

JRH One is the product / backoffice brand. It must never appear inside a
publishable creative. The visible brand is the active real-estate office.
"""

from __future__ import annotations

import logging
from pathlib import Path

from modules.branding import get_brand_name

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

SYSTEM_BRAND_NAME = "JRH One"
SYSTEM_BRAND_TOKENS = (
    "jrh one",
    "jrhone",
    "jrh-one",
    "jrh_one",
)
PLACEHOLDER_BRANDS = frozenset(
    {
        "inmobiliaria principal",
        "principal brokerage",
        "principal office",
        "oficina principal",
    }
)
NEUTRAL_BRAND_FALLBACK = "RE/MAX Data House"

DEMO_MARKETING_BRANDING = {
    "marketing_brand_name": "RE/MAX Data House",
    "legal_broker_name": "Mauro Marvisi",
    "legal_broker_license": "CUCICBA 1762 / CMCPSI 5574",
    "legal_office_name": "RE/MAX Data House",
    "legal_footer_line": (
        "Corredor Público Mauro Marvisi CUCICBA 1762 / CMCPSI 5574"
    ),
}

BRANDING_ALIASES = {
    "office_name": "marketing_brand_name",
    "organization_name": "organization_name",
    "marketing_logo": "marketing_logo_url",
    "office_logo": "logo_path",
    "organization_logo": "logo_path",
    "logo": "logo_path",
    "broker_name": "legal_broker_name",
    "broker_license": "legal_broker_license",
    "broker_footer_text": "legal_footer_line",
}

MARKETING_BRANDING_KEYS = (
    "marketing_brand_name",
    "marketing_logo_url",
    "marketing_logo_dark_url",
    "marketing_logo_light_url",
    "legal_broker_name",
    "legal_broker_license",
    "legal_office_name",
    "legal_footer_line",
    "marketing_phone",
    "marketing_instagram",
    "marketing_whatsapp",
    "marketing_email",
)


def _clean(value):
    return " ".join(str(value or "").split())


def system_brand_name():
    return get_brand_name() or SYSTEM_BRAND_NAME


def is_system_brand_text(text):
    folded = " ".join(str(text or "").lower().split())
    if not folded:
        return False
    compact = folded.replace(" ", "").replace("-", "").replace("_", "")
    return "jrhone" in compact or any(token in folded for token in SYSTEM_BRAND_TOKENS)


def is_placeholder_brand(text):
    folded = _clean(text).casefold()
    if not folded:
        return True
    return folded in PLACEHOLDER_BRANDS or is_system_brand_text(text)


def usable_brand_name(*candidates):
    for candidate in candidates:
        value = _clean(candidate)
        if value and not is_placeholder_brand(value):
            return value
    return ""


def marketing_legal_is_configured(settings):
    settings = settings or {}
    return bool(
        _clean(settings.get("legal_broker_name"))
        and _clean(settings.get("legal_broker_license"))
    )


def resolve_local_logo_path(candidate):
    from modules.organization_marketing_logo import resolve_stored_logo_file

    return resolve_stored_logo_file(candidate)


def get_organization_marketing_branding(organization, **kwargs):
    from modules.organization_marketing_logo import (
        get_organization_marketing_branding as resolve_office_branding,
    )

    return resolve_office_branding(organization, **kwargs)


def apply_branding_aliases(settings):
    """Accept office_* / organization_* / broker_* aliases from any inmobiliaria."""
    normalized = dict(settings or {})
    for alias, canonical in BRANDING_ALIASES.items():
        if _clean(normalized.get(canonical)):
            continue
        if _clean(normalized.get(alias)):
            normalized[canonical] = normalized.get(alias)
    return normalized


def office_wordmark(brand_name, *, has_logo=False, logo_path=None):
    """Full commercial office name. Keep RE/MAX in the wordmark next to the pin."""
    del has_logo, logo_path
    return _clean(brand_name)


def resolve_creative_logo_path(settings, *, organization_id=None, brand_name=None):
    from modules.organization_marketing_logo import get_organization_marketing_branding

    payload = dict(settings or {})
    if organization_id and not payload.get("organization_id"):
        payload["organization_id"] = organization_id
    branding = get_organization_marketing_branding(payload, materialize_url=True)
    path = branding.get("logo_path")
    return Path(path) if path and Path(path).is_file() else None


def build_legal_footer_line(settings, *, language="es"):
    settings = settings or {}
    stored = _clean(settings.get("legal_footer_line"))
    if stored:
        return stored
    name = _clean(settings.get("legal_broker_name"))
    license_no = _clean(settings.get("legal_broker_license"))
    if not name and not license_no:
        return ""
    role = "Corredor Público" if language == "es" else "Licensed Broker"
    if name and license_no:
        return f"{role} {name} {license_no}"
    return name or license_no


def resolve_marketing_branding(
    settings,
    *,
    language="es",
    organization_name=None,
    apply_demo_fallback=True,
):
    """Resolve the brand that may appear inside a generated creative."""
    settings = apply_branding_aliases(settings)
    stored_brand = usable_brand_name(
        settings.get("marketing_brand_name"),
        settings.get("office_name"),
    )
    stored_office = usable_brand_name(
        settings.get("legal_office_name"),
        settings.get("office_name"),
    )
    stored_broker = _clean(
        settings.get("legal_broker_name") or settings.get("broker_name")
    )
    stored_license = _clean(
        settings.get("legal_broker_license") or settings.get("broker_license")
    )
    stored_footer = _clean(
        settings.get("legal_footer_line") or settings.get("broker_footer_text")
    )
    org_name = usable_brand_name(
        organization_name,
        settings.get("organization_name"),
        settings.get("office_name"),
        settings.get("display_name"),
        settings.get("trade_name"),
    )
    used_demo = False
    brand_name = stored_brand or org_name
    if not brand_name:
        logger.warning(
            "marketing branding missing commercial name org=%s; using demo fallback",
            settings.get("organization_id"),
        )
        if apply_demo_fallback:
            brand_name = DEMO_MARKETING_BRANDING["marketing_brand_name"]
            used_demo = True
        else:
            brand_name = NEUTRAL_BRAND_FALLBACK

    if apply_demo_fallback:
        if not stored_broker:
            stored_broker = DEMO_MARKETING_BRANDING["legal_broker_name"]
            used_demo = True
        if not stored_license:
            stored_license = DEMO_MARKETING_BRANDING["legal_broker_license"]
            used_demo = True
        if not stored_footer:
            stored_footer = DEMO_MARKETING_BRANDING["legal_footer_line"]
        if not stored_office:
            stored_office = brand_name

    office_name = stored_office or brand_name
    footer = stored_footer or build_legal_footer_line(
        {
            "legal_broker_name": stored_broker,
            "legal_broker_license": stored_license,
        },
        language=language,
    )
    office_logo = get_organization_marketing_branding(
        settings,
        language=language,
        materialize_url=True,
    )
    logo_path = office_logo.get("logo_path")
    if logo_path and Path(logo_path).is_file():
        logo_path = Path(logo_path)
    else:
        logo_path = None
    if not logo_path and office_logo.get("logo_source") != "url":
        logger.warning(
            "marketing branding missing office logo org=%s; using wordmark only",
            settings.get("organization_id"),
        )
    configured = marketing_legal_is_configured(settings)
    legal_complete = bool(stored_broker and stored_license)
    logo_value = str(logo_path) if logo_path else None
    return {
        "brand_name": brand_name,
        "office_name": office_name,
        "organization_name": office_name,
        "office_logo": logo_value,
        "organization_logo": logo_value,
        "logo_path": logo_value,
        "logo_source": office_logo.get("logo_source"),
        "logo_url": office_logo.get("logo_url"),
        "has_logo": bool(logo_path) or bool(office_logo.get("has_logo")),
        "wordmark_text": office_wordmark(
            brand_name, has_logo=bool(logo_path), logo_path=logo_value
        ),
        "show_wordmark": True,
        "legal_broker_name": stored_broker,
        "legal_broker_license": stored_license,
        "broker_name": stored_broker,
        "broker_license": stored_license,
        "broker_footer_text": footer,
        "legal_footer_line": footer,
        "marketing_phone": _clean(settings.get("marketing_phone")),
        "marketing_instagram": _clean(settings.get("marketing_instagram")),
        "marketing_whatsapp": _clean(settings.get("marketing_whatsapp")),
        "marketing_email": _clean(settings.get("marketing_email")),
        "legal_complete": legal_complete,
        "configured": configured,
        "requires_legal_review": not configured,
        "used_demo_fallback": used_demo or not configured,
        "publishable": configured and legal_complete,
        "system_brand_name": system_brand_name(),
    }


def branding_from_facts(facts):
    facts = facts or {}
    brand = usable_brand_name(
        facts.get("brand_name"),
        facts.get("office_name"),
        facts.get("organization_name"),
    ) or DEMO_MARKETING_BRANDING["marketing_brand_name"]
    logo = (
        facts.get("organization_logo")
        or facts.get("office_logo")
        or facts.get("logo_path")
    )
    has_logo = bool(logo or facts.get("has_logo"))
    footer = (
        facts.get("legal_footer_line")
        or facts.get("broker_footer_text")
        or ""
    )
    broker = facts.get("legal_broker_name") or facts.get("broker_name") or ""
    license_no = facts.get("legal_broker_license") or facts.get("broker_license") or ""
    return {
        "brand_name": brand,
        "office_name": usable_brand_name(
            facts.get("office_name"), facts.get("organization_name")
        ) or brand,
        "logo_path": logo,
        "office_logo": logo,
        "organization_logo": logo,
        "has_logo": has_logo,
        "wordmark_text": facts.get("wordmark_text")
        or office_wordmark(brand, has_logo=has_logo, logo_path=logo),
        "show_wordmark": bool(facts.get("show_wordmark", True)),
        "legal_broker_name": broker,
        "legal_broker_license": license_no,
        "broker_name": broker,
        "broker_license": license_no,
        "broker_footer_text": footer,
        "legal_footer_line": footer,
        "marketing_phone": facts.get("marketing_phone") or "",
        "marketing_instagram": facts.get("marketing_instagram") or "",
        "marketing_whatsapp": facts.get("marketing_whatsapp") or "",
        "marketing_email": facts.get("marketing_email") or "",
        "legal_complete": bool(facts.get("legal_complete")),
        "configured": not facts.get("requires_legal_review", True),
        "requires_legal_review": bool(facts.get("requires_legal_review")),
        "used_demo_fallback": bool(facts.get("used_demo_fallback")),
        "publishable": bool(facts.get("publishable")),
        "system_brand_name": facts.get("system_brand_name") or system_brand_name(),
    }


def agent_contact_prompt_lock(whatsapp="", instagram="", *, language="es"):
    """Office branding stays separate. Only linked+enabled agent channels go here."""
    wa = _clean(whatsapp)
    ig = _clean(instagram)
    if wa and ig:
        lock_es = (
            "Mostrá al agente como contacto comercial con WhatsApp e Instagram, "
            "evitando repetir teléfono y WhatsApp."
        )
        lock_en = (
            "Show the agent as the commercial contact using WhatsApp and Instagram, "
            "without duplicating phone and WhatsApp."
        )
        agent = (
            "The agent is the commercial contact only and is not the legal broker. "
            "Show WhatsApp and Instagram only. Do not also show a regular phone. "
        )
        copy_suffix = ", agent name + title + WhatsApp + Instagram, never phone + WhatsApp"
        icons = (
            "Use a small WhatsApp icon next to the WhatsApp number and a small "
            "Instagram icon next to the Instagram handle. "
        )
    elif wa:
        lock_es = (
            "Mostrá al agente como contacto comercial solo con WhatsApp. "
            "No muestres Instagram ni un teléfono extra."
        )
        lock_en = (
            "Show the agent as the commercial contact with WhatsApp only. "
            "Do not show Instagram or an extra phone number."
        )
        agent = (
            "The agent is the commercial contact only and is not the legal broker. "
            "Show WhatsApp only. Do not invent Instagram. Do not also show a regular phone. "
        )
        copy_suffix = ", agent name + title + WhatsApp only, never phone + WhatsApp"
        icons = "Use a small WhatsApp icon next to the WhatsApp number. Do not add Instagram. "
    elif ig:
        lock_es = (
            "Mostrá al agente como contacto comercial solo con Instagram. "
            "No muestres WhatsApp ni teléfono."
        )
        lock_en = (
            "Show the agent as the commercial contact with Instagram only. "
            "Do not show WhatsApp or a phone number."
        )
        agent = (
            "The agent is the commercial contact only and is not the legal broker. "
            "Show Instagram only. Do not invent WhatsApp or a regular phone. "
        )
        copy_suffix = ", agent name + title + Instagram only, no phone or WhatsApp"
        icons = "Use a small Instagram icon next to the Instagram handle. Do not add WhatsApp. "
    else:
        lock_es = (
            "Mostrá solo el nombre y el cargo del agente. "
            "No inventes WhatsApp, Instagram ni teléfono."
        )
        lock_en = (
            "Show only the agent name and title. "
            "Do not invent WhatsApp, Instagram, or a phone number."
        )
        agent = (
            "The agent is the commercial contact only and is not the legal broker. "
            "Show name and title only. Do not invent WhatsApp, Instagram, or a phone. "
        )
        copy_suffix = ", agent name + title only, no invented WhatsApp, Instagram, or phone"
        icons = "Do not add WhatsApp or Instagram icons or invented handles. "
    return {
        "lock": lock_es if language == "es" else lock_en,
        "agent": agent,
        "copy_suffix": copy_suffix,
        "icons": icons,
        "has_whatsapp": bool(wa),
        "has_instagram": bool(ig),
    }


def branding_prompt_block(
    branding,
    *,
    include_agent=False,
    language="es",
    agent_whatsapp="",
    agent_instagram="",
):
    branding = branding or {}
    brand = usable_brand_name(branding.get("brand_name"), branding.get("office_name"))
    brand = brand or DEMO_MARKETING_BRANDING["marketing_brand_name"]
    office = usable_brand_name(branding.get("office_name")) or brand
    footer = branding.get("legal_footer_line") or DEMO_MARKETING_BRANDING["legal_footer_line"]
    broker = branding.get("legal_broker_name") or DEMO_MARKETING_BRANDING["legal_broker_name"]
    license_no = (
        branding.get("legal_broker_license")
        or DEMO_MARKETING_BRANDING["legal_broker_license"]
    )
    wordmark = _clean(branding.get("wordmark_text")) or brand
    if branding.get("has_logo"):
        logo = (
            "Place the REAL office logo from the logo reference in the top-left. "
            "Reproduce that exact current file. Do not redraw a generic RE/MAX balloon, "
            "do not use an old mark, and do not invent a logo. Immediately after the "
            f"logo set the full office name '{wordmark}'. Keep RE/MAX in the name if "
            "it is part of the office name. Small, sharp, no extra plate, never JRH One."
        )
    else:
        logo = (
            f"No real office logo file is available. Set the wordmark '{brand}' top-left "
            "in clean type. Do not invent a logo, balloon, or similar mark."
        )
    contact = agent_contact_prompt_lock(
        agent_whatsapp,
        agent_instagram,
        language=language,
    )
    agent = contact["agent"] if include_agent else "Do not show an agent. "
    if language == "es":
        lock = (
            f"Usar la marca visible de la inmobiliaria activa: {brand}. "
            "Usá el logo real de la inmobiliaria provisto como referencia. "
            "No inventes ni rediseñes el logo. No muestres JRH One dentro del creativo. "
            f"{contact['lock'] if include_agent else 'No muestres un agente.'} "
            "Mantené separado y visible el texto "
            f"legal del corredor responsable y su matrícula: {broker} {license_no}."
        )
    else:
        lock = (
            f"Use the active real estate office branding ({brand}), not the app brand. "
            "Use the real estate office logo provided as an input asset. "
            "Do not recreate, redesign, or hallucinate the logo. "
            "Do not display JRH One in the final creative. "
            f"{contact['lock'] if include_agent else 'Do not show an agent.'} "
            "Keep the legal broker name and "
            f"license clearly visible and separate: {broker} {license_no}."
        )
    return (
        f"CREATIVE BRAND LOCK: {lock} "
        f"Never write Inmobiliaria Principal or any generic office placeholder. "
        f"{logo} "
        f"Mandatory legal footer, small, clean, fully inside the safe area, never cropped: '{footer}'. "
        f"{agent}"
        "Visual hierarchy: header office logo + office name top-left and a tiny kicker "
        "top-right; then a large property photo; then operation title, street, locality; "
        "facts, price, CTA; footer agent/contact then legal broker and license."
    )


def validate_creative_branding(
    png_bytes=None,
    *,
    planned_copy=None,
    extra_text="",
    branding=None,
):
    from modules.marketing_language import extract_image_text

    parts = [extra_text or ""]
    if isinstance(planned_copy, dict):
        parts.extend(str(planned_copy.get(key) or "") for key in planned_copy)
    elif planned_copy:
        parts.append(str(planned_copy))
    parts.append(extract_image_text(png_bytes))
    combined = " ".join(part for part in parts if part)
    reasons = []
    if is_system_brand_text(combined):
        reasons.append("system_brand_visible")
    if "inmobiliaria principal" in combined.casefold():
        reasons.append("placeholder_brand")
    branding = branding or {}
    legal_text = " ".join(
        part
        for part in (
            branding.get("legal_broker_name"),
            branding.get("legal_broker_license"),
            branding.get("legal_footer_line"),
            (planned_copy or {}).get("legal_footer") if isinstance(planned_copy, dict) else "",
        )
        if part
    )
    if not branding.get("legal_complete") and not _clean(legal_text):
        reasons.append("missing_legal")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "requires_legal_review": bool(branding.get("requires_legal_review")),
    }
