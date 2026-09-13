"""Organization branding for generated marketing pieces.

JRH One is the product / backoffice brand. It must never appear inside a
publishable creative. The visible brand is the active real-estate office.
"""

from __future__ import annotations

from pathlib import Path

from modules.branding import get_brand_name

BASE_DIR = Path(__file__).resolve().parent.parent

SYSTEM_BRAND_NAME = "JRH One"
SYSTEM_BRAND_TOKENS = (
    "jrh one",
    "jrhone",
    "jrh-one",
    "jrh_one",
)

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


def resolve_creative_logo_path(settings):
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
    return None


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


def resolve_marketing_branding(settings, *, language="es", apply_demo_fallback=True):
    """Resolve the brand that may appear inside a generated creative."""
    settings = dict(settings or {})
    stored_brand = _clean(settings.get("marketing_brand_name"))
    stored_office = _clean(settings.get("legal_office_name"))
    stored_broker = _clean(settings.get("legal_broker_name"))
    stored_license = _clean(settings.get("legal_broker_license"))
    stored_footer = _clean(settings.get("legal_footer_line"))
    display_name = _clean(settings.get("display_name") or settings.get("trade_name"))
    used_demo = False

    if apply_demo_fallback:
        if not stored_brand and (not display_name or is_system_brand_text(display_name)):
            stored_brand = DEMO_MARKETING_BRANDING["marketing_brand_name"]
            used_demo = True
        if not stored_broker:
            stored_broker = DEMO_MARKETING_BRANDING["legal_broker_name"]
            used_demo = True
        if not stored_license:
            stored_license = DEMO_MARKETING_BRANDING["legal_broker_license"]
            used_demo = True
        if not stored_footer:
            stored_footer = DEMO_MARKETING_BRANDING["legal_footer_line"]
        if not stored_office:
            stored_office = stored_brand or DEMO_MARKETING_BRANDING["legal_office_name"]

    brand_name = stored_brand or display_name or DEMO_MARKETING_BRANDING["marketing_brand_name"]
    if is_system_brand_text(brand_name):
        brand_name = DEMO_MARKETING_BRANDING["marketing_brand_name"]
        used_demo = True
    office_name = stored_office or brand_name
    footer = stored_footer or build_legal_footer_line(
        {
            "legal_broker_name": stored_broker,
            "legal_broker_license": stored_license,
        },
        language=language,
    )
    logo_path = resolve_creative_logo_path(settings)
    configured = marketing_legal_is_configured(settings)
    legal_complete = bool(stored_broker and stored_license)
    return {
        "brand_name": brand_name,
        "office_name": office_name,
        "logo_path": str(logo_path) if logo_path else None,
        "has_logo": bool(logo_path),
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
    return {
        "brand_name": facts.get("brand_name") or DEMO_MARKETING_BRANDING["marketing_brand_name"],
        "office_name": facts.get("organization_name") or facts.get("brand_name"),
        "logo_path": facts.get("organization_logo"),
        "has_logo": bool(facts.get("organization_logo")),
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
    brand = branding.get("brand_name") or DEMO_MARKETING_BRANDING["marketing_brand_name"]
    office = branding.get("office_name") or brand
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
    logo = (
        f"Use the REAL office logo from the logo reference. Never invent a logo. "
        f"Never replace it with {system_brand_name()}. "
        if branding.get("has_logo")
        else (
            f"No logo file is available. Use the wordmark '{brand}' in clean typography. "
            "Do not invent a logo or balloon mark."
        )
    )
    agent = (
        "The agent is the commercial contact only and is not the legal broker. "
        if include_agent
        else "Do not show an agent. "
    )
    return (
        f"CREATIVE BRAND LOCK: This ad is branded as {brand} / {office}. "
        f"Never write, draw or imply {system_brand_name()}, JRH One, or JRH. "
        f"{logo}"
        f"Mandatory legal footer, small, clean, fully inside the safe area, never cropped: '{footer}'. "
        f"Legal broker / martillero (not the agent): {broker} {license_no}. "
        f"{agent}{contact}"
        "Visual hierarchy: header agency logo + office name; body property, title, "
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
