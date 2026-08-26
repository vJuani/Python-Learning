"""
Office cash / treasury domain logic.
"""

from __future__ import annotations

from datetime import date, datetime

from modules.database.cash_treasury_repository import (
    create_cash_movement_atomic,
    get_cash_account,
    get_cash_movement,
    list_cash_accounts,
    list_cash_movements,
    reverse_cash_movement_atomic,
    sum_movements_by_type,
)
from modules.database.tenant import require_organization_id


CURRENCIES = ("ARS", "USD")

TYPE_INCOME = "income"
TYPE_EXPENSE = "expense"
TYPE_ADJUSTMENT = "adjustment"
TYPE_OPENING = "opening_balance"
TYPE_REVERSAL = "reversal"

MOVEMENT_TYPES = (
    TYPE_INCOME,
    TYPE_EXPENSE,
    TYPE_ADJUSTMENT,
    TYPE_OPENING,
    TYPE_REVERSAL,
)

PAYMENT_METHODS = (
    "cash",
    "transfer",
    "card",
    "debit",
    "credit",
    "wallet",
    "other",
)

INCOME_CATEGORIES = (
    "agent_fee",
    "invoice_collection",
    "reimbursement",
    "operating_income",
    "other_income",
)

EXPENSE_CATEGORIES = (
    "office_supplies",
    "stationery",
    "utilities",
    "rent",
    "marketing",
    "advertising",
    "cleaning",
    "maintenance",
    "fees",
    "taxes",
    "mobility",
    "meals",
    "bank_fees",
    "other_expense",
)

OPENING_CATEGORY = "opening_balance"

CASH_PER_PAGE_OPTIONS = (20, 50, 100)
DEFAULT_CASH_PER_PAGE = 20


class CashTreasuryError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def format_cash_display_id(movement_number):
    return f"CAJ-{int(movement_number):06d}"


def parse_cash_amount(raw_value):
    if raw_value is None:
        return None

    text = str(raw_value).strip()

    if not text:
        return None

    normalized = text.replace(" ", "")

    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(
                ",",
                ".",
            )
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")

    try:
        return float(normalized)
    except ValueError:
        return None


def parse_cash_date(raw_value):
    if not raw_value:
        return None

    text = str(raw_value).strip()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def categories_for_type(movement_type):
    if movement_type == TYPE_INCOME:
        return INCOME_CATEGORIES

    if movement_type == TYPE_EXPENSE:
        return EXPENSE_CATEGORIES

    if movement_type == TYPE_OPENING:
        return (OPENING_CATEGORY,)

    return INCOME_CATEGORIES + EXPENSE_CATEGORIES


def signed_delta_for_type(movement_type, amount):
    amount = float(amount)

    if movement_type in (
        TYPE_INCOME,
        TYPE_OPENING,
        TYPE_ADJUSTMENT,
    ):
        return amount

    if movement_type == TYPE_EXPENSE:
        return -amount

    raise CashTreasuryError("cash_err_invalid_type")


def get_balances(organization_id):
    organization_id = require_organization_id(
        organization_id
    )
    accounts = {
        item["currency"]: item["cached_balance"]
        for item in list_cash_accounts(organization_id)
    }

    return {
        "ARS": float(accounts.get("ARS", 0) or 0),
        "USD": float(accounts.get("USD", 0) or 0),
    }


def month_bounds(today=None):
    today = today or date.today()
    start = today.replace(day=1)
    return start.isoformat(), today.isoformat()


def build_cash_kpis(organization_id, today=None):
    balances = get_balances(organization_id)
    date_from, date_to = month_bounds(today)
    income = {}
    expense = {}

    for currency in CURRENCIES:
        income[currency] = sum_movements_by_type(
            organization_id,
            currency=currency,
            movement_type=TYPE_INCOME,
            date_from=date_from,
            date_to=date_to,
        ) + sum_movements_by_type(
            organization_id,
            currency=currency,
            movement_type=TYPE_OPENING,
            date_from=date_from,
            date_to=date_to,
        )
        expense[currency] = sum_movements_by_type(
            organization_id,
            currency=currency,
            movement_type=TYPE_EXPENSE,
            date_from=date_from,
            date_to=date_to,
        )

    return {
        "balances": balances,
        "income_month": income,
        "expense_month": expense,
        "period_from": date_from,
        "period_to": date_to,
    }


