"""
Cash AI orchestration: analyze → draft → confirm via cash_treasury.
"""

from __future__ import annotations

import logging
import secrets
from datetime import date

from modules.cash_ai_provider import (
    CashAiProviderError,
    extract_cash_draft_from_provider,
    get_cash_ai_config_status,
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
from modules.config import get_private_upload_root


logger = logging.getLogger(__name__)

PAYMENT_UNDETERMINED = "undetermined"

AI_PAYMENT_METHODS = PAYMENT_METHODS + (PAYMENT_UNDETERMINED,)

ALL_AI_CATEGORIES = INCOME_CATEGORIES + EXPENSE_CATEGORIES


class CashAiError(Exception):
    def __init__(self, message_key, **kwargs):
        super().__init__(message_key)
        self.message_key = message_key
        self.kwargs = kwargs


def map_provider_error(error):
    """Map provider failures to UI keys; keep stage in kwargs."""
    message = str(error)
    stage = getattr(error, "stage", None) or "provider"
    details = getattr(error, "details", None) or {}

    if message == "missing_openai_api_key":
        ui_key = "cash_ai_err_not_configured"
    elif message == "openai_invalid_response":
        ui_key = "cash_ai_err_provider_parse_failed"
    else:
        ui_key = "cash_ai_err_provider_failed"

    logger.error(
        "cash_ai stage=%s provider_error=%s details=%s",
        stage,
        message,
        details,
    )

    return CashAiError(
        ui_key,
        stage=stage,
        provider_error=message,
        details=details,
    )


def log_cash_ai_runtime_config():
    status = get_cash_ai_config_status()
    logger.info(
        "cash_ai config provider_configured=%s provider=%s "
        "model_configured=%s model=%s "
        "openai_api_key_present=%s private_upload_root=%s",
        status["provider_configured"],
        status["provider"],
        status["model_configured"],
        status["model"],
        status["openai_api_key_present"],
        get_private_upload_root(),
    )
    return status


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
        "cash": "cash",
        "transferencia": "transfer",
        "transferencia bancaria": "transfer",
        "transfer bancaria": "transfer",
        "transfer": "transfer",
        "banco": "transfer",
        "tarjeta": "card",
        "card": "card",
        "debito": "debit",
        "débito": "debit",
        "debit": "debit",
        "credito": "credit",
        "crédito": "credit",
        "credit": "credit",
        "mercado pago": "wallet",
        "mercadopago": "wallet",
        "mercado_pago": "wallet",
        "mp": "wallet",
        "billetera": "wallet",
        "wallet": "wallet",
        "otro": "other",
        "other": "other",
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
    log_cash_ai_runtime_config()
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
            logger.warning(
                "cash_ai stage=receipt_validated_failed key=%s",
                error.message_key,
            )
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
    logger.info(
        "cash_ai stage=draft_created draft_id=%s "
        "has_image=%s context_len=%s",
        draft_id,
        image_bytes is not None,
        len(context),
    )

    if payload is not None:
        try:
            attachment = save_receipt_bytes(
                organization_id,
                payload=payload,
                draft_id=draft_id,
            )
        except CashReceiptError as error:
            update_cash_ai_draft(
                draft_id,
                organization_id,
                status=STATUS_FAILED,
                error_message_key=error.message_key,
            )
            raise CashAiError(error.message_key) from error

        # Prove Gunicorn process can re-read what we saved.
        saved_path = absolute_receipt_path(
            attachment["relative_path"],
            organization_id,
        )
        reread = saved_path.read_bytes()
        logger.info(
            "cash_ai stage=receipt_reread_ok path_exists=%s "
            "reread_size=%s matches_upload=%s",
            saved_path.is_file(),
            len(reread),
            len(reread) == payload["size"],
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
    except CashAiProviderError as error:
        mapped = map_provider_error(error)
        update_cash_ai_draft(
            draft_id,
            organization_id,
            status=STATUS_FAILED,
            error_message_key=mapped.message_key,
        )
        raise mapped from error

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
    logger.info(
        "cash_ai stage=draft_ready draft_id=%s "
        "confidence=%s review_fields=%s",
        draft_id,
        normalized.get("confidence"),
        normalized.get("fields_needing_review"),
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
    except CashAiProviderError as error:
        mapped = map_provider_error(error)
        update_cash_ai_draft(
            draft_id,
            organization_id,
            status=STATUS_FAILED,
            error_message_key=mapped.message_key,
        )
        raise mapped from error

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

    merchant = (payload.get("merchant") or "").strip()
    receipt_number = (
        payload.get("receipt_number") or ""
    ).strip()

    # Require a second signal beyond amount+currency+date
    # to avoid false positives that block confirm.
    if (
        payload.get("amount")
        and payload.get("currency")
        and payload.get("movement_date")
        and (merchant or receipt_number)
    ):
        similar = find_duplicate_cash_movements(
            organization_id,
            amount=payload["amount"],
            currency=payload["currency"],
            movement_date=payload["movement_date"],
            merchant=merchant or None,
            receipt_number=receipt_number or None,
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
    logger.info(
        "cash_ai.confirm_received draft_id=%s "
        "user_id=%s acknowledge_duplicates=%s "
        "token_present=%s",
        draft_id,
        user_id,
        acknowledge_duplicates,
        bool(confirm_token),
    )

    draft = get_cash_ai_draft(draft_id, organization_id)

    if draft is None:
        logger.warning(
            "cash_ai.confirm_failed stage=draft_loaded "
            "reason=not_found draft_id=%s",
            draft_id,
        )
        raise CashAiError("cash_ai_err_draft_not_found")

    logger.info(
        "cash_ai.draft_loaded draft_id=%s status=%s",
        draft_id,
        draft.get("status"),
    )

    if draft["confirm_token"] != confirm_token:
        logger.warning(
            "cash_ai.confirm_failed stage=confirm_token_valid "
            "draft_id=%s",
            draft_id,
        )
        raise CashAiError("cash_ai_err_invalid_token")

    logger.info(
        "cash_ai.confirm_token_valid draft_id=%s",
        draft_id,
    )

    if draft["status"] == STATUS_CONFIRMED:
        if draft.get("confirmed_movement_id"):
            from modules.database.cash_treasury_repository import (
                get_cash_movement,
            )

            logger.info(
                "cash_ai.confirm_idempotent draft_id=%s "
                "movement_id=%s",
                draft_id,
                draft["confirmed_movement_id"],
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
        logger.warning(
            "cash_ai.confirm_failed stage=validation "
            "reason=payment_undetermined draft_id=%s",
            draft_id,
        )
        raise CashAiError("cash_ai_err_payment_required")

    if not acknowledge_duplicates:
        duplicates = find_potential_duplicates(
            organization_id,
            draft,
        )
        if duplicates:
            logger.info(
                "cash_ai.confirm_blocked_duplicate "
                "draft_id=%s count=%s",
                draft_id,
                len(duplicates),
            )
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
        logger.warning(
            "cash_ai.confirm_failed stage=validation_passed "
            "draft_id=%s errors=%s payload=%s",
            draft_id,
            errors,
            {
                "movement_type": form["movement_type"],
                "currency": form["currency"],
                "amount": form["amount"],
                "category": form["category"],
                "payment_method": form["payment_method"],
                "movement_date": form["movement_date"],
            },
        )
        raise CashAiError(
            errors[0],
            validation_errors=errors,
        )

    logger.info(
        "cash_ai.validation_passed draft_id=%s",
        draft_id,
    )

    values["merchant"] = payload.get("merchant")
    values["receipt_number"] = payload.get("receipt_number")
    source_reference = json_source_reference(draft)

    logger.info(
        "cash_ai.cash_service_called draft_id=%s",
        draft_id,
    )

    try:
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
    except CashTreasuryError as error:
        logger.warning(
            "cash_ai.confirm_failed stage=cash_service "
            "draft_id=%s error=%s",
            draft_id,
            error.message_key,
        )
        raise

    logger.info(
        "cash_ai.movement_created draft_id=%s "
        "movement_id=%s display_id=%s",
        draft_id,
        movement["id"],
        movement.get("display_id"),
    )

    update_cash_ai_draft(
        draft_id,
        organization_id,
        status=STATUS_CONFIRMED,
        confirmed_movement_id=movement["id"],
    )
    logger.info(
        "cash_ai.draft_marked_confirmed draft_id=%s "
        "movement_id=%s",
        draft_id,
        movement["id"],
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
