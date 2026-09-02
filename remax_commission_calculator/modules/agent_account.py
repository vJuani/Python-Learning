"""
Agent current account (cuenta corriente) domain logic.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from modules.agent_account_presentation import (
    PAYMENT_METHODS,
    agent_list_status,
    enrich_movement_for_display,
    filter_movements_for_display,
    human_balance,
    parse_money_decimal,
)
from modules.database.agent_account_repository import (
    CURRENCIES,
    MOVEMENT_TYPES,
    SOURCE_MANUAL,
    STATUS_CONFIRMED,
    STATUS_REVERSED,
    create_agent_account_movement_atomic,
    get_agent_account_metadata,
    get_agent_balances,
    get_movement_by_idempotency_key,
    list_agent_account_movements,
    list_agents_account_summary,
    reverse_agent_account_movement_atomic,
    sum_payments_collected_month,
    sum_receivable_balances,
)
from modules.database.tenant import require_organization_id


CREDIT_MOVEMENT_TYPES = ("commission", "credit", "payment")
DEBIT_MOVEMENT_TYPES = ("charge", "fee")
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


def _optional_decimal(raw_value):
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    return float(parse_money_decimal(text))


def _build_reference_text(payload, validated):
    explicit = (payload.get("reference_text") or "").strip()
    if explicit:
        return explicit

    movement_type = validated["movement_type"]
    movement_date = validated["movement_date"]
    period_label = (payload.get("period_label") or "").strip()
    payment_method = (payload.get("payment_method") or "").strip()

    if movement_type == "fee" and period_label:
        return f"Fee mensual · {period_label}"
    if movement_type == "payment" and payment_method:
        label = payment_method.replace("_", " ")
        return f"Pago {label} · {movement_date}"
    if movement_type == "commission":
        operation_ref = (
            payload.get("operation_reference") or ""
        ).strip()
        if operation_ref:
            return f"Comisión · {operation_ref}"
    return validated["description"]


def validate_movement_payload(payload, *, language="es"):
    charge_category = (
        payload.get("charge_category") or ""
    ).strip().lower()
    if charge_category:
        from modules.agent_account_charges import (
            validate_charge_payload,
        )

        return validate_charge_payload(
            payload,
            language=language,
        )

    movement_type = (payload.get("movement_type") or "").strip()
    currency = (payload.get("currency") or "").strip().upper()
    description = (payload.get("description") or "").strip()
    movement_date = (payload.get("movement_date") or "").strip()
    adjustment_direction = (
        payload.get("adjustment_direction") or ""
    ).strip().lower()
    notes = (payload.get("notes") or "").strip()
    period_label = (payload.get("period_label") or "").strip()
    payment_method = (
        payload.get("payment_method") or ""
    ).strip().lower()

    if movement_type not in MOVEMENT_TYPES:
        raise AgentAccountError(
            "agent_account_err_invalid_movement_type"
        )
    if currency not in CURRENCIES:
        raise AgentAccountError(
            "agent_account_err_invalid_currency"
        )

    if movement_type == "adjustment" and not description:
        raise AgentAccountError(
            "agent_account_err_description_required"
        )
    if movement_type != "adjustment" and not description:
        if movement_type == "fee" and period_label:
            description = f"Fee {period_label}"
        elif movement_type == "payment":
            description = "Pago recibido"
        else:
            raise AgentAccountError(
                "agent_account_err_description_required"
            )

    if (
        movement_type == "payment"
        and payment_method
        and payment_method not in PAYMENT_METHODS
    ):
        raise AgentAccountError(
            "agent_account_err_invalid_payment_method"
        )

    try:
        amount_decimal = parse_money_decimal(
            payload.get("amount"),
            language=language,
        )
    except ValueError:
        raise AgentAccountError(
            "agent_account_err_invalid_amount"
        ) from None

    amount = float(amount_decimal)
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

    exchange_rate = None
    exchange_rate_date = None
    exchange_rate_source = None
    equivalent_amount_ars = None

    if currency == "USD":
        raw_rate = payload.get("exchange_rate")
        if raw_rate is not None and str(raw_rate).strip():
            try:
                exchange_rate = float(
                    parse_money_decimal(
                        raw_rate,
                        language=language,
                    )
                )
            except ValueError:
                raise AgentAccountError(
                    "agent_account_err_invalid_exchange_rate"
                ) from None
            exchange_rate_date = (
                payload.get("exchange_rate_date") or movement_date
            ).strip()
            exchange_rate_source = (
                payload.get("exchange_rate_source") or "manual"
            ).strip()
            equivalent_amount_ars = float(
                Decimal(str(amount))
                * Decimal(str(exchange_rate))
            )

    validated = {
        "movement_type": movement_type,
        "currency": currency,
        "amount": amount,
        "signed_delta": signed_delta,
        "description": description,
        "movement_date": movement_date,
        "adjustment_direction": adjustment_direction or None,
        "notes": notes or None,
        "period_label": period_label or None,
        "payment_method": payment_method or None,
        "exchange_rate": exchange_rate,
        "exchange_rate_date": exchange_rate_date,
        "exchange_rate_source": exchange_rate_source,
        "equivalent_amount_ars": equivalent_amount_ars,
        "reference_text": None,
    }
    validated["reference_text"] = _build_reference_text(
        payload,
        validated,
    )
    return validated


def create_movement(
    organization_id,
    agent_id,
    payload,
    *,
    created_by_user_id,
    idempotency_key=None,
    language="es",
):
    organization_id = require_organization_id(
        organization_id
    )
    validated = validate_movement_payload(
        payload,
        language=language,
    )

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
            exchange_rate=validated.get("exchange_rate"),
            exchange_rate_date=validated.get("exchange_rate_date"),
            exchange_rate_source=validated.get("exchange_rate_source"),
            equivalent_amount_ars=validated.get("equivalent_amount_ars"),
            payment_method=validated.get("payment_method"),
            reference_text=validated.get("reference_text"),
            notes=validated.get("notes"),
            period_label=validated.get("period_label"),
            charge_category=validated.get("charge_category"),
            net_amount=validated.get("net_amount"),
            vat_rate=validated.get("vat_rate"),
            vat_amount=validated.get("vat_amount"),
            gross_amount=validated.get("gross_amount"),
            billing_period=validated.get("billing_period"),
            recurring=validated.get("recurring", 0),
            recurrence_type=validated.get(
                "recurrence_type",
                "one_time",
            ),
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


def cancel_movement(
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
            "agent_account_err_cancel_reason_required"
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
                "agent_account_err_movement_not_cancellable"
            ) from error
        raise AgentAccountError(
            "agent_account_err_cancel_failed"
        ) from error


reverse_movement = cancel_movement


def _load_display_movements(
    organization_id,
    agent_id,
    *,
    filters,
    language,
    recent_limit=None,
):
    show_cancelled = bool(filters.get("show_cancelled"))
    status = filters.get("status") or None
    if status == STATUS_REVERSED and not show_cancelled:
        show_cancelled = True

    movements = list_agent_account_movements(
        organization_id,
        agent_id,
        currency=filters.get("currency") or None,
        movement_type=filters.get("movement_type") or None,
        status=status,
        date_from=filters.get("date_from") or None,
        date_to=filters.get("date_to") or None,
        limit=recent_limit,
        include_internal_reversals=False,
    )

    visible = filter_movements_for_display(
        movements,
        show_cancelled=show_cancelled,
    )
    return [
        enrich_movement_for_display(
            movement,
            language=language,
        )
        for movement in visible
    ]


def build_staff_index_view(
    organization_id,
    *,
    search_query=None,
    language="es",
):
    organization_id = require_organization_id(
        organization_id
    )
    year_month = date.today().strftime("%Y-%m")
    receivables = sum_receivable_balances(organization_id)
    payments_month = sum_payments_collected_month(
        organization_id,
        year_month=year_month,
    )
    agents = list_agents_account_summary(
        organization_id,
        search_query=search_query,
    )

    for row in agents:
        row["display_ars"] = human_balance(
            row["balance_ars"],
            currency="ARS",
            language=language,
        )
        row["display_usd"] = human_balance(
            row["balance_usd"],
            currency="USD",
            language=language,
        )
        row["list_status"] = agent_list_status(
            row["balance_ars"],
            row["balance_usd"],
        )

    return {
        "kpis": {
            "receivable_ars": receivables["receivable"]["ARS"],
            "receivable_usd": receivables["receivable"]["USD"],
            "pending_agents": receivables["pending_agents"],
            "collected_month_ars": payments_month["ARS"],
            "collected_month_usd": payments_month["USD"],
        },
        "agents": agents,
        "filters": {
            "q": (search_query or "").strip(),
        },
    }


def build_agent_detail_view(
    organization_id,
    agent_id,
    *,
    filters=None,
    language="es",
):
    organization_id = require_organization_id(
        organization_id
    )
    filters = filters or {}
    balances = get_agent_balances(
        organization_id,
        agent_id,
    )

    return {
        "balances": balances,
        "display_balances": {
            currency: human_balance(
                balances[currency],
                currency=currency,
                language=language,
            )
            for currency in CURRENCIES
        },
        "metadata": get_agent_account_metadata(
            organization_id,
            agent_id,
        ),
        "movements": _load_display_movements(
            organization_id,
            agent_id,
            filters=filters,
            language=language,
        ),
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
    language="es",
):
    organization_id = require_organization_id(
        organization_id
    )
    filters = filters or {}
    balances = get_agent_balances(
        organization_id,
        agent_id,
    )

    return {
        "balances": balances,
        "display_balances": {
            currency: human_balance(
                balances[currency],
                currency=currency,
                language=language,
            )
            for currency in CURRENCIES
        },
        "metadata": get_agent_account_metadata(
            organization_id,
            agent_id,
        ),
        "movements": _load_display_movements(
            organization_id,
            agent_id,
            filters=filters,
            language=language,
            recent_limit=recent_limit,
        ),
        "filters": filters,
        "movement_types": MOVEMENT_TYPES,
        "currencies": CURRENCIES,
    }


def movement_signed_display(movement):
    delta = float(movement["balance_after"]) - float(
        movement["balance_before"]
    )
    return delta
