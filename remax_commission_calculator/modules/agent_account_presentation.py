"""
Presentation helpers for agent current account (human labels, colors).
Accounting balances remain signed in the repository layer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from modules.formatting import format_money
from modules.i18n import translate


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
    movement_type = movement.get("movement_type") or ""
    currency = movement.get("currency") or "USD"

    if movement_type in DEBT_DISPLAY_TYPES:
        gross = movement.get("gross_amount")
        amount = abs(
            float(gross if gross is not None else movement.get("amount") or 0)
        )
    else:
        amount = abs(float(movement.get("amount") or 0))

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


def _detail_row(label_key, value, *, language="es", label_override=None):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return {
        "label": label_override or translate(
            label_key,
            language=language,
        ),
        "value": text,
    }


def build_movement_detail_display(
    movement,
    *,
    language="es",
    movement_lookup=None,
    payment_allocations=None,
):
    movement_lookup = movement_lookup or {}
    currency = movement.get("currency") or "USD"
    rows = []

    def add(label_key, value, label_override=None):
        row = _detail_row(
            label_key,
            value,
            language=language,
            label_override=label_override,
        )
        if row:
            rows.append(row)

    movement_type = movement.get("movement_type") or ""
    if movement_type in DEBT_DISPLAY_TYPES:
        net = movement.get("net_amount")
        vat = movement.get("vat_amount")
        gross = movement.get("gross_amount") or movement.get(
            "amount"
        )
        if net is not None:
            add(
                "agent_account_vat_net",
                format_money(
                    net,
                    currency=currency,
                    language=language,
                ),
            )
        if vat is not None and float(vat or 0) > 0:
            rate_pct = float(movement.get("vat_rate") or 0) * 100
            label = (
                f"IVA {rate_pct:.0f}%"
                if rate_pct
                else translate(
                    "agent_account_vat_line",
                    language=language,
                    rate=21,
                )
            )
            add(
                "agent_account_vat_line",
                format_money(
                    vat,
                    currency=currency,
                    language=language,
                ),
                label_override=label,
            )
        if gross is not None:
            add(
                "agent_account_vat_total",
                format_money(
                    gross,
                    currency=currency,
                    language=language,
                ),
            )

    add("agent_account_currency", currency)

    if movement.get("exchange_rate") is not None:
        add(
            "agent_account_exchange_rate",
            format_money(
                movement.get("exchange_rate"),
                currency="ARS",
                language=language,
            ),
        )
        if movement.get("exchange_rate_date"):
            add(
                "agent_account_exchange_rate_date",
                movement.get("exchange_rate_date"),
            )

    period = movement.get("billing_period") or movement.get(
        "period_label"
    )
    if period:
        add("agent_account_period_label", period)

    if movement.get("reference_text"):
        add(
            "agent_account_reference_column",
            movement.get("reference_text"),
        )

    if movement.get("notes"):
        add("agent_account_notes_optional", movement.get("notes"))

    if movement.get("recurring"):
        recurrence = movement.get("recurrence_type") or "one_time"
        add(
            "agent_account_recurrence_type",
            translate(
                f"agent_account_recurrence_{recurrence}",
                language=language,
            ),
        )

    if movement_type == "payment" and movement.get("source_id"):
        if movement.get("source_type") == "cash":
            add(
                "agent_account_payment_cash_origin",
                translate(
                    "agent_account_payment_cash_origin",
                    language=language,
                ),
            )
            add(
                "agent_account_cash_movement",
                f"#{movement.get('source_id')}",
            )
        elif movement.get("source_type") == "manual":
            linked = movement_lookup.get(
                movement.get("source_id")
            )
            if linked:
                add(
                    "agent_account_apply_payment_to",
                    linked.get("description"),
                )

    if movement_type == "payment" and movement.get(
        "payment_method"
    ):
        add(
            "agent_account_payment_method",
            translate(
                f"agent_account_pay_{movement['payment_method']}",
                language=language,
            ),
        )

    if payment_allocations:
        for allocation in payment_allocations:
            label = allocation.get("charge_description") or "—"
            add(
                "agent_account_apply_payment_to",
                (
                    f"{label} — "
                    f"{format_money(allocation['amount'], currency=movement.get('currency') or 'USD', language=language)}"
                ),
            )
    elif (
        movement_type == "payment"
        and not movement.get("source_id")
        and not payment_allocations
    ):
        add(
            "agent_account_apply_payment_to",
            translate(
                "agent_account_payment_general",
                language=language,
            ),
        )

    if movement.get("created_by_username"):
        add(
            "agent_account_created_by",
            movement.get("created_by_username"),
        )

    if movement.get("created_at"):
        add(
            "agent_account_created_at",
            movement.get("created_at")[:10],
        )

    status = movement.get("status")
    if status == "reversed":
        add(
            "agent_account_status_column",
            translate(
                "agent_account_status_cancelled",
                language=language,
            ),
        )
    elif status:
        add(
            "agent_account_status_column",
            translate(
                "agent_account_status_normal",
                language=language,
            ),
        )

    return rows


def format_pending_charge_option(charge, *, language="es"):
    amount_text = format_money(
        charge["pending_amount"],
        currency=charge["currency"],
        language=language,
    )
    status_suffix = ""
    payment_status = charge.get("payment_status")
    if payment_status == "partially_paid":
        status_suffix = " · parcialmente pagado"
    label = (
        f"{charge['description']} — {amount_text} "
        f"pendiente{status_suffix}"
    )
    return {
        "id": charge["id"],
        "description": charge["description"],
        "currency": charge["currency"],
        "pending_amount": charge["pending_amount"],
        "payment_status": payment_status,
        "label": label,
    }


def enrich_movement_for_display(
    movement,
    *,
    language="es",
    movement_lookup=None,
    organization_id=None,
):
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
    lookup = movement_lookup or {}
    enriched["linked_operation"] = None
    enriched["invoice_context"] = None
    operation_detail_rows = []
    if (
        organization_id
        and movement.get("movement_type") == "commission"
        and movement.get("source_type") == "operation"
        and movement.get("source_id")
    ):
        from modules.database.operations_repository import (
            get_operation_record,
        )

        operation = get_operation_record(
            movement["source_id"],
            organization_id,
        )
        if operation is not None:
            enriched["linked_operation"] = {
                "id": operation["db_id"],
                "display_id": operation["id"],
                "property": operation.get("property"),
            }
            operation_detail_rows = [
                    {
                        "label": translate(
                            "operation",
                            language=language,
                        ),
                        "value": operation["id"],
                    },
                    {
                        "label": translate(
                            "property",
                            language=language,
                        ),
                        "value": operation.get("property") or "—",
                    },
                    {
                        "label": translate(
                            "operation_commission_side",
                            language=language,
                        ),
                        "value": translate(
                            "operation_commission_side_"
                            + (
                                movement.get("commission_side")
                                or "general"
                            ),
                            language=language,
                        ),
                    },
                ]

    if (
        organization_id
        and movement.get("movement_type") in ("charge", "fee")
    ):
        from modules.invoicing import get_charge_invoice_context

        invoice_context = get_charge_invoice_context(
            organization_id,
            movement["id"],
        )
        active_invoice = invoice_context.get("active_invoice")
        enriched["invoice_context"] = {
            "is_billable": invoice_context["is_billable"],
            "state": invoice_context["invoice_state"],
            "active_invoice": active_invoice,
            "latest_invoice": invoice_context.get("latest_invoice"),
            "payment": invoice_context.get("payment"),
        }

    if (
        organization_id
        and movement.get("movement_type") == "payment"
    ):
        from modules.database.agent_account_payment_repository import (
            list_payment_allocations,
        )

        enriched["payment_allocations"] = (
            list_payment_allocations(
                organization_id,
                movement["id"],
            )
        )
    else:
        enriched["payment_allocations"] = []

    enriched["display_detail"] = build_movement_detail_display(
        movement,
        language=language,
        movement_lookup=lookup,
        payment_allocations=enriched["payment_allocations"],
    )
    enriched["display_detail"].extend(operation_detail_rows)
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
