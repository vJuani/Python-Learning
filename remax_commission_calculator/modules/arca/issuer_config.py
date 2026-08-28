"""
ARCA issuer configuration helpers (UI + connection test).
"""

from __future__ import annotations

from datetime import datetime, timezone

from modules.arca.config import get_arca_environment, is_arca_fiscal_enabled
from modules.arca.secrets import load_credentials


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()


def credentials_available(issuer_profile: dict) -> bool:
    try:
        load_credentials(issuer_profile or {})
        return True
    except (FileNotFoundError, ValueError):
        return False


def build_arca_display(issuer_profile: dict | None) -> dict:
    profile = issuer_profile or {}
    status = (
        profile.get("arca_connection_status")
        or "not_configured"
    )
    try:
        environment = get_arca_environment()
    except Exception:
        environment = "homologation"

    return {
        "status": status,
        "cuit": profile.get("tax_id") or "",
        "point_of_sale": profile.get("arca_point_of_sale") or "",
        "environment": profile.get("arca_environment") or environment,
        "last_validated_at": profile.get(
            "arca_last_validated_at"
        ),
        "certificate_ref": profile.get(
            "arca_certificate_ref"
        )
        or "",
        "credentials_available": credentials_available(profile),
        "enabled": is_arca_fiscal_enabled(),
    }


def test_arca_connection(issuer_profile: dict) -> tuple[str, str | None]:
    """
    Authenticate with WSAA homologation.

    Returns (connection_status, error_key).
    """
    from modules.arca.client import ArcaClient

    if not is_arca_fiscal_enabled():
        return "not_configured", (
            "invoice_err_fiscal_issue_unavailable"
        )

    profile = dict(issuer_profile or {})
    if not profile.get("arca_point_of_sale"):
        return "error", "billing_missing_arca_point_of_sale"

    if not credentials_available(profile):
        return "error", "invoice_err_arca_credentials_missing"

    try:
        client = ArcaClient()
        client.authenticate(
            profile,
            {"issuer_tax_id": profile.get("tax_id")},
        )
        return "connected", None
    except Exception:
        return "error", "invoice_err_arca_auth_failed"
