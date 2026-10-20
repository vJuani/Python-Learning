"""
Office cash / treasury domain logic.
"""

from __future__ import annotations

from datetime import date, datetime

from modules.database.cash_treasury_repository import (
    OPERATION_COLLECTION_CATEGORY,
    collection_block_reason,
    create_cash_movement_atomic,
    create_internal_transfer_atomic,
    get_cash_movement,
    get_operation_side_collection,
    list_cash_movements,
    list_participating_operation_sides,
    reverse_cash_movement_atomic,
    sum_movements_by_type,
)
from modules.database.tenant import require_organization_id
from modules.database.treasury_accounts_repository import (
    get_default_treasury_account,
    get_treasury_account,
    list_treasury_accounts,
    suggest_treasury_account_for_payment,
    sum_treasury_balances_by_currency,
)


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
    "commission_collection",
    "invoice_collection",
    "reimbursement",
    "operating_income",
    "other_income",
)

# Income categories that can carry a structured operation + side link.
OPERATION_COLLECTION_CATEGORIES = (
    OPERATION_COLLECTION_CATEGORY,
)

def _raise_operation_link_error(reason, state=None):
    kwargs = {}
    if reason == "operation_side_exceeded" and state:
        kwargs["pending"] = state.get("remaining")
        kwargs["currency"] = state.get("currency")
    raise CashTreasuryError(
        _OPERATION_LINK_ERRORS[reason],
        **kwargs,
    )


_OPERATION_LINK_ERRORS = {
    "invalid_operation": "cash_err_operation_invalid",
    "invalid_operation_side": "cash_err_operation_side_invalid",
    "operation_currency_mismatch": "cash_err_operation_currency",
    "operation_commission_missing": (
        "cash_err_operation_commission_missing"
    ),
    "operation_side_settled": "cash_err_operation_side_settled",
    "operation_side_exceeded": "cash_err_operation_side_exceeds",
}

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
    "agent_payout",
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
    return sum_treasury_balances_by_currency(
        organization_id
    )


def get_treasury_accounts(organization_id, *, currency=None):
    return list_treasury_accounts(
        organization_id,
        currency=currency,
        active_only=True,
    )


def month_bounds(today=None):
    today = today or date.today()
    start = today.replace(day=1)
    return start.isoformat(), today.isoformat()


def build_cash_kpis(
    organization_id,
    today=None,
    *,
    treasury_account_id=None,
    currency_view=None,
):
    balances = get_balances(organization_id)
    date_from, date_to = month_bounds(today)
    income = {}
    expense = {}

    currencies = (
        (currency_view,)
        if currency_view in CURRENCIES
        else CURRENCIES
    )

    for currency in currencies:
        income[currency] = sum_movements_by_type(
            organization_id,
            currency=currency,
            movement_type=TYPE_INCOME,
            date_from=date_from,
            date_to=date_to,
            treasury_account_id=treasury_account_id,
        )
        expense[currency] = sum_movements_by_type(
            organization_id,
            currency=currency,
            movement_type=TYPE_EXPENSE,
            date_from=date_from,
            date_to=date_to,
            treasury_account_id=treasury_account_id,
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

    treasury_account_raw = (
        raw.get("treasury_account_id") or ""
    ).strip()
    if treasury_account_raw:
        try:
            values["treasury_account_id"] = int(
                treasury_account_raw
            )
        except ValueError:
            errors.append(
                "cash_err_treasury_account_invalid"
            )
    else:
        values["treasury_account_id"] = None

    operation_raw = str(
        raw.get("linked_operation_id") or ""
    ).strip()
    role_raw = str(raw.get("linked_party_role") or "").strip()
    values["linked_operation_id"] = None
    values["linked_party_role"] = None
    if operation_raw:
        try:
            values["linked_operation_id"] = int(operation_raw)
        except ValueError:
            errors.append("cash_err_operation_invalid")
    if role_raw:
        if role_raw not in ("buyer", "seller"):
            errors.append("cash_err_operation_side_invalid")
        else:
            values["linked_party_role"] = role_raw
    if (
        values["linked_operation_id"]
        and not values["linked_party_role"]
    ) or (
        values["linked_party_role"]
        and not values["linked_operation_id"]
    ):
        errors.append("cash_err_operation_side_required")
    if (
        values["linked_operation_id"]
        and (
            movement_type != TYPE_INCOME
            or values["category"]
            not in OPERATION_COLLECTION_CATEGORIES
        )
    ):
        errors.append("cash_err_operation_category")

    return errors, values


def preview_movement(organization_id, values):
    organization_id = require_organization_id(
        organization_id
    )
    currency = values["currency"]
    amount = float(values["amount_value"])
    movement_type = values["movement_type"]

    treasury_account_id = values.get(
        "treasury_account_id"
    )
    if treasury_account_id:
        account = get_treasury_account(
            treasury_account_id,
            organization_id,
        )
        if account is None:
            raise CashTreasuryError(
                "cash_err_treasury_account_invalid"
            )
        if account["currency"] != currency:
            raise CashTreasuryError(
                "cash_err_treasury_currency_mismatch"
            )
        balance_before = float(
            account["cached_balance"]
        )
        treasury_account_name = account["name"]
    else:
        suggested = suggest_treasury_account_for_payment(
            organization_id,
            currency,
            values["payment_method"],
        )
        if suggested is None:
            raise CashTreasuryError(
                "cash_err_treasury_account_missing"
            )
        treasury_account_id = suggested["id"]
        balance_before = float(
            suggested["cached_balance"]
        )
        treasury_account_name = suggested["name"]

    delta = signed_delta_for_type(movement_type, amount)
    balance_after = balance_before + delta

    if delta < 0 and balance_after < -1e-9:
        raise CashTreasuryError(
            "cash_err_insufficient_balance",
            currency=currency,
        )

    linked_operation_id = values.get("linked_operation_id")
    linked_party_role = values.get("linked_party_role")
    if linked_operation_id:
        state = get_operation_side_collection(
            organization_id,
            linked_operation_id,
            linked_party_role,
        )
        reason = collection_block_reason(
            state,
            amount,
            currency,
        )
        if reason:
            _raise_operation_link_error(reason, state)

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
        "treasury_account_id": treasury_account_id,
        "treasury_account_name": treasury_account_name,
        "linked_operation_id": linked_operation_id,
        "linked_party_role": linked_party_role,
    }


