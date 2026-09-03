"""
Pre-issue validation before calling ARCA.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.arca.config import get_arca_environment, is_arca_fiscal_enabled
from modules.arca.secrets import load_credentials
from modules.arca.voucher_mapping import resolve_voucher_type
from modules.billing_issuer_validation import validate_cuit


@dataclass
class FiscalIssueValidation:
    is_valid: bool
    error_key: str | None = None
    missing: list[str] = field(default_factory=list)
    voucher_type: int | None = None
    point_of_sale: int | None = None


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def validate_fiscal_issue(
    invoice: dict,
    issuer_profile: dict,
) -> FiscalIssueValidation:
    missing = []

    if not is_arca_fiscal_enabled():
        return FiscalIssueValidation(
            is_valid=False,
            error_key="invoice_err_fiscal_issue_unavailable",
        )

    status = (issuer_profile or {}).get(
        "arca_connection_status"
    )
    if status not in ("connected", "configuring"):
        return FiscalIssueValidation(
            is_valid=False,
            error_key="invoice_err_arca_not_configured",
        )

    if invoice.get("status") not in (
        "ready_to_issue",
        "error",
    ):
        return FiscalIssueValidation(
            is_valid=False,
            error_key="invoice_err_invalid_transition",
        )

    if (invoice.get("currency") or "ARS").upper() != "ARS":
        return FiscalIssueValidation(
            is_valid=False,
            error_key="invoice_err_arca_currency_not_supported",
        )

    # Credentials must exist (does not call ARCA).
    try:
        load_credentials(issuer_profile)
    except (FileNotFoundError, ValueError):
        return FiscalIssueValidation(
            is_valid=False,
            error_key="invoice_err_arca_credentials_missing",
        )

    cuit = issuer_profile.get("tax_id") or invoice.get(
        "issuer_tax_id"
    )
    if not validate_cuit(cuit):
        missing.append("billing_missing_issuer_tax_id")

    pv_raw = (
        issuer_profile.get("arca_point_of_sale")
        or issuer_profile.get("point_of_sale")
    )
    if not pv_raw or not str(pv_raw).strip().isdigit():
        missing.append("billing_missing_arca_point_of_sale")

    if not (invoice.get("recipient_name") or "").strip():
        missing.append("billing_missing_client_legal_name")
    if not validate_cuit(invoice.get("recipient_tax_id")):
        if len(_digits(invoice.get("recipient_tax_id"))) < 7:
            missing.append("billing_missing_client_tax_id")

    if not (invoice.get("recipient_tax_condition") or "").strip():
        missing.append("billing_missing_client_tax_condition")

    total = float(invoice.get("total_amount") or 0)
    if total <= 0:
        missing.append("invoice_err_amount_invalid")

    if not (invoice.get("issue_date") or "").strip():
        missing.append("billing_missing_issue_date")

    if (
        invoice.get("origin_type", "operation") == "operation"
        and not invoice.get("side")
    ):
        missing.append("invoice_err_side_invalid")

    if missing:
        return FiscalIssueValidation(
            is_valid=False,
            error_key="invoice_err_arca_preissue_incomplete",
            missing=missing,
        )

    voucher_type = resolve_voucher_type(
        issuer_tax_condition=issuer_profile.get(
            "tax_condition"
        )
        or invoice.get("issuer_tax_condition"),
        recipient_tax_condition=invoice.get(
            "recipient_tax_condition"
        ),
        explicit_type=issuer_profile.get("arca_voucher_types"),
    )

    try:
        get_arca_environment()
    except Exception:
        return FiscalIssueValidation(
            is_valid=False,
            error_key="invoice_err_arca_production_blocked",
        )

    return FiscalIssueValidation(
        is_valid=True,
        voucher_type=voucher_type,
        point_of_sale=int(str(pv_raw).strip()),
    )
