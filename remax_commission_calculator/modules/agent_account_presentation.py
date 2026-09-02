"""
Presentation helpers for agent current account (human labels, colors).
Accounting balances remain signed in the repository layer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from modules.formatting import format_money


POSITIVE_DISPLAY_TYPES = frozenset(
    {"payment", "commission", "credit"}
)
DEBT_DISPLAY_TYPES = frozenset({"fee", "charge"})
NEUTRAL_DISPLAY_TYPES = frozenset({"adjustment"})

PAYMENT_METHODS = (
    "transfer",
    "cash",
    "card",
    "other",
)


def parse_money_decimal(raw_value, *, language="es"):
    if raw_value is None:
        raise ValueError("invalid_amount")

    text = str(raw_value).strip()
    if not text:
        raise ValueError("invalid_amount")

    normalized = (
        text.replace("$", "")
        .replace("USD", "")
        .replace("ARS", "")
        .strip()
    )

    if language == "es":
        if "," in normalized and "." in normalized:
            normalized = normalized.replace(".", "").replace(",", ".")
        elif "," in normalized:
            normalized = normalized.replace(",", ".")
    else:
        normalized = normalized.replace(",", "")

    try:
        value = Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError("invalid_amount") from error

    if value <= 0:
        raise ValueError("invalid_amount")

    return value


def human_balance(signed_balance, *, currency="USD", language="es"):
    try:
        value = Decimal(str(signed_balance or 0))
    except InvalidOperation:
        value = Decimal("0")

    abs_amount = abs(value)
    if abs_amount < Decimal("0.0000001"):
        return {
            "label_key": "agent_account_balance_even",
            "amount": Decimal("0"),
            "currency": currency,
            "tone": "neutral",
            "formatted": format_money(
                0,
                currency=currency,
                language=language,
            ),
        }

    if value < 0:
        return {
            "label_key": "agent_account_balance_pending",
            "amount": abs_amount,
            "currency": currency,
            "tone": "debt",
            "formatted": format_money(
                abs_amount,
                currency=currency,
                language=language,
            ),
        }

    return {
        "label_key": "agent_account_balance_in_favor",
        "amount": abs_amount,
        "currency": currency,
        "tone": "credit",
        "formatted": format_money(
            abs_amount,
            currency=currency,
            language=language,
        ),
    }


def agent_list_status(balance_ars, balance_usd):
    balances = [balance_ars, balance_usd]
    has_debt = any(
        float(value or 0) < -1e-9 for value in balances
    )
    has_credit = any(
        float(value or 0) > 1e-9 for value in balances
    )
    if has_debt:
        return "pending"
    if has_credit:
        return "in_favor"
    return "even"


def movement_is_internal_reversal(movement):
    if movement is None:
        return False
    if movement.get("is_internal_reversal"):
        return True
    return movement.get("reversed_movement_id") is not None


def movement_display_amount(movement, *, language="es"):
    amount = abs(float(movement.get("amount") or 0))
    movement_type = movement.get("movement_type") or ""
    currency = movement.get("currency") or "USD"

    if movement_type == "adjustment":
        delta = float(movement.get("balance_after") or 0) - float(
            movement.get("balance_before") or 0
        )
        if delta >= 0:
            tone = "credit"
            prefix = "+"
        else:
            tone = "debit"
            prefix = ""
        formatted = format_money(
            amount,
            currency=currency,
            language=language,
        )
        if prefix:
            formatted = f"+ {formatted}"
        return {
            "tone": tone,
            "prefix": prefix,
            "amount": amount,
            "formatted": formatted,
        }

    if movement_type in POSITIVE_DISPLAY_TYPES:
        formatted = format_money(
            amount,
            currency=currency,
            language=language,
        )
        return {
            "tone": "credit",
            "prefix": "+",
            "amount": amount,
            "formatted": f"+ {formatted}",
        }

    formatted = format_money(
        amount,
        currency=currency,
        language=language,
    )
    return {
        "tone": "debit",
        "prefix": "",
        "amount": amount,
        "formatted": formatted,
    }


def movement_balance_after_display(
    movement,
    *,
    language="es",
):
    return human_balance(
        movement.get("balance_after"),
        currency=movement.get("currency") or "USD",
        language=language,
    )


def movement_status_display(movement):
    if movement.get("status") == "reversed":
        return {
            "key": "agent_account_status_cancelled",
            "tone": "muted",
            "show_badge": True,
        }
    return {
        "key": None,
        "tone": "normal",
        "show_badge": False,
    }


def enrich_movement_for_display(movement, *, language="es"):
    enriched = dict(movement)
    enriched["display_amount"] = movement_display_amount(
        movement,
        language=language,
    )
    enriched["display_balance_after"] = (
        movement_balance_after_display(
            movement,
            language=language,
        )
    )
    enriched["display_status"] = movement_status_display(
        movement
    )
    enriched["is_internal_reversal"] = (
        movement_is_internal_reversal(movement)
    )
    return enriched


def filter_movements_for_display(
    movements,
    *,
    show_cancelled=False,
):
    visible = []
    for movement in movements:
        if movement_is_internal_reversal(movement):
            continue
        if (
            not show_cancelled
            and movement.get("status") == "reversed"
        ):
            continue
        visible.append(movement)
    return visible
