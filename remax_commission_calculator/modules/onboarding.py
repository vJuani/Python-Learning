"""Invite-gated office onboarding. One transaction, then an optional logo file."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time

from modules.access_codes import generate_registration_code, hash_access_secret
from modules.auth import ROLE_ADMIN, hash_password
from modules.database.connection import get_connection
from modules.database.organization_settings_repository import (
    get_organization_settings,
    set_organization_logo_path,
)
from modules.database.organizations_repository import (
    OrganizationProvisioningError,
    provision_organization,
)
from modules.i18n import SUPPORTED_LANGUAGES, normalize_language
from modules.integration_status import describe_integrations
from modules.marketing_branding import marketing_legal_is_configured
from modules.organization_settings import (
    SUPPORTED_CURRENCIES,
    is_valid_timezone,
    normalize_accent_color,
)
from modules.passwords import validate_password_policy

INVITE_ENV = "JRH_ONBOARDING_INVITE_CODE"
EVENT_INVITE_FAILED = "invite_failed"
EVENT_CREATED = "created"
RATE_WINDOW_SECONDS = 3600
FAILED_LIMIT = 10
CREATED_LIMIT = 30

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_MAX = 64
NAME_MAX = 120
PLACE_MAX = 80
CONTACT_MAX = 80
PHONE_MAX = 40
WEBSITE_MAX = 200
LEGAL_MAX = 200
GENERIC_ERROR = "onboarding_generic_error"

COUNTRY_TIMEZONES = {
    "Argentina": "America/Argentina/Buenos_Aires",
    "Uruguay": "America/Montevideo",
    "Chile": "America/Santiago",
    "Brasil": "America/Sao_Paulo",
    "España": "Europe/Madrid",
    "México": "America/Mexico_City",
    "Estados Unidos": "America/New_York",
}


def invite_matches(provided):
    expected = os.environ.get(INVITE_ENV, "").strip()
    offered = (provided or "").strip()
    if expected == "" or offered == "":
        return False
    return hmac.compare_digest(
        hashlib.sha256(offered.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def _recent_count(event_type, now=None):
    moment = time.time() if now is None else float(now)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT id, created_at
        FROM onboarding_rate_events
        WHERE event_type = ?
        """,
        (event_type,),
    )
    rows = cursor.fetchall()
    recent = 0
    expired = []
    for row in rows:
        try:
            stamp = float(row[1])
        except (TypeError, ValueError):
            expired.append(row[0])
            continue
        if moment - stamp < RATE_WINDOW_SECONDS:
            recent += 1
        else:
            expired.append(row[0])
    for event_id in expired:
        cursor.execute(
            "DELETE FROM onboarding_rate_events WHERE id = ?",
            (event_id,),
        )
    connection.commit()
    connection.close()
    return recent


