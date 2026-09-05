"""
ARCA issuer configuration helpers (UI + connection test).
"""

from __future__ import annotations

from datetime import datetime, timezone

from modules.arca.config import get_arca_environment, is_arca_fiscal_enabled
from modules.arca.connections import arca_chip_for


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def credentials_available(issuer_profile: dict) -> bool:
    return False


def build_arca_display(
    issuer_profile: dict | None,
    *,
    user=None,
    organization_id=None,
) -> dict:
    profile = issuer_profile or {}
    try:
        environment = get_arca_environment()
    except Exception:
        environment = "homologation"

    display = {
        "status": "not_configured",
        "cuit": profile.get("tax_id") or "",
        "point_of_sale": "",
        "environment": environment,
        "last_validated_at": "",
        "certificate_ref": "",
        "credentials_available": False,
        "enabled": is_arca_fiscal_enabled(),
    }
    if organization_id and user:
        chip = arca_chip_for(organization_id, user)
        display["status"] = chip.get("connection_status") or display["status"]
        display["point_of_sale"] = chip.get("point_of_sale") or ""
        display["credentials_available"] = bool(chip.get("connected"))
        display["last_validated_at"] = chip.get("last_verified_at") or ""
        display["environment"] = chip.get("environment") or environment
    return display


def test_arca_connection(
    issuer_profile: dict,
    *,
    connection=None,
    organization_id=None,
    user_id=None,
    transport=None,
) -> tuple[str, str | None]:
    from modules.arca.client import ArcaClient
    from modules.arca.connections import ArcaConnectionError, load_credentials
    from modules.database.arca_repository import get_cached_ta, store_cached_ta

    if not is_arca_fiscal_enabled():
        return "not_configured", "invoice_err_fiscal_issue_unavailable"

    if connection is None:
        return "error", "invoice_err_arca_not_linked"
    if not (connection.get("point_of_sale") or "").strip():
        return "error", "billing_missing_arca_point_of_sale"
    try:
        load_credentials(connection)
    except ArcaConnectionError as error:
        return "error", error.message_key

    try:
        client = ArcaClient(
            transport=transport,
            cache_getter=get_cached_ta,
            cache_setter=store_cached_ta,
        )
        client.authenticate(
            issuer_profile or {},
            {"issuer_tax_id": (issuer_profile or {}).get("tax_id")},
            connection=connection,
            organization_id=organization_id,
            user_id=user_id,
        )
        return "connected", None
    except Exception:
        return "error", "invoice_err_arca_auth_failed"
