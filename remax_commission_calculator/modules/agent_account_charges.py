"""
Agent account charge categories, VAT calculation, and validation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from modules.agent_account_presentation import parse_money_decimal


DEFAULT_VAT_RATE = Decimal("0.21")
TWOPLACES = Decimal("0.01")

CHARGE_CATEGORY_FEE = "fee"
CHARGE_CATEGORY_MAINSTREET = "mainstreet"
CHARGE_CATEGORY_ADVERTISING = "advertising"
CHARGE_CATEGORY_AGENT_EXPENSES = "agent_expenses"
CHARGE_CATEGORY_JRH_SUBSCRIPTION = "jrh_subscription"
CHARGE_CATEGORY_OTHER = "other"

CHARGE_CATEGORIES = (
    CHARGE_CATEGORY_FEE,
    CHARGE_CATEGORY_MAINSTREET,
    CHARGE_CATEGORY_ADVERTISING,
    CHARGE_CATEGORY_AGENT_EXPENSES,
    CHARGE_CATEGORY_JRH_SUBSCRIPTION,
    CHARGE_CATEGORY_OTHER,
)

VAT_MODE_NONE = "none"
VAT_MODE_ADD = "add_vat"
VAT_MODE_GROSS_INCLUDES = "gross_includes_vat"

VAT_MODES = (
    VAT_MODE_NONE,
    VAT_MODE_ADD,
    VAT_MODE_GROSS_INCLUDES,
)

RECURRENCE_ONE_TIME = "one_time"
RECURRENCE_MONTHLY = "monthly"
RECURRENCE_ANNUAL = "annual"

RECURRENCE_TYPES = (
    RECURRENCE_ONE_TIME,
    RECURRENCE_MONTHLY,
    RECURRENCE_ANNUAL,
)


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def compute_vat_amounts(
    *,
    vat_mode,
    input_amount,
    vat_rate=None,
):
    vat_rate = (
        Decimal(str(vat_rate))
        if vat_rate is not None
        else DEFAULT_VAT_RATE
    )
    amount = _quantize_money(Decimal(str(input_amount)))

    if vat_mode == VAT_MODE_NONE:
        net = amount
        rate = Decimal("0")
        vat = Decimal("0")
        gross = amount
    elif vat_mode == VAT_MODE_ADD:
        net = amount
        rate = vat_rate
        vat = _quantize_money(net * rate)
        gross = _quantize_money(net + vat)
    elif vat_mode == VAT_MODE_GROSS_INCLUDES:
        gross = amount
        rate = vat_rate
        if rate > 0:
            net = _quantize_money(gross / (Decimal("1") + rate))
            vat = _quantize_money(gross - net)
        else:
            net = gross
            vat = Decimal("0")
    else:
        raise ValueError("invalid_vat_mode")

    return {
        "net_amount": net,
        "vat_rate": rate,
        "vat_amount": vat,
        "gross_amount": gross,
    }


def charge_category_label_key(charge_category):
    return f"agent_account_charge_cat_{charge_category}"


def build_charge_description(
    charge_category,
    *,
    billing_period=None,
    custom_description=None,
):
    if charge_category == CHARGE_CATEGORY_OTHER:
        return (custom_description or "").strip()

    parts = [charge_category.replace("_", " ").title()]
    if charge_category == CHARGE_CATEGORY_FEE:
        parts[0] = "Fee"
    elif charge_category == CHARGE_CATEGORY_JRH_SUBSCRIPTION:
        parts[0] = "Suscripción JRH One"
    elif charge_category == CHARGE_CATEGORY_ADVERTISING:
        parts[0] = "Destaques / publicidad"
    elif charge_category == CHARGE_CATEGORY_AGENT_EXPENSES:
        parts[0] = "Gastos del agente"

    if billing_period:
        return f"{parts[0]} · {billing_period}"
    return parts[0]


def build_charge_reference_text(
    charge_category,
    *,
    billing_period=None,
    movement_date=None,
):
    label = build_charge_description(
        charge_category,
        billing_period=billing_period,
    )
    if billing_period:
        return label
    if movement_date:
        return f"{label} · {movement_date}"
    return label


def validate_charge_payload(payload, *, language="es"):
    from modules.agent_account import AgentAccountError
    from modules.database.agent_account_repository import CURRENCIES

    charge_category = (
        payload.get("charge_category") or ""
    ).strip().lower()
    currency = (payload.get("currency") or "").strip().upper()
    movement_date = (payload.get("movement_date") or "").strip()
    billing_period = (
        payload.get("billing_period")
        or payload.get("period_label")
        or ""
    ).strip()
    description = (payload.get("description") or "").strip()
    notes = (payload.get("notes") or "").strip()
    reference_text = (payload.get("reference_text") or "").strip()
    vat_mode = (payload.get("vat_mode") or VAT_MODE_NONE).strip()
    recurrence_type = (
        payload.get("recurrence_type") or RECURRENCE_ONE_TIME
    ).strip().lower()
    recurring_raw = payload.get("recurring", "")

    if charge_category not in CHARGE_CATEGORIES:
        raise AgentAccountError(
            "agent_account_err_invalid_charge_category"
        )
    if currency not in CURRENCIES:
        raise AgentAccountError(
            "agent_account_err_invalid_currency"
        )
    if vat_mode not in VAT_MODES:
        raise AgentAccountError(
            "agent_account_err_invalid_vat_mode"
        )
    if recurrence_type not in RECURRENCE_TYPES:
        raise AgentAccountError(
            "agent_account_err_invalid_recurrence_type"
        )

    if charge_category == CHARGE_CATEGORY_OTHER and not description:
        raise AgentAccountError(
            "agent_account_err_description_required"
        )

    try:
        input_amount = parse_money_decimal(
            payload.get("amount"),
            language=language,
        )
    except ValueError:
        raise AgentAccountError(
            "agent_account_err_invalid_amount"
        ) from None

    raw_vat_rate = payload.get("vat_rate")
    vat_rate = DEFAULT_VAT_RATE
    if raw_vat_rate is not None and str(raw_vat_rate).strip():
        try:
            vat_rate = parse_money_decimal(
                raw_vat_rate,
                language=language,
            ) / Decimal("100")
        except ValueError:
            raise AgentAccountError(
                "agent_account_err_invalid_vat_rate"
            ) from None

    vat_breakdown = compute_vat_amounts(
        vat_mode=vat_mode,
        input_amount=input_amount,
        vat_rate=vat_rate,
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

    recurring = str(recurring_raw).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )

    if not description:
        description = build_charge_description(
            charge_category,
            billing_period=billing_period or None,
            custom_description=description or None,
        )

    if not reference_text:
        reference_text = build_charge_reference_text(
            charge_category,
            billing_period=billing_period or None,
            movement_date=movement_date,
        )

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
                vat_breakdown["gross_amount"]
                * Decimal(str(exchange_rate))
            )

    gross = float(vat_breakdown["gross_amount"])

    return {
        "movement_type": "charge",
        "charge_category": charge_category,
        "currency": currency,
        "amount": gross,
        "signed_delta": -gross,
        "net_amount": float(vat_breakdown["net_amount"]),
        "vat_rate": float(vat_breakdown["vat_rate"]),
        "vat_amount": float(vat_breakdown["vat_amount"]),
        "gross_amount": gross,
        "description": description,
        "movement_date": movement_date,
        "billing_period": billing_period or None,
        "period_label": billing_period or None,
        "notes": notes or None,
        "reference_text": reference_text,
        "recurring": 1 if recurring else 0,
        "recurrence_type": recurrence_type,
        "exchange_rate": exchange_rate,
        "exchange_rate_date": exchange_rate_date,
        "exchange_rate_source": exchange_rate_source,
        "equivalent_amount_ars": equivalent_amount_ars,
        "vat_mode": vat_mode,
        "payment_method": None,
    }
