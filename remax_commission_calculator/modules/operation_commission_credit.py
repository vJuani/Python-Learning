"""
Operation -> agent commission -> current account integration.

The operation's persisted ``agent_payment`` is the only calculated
commission source. Human confirmation creates a regular current-account
``commission`` movement through ``agent_account.create_movement``.
No cash or invoicing service is called here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from modules.agent_account import (
    AgentAccountError,
    cancel_movement,
    create_movement,
)
from modules.agent_account_presentation import parse_money_decimal
from modules.database import IntegrityError, get_agent_record
from modules.database.agent_account_repository import (
    CURRENCIES,
    SOURCE_OPERATION,
    STATUS_CONFIRMED,
    list_operation_commission_movements,
)
from modules.database.operation_parties_repository import (
    get_parties_for_operation,
)
from modules.database.operations_repository import get_operation_record
from modules.database.tenant import require_organization_id
from modules.notifications_service import emit_commission_credited


STATE_NOT_READY = "not_ready"
STATE_READY = "ready"
STATE_CREDITED = "credited"

PURPOSE_OWN_COMMISSION = "own_commission"
SOURCE_CURRENCY = "USD"


class OperationCommissionError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def _commission_side(organization_id, operation_id):
    participating = {
        party["party_role"]
        for party in get_parties_for_operation(
            organization_id,
            operation_id,
        )
        if party.get("is_participating")
    }
    if participating == {"seller", "buyer"}:
        return "both"
    if "seller" in participating:
        return "seller"
    if "buyer" in participating:
        return "buyer"
    return "general"


def _positive_decimal(raw):
    try:
        value = Decimal(str(raw))
    except Exception:
        return None
    return value if value > 0 else None


def _load_operation(organization_id, operation_id):
    operation = get_operation_record(
        operation_id,
        organization_id,
    )
    if operation is None:
        raise OperationCommissionError(
            "operation_commission_err_not_found"
        )
    return operation


def build_operation_commission_state(
    organization_id,
    operation_id,
):
    """
    Build the auditable, derived state shown in operation detail.

    ``agent_payment`` is calculated on normalized ``sale_price`` (USD),
    even when the property's original transaction currency is ARS.
    Therefore its truthful source currency is USD; Staff may explicitly
    override the credited snapshot during review.
    """
    organization_id = require_organization_id(organization_id)
    operation = _load_operation(organization_id, operation_id)
    side = _commission_side(organization_id, operation_id)
    agent_id = operation.get("agent_db_id")
    agent = (
        get_agent_record(agent_id, organization_id)
        if agent_id is not None
        else None
    )
    source_amount = _positive_decimal(
        operation.get("agent_payment")
    )

    history = []
    if agent is not None:
        history = list_operation_commission_movements(
            organization_id,
            operation_id,
            agent_id,
        )

    active = next(
        (
            movement
            for movement in history
            if movement.get("status") == STATUS_CONFIRMED
        ),
        None,
    )
    latest_reversed = next(
        (
            movement
            for movement in history
            if movement.get("status") == "reversed"
        ),
        None,
    )

    reasons = []
    if operation.get("status") != "approved":
        reasons.append("operation_commission_not_approved")
    if operation.get("was_invoiced") != "yes":
        reasons.append("operation_commission_not_closed")
    if operation.get("currency_raw") not in CURRENCIES:
        reasons.append("operation_commission_currency_missing")
    if agent is None:
        reasons.append("operation_commission_agent_missing")
    if source_amount is None:
        reasons.append("operation_commission_amount_missing")

    if active is not None:
        state = STATE_CREDITED
    elif reasons:
        state = STATE_NOT_READY
    else:
        state = STATE_READY

    return {
        "state": state,
        "reasons": reasons,
        "operation": operation,
        "agent": agent,
        "commission_side": side,
        "commission_purpose": PURPOSE_OWN_COMMISSION,
        "suggested_amount": (
            float(source_amount)
            if source_amount is not None
            else None
        ),
        "suggested_currency": SOURCE_CURRENCY,
        "source_amount": (
            float(source_amount)
            if source_amount is not None
            else None
        ),
        "source_currency": SOURCE_CURRENCY,
        "active_movement": active,
        "last_reversed_movement": latest_reversed,
        "history": history,
        "is_ready": state == STATE_READY,
        "is_credited": state == STATE_CREDITED,
    }


def _parse_credit_amount(raw, *, language):
    try:
        return parse_money_decimal(raw, language=language)
    except ValueError:
        raise OperationCommissionError(
            "operation_commission_err_invalid_amount"
        ) from None


def credit_operation_commission(
    organization_id,
    operation_id,
    *,
    amount,
    currency,
    created_by_user_id,
    language="es",
):
    """Human-confirmed credit through the existing account service."""
    organization_id = require_organization_id(organization_id)
    state = build_operation_commission_state(
        organization_id,
        operation_id,
    )

    if state["is_credited"]:
        return state["active_movement"]
    if not state["is_ready"]:
        raise OperationCommissionError(
            state["reasons"][0]
            if state["reasons"]
            else "operation_commission_err_not_ready"
        )

    currency = (currency or "").strip().upper()
    if currency not in CURRENCIES:
        raise OperationCommissionError(
            "operation_commission_err_currency_missing"
        )
    amount_decimal = _parse_credit_amount(
        amount,
        language=language,
    )

    operation = state["operation"]
    agent = state["agent"]
    side = state["commission_side"]
    revision = len(state["history"]) + 1
    idempotency_key = (
        "operation-commission:"
        f"{organization_id}:{operation_id}:{agent['id']}:"
        f"{side}:{PURPOSE_OWN_COMMISSION}:v{revision}"
    )

    payload = {
        "movement_type": "commission",
        "currency": currency,
        "amount": str(amount_decimal),
        "description": f"Comisión · {operation['id']}",
        "movement_date": date.today().isoformat(),
        "operation_id": str(operation_id),
        "operation_reference": operation["id"],
        "reference_text": (
            f"{operation['id']} · {operation.get('property') or '—'}"
        ),
        "commission_side": side,
        "commission_purpose": PURPOSE_OWN_COMMISSION,
        "commission_source_amount": str(state["source_amount"]),
        "commission_source_currency": state["source_currency"],
    }

    if operation.get("exchange_rate"):
        payload["exchange_rate"] = str(operation["exchange_rate"])
        payload["exchange_rate_date"] = operation.get("date") or ""
        payload["exchange_rate_source"] = "operation_snapshot"

    try:
        movement = create_movement(
            organization_id,
            agent["id"],
            payload,
            created_by_user_id=created_by_user_id,
            idempotency_key=idempotency_key,
            language=language,
        )
        emit_commission_credited(
            organization_id,
            agent["id"],
            movement["id"],
            currency=currency,
            amount=str(amount_decimal),
            operation_reference=operation["id"],
            actor_user_id=created_by_user_id,
        )
        return movement
    except (AgentAccountError, *IntegrityError):
        # A concurrent request can win the active-credit unique index.
        refreshed = build_operation_commission_state(
            organization_id,
            operation_id,
        )
        if refreshed["is_credited"]:
            return refreshed["active_movement"]
        raise


def reverse_operation_commission(
    organization_id,
    operation_id,
    *,
    created_by_user_id,
    reason,
):
    organization_id = require_organization_id(organization_id)
    reason = (reason or "").strip()
    if not reason:
        raise OperationCommissionError(
            "operation_commission_err_reason_required"
        )

    state = build_operation_commission_state(
        organization_id,
        operation_id,
    )
    movement = state.get("active_movement")
    if movement is None:
        raise OperationCommissionError(
            "operation_commission_err_not_credited"
        )
    if (
        movement.get("organization_id") != organization_id
        or movement.get("source_type") != SOURCE_OPERATION
        or movement.get("source_id") != operation_id
    ):
        raise OperationCommissionError(
            "operation_commission_err_not_credited"
        )

    try:
        return cancel_movement(
            organization_id,
            movement["id"],
            created_by_user_id=created_by_user_id,
            reason=reason,
        )
    except AgentAccountError as error:
        raise OperationCommissionError(
            error.message_key
        ) from error