def _record_event(event_type, now=None):
    moment = time.time() if now is None else float(now)
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        INSERT INTO onboarding_rate_events (event_type, created_at)
        VALUES (?, ?)
        """,
        (event_type, str(moment)),
    )
    connection.commit()
    connection.close()


def rate_limited(event_type, limit, now=None):
    return _recent_count(event_type, now=now) >= limit


def _clean(value, limit):
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return None
    return text


def validate_onboarding_form(form):
    errors = []
    name = _clean(form.get("name"), NAME_MAX)
    if name is None:
        errors.append("onboarding_err_field_long")
        name = ""
    elif name == "":
        errors.append("onboarding_err_name")

    country = _clean(form.get("country"), PLACE_MAX)
    region = _clean(form.get("region"), PLACE_MAX)
    city = _clean(form.get("city"), PLACE_MAX)
    if country is None or region is None or city is None:
        errors.append("onboarding_err_field_long")
        country = country or ""
        region = region or ""
        city = city or ""

    language = normalize_language(form.get("language") or "es")
    if language not in SUPPORTED_LANGUAGES:
        errors.append("onboarding_err_language")

    currency = str(form.get("currency") or "USD").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        errors.append("onboarding_err_currency")

    timezone = str(form.get("timezone") or "").strip()
    if not is_valid_timezone(timezone):
        errors.append("onboarding_err_timezone")

    first_name = _clean(form.get("first_name"), CONTACT_MAX)
    last_name = _clean(form.get("last_name"), CONTACT_MAX)
    if first_name is None or last_name is None:
        errors.append("onboarding_err_field_long")
        first_name = ""
        last_name = ""
    elif first_name == "" or last_name == "":
        errors.append("onboarding_err_name")

    email = str(form.get("email") or "").strip().lower()
    if " " in email or not EMAIL_PATTERN.match(email):
        errors.append("onboarding_err_email")
    elif len(email) > USERNAME_MAX:
        errors.append("onboarding_err_email_long")

    password_error = validate_password_policy(
        form.get("password") or "",
        form.get("confirm_password") or "",
    )
    if password_error is not None:
        errors.append(password_error)

    commercial_name = _clean(form.get("commercial_name"), NAME_MAX)
    if commercial_name is None:
        errors.append("onboarding_err_field_long")
        commercial_name = name
    elif commercial_name == "":
        commercial_name = name

    phone = _clean(form.get("marketing_phone"), PHONE_MAX)
    website = str(form.get("marketing_website") or "").strip()
    commercial_email = str(form.get("marketing_email") or "").strip().lower()
    if phone is None:
        errors.append("onboarding_err_field_long")
        phone = ""
    if len(website) > WEBSITE_MAX or " " in website:
        errors.append("onboarding_err_website")
        website = ""
    if commercial_email and (
        " " in commercial_email or not EMAIL_PATTERN.match(commercial_email)
    ):
        errors.append("onboarding_err_commercial_email")

    raw_color = str(form.get("accent_color") or "").strip()
    accent = normalize_accent_color(raw_color)
    if raw_color and accent is None:
        errors.append("onboarding_err_color")

    legal = {}
    for key in (
        "legal_office_name",
        "legal_broker_name",
        "legal_broker_license",
        "legal_footer_line",
    ):
        cleaned = _clean(form.get(key), LEGAL_MAX)
        if cleaned is None:
            errors.append("onboarding_err_field_long")
            cleaned = ""
        legal[key] = cleaned

    cleaned = {
        "name": name,
        "country": country or "",
        "region": region or "",
        "city": city or "",
        "language": language,
        "currency": currency,
        "timezone": timezone,
        "first_name": first_name or "",
        "last_name": last_name or "",
        "email": email,
        "password": form.get("password") or "",
        "commercial_name": commercial_name or name,
        "marketing_phone": phone or "",
        "marketing_email": commercial_email,
        "marketing_website": website,
        "accent_color": accent,
        **legal,
    }
    return errors, cleaned


def complete_onboarding(form, logo_file=None, save_logo=None):
    """Create the office or return a generic failure. Never reads an organization id."""
    honeypot = str(form.get("company_website") or "").strip()
    if honeypot:
        if not rate_limited(EVENT_INVITE_FAILED, FAILED_LIMIT):
            _record_event(EVENT_INVITE_FAILED)
        return {"ok": False, "generic": True, "errors": [GENERIC_ERROR]}

    if rate_limited(EVENT_INVITE_FAILED, FAILED_LIMIT):
        return {"ok": False, "generic": True, "errors": [GENERIC_ERROR]}

    if not invite_matches(form.get("invite_code")):
        _record_event(EVENT_INVITE_FAILED)
        return {"ok": False, "generic": True, "errors": [GENERIC_ERROR]}

    if rate_limited(EVENT_CREATED, CREATED_LIMIT):
        return {"ok": False, "generic": True, "errors": [GENERIC_ERROR]}

    errors, cleaned = validate_onboarding_form(form)
    if errors:
        return {"ok": False, "generic": False, "errors": errors, "values": cleaned}

    registration_code = generate_registration_code()
    try:
        created = provision_organization(
            name=cleaned["name"],
            display_name=cleaned["commercial_name"],
            default_language=cleaned["language"],
            default_currency=cleaned["currency"],
            timezone=cleaned["timezone"],
            admin_username=cleaned["email"],
            admin_password_hash=hash_password(cleaned["password"]),
            admin_role=ROLE_ADMIN,
            registration_code_hash=hash_access_secret(registration_code),
            admin_email=cleaned["email"],
            first_name=cleaned["first_name"],
            last_name=cleaned["last_name"],
            country=cleaned["country"],
            region=cleaned["region"],
            city=cleaned["city"],
            accent_color=cleaned["accent_color"],
            marketing_phone=cleaned["marketing_phone"],
            marketing_email=cleaned["marketing_email"],
            marketing_website=cleaned["marketing_website"],
            legal_office_name=cleaned["legal_office_name"],
            legal_broker_name=cleaned["legal_broker_name"],
            legal_broker_license=cleaned["legal_broker_license"],
            legal_footer_line=cleaned["legal_footer_line"],
        )
    except OrganizationProvisioningError:
        return {"ok": False, "generic": True, "errors": [GENERIC_ERROR]}

    _record_event(EVENT_CREATED)
    logo_pending = True
    if logo_file is not None and getattr(logo_file, "filename", ""):
        try:
            stored = save_logo(created["organization_id"], logo_file) if save_logo else None
        except Exception:
            stored = None
        if stored:
            set_organization_logo_path(created["organization_id"], stored)
            logo_pending = False

    return {
        "ok": True,
        "generic": False,
        "errors": [],
        "organization_id": created["organization_id"],
        "admin_user_id": created["admin_user_id"],
        "registration_code": registration_code,
        "logo_pending": logo_pending,
    }


def build_onboarding_checklist(organization_id, language="es"):
    from modules.i18n import translate

    settings = get_organization_settings(organization_id) or {}
    if settings.get("onboarding_checklist_dismissed_at"):
        return None

    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM agents WHERE organization_id = ?",
        (organization_id,),
    )
    agent_count = int(cursor.fetchone()[0] or 0)
    cursor.execute(
        "SELECT COUNT(*) FROM properties WHERE organization_id = ?",
        (organization_id,),
    )
    property_count = int(cursor.fetchone()[0] or 0)
    connection.close()

    logo_done = bool(settings.get("logo_path") or settings.get("marketing_logo_path"))
    legal_done = marketing_legal_is_configured(settings)
    integrations = describe_integrations()
    integrations_done = all(item["configured"] for item in integrations)

    items = [
        {"key": "organization", "done": True, "optional": False, "label": translate("onboarding_check_org", language)},
        {"key": "admin", "done": True, "optional": False, "label": translate("onboarding_check_admin", language)},
        {"key": "code", "done": bool(settings.get("registration_code_hash")), "optional": False, "label": translate("onboarding_check_code", language)},
        {"key": "settings", "done": True, "optional": False, "label": translate("onboarding_check_settings", language)},
        {"key": "logo", "done": logo_done, "optional": True, "label": translate("onboarding_warn_logo", language), "href": "organization_settings"},
        {"key": "legal", "done": legal_done, "optional": True, "label": translate("onboarding_warn_legal", language), "href": "organization_settings"},
        {"key": "agents", "done": agent_count > 0, "optional": True, "label": translate("onboarding_warn_agents", language), "href": "agents_list"},
        {"key": "properties", "done": property_count > 0, "optional": True, "label": translate("onboarding_warn_properties", language), "href": "properties_new"},
        {"key": "integrations", "done": integrations_done, "optional": True, "label": translate("onboarding_warn_integrations", language), "href": "organization_settings"},
    ]
    office_items = [
        item for item in items
        if item["optional"] and item["key"] != "integrations"
    ]
    return {
        "items": items,
        "can_dismiss": all(item["done"] for item in office_items),
        "integrations": integrations,
    }
