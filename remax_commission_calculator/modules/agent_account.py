"""
Agent current account (cuenta corriente) domain logic.
"""

from __future__ import annotations

from datetime import date, datetime

from modules.database.agent_account_repository import (
    CURRENCIES,
    MOVEMENT_TYPES,
    SOURCE_MANUAL,
    STATUS_CONFIRMED,
    count_movements_in_month,
    create_agent_account_movement_atomic,
    get_agent_account_movement,
    get_agent_balances,
    get_movement_by_idempotency_key,
    list_agent_account_movements,
    list_agents_account_summary,
    reverse_agent_account_movement_atomic,
    sum_organization_balances,
)
from modules.database.tenant import require_organization_id


CREDIT_MOVEMENT_TYPES = ("commission", "credit")
DEBIT_MOVEMENT_TYPES = ("charge", "fee", "payment")
ADJUSTMENT_DIRECTIONS = ("credit", "debit")


class AgentAccountError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def signed_delta_for_movement(
    movement_type,
    amount,
    *,
    adjustment_direction=None,
):
    value = float(amount)
    if value <= 0:
        raise AgentAccountError("agent_account_err_invalid_amount")

    if movement_type in CREDIT_MOVEMENT_TYPES:
        return value
    if movement_type in DEBIT_MOVEMENT_TYPES:
        return -value
    if movement_type == "adjustment":
        if adjustment_direction == "credit":
            return value
        if adjustment_direction == "debit":
            return -value
        raise AgentAccountError(
            "agent_account_err_adjustment_direction"
        )

    raise AgentAccountError(
        "agent_account_err_invalid_movement_type"
    )


def validate_movement_payload(payload):
    movement_type = (payload.get("movement_type") or "").strip()
    currency = (payload.get("currency") or "").strip().upper()
    description = (payload.get("description") or "").strip()
    movement_date = (payload.get("movement_date") or "").strip()
    adjustment_direction = (
        payload.get("adjustment_direction") or ""
    ).strip().lower()

    if movement_type not in MOVEMENT_TYPES:
        raise AgentAccountError(
            "agent_account_err_invalid_movement_type"
        )
    if currency not in CURRENCIES:
        raise AgentAccountError(
            "agent_account_err_invalid_currency"
        )
    if not description:
        raise AgentAccountError(
            "agent_account_err_description_required"
        )

    raw_amount = payload.get("amount")
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        raise AgentAccountError(
            "agent_account_err_invalid_amount"
        ) from None

    signed_delta = signed_delta_for_movement(
        movement_type,
        amount,
        adjustment_direction=adjustment_direction,
    )

    if not movement_date:
        movement_date = date.today().isoformat()
    else:
        try:
            datetime.strptime(movement_date, "%Y-%m-%d")
        except ValueError:
            raise AgentAccountError(
                "agent_account_err_invalid_date"
            ) from None

    return {
        "movement_type": movement_type,
        "currency": currency,
        "amount": amount,
        "signed_delta": signed_delta,
        "description": description,
        "movement_date": movement_date,
        "adjustment_direction": adjustment_direction or None,
    }


def create_movement(
    organization_id,
    agent_id,
    payload,
    *,
    created_by_user_id,
    idempotency_key=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    validated = validate_movement_payload(payload)

    if idempotency_key:
        existing = get_movement_by_idempotency_key(
            organization_id,
            idempotency_key,
        )
        if existing is not None:
            return existing

    try:
        return create_agent_account_movement_atomic(
            organization_id,
            agent_id,
            movement_type=validated["movement_type"],
            currency=validated["currency"],
            amount=validated["amount"],
            signed_delta=validated["signed_delta"],
            description=validated["description"],
            movement_date=validated["movement_date"],
            created_by_user_id=created_by_user_id,
            source_type=SOURCE_MANUAL,
            idempotency_key=idempotency_key,
        )
    except ValueError as error:
        key = str(error)
        if key == "invalid_amount":
            raise AgentAccountError(
                "agent_account_err_invalid_amount"
            ) from error
        raise AgentAccountError(
            "agent_account_err_create_failed"
        ) from error


def reverse_movement(
    organization_id,
    movement_id,
    *,
    created_by_user_id,
    reason,
):
    organization_id = require_organization_id(
        organization_id
    )
    reason = (reason or "").strip()
    if not reason:
        raise AgentAccountError(
            "agent_account_err_reversal_reason_required"
        )

    try:
        return reverse_agent_account_movement_atomic(
            organization_id,
            movement_id,
            created_by_user_id=created_by_user_id,
            reversal_reason=reason,
        )
    except ValueError as error:
        key = str(error)
        if key == "movement_not_found":
            raise AgentAccountError(
                "agent_account_err_movement_not_found"
            ) from error
        if key == "movement_not_reversible":
            raise AgentAccountError(
                "agent_account_err_movement_not_reversible"
            ) from error
        raise AgentAccountError(
            "agent_account_err_reverse_failed"
        ) from error


def build_staff_index_view(
    organization_id,
    *,
    search_query=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    year_month = date.today().strftime("%Y-%m")
    org_totals = sum_organization_balances(organization_id)

    return {
        "kpis": {
            "balance_ars": org_totals["totals"]["ARS"],
            "balance_usd": org_totals["totals"]["USD"],
            "pending_agents": org_totals["pending_agents"],
            "movements_month": count_movements_in_month(
                organization_id,
                year_month=year_month,
            ),
        },
        "agents": list_agents_account_summary(
            organization_id,
            search_query=search_query,
        ),
        "filters": {
            "q": (search_query or "").strip(),
        },
    }


def build_agent_detail_view(
    organization_id,
    agent_id,
    *,
    filters=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    filters = filters or {}

    movements = list_agent_account_movements(
        organization_id,
        agent_id,
        currency=filters.get("currency") or None,
        movement_type=filters.get("movement_type") or None,
        date_from=filters.get("date_from") or None,
        date_to=filters.get("date_to") or None,
    )

    return {
        "balances": get_agent_balances(
            organization_id,
            agent_id,
        ),
        "movements": movements,
        "filters": filters,
        "movement_types": MOVEMENT_TYPES,
        "currencies": CURRENCIES,
    }


def build_my_account_view(
    organization_id,
    agent_id,
    *,
    filters=None,
    recent_limit=50,
):
    organization_id = require_organization_id(
        organization_id
    )
    filters = filters or {}

    movements = list_agent_account_movements(
        organization_id,
        agent_id,
        currency=filters.get("currency") or None,
        movement_type=filters.get("movement_type") or None,
        date_from=filters.get("date_from") or None,
        date_to=filters.get("date_to") or None,
        limit=recent_limit,
    )

    return {
        "balances": get_agent_balances(
            organization_id,
            agent_id,
        ),
        "movements": movements,
        "filters": filters,
        "movement_types": MOVEMENT_TYPES,
        "currencies": CURRENCIES,
    }


def movement_signed_display(movement):
    delta = float(movement["balance_after"]) - float(
        movement["balance_before"]
    )
    return delta
