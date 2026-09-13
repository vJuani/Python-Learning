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
    value = _clean(candidate)
    if not value or value.startswith(("http://", "https://")):
        return None
    raw = Path(value)
    if raw.is_file():
        return raw
    static_root = (BASE_DIR / "static").resolve()
    static_candidate = (static_root / value).resolve()
    try:
        static_candidate.relative_to(static_root)
    except ValueError:
        static_candidate = None
    if static_candidate and static_candidate.is_file():
        return static_candidate
    rooted = (BASE_DIR / value).resolve()
    if rooted.is_file():
        return rooted
    return None


def _scan_organization_logo(organization_id):
    if not organization_id:
        return None
    folder = BASE_DIR / "static" / "uploads" / "organizations" / str(organization_id)
    for name in ("logo.png", "logo.jpg", "logo.jpeg", "logo.webp", "logo.gif"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def resolve_creative_logo_path(settings, *, organization_id=None):
    settings = settings or {}
    for key in (
        "marketing_logo_light_url",
        "marketing_logo_url",
        "logo_path",
        "marketing_logo_dark_url",
    ):
        path = resolve_local_logo_path(settings.get(key))
        if path:
            return path
    return _scan_organization_logo(
        organization_id or settings.get("organization_id")
    )


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
    settings = dict(settings or {})
    stored_brand = usable_brand_name(settings.get("marketing_brand_name"))
    stored_office = usable_brand_name(settings.get("legal_office_name"))
    stored_broker = _clean(settings.get("legal_broker_name"))
    stored_license = _clean(settings.get("legal_broker_license"))
    stored_footer = _clean(settings.get("legal_footer_line"))
    org_name = usable_brand_name(
        organization_name,
        settings.get("organization_name"),
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
    logo_path = resolve_creative_logo_path(
        settings,
        organization_id=settings.get("organization_id"),
    )
    if not logo_path:
        logger.warning(
            "marketing branding missing office logo org=%s; using wordmark only",
            settings.get("organization_id"),
        )
    configured = marketing_legal_is_configured(settings)
    legal_complete = bool(stored_broker and stored_license)
    return {
        "brand_name": brand_name,
        "office_name": office_name,
        "logo_path": str(logo_path) if logo_path else None,
        "has_logo": bool(logo_path),
        "show_wordmark": not bool(logo_path),
        "legal_broker_name": stored_broker,
        "legal_broker_license": stored_license,
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
        facts.get("organization_name"),
    ) or DEMO_MARKETING_BRANDING["marketing_brand_name"]
    has_logo = bool(facts.get("organization_logo") or facts.get("has_logo"))
    return {
        "brand_name": brand,
        "office_name": usable_brand_name(facts.get("organization_name")) or brand,
        "logo_path": facts.get("organization_logo"),
        "has_logo": has_logo,
        "show_wordmark": bool(facts.get("show_wordmark", not has_logo)),
        "legal_broker_name": facts.get("legal_broker_name") or "",
        "legal_broker_license": facts.get("legal_broker_license") or "",
        "legal_footer_line": facts.get("legal_footer_line") or "",
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


def branding_prompt_block(branding, *, include_agent=False, language="es"):
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
    contact_bits = []
    labels = {
        "marketing_instagram": "Instagram",
        "marketing_whatsapp": "WhatsApp",
        "marketing_phone": "Tel" if language == "es" else "Phone",
        "marketing_email": "Email",
    }
    for key, label in labels.items():
        value = branding.get(key)
        if value:
            contact_bits.append(f"{label} {value}")
    contact = (", ".join(contact_bits) + ". ") if contact_bits else ""
    if branding.get("has_logo"):
        logo = (
            "Place the REAL office logo from the logo reference in the top-left, "
            "inside the safe area. Small, sharp, clean, no extra plate or colored box, "
            "never pixelated, never invented, never replaced by JRH One. "
            "If the mark already includes the office name, do not repeat the name beside it."
        )
    else:
        logo = (
            f"No logo file is available. Set the wordmark '{brand}' top-left in clean type. "
            "Do not invent a logo, balloon, or similar mark."
        )
    agent = (
        "The agent is the commercial contact only and is not the legal broker. "
        if include_agent
        else "Do not show an agent. "
    )
    if language == "es":
        lock = (
            f"Usar la marca visible de la inmobiliaria activa: {brand}. "
            "No mostrar JRH One dentro del creativo. "
            "Colocar el logo real de la inmobiliaria si está disponible. "
            f"Mostrar el nombre comercial correcto de la oficina ({office}) solo si el logo no lo incluye. "
            f"Mantener el responsable legal y matrícula visibles: {broker} {license_no}."
        )
    else:
        lock = (
            f"Use the active real estate office branding ({brand}), not the app brand. "
            "Show the real estate office logo if provided, and use the office commercial name "
            "only when the logo does not already include it. "
            "Do not display JRH One in the final creative. "
            f"Keep the legal broker and license visible: {broker} {license_no}."
        )
    return (
        f"CREATIVE BRAND LOCK: {lock} "
        f"Never write Inmobiliaria Principal or any generic office placeholder. "
        f"{logo} "
        f"Mandatory legal footer, small, clean, fully inside the safe area, never cropped: '{footer}'. "
        f"{agent}{contact}"
        "Visual hierarchy: header office logo top-left; body property, title, "
        "location, facts and price; footer agent/contact then legal broker and license."
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
