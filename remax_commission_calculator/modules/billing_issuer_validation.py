"""
Unified fiscal issuer profile validation for billing UI and backend.
"""

from __future__ import annotations

import re

from modules.database.billing_issuer_profiles_repository import (
    list_profiles as list_billing_issuer_profiles,
)

_CUIT_RE = re.compile(r"^\d{2}-?\d{8}-?\d$")

TAX_CONDITIONS = (
    "responsable_inscripto",
    "monotributo",
    "exento",
    "consumidor_final",
)

VALID_ISSUER_PROFILE_TYPES = (
    "organization",
    "broker",
    "other",
)

ARCA_CONNECTION_NOT_CONFIGURED = "not_configured"
ARCA_CONNECTION_CONFIGURING = "configuring"
ARCA_CONNECTION_CONNECTED = "connected"
ARCA_CONNECTION_ERROR = "error"

ARCA_ENV_HOMOLOGATION = "homologation"
ARCA_ENV_PRODUCTION = "production"

ISSUER_FIELD_TO_I18N = {
    "legal_name": "billing_missing_issuer_legal_name",
    "tax_id": "billing_missing_issuer_tax_id",
    "tax_condition": "billing_missing_issuer_tax_condition",
    "fiscal_address": "billing_missing_issuer_fiscal_address",
    "email": "billing_missing_issuer_email",
}

AGENT_FIELD_TO_I18N = {
    "legal_name": "billing_missing_agent_legal_name",
    "tax_id": "billing_missing_agent_tax_id",
    "tax_condition": "billing_missing_agent_tax_condition",
    "fiscal_address": "billing_missing_agent_fiscal_address",
    "email": "billing_missing_agent_email",
}

ISSUER_FIELD_TO_ERROR = {
    "legal_name": "invoice_err_issuer_missing_legal_name",
    "tax_id": "invoice_err_issuer_missing_tax_id",
    "tax_condition": "invoice_err_issuer_missing_tax_condition",
    "fiscal_address": "invoice_err_issuer_missing_fiscal_address",
    "email": "invoice_err_issuer_missing_email",
}

AGENT_FIELD_TO_ERROR = {
    "legal_name": "invoice_err_agent_missing_legal_name",
    "tax_id": "invoice_err_agent_missing_tax_id",
    "tax_condition": "invoice_err_agent_missing_tax_condition",
    "fiscal_address": "invoice_err_agent_missing_fiscal_address",
    "email": "invoice_err_agent_missing_email",
}


def validate_cuit(tax_id):
    if tax_id is None:
        return False
    cleaned = str(tax_id).strip().replace(" ", "")
    if not _CUIT_RE.match(cleaned):
        return False
    digits = cleaned.replace("-", "")
    return len(digits) == 11 and digits.isdigit()


def normalize_cuit(tax_id):
    digits = re.sub(r"\D", "", str(tax_id or ""))
    if len(digits) != 11:
        return (tax_id or "").strip()
    return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"


def _strip(value):
    if value is None:
        return ""
    return str(value).strip()


def _tax_condition_valid(value):
    condition = _strip(value)
    return bool(condition) and condition in TAX_CONDITIONS


def validate_billing_issuer_profile(
    profile,
    *,
    require_active=True,
    require_email=False,
):
    """
    Validate an organization billing issuer profile (broker, office, etc.).

    Returns dict with is_valid, missing_fields, missing_i18n_keys,
    warnings, and error_key (primary user-facing error when invalid).
    """
    missing_fields = []
    warnings = []

    if profile is None:
        missing_fields = list(ISSUER_FIELD_TO_I18N.keys())
        return {
            "is_valid": False,
            "missing_fields": missing_fields,
            "missing_i18n_keys": [
                ISSUER_FIELD_TO_I18N[f] for f in missing_fields
            ],
            "warnings": warnings,
            "error_key": "invoice_err_issuer_profile_not_found",
        }

    if require_active and not profile.get("is_active", True):
        return {
            "is_valid": False,
            "missing_fields": [],
            "missing_i18n_keys": [],
            "warnings": warnings,
            "error_key": "invoice_err_issuer_inactive",
        }

    issuer_type = _strip(profile.get("issuer_type")).lower()
    if issuer_type not in VALID_ISSUER_PROFILE_TYPES:
        return {
            "is_valid": False,
            "missing_fields": ["issuer_type"],
            "missing_i18n_keys": [],
            "warnings": warnings,
            "error_key": "invoice_err_issuer_type_invalid",
        }

    if not _strip(profile.get("legal_name")):
        missing_fields.append("legal_name")
    if not validate_cuit(profile.get("tax_id")):
        missing_fields.append("tax_id")
    if not _tax_condition_valid(profile.get("tax_condition")):
        missing_fields.append("tax_condition")
    if not _strip(profile.get("fiscal_address")):
        missing_fields.append("fiscal_address")

    email = _strip(profile.get("email"))
    if require_email and not email:
        missing_fields.append("email")
    elif not email:
        warnings.append("billing_warn_issuer_email_missing")

    missing_i18n = [
        ISSUER_FIELD_TO_I18N[field]
        for field in missing_fields
        if field in ISSUER_FIELD_TO_I18N
    ]

    error_key = None
    if missing_fields:
        first = missing_fields[0]
        error_key = ISSUER_FIELD_TO_ERROR.get(
            first,
            "invoice_err_billing_profile_incomplete",
        )

    return {
        "is_valid": len(missing_fields) == 0,
        "missing_fields": missing_fields,
        "missing_i18n_keys": missing_i18n,
        "warnings": warnings,
        "error_key": error_key,
    }


