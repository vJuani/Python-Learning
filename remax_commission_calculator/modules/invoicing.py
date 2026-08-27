"""
Invoicing domain: drafts, fiscal readiness, operation amounts.

Provider ``internal`` never marks invoices as fiscally issued
and never mutates operations.was_invoiced.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from modules.auth import ROLE_ADMIN, ROLE_AGENT
from modules.database.agent_billing_profiles_repository import (
    get_by_agent as get_agent_billing_profile,
)
from modules.database.connection import IntegrityError, get_connection
from modules.database.invoices_repository import (
    ACTIVE_STATUSES,
    count_invoices_by_status,
    count_pending_operations_to_invoice,
    create_invoice_atomic,
    get_active_invoice_for_operation,
    get_invoice,
    list_invoices,
    sum_invoiced_amount,
    update_invoice_fields,
    update_invoice_status,
)
from modules.database.operations_repository import (
    get_operation_record,
    update_operation_invoice_amount,
)
from modules.database.organization_settings_repository import (
    get_organization_settings,
)
from modules.invoice_provider import (
    get_invoice_provider,
    get_invoice_provider_name,
)
from modules.notifications_service import (
    notify_operation_invoice_amount_ready,
)


DEFAULT_DESCRIPTION = "Asesoramiento Integral de Gestión"
DEFAULT_SERVICE_TYPE = "services"
DEFAULT_QUANTITY = 1
DEFAULT_INVOICE_TYPE = "internal"

STATUS_DRAFT = "draft"
STATUS_READY = "ready_to_issue"
STATUS_ISSUED = "issued"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

PAYMENT_CONTADO = "contado"
PAYMENT_CUENTA_CORRIENTE = "cuenta_corriente"
PAYMENT_CONDITIONS = (
    PAYMENT_CONTADO,
    PAYMENT_CUENTA_CORRIENTE,
)

TAX_CONDITIONS = (
    "responsable_inscripto",
    "monotributo",
    "exento",
    "consumidor_final",
)

_CUIT_RE = re.compile(r"^\d{2}-?\d{8}-?\d$")


class InvoicingError(Exception):
    def __init__(self, message_key, *, missing=None):
        super().__init__(message_key)
        self.message_key = message_key
        self.missing = missing or []


def _now_iso():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def _today_iso():
    return date.today().isoformat()


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


def org_billing_ready(settings):
    missing = []
    if settings is None:
        return False, [
            "billing_missing_org_legal_name",
            "billing_missing_org_tax_id",
            "billing_missing_org_tax_condition",
            "billing_missing_org_fiscal_address",
        ]

    if not (settings.get("legal_name") or "").strip():
        missing.append("billing_missing_org_legal_name")
    if not validate_cuit(settings.get("tax_id")):
        missing.append("billing_missing_org_tax_id")
    if not (settings.get("tax_condition") or "").strip():
        missing.append("billing_missing_org_tax_condition")
    if not (settings.get("fiscal_address") or "").strip():
        missing.append("billing_missing_org_fiscal_address")

    return len(missing) == 0, missing


def agent_billing_ready(profile):
    missing = []
    if profile is None:
        return False, [
            "billing_missing_agent_legal_name",
            "billing_missing_agent_tax_id",
            "billing_missing_agent_tax_condition",
            "billing_missing_agent_fiscal_address",
            "billing_missing_agent_email",
        ]

    if not (profile.get("legal_name") or "").strip():
        missing.append("billing_missing_agent_legal_name")
    if not validate_cuit(profile.get("tax_id")):
        missing.append("billing_missing_agent_tax_id")
    if not (profile.get("tax_condition") or "").strip():
        missing.append("billing_missing_agent_tax_condition")
    if not (profile.get("fiscal_address") or "").strip():
        missing.append("billing_missing_agent_fiscal_address")
    if not (profile.get("email") or "").strip():
        missing.append("billing_missing_agent_email")

    return len(missing) == 0, missing


def operation_has_invoice_amount(operation):
    amount = operation.get("invoice_amount")
    if amount is None:
        return False
    try:
        return float(amount) > 0
    except (TypeError, ValueError):
        return False


def get_operation_billing_state(operation, organization_id):
    active = get_active_invoice_for_operation(
        organization_id,
        operation["db_id"],
    )
    has_amount = operation_has_invoice_amount(operation)

    if active is not None:
        status = active.get("status")
        state_map = {
            STATUS_DRAFT: "has_draft",
            STATUS_READY: "ready_to_issue",
            STATUS_ISSUED: "issued",
            STATUS_ERROR: "error",
        }
        return {
            "state": state_map.get(status, "has_invoice"),
            "invoice": active,
            "can_invoice": False,
            "can_create_draft": False,
            "has_amount": has_amount,
        }

    if not has_amount:
        return {
            "state": "pending_amount",
            "invoice": None,
            "can_invoice": False,
            "can_create_draft": False,
            "has_amount": False,
        }

    return {
        "state": "pending",
        "invoice": None,
        "can_invoice": True,
        "can_create_draft": True,
        "has_amount": True,
    }


def parse_invoice_amount(raw_amount):
    text = str(raw_amount or "").strip().replace(",", ".")
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise InvoicingError("invoice_err_amount_invalid")
    if value <= 0:
        raise InvoicingError("invoice_err_amount_invalid")
    return value


def set_operation_invoice_amount(
    organization_id,
    operation_id,
    amount,
    currency,
    exchange_rate,
    user_id,
    *,
    notify=True,
):
    operation = get_operation_record(
        operation_id,
        organization_id,
    )
    if operation is None:
        raise InvoicingError("invoice_err_operation_not_found")

    amount_value = parse_invoice_amount(amount)
    currency_value = (currency or "ARS").strip().upper()
    if currency_value not in ("ARS", "USD"):
        raise InvoicingError("invoice_err_currency_invalid")

    rate_value = None
    if exchange_rate is not None and str(exchange_rate).strip():
        try:
            rate_value = float(
                str(exchange_rate).strip().replace(",", ".")
            )
        except (TypeError, ValueError):
            raise InvoicingError("invoice_err_exchange_invalid")
        if rate_value <= 0:
            raise InvoicingError("invoice_err_exchange_invalid")

    set_at = _now_iso()
    update_operation_invoice_amount(
        operation_id,
        organization_id,
        amount_value,
        currency_value,
        rate_value,
        set_at,
        user_id,
    )

    if notify:
        notify_operation_invoice_amount_ready(
            organization_id,
            operation["agent_db_id"],
            operation_id,
            payload={
                "operation_id": operation.get("id"),
                "property": operation.get("property"),
                "amount": amount_value,
                "currency": currency_value,
            },
            actor_user_id=user_id,
        )

    return get_operation_record(operation_id, organization_id)


def _recipient_from_org(settings):
    name = (
        (settings.get("legal_name") or "").strip()
        or (settings.get("trade_name") or "").strip()
        or (settings.get("display_name") or "").strip()
    )
    return {
        "recipient_name": name,
        "recipient_tax_id": normalize_cuit(
            settings.get("tax_id")
        ),
        "recipient_tax_condition": (
            settings.get("tax_condition") or ""
        ).strip(),
        "recipient_address": (
            settings.get("fiscal_address") or ""
        ).strip(),
    }


def build_draft_preview_from_operation(
    organization_id,
    operation,
    user,
    *,
    payment_condition=None,
    issue_date=None,
):
    settings = get_organization_settings(organization_id)
    org_ok, org_missing = org_billing_ready(settings)
    profile = get_agent_billing_profile(
        organization_id,
        operation["agent_db_id"],
    )
    agent_ok, agent_missing = agent_billing_ready(profile)

    missing = org_missing + agent_missing
    if missing:
        raise InvoicingError(
            "invoice_err_billing_profile_incomplete",
            missing=missing,
        )

    if not operation_has_invoice_amount(operation):
        raise InvoicingError("invoice_err_amount_not_set")

    active = get_active_invoice_for_operation(
        organization_id,
        operation["db_id"],
    )
    if active is not None:
        if active.get("status") == STATUS_ERROR:
            raise InvoicingError(
                "invoice_err_retry_same_invoice",
                missing=[active["id"]],
            )
        raise InvoicingError(
            "invoice_err_already_invoiced",
            missing=[active["id"]],
        )

    amount = float(operation["invoice_amount"])
    currency = (
        operation.get("invoice_currency") or "ARS"
    ).upper()
    exchange_rate = operation.get("invoice_exchange_rate")

    pay = payment_condition or settings.get(
        "default_payment_condition"
    ) or PAYMENT_CUENTA_CORRIENTE
    if pay not in PAYMENT_CONDITIONS:
        raise InvoicingError(
            "invoice_err_payment_condition_invalid"
        )

    issue = issue_date or _today_iso()

    issuer_type = (
        "admin"
        if user.get("role") == ROLE_ADMIN
        else "agent"
    )

    recipient = _recipient_from_org(settings)

    return {
        "operation_id": operation["db_id"],
        "operation_display_id": operation.get("id"),
        "property_address": operation.get("property"),
        "agent_id": operation["agent_db_id"],
        "agent_name": operation.get("agent"),
        "issuer_user_id": user.get("id"),
        "issuer_type": issuer_type,
        "issuer_name": profile["legal_name"].strip(),
        "issuer_tax_id": normalize_cuit(profile["tax_id"]),
        "issuer_tax_condition": profile["tax_condition"],
        "issuer_address": profile["fiscal_address"],
        **recipient,
        "invoice_type": DEFAULT_INVOICE_TYPE,
        "service_type": DEFAULT_SERVICE_TYPE,
        "description": DEFAULT_DESCRIPTION,
        "quantity": DEFAULT_QUANTITY,
        "unit_price": amount,
        "subtotal": amount,
        "vat_amount": 0,
        "total_amount": amount,
        "currency": currency,
        "exchange_rate": exchange_rate,
        "payment_condition": pay,
        "issue_date": issue,
        "status": STATUS_DRAFT,
        "source": (
            "admin"
            if issuer_type == "admin"
            else "agent_operation"
        ),
        "provider": get_invoice_provider_name(),
        "non_fiscal_notice": True,
    }


def create_draft_from_operation(
    organization_id,
    operation_id,
    user,
    *,
    payment_condition=None,
    issue_date=None,
):
    # Guest must also be blocked at the route layer.
    if user is None or user.get("role") not in (
        ROLE_ADMIN,
        ROLE_AGENT,
    ):
        raise InvoicingError("invoice_err_forbidden")

    operation = get_operation_record(
        operation_id,
        organization_id,
    )
    if operation is None:
        raise InvoicingError("invoice_err_operation_not_found")

    # Agent may only invoice own operations.
    if user.get("role") == ROLE_AGENT:
        if user.get("agent_id") != operation["agent_db_id"]:
            raise InvoicingError("invoice_err_forbidden")

    preview = build_draft_preview_from_operation(
        organization_id,
        operation,
        user,
        payment_condition=payment_condition,
        issue_date=issue_date,
    )

    fields = {
        "operation_id": preview["operation_id"],
        "agent_id": preview["agent_id"],
        "issuer_user_id": preview["issuer_user_id"],
        "issuer_type": preview["issuer_type"],
        "issuer_name": preview["issuer_name"],
        "issuer_tax_id": preview["issuer_tax_id"],
        "issuer_tax_condition": preview["issuer_tax_condition"],
        "issuer_address": preview["issuer_address"],
        "recipient_name": preview["recipient_name"],
        "recipient_tax_id": preview["recipient_tax_id"],
        "recipient_tax_condition": preview[
            "recipient_tax_condition"
        ],
        "recipient_address": preview["recipient_address"],
        "invoice_type": preview["invoice_type"],
        "service_type": preview["service_type"],
        "description": preview["description"],
        "quantity": preview["quantity"],
        "unit_price": preview["unit_price"],
        "subtotal": preview["subtotal"],
        "vat_amount": preview["vat_amount"],
        "total_amount": preview["total_amount"],
        "currency": preview["currency"],
        "exchange_rate": preview["exchange_rate"],
        "payment_condition": preview["payment_condition"],
        "issue_date": preview["issue_date"],
        "status": STATUS_DRAFT,
        "source": preview["source"],
        "provider": preview["provider"],
        "created_by_user_id": user.get("id"),
    }

    try:
        created = create_invoice_atomic(
            organization_id,
            fields=fields,
        )
    except IntegrityError as exc:
        raise InvoicingError(
            "invoice_err_already_invoiced"
        ) from exc

    return get_invoice(organization_id, created["id"])


def confirm_draft(organization_id, invoice_id, user):
    invoice = _require_invoice_access(
        organization_id,
        invoice_id,
        user,
    )
    if invoice["status"] not in (
        STATUS_DRAFT,
        STATUS_ERROR,
    ):
        raise InvoicingError(
            "invoice_err_invalid_transition"
        )

    provider = get_invoice_provider()
    if provider.can_issue_fiscal():
        # Future ARCA path would issue here.
        raise InvoicingError(
            "invoice_err_fiscal_issue_unavailable"
        )

    # Internal: confirm → ready_to_issue (still non-fiscal).
    update_invoice_status(
        organization_id,
        invoice_id,
        STATUS_READY,
        confirmed_at=_now_iso(),
        confirmed_by_user_id=user.get("id"),
        clear_cancellation=True,
    )
    return get_invoice(organization_id, invoice_id)


def cancel_invoice(
    organization_id,
    invoice_id,
    user,
    *,
    reason="",
):
    invoice = _require_invoice_access(
        organization_id,
        invoice_id,
        user,
        admin_or_owner=True,
    )
    if invoice["status"] == STATUS_ISSUED:
        raise InvoicingError(
            "invoice_err_cannot_cancel_issued"
        )
    if invoice["status"] == STATUS_CANCELLED:
        raise InvoicingError(
            "invoice_err_invalid_transition"
        )
    if invoice["provider"] == "internal" and invoice[
        "status"
    ] not in (
        STATUS_DRAFT,
        STATUS_READY,
        STATUS_ERROR,
    ):
        raise InvoicingError(
            "invoice_err_invalid_transition"
        )

    update_invoice_status(
        organization_id,
        invoice_id,
        STATUS_CANCELLED,
        cancelled_at=_now_iso(),
        cancelled_by_user_id=user.get("id"),
        cancellation_reason=(reason or "").strip() or None,
    )
    return get_invoice(organization_id, invoice_id)


def retry_error_invoice(organization_id, invoice_id, user):
    invoice = _require_invoice_access(
        organization_id,
        invoice_id,
        user,
    )
    if invoice["status"] != STATUS_ERROR:
        raise InvoicingError(
            "invoice_err_invalid_transition"
        )
    update_invoice_status(
        organization_id,
        invoice_id,
        STATUS_DRAFT,
        clear_cancellation=True,
        clear_confirmation=True,
    )
    return get_invoice(organization_id, invoice_id)


def update_draft_options(
    organization_id,
    invoice_id,
    user,
    *,
    payment_condition=None,
    issue_date=None,
):
    invoice = _require_invoice_access(
        organization_id,
        invoice_id,
        user,
    )
    if invoice["status"] != STATUS_DRAFT:
        raise InvoicingError(
            "invoice_err_invalid_transition"
        )
    if (
        payment_condition is not None
        and payment_condition not in PAYMENT_CONDITIONS
    ):
        raise InvoicingError(
            "invoice_err_payment_condition_invalid"
        )
    update_invoice_fields(
        organization_id,
        invoice_id,
        payment_condition=payment_condition,
        issue_date=issue_date,
    )
    return get_invoice(organization_id, invoice_id)


def generate_draft_pdf_bytes(organization_id, invoice_id, user):
    invoice = _require_invoice_access(
        organization_id,
        invoice_id,
        user,
    )
    provider = get_invoice_provider()
    return provider.generate_draft_pdf(invoice)


def _require_invoice_access(
    organization_id,
    invoice_id,
    user,
    *,
    admin_or_owner=False,
):
    invoice = get_invoice(organization_id, invoice_id)
    if invoice is None:
        raise InvoicingError("invoice_err_not_found")

    if user.get("role") == ROLE_ADMIN:
        return invoice

    if user.get("role") == ROLE_AGENT:
        if user.get("agent_id") != invoice["agent_id"]:
            raise InvoicingError("invoice_err_forbidden")
        return invoice

    raise InvoicingError("invoice_err_forbidden")


def count_pending_to_invoice(
    organization_id,
    agent_id=None,
):
    """Reusable counter for agent dashboard (prep)."""
    return count_pending_operations_to_invoice(
        organization_id,
        agent_id=agent_id,
    )


def billing_kpis(organization_id, *, agent_id=None):
    counts = count_invoices_by_status(
        organization_id,
        agent_id=agent_id,
    )
    pending = count_pending_to_invoice(
        organization_id,
        agent_id=agent_id,
    )
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    total_month = sum_invoiced_amount(
        organization_id,
        month_start=month_start,
        agent_id=agent_id,
    )
    return {
        "pending": pending,
        "drafts": counts["draft"] + counts["error"],
        "ready_to_issue": counts["ready_to_issue"],
        # Never show as fiscal "issued" for internal provider.
        "issued_month": counts["issued"],
        "total_month": total_month,
        "by_status": counts,
    }


def list_pending_operations(
    organization_id,
    *,
    agent_id=None,
    limit=100,
):
    """Operations ready to invoice (amount set, no active invoice)."""
    from modules.database.operations_repository import (
        OPERATIONS_BASE_QUERY,
        build_operation_dict,
    )

    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    clauses = [
        "operations.organization_id = ?",
        "operations.invoice_amount IS NOT NULL",
        "operations.invoice_amount > 0",
        f"""
        NOT EXISTS (
            SELECT 1 FROM invoices
            WHERE invoices.organization_id
                = operations.organization_id
              AND invoices.operation_id = operations.id
              AND invoices.status IN ({placeholders})
        )
        """,
    ]
    params = [organization_id, *ACTIVE_STATUSES]
    if agent_id is not None:
        clauses.append("operations.agent_id = ?")
        params.append(agent_id)

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            OPERATIONS_BASE_QUERY
            + f"""
            WHERE {" AND ".join(clauses)}
            ORDER BY operations.id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        )
        return build_operation_dict(cursor.fetchall())
    finally:
        connection.close()


# Re-export helpers used by routes/tests
__all__ = [
    "InvoicingError",
    "DEFAULT_DESCRIPTION",
    "PAYMENT_CONDITIONS",
    "TAX_CONDITIONS",
    "STATUS_DRAFT",
    "STATUS_READY",
    "STATUS_ISSUED",
    "STATUS_ERROR",
    "STATUS_CANCELLED",
    "validate_cuit",
    "org_billing_ready",
    "agent_billing_ready",
    "set_operation_invoice_amount",
    "get_operation_billing_state",
    "build_draft_preview_from_operation",
    "create_draft_from_operation",
    "confirm_draft",
    "cancel_invoice",
    "retry_error_invoice",
    "update_draft_options",
    "generate_draft_pdf_bytes",
    "count_pending_to_invoice",
    "billing_kpis",
    "list_pending_operations",
    "list_invoices",
    "get_invoice",
]
