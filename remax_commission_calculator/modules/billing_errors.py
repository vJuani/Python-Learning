"""
Billing error messages and optional CTAs for the UI.
"""

from __future__ import annotations

from flask import session, url_for

from modules.auth import ROLE_ADMIN, ROLE_AGENT


def resolve_billing_error_cta(
    error,
    *,
    user,
    operation_id=None,
    side=None,
):
    key = error.message_key
    is_staff = user and user.get("role") == ROLE_ADMIN
    is_agent = user and user.get("role") == ROLE_AGENT

    if key == "invoice_err_billing_profile_incomplete":
        if is_agent and not is_staff:
            return {
                "url": url_for("billing_agent_profile_self"),
                "label_key": "billing_cta_complete_profile",
            }
        if is_staff:
            return {
                "url": url_for("billing_issuers"),
                "label_key": "billing_cta_manage_issuers",
            }

    if is_staff and key in (
        "invoice_err_issuer_default_required",
        "invoice_err_issuer_profile_required",
        "invoice_err_issuer_inactive",
        "invoice_err_issuer_type_invalid",
    ):
        return {
            "url": url_for("billing_issuers"),
            "label_key": "billing_cta_manage_issuers",
        }

    if is_agent and not is_staff and key.startswith(
        "invoice_err_agent_missing_"
    ):
        return {
            "url": url_for("billing_agent_profile_self"),
            "label_key": "billing_cta_complete_profile",
        }

    if is_staff and key.startswith("invoice_err_issuer_missing_"):
        return {
            "url": url_for("billing_issuers"),
            "label_key": "billing_cta_manage_issuers",
        }

    if operation_id is not None and key in (
        "invoice_err_amount_not_set",
        "invoice_err_side_billing_disabled",
        "invoice_err_party_not_participating",
    ):
        return {
            "url": url_for(
                "operations_detail",
                operation_id=operation_id,
            ),
            "label_key": "billing_cta_open_operation",
        }

    if key == "invoice_err_already_invoiced" and error.missing:
        invoice_id = error.missing[0]
        if isinstance(invoice_id, int):
            return {
                "url": url_for(
                    "billing_detail",
                    invoice_id=invoice_id,
                ),
                "label_key": "billing_cta_view_invoice",
            }

    if key == "invoice_err_retry_same_invoice" and error.missing:
        invoice_id = error.missing[0]
        if isinstance(invoice_id, int):
            return {
                "url": url_for(
                    "billing_detail",
                    invoice_id=invoice_id,
                ),
                "label_key": "billing_cta_view_invoice",
            }

    if key in (
        "invoice_err_client_incomplete",
        "invoice_err_party_client_incomplete",
    ) and operation_id and side:
        return {
            "url": url_for(
                "billing_prepare",
                operation_id=operation_id,
                side=side,
            ),
            "label_key": "billing_cta_complete_client",
        }

    return None


def store_billing_error_cta(cta):
    if cta:
        session["billing_flash_cta"] = cta
    else:
        session.pop("billing_flash_cta", None)
