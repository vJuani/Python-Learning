"""
Cash AI orchestration: analyze → draft → confirm via cash_treasury.
"""

from __future__ import annotations

import secrets
from datetime import date

from modules.cash_ai_provider import (
    CashAiProviderError,
    extract_cash_draft_from_provider,
    get_cash_ai_provider_name,
)
from modules.cash_receipts import (
    CashReceiptError,
    absolute_receipt_path,
    prepare_image_for_ai,
    save_receipt_bytes,
    validate_receipt_upload,
)
from modules.cash_treasury import (
    CURRENCIES,
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    PAYMENT_METHODS,
    TYPE_EXPENSE,
    TYPE_INCOME,
    CashTreasuryError,
    confirm_movement,
    get_balances,
    parse_cash_amount,
    parse_cash_date,
    preview_movement,
    validate_movement_payload,
)
from modules.database.cash_ai_drafts_repository import (
    STATUS_CONFIRMED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_REVIEW,
    create_cash_ai_draft,
    get_cash_ai_draft,
    update_cash_ai_draft,
)
from modules.database.cash_treasury_repository import (
    find_duplicate_cash_movements,
)
from modules.database.tenant import require_organization_id


PAYMENT_UNDETERMINED = "undetermined"

AI_PAYMENT_METHODS = PAYMENT_METHODS + (PAYMENT_UNDETERMINED,)

ALL_AI_CATEGORIES = INCOME_CATEGORIES + EXPENSE_CATEGORIES


class CashAiError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def _normalize_confidence(raw):
    text = str(raw or "").strip().lower()

    if text in ("high", "alta", "0.9", "0.95", "1", "1.0"):
        return "high"

    if text in ("low", "baja", "0.2", "0.3", "0.4"):
        return "low"

    try:
        value = float(text)
        if value >= 0.8:
            return "high"
        if value < 0.5:
            return "low"
    except (TypeError, ValueError):
        pass

    return "medium"


def _normalize_extraction(raw):
    raw = raw or {}
    review = list(raw.get("fields_needing_review") or [])
    review = [str(item).strip() for item in review if item]

    movement_type = (raw.get("movement_type") or "").strip().lower()

    if movement_type not in (TYPE_INCOME, TYPE_EXPENSE):
        movement_type = TYPE_EXPENSE
        if "movement_type" not in review:
            review.append("movement_type")

    currency = (raw.get("currency") or "").strip().upper() or None

    if currency not in CURRENCIES:
        currency = None
        if "currency" not in review:
            review.append("currency")

    amount = parse_cash_amount(raw.get("amount"))

    if amount is None or amount <= 0:
        amount = None
        if "amount" not in review:
            review.append("amount")

    movement_date = parse_cash_date(raw.get("movement_date"))

    if movement_date is None:
        if "movement_date" not in review:
            review.append("movement_date")
        movement_date_iso = None
    else:
        movement_date_iso = movement_date.isoformat()

    category = (raw.get("category") or "").strip()
    allowed = (
        INCOME_CATEGORIES
        if movement_type == TYPE_INCOME
        else EXPENSE_CATEGORIES
    )

    if category not in allowed:
        category = (
            "other_income"
            if movement_type == TYPE_INCOME
            else "other_expense"
        )
        if "category" not in review:
            review.append("category")

    payment_method = (
        raw.get("payment_method") or ""
    ).strip().lower()

    aliases = {
        "efectivo": "cash",
        "transferencia": "transfer",
        "tarjeta": "card",
        "debito": "debit",
        "débito": "debit",
        "credito": "credit",
        "crédito": "credit",
        "mercado pago": "wallet",
        "mercadopago": "wallet",
        "billetera": "wallet",
        "otro": "other",
        "sin determinar": PAYMENT_UNDETERMINED,
        "unknown": PAYMENT_UNDETERMINED,
        "undetermined": PAYMENT_UNDETERMINED,
    }
    payment_method = aliases.get(
        payment_method,
        payment_method,
    )

    if payment_method not in AI_PAYMENT_METHODS:
        payment_method = PAYMENT_UNDETERMINED
        if "payment_method" not in review:
            review.append("payment_method")

    description = (raw.get("description") or "").strip() or None

    if not description:
        if "description" not in review:
            review.append("description")

    merchant = (raw.get("merchant") or "").strip() or None
    receipt_number = (
        raw.get("receipt_number") or ""
    ).strip() or None
    notes = (raw.get("notes") or "").strip() or None

    return {
        "movement_type": movement_type,
        "currency": currency,
        "amount": amount,
        "movement_date": movement_date_iso,
        "category": category,
        "description": description or "",
        "merchant": merchant,
        "payment_method": payment_method,
        "receipt_number": receipt_number,
        "notes": notes or "",
        "confidence": _normalize_confidence(
            raw.get("confidence")
        ),
        "fields_needing_review": sorted(set(review)),
    }


