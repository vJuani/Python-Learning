"""
Ephemeral VAT / billing helper formulas.

Pure functions only. No database, Flask, or operation mutations.
Internal math uses Decimal; UI amounts are truncated to 2 decimals.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, InvalidOperation


VAT_RATE = Decimal("0.21")
MINIMUM_VAT_SHARE = Decimal("0.60")
MINIMUM_VAT_BASE_RATIO = Decimal("0.55")
ROUND_MULTIPLE = Decimal("50")
MARTILLERO_SHARE = Decimal("55")
AGENT_SHARE = Decimal("45")
MONEY_CENTS = Decimal("0.01")
HUNDRED = Decimal("100")

TIP_BUYER = "buyer"
TIP_SELLER = "seller"
MODE_MINIMUM_VAT = "minimum_vat"
MODE_COMMISSION_PLUS_VAT = "commission_plus_vat"

VALID_TIPS = (TIP_BUYER, TIP_SELLER)
VALID_MODES = (
    MODE_MINIMUM_VAT,
    MODE_COMMISSION_PLUS_VAT,
)


def to_decimal(value):
    if isinstance(value, Decimal):
        return value

    if value is None:
        raise InvalidOperation("null amount")

    text = str(value).strip().replace(",", ".")
    return Decimal(text)


def truncate_money_2(amount):
    """
    Keep only the first two decimal places for positive amounts.
    Does not round up (uses ROUND_DOWN toward -infinity for positives).
    """
    value = to_decimal(amount)

    if value < 0:
        raise ValueError("truncate_money_2 expects a non-negative amount")

    return (value / MONEY_CENTS).to_integral_value(
        rounding=ROUND_DOWN
    ) * MONEY_CENTS


def tip_commission(operation_amount, tip_percent):
    amount = to_decimal(operation_amount)
    percent = to_decimal(tip_percent)
    return amount * (percent / HUNDRED)


def round_to_nearest_50(amount):
    """
    Round to the nearest multiple of 50.

    Uses Python round() on amount/50 (banker's rounding), matching:
    207.90 -> 200, 225 -> 200, 230 -> 250, etc.
    """
    value = to_decimal(amount)
    scaled = float(value / ROUND_MULTIPLE)
    return Decimal(round(scaled)) * ROUND_MULTIPLE


def minimum_vat(commission):
    """
    Internal minimum-VAT chain (tested; not shown in the UI).
    """
    commission = to_decimal(commission)
    base_60 = commission * MINIMUM_VAT_SHARE
    base_55 = base_60 * MINIMUM_VAT_BASE_RATIO
    iva_exact = base_55 * VAT_RATE
    iva_suggested = round_to_nearest_50(iva_exact)

    return {
        "commission": commission,
        "base_60": base_60,
        "base_55": base_55,
        "iva_exact": iva_exact,
        "iva_suggested": iva_suggested,
    }


def commission_plus_vat(commission):
    commission = to_decimal(commission)
    iva = commission * VAT_RATE
    total = commission + iva

    return {
        "commission": commission,
        "iva": iva,
        "total": total,
    }


def agent_invoice_from_martillero_net(martillero_net_ars):
    """
    Agent client invoice net from martillero net (before VAT):
    neto_martillero / 55 * 45
    """
    net = to_decimal(martillero_net_ars)
    return net / MARTILLERO_SHARE * AGENT_SHARE


def build_client_invoices(vat_usd, exchange_rate):
    """
    From chosen VAT (USD) and FX, build ARS client invoices.

    Internal values keep full Decimal precision.
    Display values are truncated to 2 decimals.
    """
    vat_usd = to_decimal(vat_usd)
    exchange_rate = to_decimal(exchange_rate)

    if exchange_rate <= 0:
        raise ValueError("exchange_rate must be > 0")

    iva_ars_raw = vat_usd * exchange_rate
    martillero_net_raw = iva_ars_raw / VAT_RATE
    agent_net_raw = agent_invoice_from_martillero_net(
        martillero_net_raw
    )

    iva_ars = truncate_money_2(iva_ars_raw)
    martillero_net = truncate_money_2(martillero_net_raw)
    agent_net = truncate_money_2(agent_net_raw)

    return {
        "vat_usd": vat_usd,
        "exchange_rate": exchange_rate,
        "iva_ars_raw": iva_ars_raw,
        "martillero_net_raw": martillero_net_raw,
        "agent_net_raw": agent_net_raw,
        "iva_ars": iva_ars,
        "martillero_net": martillero_net,
        "agent_net": agent_net,
    }


def _parse_decimal(raw_value, field_key, *, allow_empty=False):
    text = str(raw_value or "").strip()

    if text == "":
        if allow_empty:
            return None, None

        return None, field_key

    try:
        value = to_decimal(text)
    except (InvalidOperation, ValueError):
        return None, field_key

    if value < 0:
        return None, field_key

    return value, None


def parse_calculator_inputs(form_values):
    """
    Parse UI inputs into Decimal values.

    Returns (parsed_dict, error_keys_list).
    """
    errors = []
    parsed = {
        "operation_amount": Decimal("0"),
        "buyer_rate": Decimal("0"),
        "seller_rate": Decimal("0"),
        "tip": TIP_BUYER,
        "mode": MODE_MINIMUM_VAT,
        "exchange_rate": None,
        "vat_usd_override": None,
    }

    amount, amount_error = _parse_decimal(
        form_values.get("operation_amount"),
        "err_vat_calc_amount"
    )
    if amount_error:
        errors.append(amount_error)
    else:
        parsed["operation_amount"] = amount

    buyer_rate, buyer_error = _parse_decimal(
        form_values.get("buyer_rate"),
        "err_vat_calc_buyer_rate"
    )
    if buyer_error:
        errors.append(buyer_error)
    else:
        parsed["buyer_rate"] = buyer_rate

    seller_rate, seller_error = _parse_decimal(
        form_values.get("seller_rate"),
        "err_vat_calc_seller_rate"
    )
    if seller_error:
        errors.append(seller_error)
    else:
        parsed["seller_rate"] = seller_rate

    tip = str(
        form_values.get("tip", TIP_BUYER)
    ).strip().lower()
    if tip not in VALID_TIPS:
        errors.append("err_vat_calc_tip")
    else:
        parsed["tip"] = tip

    mode = str(
        form_values.get("mode", MODE_MINIMUM_VAT)
    ).strip().lower()
    if mode not in VALID_MODES:
        errors.append("err_vat_calc_mode")
    else:
        parsed["mode"] = mode

    exchange_raw = form_values.get("exchange_rate", "")
    if str(exchange_raw or "").strip() != "":
        exchange_rate, fx_error = _parse_decimal(
            exchange_raw,
            "err_vat_calc_exchange_rate"
        )
        if fx_error:
            errors.append(fx_error)
        elif exchange_rate <= 0:
            errors.append("err_vat_calc_exchange_rate")
        else:
            parsed["exchange_rate"] = exchange_rate

    # Accept both new and legacy field names.
    override_raw = form_values.get("vat_usd", "")
    if str(override_raw or "").strip() == "":
        override_raw = form_values.get(
            "suggested_vat_override",
            ""
        )

    if str(override_raw or "").strip() != "":
        override, override_error = _parse_decimal(
            override_raw,
            "err_vat_calc_suggested_vat"
        )
        if override_error:
            errors.append(override_error)
        else:
            parsed["vat_usd_override"] = override

    return parsed, errors


def selected_tip_rate(parsed):
    if parsed["tip"] == TIP_SELLER:
        return parsed["seller_rate"]

    return parsed["buyer_rate"]


def build_calculator_result(parsed):
    """
    Compute ephemeral results for the simplified UI.
    """
    commission = tip_commission(
        parsed["operation_amount"],
        selected_tip_rate(parsed)
    )

    vat_auto = None
    internal_minimum = None
    internal_plus = None

    if parsed["mode"] == MODE_MINIMUM_VAT:
        internal_minimum = minimum_vat(commission)
        vat_auto = internal_minimum["iva_suggested"]
    else:
        internal_plus = commission_plus_vat(commission)
        vat_auto = internal_plus["iva"]

    if parsed.get("vat_usd_override") is not None:
        vat_usd = parsed["vat_usd_override"]
    else:
        vat_usd = vat_auto

    result = {
        "tip_commission": commission,
        "tip": parsed["tip"],
        "mode": parsed["mode"],
        "vat_usd_auto": vat_auto,
        "vat_usd": vat_usd,
        "vat_editable": True,
        # Kept for unit tests / internal inspection only.
        "minimum_vat": internal_minimum,
        "commission_plus_vat": internal_plus,
        "billing": None,
    }

    exchange_rate = parsed.get("exchange_rate")

    if exchange_rate is not None:
        invoices = build_client_invoices(
            vat_usd,
            exchange_rate
        )
        result["billing"] = {
            "exchange_rate": invoices["exchange_rate"],
            "iva_ars": invoices["iva_ars"],
            "martillero_net": invoices["martillero_net"],
            "agent_net": invoices["agent_net"],
            "iva_ars_raw": invoices["iva_ars_raw"],
            "martillero_net_raw": invoices[
                "martillero_net_raw"
            ],
            "agent_net_raw": invoices["agent_net_raw"],
        }

    return result


def empty_form_values():
    return {
        "operation_amount": "",
        "buyer_rate": "",
        "seller_rate": "",
        "tip": TIP_BUYER,
        "mode": MODE_MINIMUM_VAT,
        "exchange_rate": "",
        "vat_usd": "",
        "operation_id": "",
    }


def form_values_from_operation(operation):
    values = empty_form_values()

    if operation is None:
        return values

    rate = operation.get("commission_rate")
    sale_price = operation.get("sale_price")

    if sale_price is not None:
        values["operation_amount"] = str(sale_price)

    if rate is not None:
        rate_text = str(rate)
        values["buyer_rate"] = rate_text
        values["seller_rate"] = rate_text

    values["operation_id"] = str(
        operation.get("db_id")
        or operation.get("id")
        or ""
    )

    exchange_rate = operation.get("exchange_rate")
    if exchange_rate not in (None, "", 0, 0.0):
        values["exchange_rate"] = str(exchange_rate)

    return values


# Backward-compatible alias used by older tests/call sites.
def agent_invoice_ars(martillero_net_ars):
    return truncate_money_2(
        agent_invoice_from_martillero_net(martillero_net_ars)
    )