def confirm_movement(
    organization_id,
    values,
    *,
    user_id,
    source="manual",
    source_reference=None,
    attachment_path=None,
    attachment_hash=None,
    attachment_content_type=None,
    attachment_original_name=None,
    merchant=None,
    receipt_number=None,
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
            source=source,
            source_reference=source_reference,
            attachment_path=attachment_path,
            attachment_hash=attachment_hash,
            attachment_content_type=attachment_content_type,
            attachment_original_name=attachment_original_name,
            merchant=merchant or values.get("merchant"),
            receipt_number=(
                receipt_number
                or values.get("receipt_number")
            ),
            treasury_account_id=preview.get(
                "treasury_account_id"
            ),
            linked_operation_id=preview.get(
                "linked_operation_id"
            ),
            linked_party_role=preview.get(
                "linked_party_role"
            ),
        )
    except ValueError as error:
        if str(error) == "insufficient_balance":
            raise CashTreasuryError(
                "cash_err_insufficient_balance",
                currency=preview["currency"],
            ) from error
        if str(error) in (
            "invalid_treasury_account",
            "inactive_treasury_account",
            "treasury_currency_mismatch",
            "treasury_account_missing",
        ):
            raise CashTreasuryError(
                "cash_err_treasury_account_invalid"
            ) from error
        if str(error) in _OPERATION_LINK_ERRORS:
            state = None
            if str(error) == "operation_side_exceeded":
                linked_id = preview.get("linked_operation_id")
                if linked_id:
                    state = get_operation_side_collection(
                        organization_id,
                        linked_id,
                        preview.get("linked_party_role"),
                    )
            _raise_operation_link_error(str(error), state)
        raise

    return get_cash_movement(movement_id, organization_id)


def collection_settles_commission(expected, collected):
    """True when confirmed collections cover the expected commission."""
    summary = describe_side_collection(expected, collected)
    return (
        summary["expected"] > 0.009
        and summary["pending"] <= 0.009
        and summary["collected"] + 0.009 >= summary["expected"]
    )


def side_collection_ready_to_invoice(
    organization_id,
    operation_id,
    party_role,
):
    """Billing readiness derived only from confirmed treasury collections.

    Reversed movements drop out because they are no longer confirmed.
    Another organization's movements are invisible to this query.
    """
    state = get_operation_side_collection(
        organization_id,
        operation_id,
        party_role,
    )
    if state is None or not state.get("participating"):
        return False
    return collection_settles_commission(
        state.get("expected"),
        state.get("collected"),
    )


def describe_side_collection(expected, collected):
    """Derive collection status from expected commission and confirmed cash."""
    expected_amount = round(float(expected or 0), 2)
    collected_amount = round(float(collected or 0), 2)
    pending = round(max(0.0, expected_amount - collected_amount), 2)
    surplus = round(max(0.0, collected_amount - expected_amount), 2)
    if collected_amount <= 0.009:
        status = "unpaid"
    elif pending <= 0.009:
        status = "collected"
    else:
        status = "partial"
    return {
        "expected": expected_amount,
        "collected": collected_amount,
        "pending": pending,
        "surplus": surplus,
        "status": status,
    }