def start_ai_analysis(
    organization_id,
    *,
    user_id,
    user_context_text="",
    file_storage=None,
    language="es",
):
    organization_id = require_organization_id(
        organization_id
    )
    context = (user_context_text or "").strip()
    has_file = bool(
        file_storage is not None
        and getattr(file_storage, "filename", None)
    )

    if not context and not has_file:
        raise CashAiError("cash_ai_err_input_required")

    confirm_token = secrets.token_urlsafe(24)
    payload = None
    image_bytes = None
    image_type = None

    if has_file:
        try:
            payload = validate_receipt_upload(file_storage)
        except CashReceiptError as error:
            raise CashAiError(error.message_key) from error

        image_bytes, image_type = prepare_image_for_ai(
            payload["bytes"],
            payload["content_type"],
        )

    draft_id = create_cash_ai_draft(
        organization_id,
        created_by_user_id=user_id,
        confirm_token=confirm_token,
        user_context_text=context,
        status=STATUS_PROCESSING,
        provider=get_cash_ai_provider_name(),
    )

    attachment = None

    if payload is not None:
        attachment = save_receipt_bytes(
            organization_id,
            payload=payload,
            draft_id=draft_id,
        )
        update_cash_ai_draft(
            draft_id,
            organization_id,
            attachment_path=attachment["relative_path"],
            attachment_hash=attachment["sha256"],
            attachment_content_type=attachment["content_type"],
            attachment_original_name=attachment[
                "original_filename"
            ],
        )

    try:
        raw = extract_cash_draft_from_provider(
            user_context_text=context,
            image_bytes=image_bytes,
            image_content_type=image_type,
            allowed_categories=list(ALL_AI_CATEGORIES),
            allowed_payment_methods=list(AI_PAYMENT_METHODS),
            language=language,
        )
        normalized = _normalize_extraction(raw)
    except CashAiProviderError:
        update_cash_ai_draft(
            draft_id,
            organization_id,
            status=STATUS_FAILED,
            error_message_key="cash_ai_err_provider_failed",
        )
        raise CashAiError("cash_ai_err_provider_failed")

    update_cash_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_REVIEW,
        draft_payload=normalized,
        fields_needing_review=normalized[
            "fields_needing_review"
        ],
        confidence=normalized["confidence"],
        error_message_key="",
    )

    return get_cash_ai_draft(draft_id, organization_id)