def validate_movement_payload(raw, *, require_type=True):
    errors = []
    values = {
        "movement_type": (
            raw.get("movement_type") or ""
        ).strip(),
        "currency": (raw.get("currency") or "").strip().upper(),
        "amount": (raw.get("amount") or "").strip(),
        "category": (raw.get("category") or "").strip(),
        "description": (
            raw.get("description") or ""
        ).strip(),
        "payment_method": (
            raw.get("payment_method") or ""
        ).strip(),
        "movement_date": (
            raw.get("movement_date") or ""
        ).strip(),
        "notes": (raw.get("notes") or "").strip(),
    }

    movement_type = values["movement_type"]

    if require_type and movement_type not in (
        TYPE_INCOME,
        TYPE_EXPENSE,
    ):
        errors.append("cash_err_type_required")

    if values["currency"] not in CURRENCIES:
        errors.append("cash_err_currency_invalid")

    amount = parse_cash_amount(values["amount"])

    if amount is None or amount <= 0:
        errors.append("cash_err_amount_invalid")
    else:
        values["amount_value"] = amount

    movement_date = parse_cash_date(
        values["movement_date"]
    ) or date.today()
    values["movement_date_iso"] = movement_date.isoformat()
    values["movement_date"] = movement_date.isoformat()

    allowed_categories = categories_for_type(
        movement_type
    ) if movement_type else ()

    if (
        not values["category"]
        or values["category"] not in allowed_categories
    ):
        errors.append("cash_err_category_invalid")

    if not values["description"]:
        errors.append("cash_err_description_required")

    if values["payment_method"] not in PAYMENT_METHODS:
        errors.append("cash_err_payment_method_invalid")

    return errors, values


def preview_movement(organization_id, values):
    organization_id = require_organization_id(
        organization_id
    )
    currency = values["currency"]
    amount = float(values["amount_value"])
    movement_type = values["movement_type"]
    balances = get_balances(organization_id)
    balance_before = balances.get(currency, 0.0)
    delta = signed_delta_for_type(movement_type, amount)
    balance_after = balance_before + delta

    if delta < 0 and balance_after < -1e-9:
        raise CashTreasuryError(
            "cash_err_insufficient_balance",
            currency=currency,
        )

    return {
        "movement_type": movement_type,
        "currency": currency,
        "amount": amount,
        "category": values["category"],
        "description": values["description"],
        "payment_method": values["payment_method"],
        "movement_date": values["movement_date_iso"],
        "notes": values.get("notes") or "",
        "balance_before": balance_before,
        "balance_after": balance_after,
        "signed_delta": delta,
    }


def confirm_movement(
    organization_id,
    values,
    *,
    user_id,
):
    preview = preview_movement(organization_id, values)

    try:
        movement_id = create_cash_movement_atomic(
            organization_id,
            movement_type=preview["movement_type"],
            currency=preview["currency"],
            amount=preview["amount"],
            category=preview["category"],
            description=preview["description"],
            payment_method=preview["payment_method"],
            movement_date=preview["movement_date"],
            created_by_user_id=user_id,
            notes=preview["notes"] or None,
            signed_delta=preview["signed_delta"],
        )
    except ValueError as error:
        if str(error) == "insufficient_balance":
            raise CashTreasuryError(
                "cash_err_insufficient_balance",
                currency=preview["currency"],
            ) from error
        raise

    return get_cash_movement(movement_id, organization_id)