def build_operation_collection_status(organization_id, operation_id):
    """Collection progress for each participating side.

    Reads confirmed treasury collections only. Nothing is stored on the operation.
    """
    organization_id = require_organization_id(organization_id)
    sides = {}
    currency = "USD"
    for role in ("buyer", "seller"):
        state = get_operation_side_collection(
            organization_id,
            operation_id,
            role,
        )
        if state is None or not state.get("participating"):
            sides[role] = None
            continue
        currency = state.get("currency") or currency
        summary = describe_side_collection(
            state.get("expected"),
            state.get("collected"),
        )
        summary["count"] = int(state.get("count") or 0)
        summary["currency"] = currency
        summary["ready_to_invoice"] = collection_settles_commission(
            summary["expected"],
            summary["collected"],
        )
        sides[role] = summary

    participating = [
        side for side in sides.values() if side
    ]
    return {
        "currency": currency,
        "buyer": sides["buyer"],
        "seller": sides["seller"],
        "expected_total": round(
            sum(side["expected"] for side in participating),
            2,
        ),
        "collected_total": round(
            sum(side["collected"] for side in participating),
            2,
        ),
        "pending_total": round(
            sum(side["pending"] for side in participating),
            2,
        ),
        "surplus_total": round(
            sum(side["surplus"] for side in participating),
            2,
        ),
    }


def list_operation_collection_options(organization_id):
    return list_participating_operation_sides(
        organization_id
    )


def opening_account_ids(organization_id):
    movements = list_cash_movements(
        organization_id,
        movement_type=TYPE_OPENING,
        status="confirmed",
    )
    return {
        movement["treasury_account_id"]
        for movement in movements
        if movement.get("treasury_account_id")
    }


def set_account_opening_balance(
    organization_id,
    *,
    treasury_account_id,
    amount,
    user_id,
    movement_date=None,
):
    organization_id = require_organization_id(
        organization_id
    )
    account = get_treasury_account(
        treasury_account_id,
        organization_id,
    )
    if account is None or not account["is_active"]:
        raise CashTreasuryError(
            "cash_err_treasury_account_invalid"
        )

    parsed_amount = parse_cash_amount(amount)
    if parsed_amount is None or parsed_amount == 0:
        raise CashTreasuryError("cash_err_opening_empty")
    if parsed_amount < 0:
        raise CashTreasuryError("cash_err_amount_invalid")

    if account["id"] in opening_account_ids(organization_id):
        raise CashTreasuryError(
            "cash_err_opening_already_set",
            currency=account["currency"],
        )

    if isinstance(movement_date, date):
        movement_date = movement_date.isoformat()
    else:
        movement_date = movement_date or date.today().isoformat()

    movement_id = create_cash_movement_atomic(
        organization_id,
        movement_type=TYPE_OPENING,
        currency=account["currency"],
        amount=parsed_amount,
        category=OPENING_CATEGORY,
        description="Opening balance",
        payment_method="other",
        movement_date=movement_date,
        created_by_user_id=user_id,
        signed_delta=parsed_amount,
        treasury_account_id=account["id"],
    )
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

        if amount is None or amount == 0:
            continue

        if amount < 0:
            raise CashTreasuryError(
                "cash_err_amount_invalid"
            )

        account = get_default_treasury_account(
            organization_id,
            currency,
        )
        if account is None:
            raise CashTreasuryError(
                "cash_err_treasury_account_invalid"
            )

        created.append(
            set_account_opening_balance(
                organization_id,
                treasury_account_id=account["id"],
                amount=amount,
                user_id=user_id,
                movement_date=movement_date,
            )
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

    treasury_account_id = filters.get(
        "treasury_account_id"
    )
    if treasury_account_id:
        try:
            treasury_account_id = int(
                treasury_account_id
            )
        except (ValueError, TypeError):
            treasury_account_id = None

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
        treasury_account_id=treasury_account_id,
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

        if item.get("source") == "internal_transfer":
            continue

        summary[currency]["net"] += float(delta)

    return summary


def create_internal_transfer(
    organization_id,
    *,
    from_account_id,
    to_account_id,
    amount,
    movement_date,
    user_id,
    description=None,
    notes=None,
    idempotency_key=None,
):
    try:
        result = create_internal_transfer_atomic(
            organization_id,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount=amount,
            movement_date=movement_date,
            created_by_user_id=user_id,
            description=description,
            notes=notes,
            idempotency_key=idempotency_key,
        )
    except ValueError as error:
        key = str(error)
        mapping = {
            "invalid_amount": "cash_err_amount_invalid",
            "same_account_transfer": (
                "cash_err_transfer_same_account"
            ),
            "cross_currency_transfer": (
                "cash_err_transfer_cross_currency"
            ),
            "insufficient_balance": (
                "cash_err_insufficient_balance"
            ),
            "invalid_treasury_account": (
                "cash_err_treasury_account_invalid"
            ),
            "inactive_treasury_account": (
                "cash_err_treasury_account_inactive"
            ),
        }
        raise CashTreasuryError(
            mapping.get(key, "cash_err_transfer_failed")
        ) from error

    return result