def retry_ai_analysis(
    organization_id,
    draft_id,
    *,
    language="es",
):
    draft = get_cash_ai_draft(draft_id, organization_id)

    if draft is None:
        raise CashAiError("cash_ai_err_draft_not_found")

    if draft["status"] == STATUS_CONFIRMED:
        raise CashAiError("cash_ai_err_already_confirmed")

    image_bytes = None
    image_type = None

    if draft.get("attachment_path"):
        path = absolute_receipt_path(
            draft["attachment_path"],
            organization_id,
        )
        raw = path.read_bytes()
        image_bytes, image_type = prepare_image_for_ai(
            raw,
            draft.get("attachment_content_type") or "image/jpeg",
        )

    update_cash_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_PROCESSING,
        error_message_key="",
    )

    try:
        raw = extract_cash_draft_from_provider(
            user_context_text=draft.get("user_context_text") or "",
            image_bytes=image_bytes,
            image_content_type=image_type,
            allowed_categories=list(ALL_AI_CATEGORIES),
            allowed_payment_methods=list(AI_PAYMENT_METHODS),
            language=language,
        )
        normalized = _normalize_extraction(raw)
    except CashAiProviderError:
        update_cash_ai_draft(
            draft_id,
            organization_id,
            status=STATUS_FAILED,
            error_message_key="cash_ai_err_provider_failed",
        )
        raise CashAiError("cash_ai_err_provider_failed")

    update_cash_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_REVIEW,
        draft_payload=normalized,
        fields_needing_review=normalized[
            "fields_needing_review"
        ],
        confidence=normalized["confidence"],
    )

    return get_cash_ai_draft(draft_id, organization_id)


def update_draft_from_form(
    organization_id,
    draft_id,
    form_values,
):
    draft = get_cash_ai_draft(draft_id, organization_id)

    if draft is None:
        raise CashAiError("cash_ai_err_draft_not_found")

    if draft["status"] == STATUS_CONFIRMED:
        raise CashAiError("cash_ai_err_already_confirmed")

    payload = dict(draft.get("draft_payload") or {})
    payload.update(
        {
            "movement_type": form_values.get("movement_type"),
            "currency": form_values.get("currency"),
            "amount": parse_cash_amount(
                form_values.get("amount")
            ),
            "movement_date": form_values.get("movement_date"),
            "category": form_values.get("category"),
            "description": (
                form_values.get("description") or ""
            ).strip(),
            "merchant": (
                form_values.get("merchant") or ""
            ).strip() or None,
            "payment_method": form_values.get(
                "payment_method"
            ),
            "receipt_number": (
                form_values.get("receipt_number") or ""
            ).strip() or None,
            "notes": (
                form_values.get("notes") or ""
            ).strip(),
        }
    )
    normalized = _normalize_extraction(
        {
            **payload,
            "confidence": payload.get("confidence")
            or draft.get("confidence")
            or "medium",
            "fields_needing_review": [],
        }
    )

    # After human edit, clear review flags for filled fields.
    still_review = []
    for field in (
        "movement_type",
        "currency",
        "amount",
        "movement_date",
        "category",
        "description",
        "payment_method",
    ):
        value = normalized.get(field)
        if value in (None, "", PAYMENT_UNDETERMINED):
            still_review.append(field)

    normalized["fields_needing_review"] = still_review
    update_cash_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_REVIEW,
        draft_payload=normalized,
        fields_needing_review=still_review,
        confidence=normalized["confidence"],
    )

    return get_cash_ai_draft(draft_id, organization_id)


def build_review_context(organization_id, draft):
    payload = draft.get("draft_payload") or {}
    preview = None
    preview_error = None
    values_for_preview = None

    if (
        payload.get("currency") in CURRENCIES
        and payload.get("amount")
        and payload.get("movement_type") in (
            TYPE_INCOME,
            TYPE_EXPENSE,
        )
        and payload.get("payment_method")
        not in (None, "", PAYMENT_UNDETERMINED)
        and payload.get("category")
        and payload.get("description")
        and payload.get("movement_date")
    ):
        form = {
            "movement_type": payload["movement_type"],
            "currency": payload["currency"],
            "amount": str(payload["amount"]),
            "category": payload["category"],
            "description": payload["description"],
            "payment_method": payload["payment_method"],
            "movement_date": payload["movement_date"],
            "notes": payload.get("notes") or "",
        }
        errors, values = validate_movement_payload(form)

        if not errors:
            values_for_preview = values
            try:
                preview = preview_movement(
                    organization_id,
                    values,
                )
            except CashTreasuryError as error:
                preview_error = error.message_key

    duplicates = find_potential_duplicates(
        organization_id,
        draft,
    )

    return {
        "draft": draft,
        "payload": payload,
        "balances": get_balances(organization_id),
        "preview": preview,
        "preview_error": preview_error,
        "duplicates": duplicates,
        "review_fields": set(
            draft.get("fields_needing_review") or []
        ),
    }


