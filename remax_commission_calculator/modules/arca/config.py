"""
ARCA environment configuration — homologation only in this phase.
"""

from __future__ import annotations

import os

ARCA_ENV_HOMOLOGATION = "homologation"
ARCA_ENV_PRODUCTION = "production"

WSAA_URLS = {
    ARCA_ENV_HOMOLOGATION: (
        "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
    ),
}

WSFE_URLS = {
    ARCA_ENV_HOMOLOGATION: (
        "https://wswhomo.afip.gov.ar/wsfev1/service.asmx"
    ),
}

WSAA_SERVICE_WSFE = "wsfe"
TA_RENEWAL_MARGIN_SECONDS = 300


class ArcaProductionBlockedError(RuntimeError):
    """Raised when production ARCA is requested but not enabled."""


def get_arca_environment() -> str:
    """
    Return the active ARCA environment.

    This sprint hard-blocks production even if ARCA_ENV=production.
    """
    requested = (
        os.environ.get("ARCA_ENV")
        or ARCA_ENV_HOMOLOGATION
    ).strip().lower()

    if requested == ARCA_ENV_PRODUCTION:
        raise ArcaProductionBlockedError(
            "ARCA production is not enabled in this release. "
            "Set ARCA_ENV=homologation."
        )

    if requested not in (ARCA_ENV_HOMOLOGATION,):
        return ARCA_ENV_HOMOLOGATION

    return ARCA_ENV_HOMOLOGATION


def is_arca_fiscal_enabled() -> bool:
    """True when fiscal ARCA issuance is allowed (homologation + provider)."""
    from modules.invoice_provider import (
        PROVIDER_ARCA,
        get_invoice_provider_name,
    )

    if get_invoice_provider_name() != PROVIDER_ARCA:
        return False

    try:
        get_arca_environment()
    except ArcaProductionBlockedError:
        return False

    return True


def get_wsaa_url(environment: str | None = None) -> str:
    env = environment or get_arca_environment()
    return WSAA_URLS[env]


def get_wsfe_url(environment: str | None = None) -> str:
    env = environment or get_arca_environment()
    return WSFE_URLS[env]