def validate_agent_billing_profile(
    profile,
    *,
    require_email=False,
):
    """Validate an agent fiscal profile using the same rules as issuers."""
    missing_fields = []
    warnings = []

    if profile is None:
        missing_fields = list(AGENT_FIELD_TO_I18N.keys())
        return {
            "is_valid": False,
            "missing_fields": missing_fields,
            "missing_i18n_keys": [
                AGENT_FIELD_TO_I18N[f] for f in missing_fields
            ],
            "warnings": warnings,
            "error_key": "invoice_err_billing_profile_incomplete",
        }

    if not _strip(profile.get("legal_name")):
        missing_fields.append("legal_name")
    if not validate_cuit(profile.get("tax_id")):
        missing_fields.append("tax_id")
    if not _tax_condition_valid(profile.get("tax_condition")):
        missing_fields.append("tax_condition")
    if not _strip(profile.get("fiscal_address")):
        missing_fields.append("fiscal_address")

    email = _strip(profile.get("email"))
    if require_email and not email:
        missing_fields.append("email")
    elif not email:
        warnings.append("billing_warn_agent_email_missing")

    missing_i18n = [
        AGENT_FIELD_TO_I18N[field]
        for field in missing_fields
        if field in AGENT_FIELD_TO_I18N
    ]

    error_key = None
    if missing_fields:
        first = missing_fields[0]
        error_key = AGENT_FIELD_TO_ERROR.get(
            first,
            "invoice_err_billing_profile_incomplete",
        )

    return {
        "is_valid": len(missing_fields) == 0,
        "missing_fields": missing_fields,
        "missing_i18n_keys": missing_i18n,
        "warnings": warnings,
        "error_key": error_key,
    }


def list_usable_issuer_profiles(organization_id):
    """Active issuer profiles that pass fiscal validation."""
    usable = []
    for profile in list_billing_issuer_profiles(
        organization_id,
        active_only=True,
    ):
        result = validate_billing_issuer_profile(
            profile,
            require_active=True,
        )
        if result["is_valid"]:
            usable.append(profile)
    return usable


def resolve_office_issuer_profile_id(
    organization_id,
    *,
    issuer_profile_id=None,
    settings=None,
):
    """
    Resolve which organization issuer profile to use for office billing.

    Priority:
    1. Explicit issuer_profile_id
    2. organization_settings.default_issuer_profile_id (if valid)
    3. Single active valid issuer (auto-select)
    4. Profile marked is_default (synced to settings on write)

    Raises InvoicingError with a specific message_key when unresolved.
    """
    from modules.invoicing import InvoicingError

    from modules.database.billing_issuer_profiles_repository import (
        get_profile as get_billing_issuer_profile,
    )
    from modules.database.organization_settings_repository import (
        get_organization_settings,
    )

    if settings is None:
        settings = get_organization_settings(organization_id)

    if issuer_profile_id is not None:
        profile = get_billing_issuer_profile(
            organization_id,
            int(issuer_profile_id),
        )
        if profile is None:
            raise InvoicingError(
                "invoice_err_issuer_profile_not_found"
            )
        validation = validate_billing_issuer_profile(profile)
        if not profile.get("is_active", True):
            raise InvoicingError("invoice_err_issuer_inactive")
        if not validation["is_valid"]:
            raise InvoicingError(
                validation["error_key"],
                missing=validation["missing_i18n_keys"],
            )
        return int(profile["id"])

    default_id = settings.get("default_issuer_profile_id")
    if default_id:
        profile = get_billing_issuer_profile(
            organization_id,
            int(default_id),
        )
        if profile and profile.get("is_active", True):
            validation = validate_billing_issuer_profile(profile)
            if validation["is_valid"]:
                return int(profile["id"])

    usable = list_usable_issuer_profiles(organization_id)
    if len(usable) == 1:
        return int(usable[0]["id"])

    if len(usable) > 1:
        raise InvoicingError(
            "invoice_err_issuer_default_required"
        )

    active_profiles = list_billing_issuer_profiles(
        organization_id,
        active_only=True,
    )
    if not active_profiles:
        raise InvoicingError(
            "invoice_err_issuer_profile_required"
        )

    # Active profiles exist but none are fiscally complete.
    first = active_profiles[0]
    validation = validate_billing_issuer_profile(first)
    raise InvoicingError(
        validation["error_key"]
        or "invoice_err_billing_profile_incomplete",
        missing=validation["missing_i18n_keys"],
    )


def normalize_profile_tax_id(profile):
    """Return profile dict copy with normalized CUIT string."""
    if profile is None:
        return None
    normalized = dict(profile)
    tax_id = normalized.get("tax_id")
    if tax_id is not None:
        normalized["tax_id"] = normalize_cuit(tax_id)
    return normalized
