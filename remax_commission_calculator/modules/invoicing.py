"""
Invoicing domain: multi-side / multi-issuer drafts.

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
from modules.database.billing_issuer_profiles_repository import (
    get_profile as get_billing_issuer_profile,
)
from modules.database.connection import IntegrityError, get_connection
from modules.database.invoices_repository import (
    ACTIVE_STATUSES,
    count_invoices_by_status,
    count_pending_parties_to_invoice,
    create_invoice_atomic,
    get_active_invoice_for_operation,
    get_active_invoice_for_side_issuer,
    get_invoice,
    list_invoices,
    list_invoices_for_operation,
    sum_invoiced_amount,
    update_invoice_fields,
    update_invoice_status,
)
from modules.database.operation_parties_repository import (
    ensure_parties_for_operation,
    get_parties_for_operation,
    get_party as get_operation_party,
    set_billing_enabled as set_operation_party_billing_enabled,
    set_invoice_amount as set_operation_party_invoice_amount,
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
    notify_operation_side_ready_to_invoice,
)


DEFAULT_DESCRIPTION = "Asesoramiento Integral de Gestión"
DEFAULT_SERVICE_TYPE = "services"
DEFAULT_QUANTITY = 1
DEFAULT_INVOICE_TYPE = "internal"

SIDE_BUYER = "buyer"
SIDE_SELLER = "seller"
VALID_SIDES = (SIDE_BUYER, SIDE_SELLER)

ISSUER_MODE_AGENT = "agent"
ISSUER_MODE_OFFICE = "office"

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


def issuer_key_for_agent(agent_id):
    return f"agent:{agent_id}"


def issuer_key_for_profile(profile_id):
    return f"issuer:{profile_id}"


def validate_cuit(tax_id):
    if tax_id is None:
        return False
    cleaned = str(tax_id).strip().replace(" ", "")
    if not _CUIT_RE.match(cleaned):
        return False
    digits = cleaned.replace("-", "")
    return len(digits) == 11 and digits.isdigit()


def validate_client_tax_id(tax_id):
    """CUIT or DNI with at least 7 digits."""
    if validate_cuit(tax_id):
        return True
    digits = re.sub(r"\D", "", str(tax_id or ""))
    return len(digits) >= 7


def normalize_cuit(tax_id):
    digits = re.sub(r"\D", "", str(tax_id or ""))
    if len(digits) != 11:
        return (tax_id or "").strip()
    return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"


def normalize_client_tax_id(tax_id):
    if validate_cuit(tax_id):
        return normalize_cuit(tax_id)
    return (tax_id or "").strip()


def org_billing_ready(settings):
    """Office fiscal data readiness (issuer profiles preferred)."""
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
    """Email is optional; required: legal_name, tax_id, tax_condition, fiscal_address."""
    missing = []
    if profile is None:
        return False, [
            "billing_missing_agent_legal_name",
            "billing_missing_agent_tax_id",
            "billing_missing_agent_tax_condition",
            "billing_missing_agent_fiscal_address",
        ]

    if not (profile.get("legal_name") or "").strip():
        missing.append("billing_missing_agent_legal_name")
    if not validate_cuit(profile.get("tax_id")):
        missing.append("billing_missing_agent_tax_id")
    if not (profile.get("tax_condition") or "").strip():
        missing.append("billing_missing_agent_tax_condition")
    if not (profile.get("fiscal_address") or "").strip():
        missing.append("billing_missing_agent_fiscal_address")

    return len(missing) == 0, missing


def issuer_profile_ready(profile):
    missing = []
    if profile is None:
        return False, [
            "billing_missing_issuer_legal_name",
            "billing_missing_issuer_tax_id",
            "billing_missing_issuer_tax_condition",
            "billing_missing_issuer_fiscal_address",
        ]

    if not (profile.get("legal_name") or "").strip():
        missing.append("billing_missing_issuer_legal_name")
    if not validate_cuit(profile.get("tax_id")):
        missing.append("billing_missing_issuer_tax_id")
    if not (profile.get("tax_condition") or "").strip():
        missing.append("billing_missing_issuer_tax_condition")
    if not (profile.get("fiscal_address") or "").strip():
        missing.append("billing_missing_issuer_fiscal_address")

    return len(missing) == 0, missing


def party_client_ready(party):
    missing = []
    if party is None:
        return False, [
            "billing_missing_client_legal_name",
            "billing_missing_client_tax_id",
            "billing_missing_client_tax_condition",
            "billing_missing_client_fiscal_address",
        ]

    if not (party.get("client_legal_name") or "").strip():
        missing.append("billing_missing_client_legal_name")
    if not validate_client_tax_id(party.get("client_tax_id")):
        missing.append("billing_missing_client_tax_id")
    if not (party.get("client_tax_condition") or "").strip():
        missing.append("billing_missing_client_tax_condition")
    if not (party.get("client_fiscal_address") or "").strip():
        missing.append("billing_missing_client_fiscal_address")

    return len(missing) == 0, missing


def operation_has_invoice_amount(operation):
    amount = operation.get("invoice_amount")
    if amount is None:
        return False
    try:
        return float(amount) > 0
    except (TypeError, ValueError):
        return False


def party_has_invoice_amount(party):
    if party is None:
        return False
    amount = party.get("invoice_amount")
    if amount is None:
        return False
    try:
        return float(amount) > 0
    except (TypeError, ValueError):
        return False


def _side_state_from_invoices(invoices, has_amount, can_create):
    active = None
    for inv in invoices:
        if inv.get("status") in ACTIVE_STATUSES:
            active = inv
            break

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
        "can_invoice": can_create,
        "can_create_draft": can_create,
        "has_amount": True,
    }


def get_operation_billing_state(operation, organization_id):
    """Legacy single-state helper (any active invoice)."""
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


def get_operation_sides_state(
    operation,
    organization_id,
    *,
    user=None,
):
    """
    Per-side billing state for buyer and seller.

    Ensures party rows exist. ``can_invoice`` considers
    participation, billing_enabled, amount, client data,
    and whether the current user still has an open slot
    for their issuer key.
    """
    ensure_parties_for_operation(
        organization_id,
        operation["db_id"],
    )
    parties = {
        p["party_role"]: p
        for p in get_parties_for_operation(
            organization_id,
            operation["db_id"],
        )
    }
    all_invoices = list_invoices_for_operation(
        organization_id,
        operation["db_id"],
    )

    issuer_key = None
    if user is not None and user.get("role") == ROLE_AGENT:
        agent_id = user.get("agent_id")
        if agent_id is not None:
            issuer_key = issuer_key_for_agent(agent_id)

    result = {}
    for side in VALID_SIDES:
        party = parties.get(side)
        side_invoices = [
            inv
            for inv in all_invoices
            if inv.get("side") == side
        ]
        has_amount = (
            party is not None
            and bool(party.get("is_participating"))
            and bool(party.get("billing_enabled"))
            and party_has_invoice_amount(party)
        )
        client_ok, _ = party_client_ready(party)

        can_create = False
        if (
            has_amount
            and client_ok
            and party is not None
        ):
            if issuer_key is not None:
                active = get_active_invoice_for_side_issuer(
                    organization_id,
                    operation["db_id"],
                    side,
                    issuer_key,
                )
                can_create = active is None
            else:
                can_create = True

        side_info = _side_state_from_invoices(
            side_invoices,
            has_amount,
            can_create,
        )
        side_info["party"] = party
        side_info["invoices"] = side_invoices
        side_info["side"] = side
        result[side] = side_info

    return result


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
    """Legacy: set operation-level amount (also mirrors buyer)."""
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

    ensure_parties_for_operation(
        organization_id,
        operation_id,
    )
    try:
        set_operation_party_invoice_amount(
            organization_id,
            operation_id,
            SIDE_BUYER,
            invoice_amount=amount_value,
            invoice_currency=currency_value,
            invoice_exchange_rate=rate_value,
            set_by_user_id=user_id,
        )
        set_operation_party_billing_enabled(
            organization_id,
            operation_id,
            SIDE_BUYER,
            enabled=True,
            by_user_id=user_id,
        )
    except ValueError:
        pass

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


def set_party_invoice_amount(
    organization_id,
    operation_id,
    side,
    amount,
    currency,
    exchange_rate,
    user_id,
    *,
    enable_billing=True,
    notify=True,
):
    if side not in VALID_SIDES:
        raise InvoicingError("invoice_err_side_invalid")

    operation = get_operation_record(
        operation_id,
        organization_id,
    )
    if operation is None:
        raise InvoicingError("invoice_err_operation_not_found")

    ensure_parties_for_operation(
        organization_id,
        operation_id,
    )

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

    party = set_operation_party_invoice_amount(
        organization_id,
        operation_id,
        side,
        invoice_amount=amount_value,
        invoice_currency=currency_value,
        invoice_exchange_rate=rate_value,
        set_by_user_id=user_id,
    )

    # Enabling billing implies the side participates.
    from modules.database.operation_parties_repository import (
        upsert_party as upsert_operation_party,
    )

    upsert_operation_party(
        organization_id,
        operation_id,
        side,
        is_participating=True,
    )

    if enable_billing:
        party = set_operation_party_billing_enabled(
            organization_id,
            operation_id,
            side,
            enabled=True,
            by_user_id=user_id,
        )

    if notify:
        notify_operation_side_ready_to_invoice(
            organization_id,
            operation["agent_db_id"],
            operation_id,
            payload={
                "operation_id": operation.get("id"),
                "property": operation.get("property"),
                "side": side,
                "amount": amount_value,
                "currency": currency_value,
            },
            actor_user_id=user_id,
        )

    return party


def _recipient_from_party(party):
    return {
        "recipient_name": (
            party.get("client_legal_name") or ""
        ).strip(),
        "recipient_tax_id": normalize_client_tax_id(
            party.get("client_tax_id")
        ),
        "recipient_tax_condition": (
            party.get("client_tax_condition") or ""
        ).strip(),
        "recipient_address": (
            party.get("client_fiscal_address") or ""
        ).strip(),
        "recipient_party_id": party.get("id"),
    }


def _resolve_issuer(
    organization_id,
    operation,
    user,
    *,
    issuer_mode,
    issuer_profile_id=None,
):
    settings = get_organization_settings(organization_id)
    role = user.get("role")

    if role == ROLE_AGENT:
        if issuer_mode != ISSUER_MODE_AGENT:
            raise InvoicingError("invoice_err_forbidden")
        if user.get("agent_id") != operation["agent_db_id"]:
            raise InvoicingError("invoice_err_forbidden")
        if not settings.get("agents_can_invoice", True):
            raise InvoicingError("invoice_err_agents_cannot_invoice")

        profile = get_agent_billing_profile(
            organization_id,
            operation["agent_db_id"],
        )
        ready, missing = agent_billing_ready(profile)
        if not ready:
            raise InvoicingError(
                "invoice_err_billing_profile_incomplete",
                missing=missing,
            )
        return {
            "issuer_user_id": user.get("id"),
            "issuer_type": "agent",
            "issuer_name": profile["legal_name"].strip(),
            "issuer_tax_id": normalize_cuit(profile["tax_id"]),
            "issuer_tax_condition": profile["tax_condition"],
            "issuer_address": profile["fiscal_address"],
            "issuer_profile_id": None,
            "issuer_key": issuer_key_for_agent(
                operation["agent_db_id"]
            ),
            "source": "agent_operation",
            "point_of_sale": None,
        }

    if role != ROLE_ADMIN:
        raise InvoicingError("invoice_err_forbidden")

    if issuer_mode == ISSUER_MODE_AGENT:
        if not settings.get("agents_can_invoice", True):
            raise InvoicingError("invoice_err_agents_cannot_invoice")
        profile = get_agent_billing_profile(
            organization_id,
            operation["agent_db_id"],
        )
        ready, missing = agent_billing_ready(profile)
        if not ready:
            raise InvoicingError(
                "invoice_err_billing_profile_incomplete",
                missing=missing,
            )
        return {
            "issuer_user_id": user.get("id"),
            "issuer_type": "agent",
            "issuer_name": profile["legal_name"].strip(),
            "issuer_tax_id": normalize_cuit(profile["tax_id"]),
            "issuer_tax_condition": profile["tax_condition"],
            "issuer_address": profile["fiscal_address"],
            "issuer_profile_id": None,
            "issuer_key": issuer_key_for_agent(
                operation["agent_db_id"]
            ),
            "source": "admin",
            "point_of_sale": None,
        }

    if issuer_mode == ISSUER_MODE_OFFICE:
        if not settings.get("office_can_invoice", True):
            raise InvoicingError("invoice_err_office_cannot_invoice")
        if issuer_profile_id is None:
            issuer_profile_id = settings.get(
                "default_issuer_profile_id"
            )
        if issuer_profile_id is None:
            raise InvoicingError(
                "invoice_err_issuer_profile_required"
            )
        profile = get_billing_issuer_profile(
            organization_id,
            int(issuer_profile_id),
        )
        if profile is None or not profile.get("is_active", True):
            raise InvoicingError(
                "invoice_err_issuer_profile_not_found"
            )
        ready, missing = issuer_profile_ready(profile)
        if not ready:
            raise InvoicingError(
                "invoice_err_billing_profile_incomplete",
                missing=missing,
            )
        return {
            "issuer_user_id": user.get("id"),
            # Stored as 'admin' to satisfy legacy CHECK; office
            # issuers are distinguished by issuer_profile_id /
            # issuer_key=issuer:{id}.
            "issuer_type": "admin",
            "issuer_name": profile["legal_name"].strip(),
            "issuer_tax_id": normalize_cuit(profile["tax_id"]),
            "issuer_tax_condition": profile["tax_condition"],
            "issuer_address": profile["fiscal_address"],
            "issuer_profile_id": profile["id"],
            "issuer_key": issuer_key_for_profile(profile["id"]),
            "source": "admin",
            "point_of_sale": profile.get("point_of_sale"),
        }

    raise InvoicingError("invoice_err_issuer_mode_invalid")


def build_draft_preview_for_side(
    organization_id,
    operation_id,
    side,
    user,
    *,
    issuer_mode,
    issuer_profile_id=None,
    payment_condition=None,
    issue_date=None,
):
    if side not in VALID_SIDES:
        raise InvoicingError("invoice_err_side_invalid")

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

    if user.get("role") == ROLE_AGENT:
        if user.get("agent_id") != operation["agent_db_id"]:
            raise InvoicingError("invoice_err_forbidden")

    ensure_parties_for_operation(
        organization_id,
        operation_id,
    )
    party = get_operation_party(
        organization_id,
        operation_id,
        side,
    )
    if party is None or not party.get("is_participating"):
        raise InvoicingError("invoice_err_party_not_participating")
    if not party.get("billing_enabled"):
        raise InvoicingError("invoice_err_billing_not_enabled")
    if not party_has_invoice_amount(party):
        raise InvoicingError("invoice_err_amount_not_set")

    client_ok, client_missing = party_client_ready(party)
    if not client_ok:
        raise InvoicingError(
            "invoice_err_client_incomplete",
            missing=client_missing,
        )

    issuer = _resolve_issuer(
        organization_id,
        operation,
        user,
        issuer_mode=issuer_mode,
        issuer_profile_id=issuer_profile_id,
    )

    active = get_active_invoice_for_side_issuer(
        organization_id,
        operation_id,
        side,
        issuer["issuer_key"],
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

    settings = get_organization_settings(organization_id)
    amount = float(party["invoice_amount"])
    currency = (
        party.get("invoice_currency") or "ARS"
    ).upper()
    exchange_rate = party.get("invoice_exchange_rate")

    pay = payment_condition or settings.get(
        "default_payment_condition"
    ) or PAYMENT_CUENTA_CORRIENTE
    if pay not in PAYMENT_CONDITIONS:
        raise InvoicingError(
            "invoice_err_payment_condition_invalid"
        )

    issue = issue_date or _today_iso()
    description = (
        (settings.get("default_invoice_description") or "").strip()
        or DEFAULT_DESCRIPTION
    )
    service_type = (
        (settings.get("default_invoice_service_type") or "").strip()
        or DEFAULT_SERVICE_TYPE
    )

    recipient = _recipient_from_party(party)

    return {
        "operation_id": operation["db_id"],
        "operation_display_id": operation.get("id"),
        "property_address": operation.get("property"),
        "agent_id": operation["agent_db_id"],
        "agent_name": operation.get("agent"),
        "side": side,
        "issuer_mode": issuer_mode,
        **{k: issuer[k] for k in (
            "issuer_user_id",
            "issuer_type",
            "issuer_name",
            "issuer_tax_id",
            "issuer_tax_condition",
            "issuer_address",
            "issuer_profile_id",
            "issuer_key",
            "source",
            "point_of_sale",
        )},
        **recipient,
        "invoice_type": DEFAULT_INVOICE_TYPE,
        "service_type": service_type,
        "description": description,
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
        "provider": get_invoice_provider_name(),
        "non_fiscal_notice": True,
        "party": party,
    }


def create_draft_for_side(
    organization_id,
    operation_id,
    side,
    user,
    *,
    issuer_mode,
    issuer_profile_id=None,
    payment_condition=None,
    issue_date=None,
):
    preview = build_draft_preview_for_side(
        organization_id,
        operation_id,
        side,
        user,
        issuer_mode=issuer_mode,
        issuer_profile_id=issuer_profile_id,
        payment_condition=payment_condition,
        issue_date=issue_date,
    )

    # Agents cannot override amount: always from party.
    fields = {
        "operation_id": preview["operation_id"],
        "agent_id": preview["agent_id"],
        "issuer_user_id": preview["issuer_user_id"],
        "issuer_type": preview["issuer_type"],
        "issuer_name": preview["issuer_name"],
        "issuer_tax_id": preview["issuer_tax_id"],
        "issuer_tax_condition": preview[
            "issuer_tax_condition"
        ],
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
        "point_of_sale": preview.get("point_of_sale"),
        "created_by_user_id": user.get("id"),
        "side": preview["side"],
        "issuer_profile_id": preview.get("issuer_profile_id"),
        "issuer_key": preview["issuer_key"],
        "recipient_party_id": preview.get(
            "recipient_party_id"
        ),
    }

    # Never issued with internal provider.
    if (
        fields["provider"] == "internal"
        and fields["status"] == STATUS_ISSUED
    ):
        fields["status"] = STATUS_DRAFT

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


def create_draft_from_operation(
    organization_id,
    operation_id,
    user,
    *,
    payment_condition=None,
    issue_date=None,
):
    raise InvoicingError(
        "invoice_err_use_create_draft_for_side"
    )


def build_draft_preview_from_operation(
    organization_id,
    operation,
    user,
    *,
    payment_condition=None,
    issue_date=None,
):
    raise InvoicingError(
        "invoice_err_use_create_draft_for_side"
    )


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
        raise InvoicingError(
            "invoice_err_fiscal_issue_unavailable"
        )

    # Internal: confirm → ready_to_issue (still non-fiscal).
    # Never set status issued; never touch was_invoiced.
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
    return count_pending_parties_to_invoice(
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
    """
    Pending parties ready to invoice (with operation).

    Returns list of dicts: operation fields + party + side.
    """
    clauses = [
        "op.organization_id = ?",
        "op.is_participating = 1",
        "op.billing_enabled = 1",
        "op.invoice_amount IS NOT NULL",
        "op.invoice_amount > 0",
    ]
    params = [organization_id]

    if agent_id is not None:
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        issuer_key = issuer_key_for_agent(agent_id)
        clauses.append("o.agent_id = ?")
        params.append(agent_id)
        clauses.append(
            f"""
            NOT EXISTS (
                SELECT 1 FROM invoices
                WHERE invoices.organization_id
                    = op.organization_id
                  AND invoices.operation_id
                    = op.operation_id
                  AND invoices.side = op.party_role
                  AND invoices.issuer_key = ?
                  AND invoices.status IN ({placeholders})
            )
            """
        )
        params.append(issuer_key)
        params.extend(ACTIVE_STATUSES)

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"""
            SELECT
                op.operation_id,
                op.party_role
            FROM operation_parties op
            INNER JOIN operations o
                ON o.id = op.operation_id
                AND o.organization_id = op.organization_id
            WHERE {" AND ".join(clauses)}
            ORDER BY op.operation_id DESC,
                CASE op.party_role
                    WHEN 'buyer' THEN 0
                    WHEN 'seller' THEN 1
                    ELSE 2
                END
            LIMIT ?
            """,
            (*params, int(limit)),
        )
        pairs = cursor.fetchall()
    finally:
        connection.close()

    results = []
    for operation_id, side in pairs:
        operation = get_operation_record(
            operation_id,
            organization_id,
        )
        if operation is None:
            continue
        party = get_operation_party(
            organization_id,
            operation_id,
            side,
        )
        if party is None:
            continue
        item = dict(operation)
        item["party"] = party
        item["side"] = side
        item["invoice_amount"] = party.get("invoice_amount")
        item["invoice_currency"] = party.get(
            "invoice_currency"
        )
        item["invoice_exchange_rate"] = party.get(
            "invoice_exchange_rate"
        )
        results.append(item)
    return results


# Re-export helpers used by routes/tests
__all__ = [
    "InvoicingError",
    "DEFAULT_DESCRIPTION",
    "SIDE_BUYER",
    "SIDE_SELLER",
    "ISSUER_MODE_AGENT",
    "ISSUER_MODE_OFFICE",
    "PAYMENT_CONDITIONS",
    "TAX_CONDITIONS",
    "STATUS_DRAFT",
    "STATUS_READY",
    "STATUS_ISSUED",
    "STATUS_ERROR",
    "STATUS_CANCELLED",
    "validate_cuit",
    "validate_client_tax_id",
    "org_billing_ready",
    "agent_billing_ready",
    "issuer_profile_ready",
    "party_client_ready",
    "issuer_key_for_agent",
    "issuer_key_for_profile",
    "set_operation_invoice_amount",
    "set_party_invoice_amount",
    "get_operation_billing_state",
    "get_operation_sides_state",
    "build_draft_preview_for_side",
    "create_draft_for_side",
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