def find_potential_duplicates(organization_id, draft):
    payload = draft.get("draft_payload") or {}
    matches = []

    if draft.get("attachment_hash"):
        matches.extend(
            find_duplicate_cash_movements(
                organization_id,
                attachment_hash=draft["attachment_hash"],
            )
        )

    if (
        payload.get("amount")
        and payload.get("currency")
        and payload.get("movement_date")
    ):
        similar = find_duplicate_cash_movements(
            organization_id,
            amount=payload["amount"],
            currency=payload["currency"],
            movement_date=payload["movement_date"],
            merchant=payload.get("merchant"),
            receipt_number=payload.get("receipt_number"),
        )
        existing_ids = {item["id"] for item in matches}
        for item in similar:
            if item["id"] not in existing_ids:
                matches.append(item)

    return matches[:5]


def confirm_ai_draft(
    organization_id,
    draft_id,
    *,
    user_id,
    confirm_token,
    form_values=None,
    acknowledge_duplicates=False,
):
    organization_id = require_organization_id(
        organization_id
    )
    draft = get_cash_ai_draft(draft_id, organization_id)

    if draft is None:
        raise CashAiError("cash_ai_err_draft_not_found")

    if draft["confirm_token"] != confirm_token:
        raise CashAiError("cash_ai_err_invalid_token")

    if draft["status"] == STATUS_CONFIRMED:
        if draft.get("confirmed_movement_id"):
            from modules.database.cash_treasury_repository import (
                get_cash_movement,
            )

            return get_cash_movement(
                draft["confirmed_movement_id"],
                organization_id,
            )
        raise CashAiError("cash_ai_err_already_confirmed")

    if form_values:
        draft = update_draft_from_form(
            organization_id,
            draft_id,
            form_values,
        )

    payload = draft.get("draft_payload") or {}

    if payload.get("payment_method") == PAYMENT_UNDETERMINED:
        raise CashAiError("cash_ai_err_payment_required")

    if not acknowledge_duplicates:
        duplicates = find_potential_duplicates(
            organization_id,
            draft,
        )
        if duplicates:
            raise CashAiError(
                "cash_ai_err_possible_duplicate",
                duplicates=duplicates,
            )

    form = {
        "movement_type": payload.get("movement_type"),
        "currency": payload.get("currency") or "",
        "amount": (
            ""
            if payload.get("amount") is None
            else str(payload["amount"])
        ),
        "category": payload.get("category") or "",
        "description": payload.get("description") or "",
        "payment_method": payload.get("payment_method") or "",
        "movement_date": payload.get("movement_date")
        or date.today().isoformat(),
        "notes": payload.get("notes") or "",
    }
    errors, values = validate_movement_payload(form)

    if errors:
        raise CashAiError(errors[0])

    values["merchant"] = payload.get("merchant")
    values["receipt_number"] = payload.get("receipt_number")

    source_reference = json_source_reference(draft)

    movement = confirm_movement(
        organization_id,
        values,
        user_id=user_id,
        source="ai",
        source_reference=source_reference,
        attachment_path=draft.get("attachment_path"),
        attachment_hash=draft.get("attachment_hash"),
        attachment_content_type=draft.get(
            "attachment_content_type"
        ),
        attachment_original_name=draft.get(
            "attachment_original_name"
        ),
        merchant=payload.get("merchant"),
        receipt_number=payload.get("receipt_number"),
    )

    update_cash_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_CONFIRMED,
        confirmed_movement_id=movement["id"],
    )

    return movement


def json_source_reference(draft):
    import json

    return json.dumps(
        {
            "draft_id": draft["id"],
            "confidence": draft.get("confidence"),
            "provider": draft.get("provider"),
            "fields_needing_review": draft.get(
                "fields_needing_review"
            )
            or [],
        },
        separators=(",", ":"),
    )
