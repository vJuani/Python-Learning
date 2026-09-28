"""
ARCA environment configuration.

ARCA_ENV must be explicit. A missing or unknown value does not fall
back to homologation or production, so a fiscal invoice cannot leave
for the wrong AFIP host.
"""

from __future__ import annotations

import os
import re

ARCA_ENV_HOMOLOGATION = "homologation"
ARCA_ENV_PRODUCTION = "production"

_HOMOLOGATION_ALIASES = frozenset({
    ARCA_ENV_HOMOLOGATION,
    "homo",
    "test",
    "testing",
})
_PRODUCTION_ALIASES = frozenset({
    ARCA_ENV_PRODUCTION,
    "prod",
})

WSAA_URLS = {
    ARCA_ENV_HOMOLOGATION: (
        "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
    ),
    ARCA_ENV_PRODUCTION: (
        "https://wsaa.afip.gov.ar/ws/services/LoginCms"
    ),
}

WSFE_URLS = {
    ARCA_ENV_HOMOLOGATION: (
        "https://wswhomo.afip.gov.ar/wsfev1/service.asmx"
    ),
    ARCA_ENV_PRODUCTION: (
        "https://servicios1.afip.gov.ar/wsfev1/service.asmx"
    ),
}

WSAA_SERVICE_WSFE = "wsfe"
TA_RENEWAL_MARGIN_SECONDS = 300


class ArcaEnvironmentError(RuntimeError):
    """ARCA_ENV is missing or is not a known environment."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ArcaProductionBlockedError(ArcaEnvironmentError):
    """Kept so older callers still catch a blocked environment."""

    def __init__(self, reason: str = "invalid"):
        super().__init__(reason)


def get_arca_environment() -> str:
    """Return homologation or production. Never guess when ARCA_ENV is empty."""
    requested = (os.environ.get("ARCA_ENV") or "").strip().lower()
    if not requested:
        raise ArcaEnvironmentError("missing")
    if requested in _HOMOLOGATION_ALIASES:
        return ARCA_ENV_HOMOLOGATION
    if requested in _PRODUCTION_ALIASES:
        return ARCA_ENV_PRODUCTION
    raise ArcaEnvironmentError("invalid")


def is_arca_fiscal_enabled() -> bool:
    """True only when the invoice provider is ARCA and ARCA_ENV is explicit."""
    from modules.invoice_provider import (
        PROVIDER_ARCA,
        get_invoice_provider_name,
    )

    if get_invoice_provider_name() != PROVIDER_ARCA:
        return False

    try:
        get_arca_environment()
    except ArcaEnvironmentError:
        return False

    return True


def _url_for(table: dict[str, str], environment: str | None) -> str:
    env = environment or get_arca_environment()
    if env not in table:
        raise ArcaEnvironmentError("invalid")
    url = table[env]
    host = url.lower()
    if env == ARCA_ENV_PRODUCTION and "homo" in host:
        raise ArcaEnvironmentError("invalid")
    if env == ARCA_ENV_HOMOLOGATION and "homo" not in host:
        raise ArcaEnvironmentError("invalid")
    return url


def get_wsaa_url(environment: str | None = None) -> str:
    return _url_for(WSAA_URLS, environment)


def get_wsfe_url(environment: str | None = None) -> str:
    return _url_for(WSFE_URLS, environment)


def describe_arca_environment(*, connection=None, cuit="") -> dict:
    """Admin-safe status. Booleans and public endpoints only."""
    try:
        environment = get_arca_environment()
        error = None
    except ArcaEnvironmentError as exc:
        environment = None
        error = exc.reason
    connection = connection or {}
    digits = re.sub(r"\D", "", str(cuit or ""))
    return {
        "environment_configured": environment is not None,
        "environment": environment,
        "environment_error": error,
        "is_production": environment == ARCA_ENV_PRODUCTION,
        "is_homologation": environment == ARCA_ENV_HOMOLOGATION,
        "wsaa_endpoint": (
            get_wsaa_url(environment) if environment else None
        ),
        "wsfe_endpoint": (
            get_wsfe_url(environment) if environment else None
        ),
        "certificates_present": bool(
            connection.get("certificate_encrypted")
            and connection.get("private_key_encrypted")
        ),
        "cuit_configured": len(digits) == 11,
    }