def set_opening_balances(
    organization_id,
    *,
    amounts_by_currency,
    user_id,
    movement_date=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    movement_date = (
        movement_date or date.today()
    ).isoformat()
    created = []

    for currency, raw_amount in amounts_by_currency.items():
        if currency not in CURRENCIES:
            raise CashTreasuryError(
                "cash_err_currency_invalid"
            )

        amount = parse_cash_amount(raw_amount)

        if amount is None:
            continue

        if amount < 0:
            raise CashTreasuryError(
                "cash_err_amount_invalid"
            )

        if amount == 0:
            continue

        account = get_cash_account(
            organization_id,
            currency,
        )

        if account is not None and abs(
            float(account["cached_balance"])
        ) > 1e-9:
            raise CashTreasuryError(
                "cash_err_opening_already_set",
                currency=currency,
            )

        existing_opening = list_cash_movements(
            organization_id,
            currency=currency,
            movement_type=TYPE_OPENING,
            status="confirmed",
            limit=1,
        )

        if existing_opening:
            raise CashTreasuryError(
                "cash_err_opening_already_set",
                currency=currency,
            )

        movement_id = create_cash_movement_atomic(
            organization_id,
            movement_type=TYPE_OPENING,
            currency=currency,
            amount=amount,
            category=OPENING_CATEGORY,
            description="Opening balance",
            payment_method="other",
            movement_date=movement_date,
            created_by_user_id=user_id,
            signed_delta=amount,
        )
        created.append(
            get_cash_movement(movement_id, organization_id)
        )

    if not created:
        raise CashTreasuryError("cash_err_opening_empty")

    return created


def reverse_movement(
    organization_id,
    movement_id,
    *,
    user_id,
    reason,
):
    try:
        reversal_id = reverse_cash_movement_atomic(
            organization_id,
            movement_id,
            reversed_by_user_id=user_id,
            reversal_reason=reason,
        )
    except ValueError as error:
        key = str(error)
        mapping = {
            "movement_not_found": "cash_err_not_found",
            "already_reversed": "cash_err_already_reversed",
            "cannot_reverse_reversal": (
                "cash_err_cannot_reverse_reversal"
            ),
            "reversal_reason_required": (
                "cash_err_reversal_reason_required"
            ),
            "insufficient_balance": (
                "cash_err_insufficient_balance"
            ),
            "account_missing": "cash_err_account_missing",
        }
        raise CashTreasuryError(
            mapping.get(key, "cash_err_reverse_failed")
        ) from error

    return get_cash_movement(reversal_id, organization_id)


def filter_movements(organization_id, filters):
    search = (filters.get("q") or "").strip()
    movement_number = None

    if search.upper().startswith("CAJ-"):
        try:
            movement_number = int(
                search.split("-", 1)[1]
            )
        except (IndexError, ValueError):
            movement_number = None

    items = list_cash_movements(
        organization_id,
        currency=filters.get("currency") or None,
        movement_type=filters.get("movement_type") or None,
        category=filters.get("category") or None,
        payment_method=(
            filters.get("payment_method") or None
        ),
        created_by_user_id=(
            filters.get("user_id") or None
        ),
        date_from=filters.get("date_from") or None,
        date_to=filters.get("date_to") or None,
        search=None if movement_number else (search or None),
        status=filters.get("status") or None,
    )

    if movement_number is not None:
        items = [
            item
            for item in items
            if item["movement_number"] == movement_number
        ]

    return items


def period_summary(organization_id, filters):
    items = filter_movements(organization_id, filters)
    summary = {
        currency: {
            "income": 0.0,
            "expense": 0.0,
            "opening": 0.0,
            "net": 0.0,
        }
        for currency in CURRENCIES
    }

    for item in items:
        if item["status"] != "confirmed":
            continue

        currency = item["currency"]
        amount = float(item["amount"])
        delta = (
            item["balance_after"] - item["balance_before"]
        )

        if item["movement_type"] == TYPE_INCOME:
            summary[currency]["income"] += amount
        elif item["movement_type"] == TYPE_EXPENSE:
            summary[currency]["expense"] += amount
        elif item["movement_type"] == TYPE_OPENING:
            summary[currency]["opening"] += amount

        summary[currency]["net"] += float(delta)

    return summary
